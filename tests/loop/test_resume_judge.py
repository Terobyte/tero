"""Resume a judge_pending run: only the judge runs, on the ref tree."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.config import Config
from src.loop import snapshot as snapshot_module
from src.loop.run import resume, run as loop_run
from src.loop.state import RunState
from tests.loop import fakes as scripted

FakeAgents = scripted.FakeAgents
judge_returns = scripted.judge_returns
player_writes = scripted.player_writes
ship = scripted.ship

PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""

HELLO = "def greet():\n    return 'hello'\n"

INVALID = {
    "__invalid__": True,
    "errors": ["'verdict' is a required property"],
    "raw": "looks good to me",
}


def _cfg(repo: Path) -> Config:
    return Config(plan_file="plan.md", working_dir=str(repo), max_rounds=8, test_timeout_s=30)


async def _pending_run(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(dict(INVALID)),
            judge_returns(dict(INVALID)),
            judge_returns(dict(INVALID)),
        ],
    )
    outcome = await loop_run(_cfg(repo), agents=agents)
    assert outcome.status == "judge_pending"
    return repo, outcome


@pytest.mark.asyncio
async def test_resume_runs_only_judge(tmp_path: Path):
    repo, outcome = await _pending_run(tmp_path)
    run_dir = Path(outcome.run_dir)
    state = RunState.read(run_dir / "state.json")
    assert state is not None
    ref, base = state["snapshot_ref"], state["base"]

    scripted.write_file(repo, "src/manual.py", "X = 1\n")

    seen: dict = {}

    def judge_checks_tree(role: str, kwargs: dict):
        assert role == "judge"
        seen["tree"] = snapshot_module.tree_of(repo, base)
        return ship()

    agents = FakeAgents(repo, [judge_checks_tree])
    out = await resume(repo, run_dir, agents=agents)
    assert out.exit_code == 0, out.reason
    assert agents.calls == ["judge"]
    assert seen["tree"] == snapshot_module.ref_tree(repo, ref)
    assert "- [x] greet() returns hello" in (repo / "plan.md").read_text()


@pytest.mark.asyncio
async def test_resume_keeps_manual_changes_in_ref(tmp_path: Path):
    repo, outcome = await _pending_run(tmp_path)
    run_dir = Path(outcome.run_dir)
    state = RunState.read(run_dir / "state.json")
    assert state is not None
    base = state["base"]

    scripted.write_file(repo, "src/manual.py", "MANUAL_MARKER = 42\n")

    agents = FakeAgents(repo, [judge_returns(ship())])
    out = await resume(repo, run_dir, agents=agents)
    assert out.exit_code == 0, out.reason

    before = f"refs/tero/{run_dir.name}/before-resume"
    assert scripted.git(repo, "rev-parse", "--verify", before).strip()
    diff = snapshot_module.diff_for(repo, base, before)
    assert "MANUAL_MARKER" in diff


@pytest.mark.asyncio
async def test_resume_other_status_refuses(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(ship()),
        ],
    )
    outcome = await loop_run(_cfg(repo), agents=agents)
    assert outcome.status == "ok"
    state = RunState.read(Path(outcome.run_dir) / "state.json")
    assert state is not None
    assert state.get("status") == "done"

    out = await resume(repo, Path(outcome.run_dir), agents=FakeAgents(repo, []))
    assert out.exit_code == 1
    assert "resume для `done` пока не умеет" in out.reason

    root = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli_entry", "resume", "-w", str(repo)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "resume для `done` пока не умеет" in (proc.stdout + proc.stderr)


@pytest.mark.asyncio
async def test_resume_without_config_refuses(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    run_dir = repo / ".tero" / "runs" / "20200101-000000-demo"
    run_dir.mkdir(parents=True)
    RunState(run_dir / "state.json").update(
        status="judge_pending", round=1, snapshot_ref="refs/tero/x", base="abc"
    )
    out = await resume(repo, run_dir, agents=FakeAgents(repo, []))
    assert out.exit_code == 1
    assert "прогон старше resume" in out.reason
