"""Обёртка JS-тестов relay: node --test с явным списком файлов.

Без node в PATH — skip, если не задан TERO_REQUIRE_NODE;
с TERO_REQUIRE_NODE=1 — fail. Поиск node: PATH, затем
~/.nvm/versions/node/*/bin/node (прогон с --detach может не видеть nvm в PATH).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

RELAY_ROOT = Path(__file__).resolve().parent.parent.parent / "relay"
# Контракт 08-4: явный список relay/test/*.test.js; widget-тесты фазы 9 —
# корневые widget/*.test.js, тоже явным списком.
ROOT = RELAY_ROOT.parent
JS_TESTS = sorted((RELAY_ROOT / "test").glob("*.test.js"))
WIDGET_TESTS = sorted((ROOT / "widget").glob("*.test.js")) if (ROOT / "widget").is_dir() else []


def _find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        cands = sorted(nvm.glob("*/bin/node"))
        if cands:
            return str(cands[-1])
    return None


def _run_node(node: str, files: list[Path]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, "--test", *[str(f) for f in files]],
        cwd=RELAY_ROOT.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_worker_js():
    files = [p for p in [*JS_TESTS, *WIDGET_TESTS] if p.is_file()]
    assert files, "нет JS-тестов relay/test/*.test.js"
    node = _find_node()
    required = os.environ.get("TERO_REQUIRE_NODE") == "1"
    if node is None:
        if required:
            pytest.fail("TERO_REQUIRE_NODE=1, но node не найден (PATH и ~/.nvm пустые)")
        pytest.skip("node не найден в PATH")
    proc = _run_node(node, files)
    assert proc.returncode == 0, (
        f"node --test красный:\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
    )
