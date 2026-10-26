"""run.py и cli_entry.py: общий цикл фаз, HUD флагом, команды отдельно."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from src.config import Config
from tests.loop import fakes as scripted

ROOT = Path(__file__).resolve().parent.parent.parent

PLAN = """# Demo

## Add greet

- [ ] greet() returns hello
"""

HELLO = "def greet():\n    return 'hello'\n"

INVALID = {"__invalid__": True, "errors": ["'verdict' is a required property"], "raw": ""}


def _calls_to(name: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    found = []
    for path in sorted((ROOT / "src/loop").glob("**/*.py")):
        tree = ast.parse(path.read_text())
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.Call):
                    callee = node.func
                    called = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
                    if called == name:
                        found.append((path.relative_to(ROOT).as_posix(), func.name))
    return sorted(set(found)), found


def test_run_and_resume_share_loop():
    places, calls = _calls_to("run_phase")
    assert places == [("src/loop/session.py", "_run_one")]  # called only from run_phases
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_resume_does_not_launch_hud(tmp_path, monkeypatch):
    import src.loop.session as session_mod
    from src.loop.run import resume, run

    launches: list[Path] = []
    monkeypatch.setattr(session_mod, "maybe_launch_hud", lambda cfg, run_dir: launches.append(run_dir))

    repo = scripted.init_repo(tmp_path, PLAN)
    cfg = Config(plan_file="plan.md", working_dir=str(repo), max_rounds=8, test_timeout_s=30)
    agents = scripted.FakeAgents(
        repo,
        [scripted.player_writes(repo, "src/greet.py", HELLO)]
        + [scripted.judge_returns(dict(INVALID)) for _ in range(3)],
    )
    outcome = await run(cfg, agents=agents)
    assert outcome.status == "judge_pending"
    assert len(launches) == 1

    out = await resume(repo, Path(outcome.run_dir), agents=scripted.FakeAgents(
        repo, [scripted.judge_returns(scripted.ship())]
    ))
    assert out.exit_code == 0, out.reason
    assert len(launches) == 1


def test_cli_help_lists_commands():
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli_entry", "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    for command in ("go", "history", "hud", "wait", "status", "answer", "compact", "review", "resume"):
        assert command in proc.stdout


def test_run_reexports():
    import src.cli_entry as cli
    import src.commands.inbox_cmds as inbox_cmds
    import src.commands.runs as runs
    import src.loop.report as report
    import src.loop.session as session
    from src.loop.run import RunOutcome, phase_summary_line, rounds_word, slugify

    assert RunOutcome is session.RunOutcome
    assert phase_summary_line is report.phase_summary_line
    assert rounds_word is report.rounds_word
    assert slugify is report.slugify
    assert cli.run_review is runs.run_review
    assert cli.run_answer is inbox_cmds.run_answer


def test_run_py_sizes():
    assert len((ROOT / "src/loop/run.py").read_text().splitlines()) <= 450
    assert len((ROOT / "src/cli_entry.py").read_text().splitlines()) <= 300
    for path in sorted(ROOT.glob("src/loop/**/*.py")) + sorted(ROOT.glob("src/commands/**/*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = node.end_lineno - node.lineno + 1
                assert length <= 120, f"{path.relative_to(ROOT)}::{node.name} is {length}"


def test_new_modules_import_no_phase_run_cli():
    for rel in (
        "src/loop/session.py", "src/loop/preflight.py", "src/loop/report.py",
        "src/commands/__init__.py", "src/commands/runs.py", "src/commands/inbox_cmds.py",
    ):
        tree = ast.parse((ROOT / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in ("src.loop.phase", "src.loop.run", "src.cli_entry"), rel
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("src.loop.phase", "src.loop.run", "src.cli_entry"), rel
