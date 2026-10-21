"""Run start gates: dirty tree, red suite, unknown provider, resume."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.config import Config
from src.loop.run import run
from tests.loop import fakes as scripted


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init(tmp_path: Path, plan: str, *, failing: bool = False) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tero@test")
    _git(tmp_path, "config", "user.name", "tero")
    scripted.write_file(tmp_path, "plan.md", plan)
    scripted.write_file(tmp_path, "src/__init__.py", "")
    if failing:
        scripted.write_file(
            tmp_path,
            "tests/test_smoke.py",
            "def test_smoke():\n    assert False\n",
        )
    else:
        scripted.write_file(
            tmp_path,
            "tests/test_smoke.py",
            "def test_smoke():\n    assert True\n",
        )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _cfg(repo: Path, **overrides) -> Config:
    data = {
        "plan_file": "plan.md",
        "working_dir": str(repo),
        "max_rounds": 8,
        "test_timeout_s": 30,
    }
    data.update(overrides)
    return Config(**data)


PLAN_ONE = """# Demo

## Add greet

- [ ] greet() returns hello
"""

PLAN_RESUME = """# Demo

## Done already

- [x] smoke

## Add greet

- [ ] greet() returns hello
"""


@pytest.mark.asyncio
async def test_dirty_tree_refuses_start(tmp_path: Path):
    repo = _init(tmp_path, PLAN_ONE)
    (repo / "README").write_text("dirty\n")
    outcome = await run(_cfg(repo), agents=scripted.FakeAgents(repo, []))
    assert outcome.exit_code == 1
    assert outcome.reason.startswith("dirty")
    assert "README" in outcome.reason


@pytest.mark.asyncio
async def test_dirty_tree_reason_carries_status_lines(tmp_path: Path):
    repo = _init(tmp_path, PLAN_ONE)
    (repo / "README").write_text("dirty\n")
    (repo / "NOTES").write_text("also dirty\n")
    outcome = await run(_cfg(repo), agents=scripted.FakeAgents(repo, []))
    assert outcome.exit_code == 1
    assert outcome.reason.startswith("dirty")
    assert "README" in outcome.reason
    assert "NOTES" in outcome.reason


@pytest.mark.asyncio
async def test_red_tests_at_start_refuse(tmp_path: Path):
    repo = _init(tmp_path, PLAN_ONE, failing=True)
    outcome = await run(_cfg(repo), agents=scripted.FakeAgents(repo, []))
    assert outcome.exit_code == 1
    assert outcome.reason == "red_start"


@pytest.mark.asyncio
async def test_start_gate_prints_command_and_red_tail(tmp_path: Path, capsys):
    repo = _init(tmp_path, PLAN_ONE, failing=True)
    outcome = await run(_cfg(repo), agents=scripted.FakeAgents(repo, []))
    assert outcome.exit_code == 1
    out = capsys.readouterr().out
    assert "стартовый прогон тестов:" in out
    assert "КРАСНЫЕ" in out
    assert "assert False" in out or "test_smoke" in out


@pytest.mark.asyncio
async def test_check_ready_prints_status_line(tmp_path: Path):
    import io
    import contextlib

    repo = _init(tmp_path, PLAN_ONE)

    def ready(_provider, _model):
        return True, ""

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await run(
            _cfg(repo),
            agents=scripted.FakeAgents(
                repo,
                [
                    scripted.player_writes(
                        repo, "src/greet.py", "def greet():\n    return 'hello'\n"
                    ),
                    scripted.judge_returns(scripted.ship()),
                ],
            ),
            check_ready=ready,
        )
    assert "проверка провайдеров" in buf.getvalue()


@pytest.mark.asyncio
async def test_unknown_provider_refuses_start(tmp_path: Path):
    repo = _init(tmp_path, PLAN_ONE)
    outcome = await run(
        _cfg(repo, player_provider="zai"),
        agents=scripted.FakeAgents(repo, []),
    )
    assert outcome.exit_code == 1
    assert "zai" in outcome.reason


@pytest.mark.asyncio
async def test_resume_skips_done_phases(tmp_path: Path):
    repo = _init(tmp_path, PLAN_RESUME)
    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            ),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome = await run(_cfg(repo), agents=agents)
    assert outcome.exit_code == 0
    assert outcome.phases_shipped == 1
    assert outcome.phases_total == 2
    assert agents.calls == ["player", "judge"]
    assert "- [x] greet() returns hello" in (repo / "plan.md").read_text()


@pytest.mark.asyncio
async def test_stop_reports_run_dir(tmp_path: Path):
    repo = _init(tmp_path, PLAN_ONE)
    agents = scripted.FakeAgents(
        repo, [lambda _r, _k: {"__unavailable__": True, "error": "down"}]
    )
    outcome = await run(_cfg(repo), agents=agents)
    assert outcome.exit_code == 2
    assert (Path(outcome.run_dir) / "ledger.jsonl").is_file()


PLAN_DONE = """# Demo

## Done already

- [x] smoke
"""


@pytest.mark.asyncio
async def test_all_phases_done_is_nothing_to_do(tmp_path: Path):
    repo = _init(tmp_path, PLAN_DONE)
    outcome = await run(_cfg(repo), agents=scripted.FakeAgents(repo, []))
    assert outcome.exit_code == 0
    assert outcome.reason == "nothing_to_do"
    assert outcome.phases_total == 1
    assert not (repo / ".tero" / "runs").exists()
