"""Экзамен судей: одноразовый judge-вызов на каждый CLI.

Настоящий запуск — только руками (`python3 -m tools.judge_exam.run --adapters`),
не в гейте. Слой 2 (кейсы, метки, пороги) в этом шаге не делается.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

EXAM_DIFF = '''--- a/exam.py
+++ b/exam.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
'''

EXAM_PROMPT = """Exam repo below has a trivial diff (one function with an obvious fix).
Review it as a strict coach: verdict SHIP if the fix is right, else NO_SHIP.

```diff
{diff}
```
""".format(diff=EXAM_DIFF)

_NOT_SCHEMA_RU = "не по схеме"


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tero@test"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "tero"], cwd=repo, check=True, capture_output=True
    )
    (repo / "exam.py").write_text("def add(a, b):\n    return a - b\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "exam base"], cwd=repo, check=True, capture_output=True)
    (repo / "exam.py").write_text("def add(a, b):\n    return a + b\n")


def _default_factory(working_dir: str) -> Callable[[str], Any]:
    from src.providers import create_provider

    def factory(name: str):
        return create_provider(name)

    return factory


def _judge_ctx():
    from src.loop.schemas import ValidateCtx

    return ValidateCtx()


def exam_adapter(
    provider: str,
    model: str,
    factory: Callable[[str], Any] | None = None,
) -> dict:
    """Одноразовое репо + judge-вызов через LiveAgents и judge_prompt."""
    from src.loop.agents import LiveAgents
    from src.loop.prompts import judge_prompt

    started = time.monotonic()
    verdict = ""
    ok = False
    error = ""
    with tempfile.TemporaryDirectory(prefix="tero-judge-exam-") as tmp:
        repo = Path(tmp)
        _init_repo(repo)
        agents = LiveAgents(
            str(repo),
            factory=factory or _default_factory(str(repo)),
            retry_wait_s=0,
        )
        try:
            data = asyncio.run(
                agents.run_role(
                    "judge",
                    provider=provider,
                    model=model,
                    prompt=judge_prompt(preamble="exam", contract=EXAM_PROMPT),
                    timeout=600,
                    ctx=_judge_ctx(),
                )
            )
        except Exception as exc:  # stub упал до ответа
            error = str(exc) or type(exc).__name__
            data = None
        if isinstance(data, dict) and "__invalid__" in data:
            errors = data.get("errors") or ["no JSON object in the final message"]
            error = f"{_NOT_SCHEMA_RU}: {'; '.join(errors)}"
        elif isinstance(data, dict) and "__unavailable__" in data:
            error = str(data.get("error") or "provider unavailable")
        elif isinstance(data, dict) and data.get("verdict") in ("SHIP", "NO_SHIP", "NEEDS_USER"):
            verdict = data["verdict"]
            ok = True
        elif not error:
            error = f"{_NOT_SCHEMA_RU}: no verdict"
    return {
        "provider": provider,
        "model": model,
        "ok": ok,
        "verdict": verdict,
        "seconds": time.monotonic() - started,
        "error": error,
    }
