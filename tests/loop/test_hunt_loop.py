"""Red proof tests for hunt bugs B01,B02,B04,B08,B09,B10,B12,B16 (writer A)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import GateResult, fingerprint
from src.loop.git import current_branch, secret_paths
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from src.loop.plan import load_phases, load_preamble
from src.loop.run import run
from tests.loop.fakes import FakeAgents, finding, judge_returns, no_ship, player_writes, ship, write_file

PLAN = """# Demo

## Add greet

- [ ] greet() returns hello
"""

PLAN_TWO = """# Demo

## Phase one

- [ ] one

## Phase two

- [ ] two
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path, plan: str = PLAN) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tero@test")
    _git(tmp_path, "config", "user.name", "tero")
    write_file(tmp_path, "plan.md", plan)
    write_file(tmp_path, "src/__init__.py", "")
    write_file(tmp_path, "tests/test_smoke.py", "def test_smoke():\n    assert True\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


def _run_cfg(repo: Path, **overrides) -> Config:
    data = {
        "plan_file": "plan.md",
        "working_dir": str(repo),
        "max_rounds": 8,
        "test_timeout_s": 30,
    }
    data.update(overrides)
    return Config(**data)


async def _run_phase(repo: Path, agents: FakeAgents, cfg: Config | None = None, plan_path: Path | None = None):
    from src.loop.gates import detect

    cfg = cfg or _cfg()
    plan_path = plan_path or (repo / "plan.md")
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(plan_path)
    outcome = await run_phase(
        phases[0],
        repo=repo,
        plan_path=plan_path,
        preamble=load_preamble(plan_path),
        cfg=cfg,
        agents=agents,
        test_command=detect(repo),
        ledger=ledger,
    )
    return outcome, ledger


def test_B01_quoted_secret_path_is_detected(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "é").mkdir(parents=True, exist_ok=True)
    (repo / "é" / ".env").write_text("SECRET=1\n")
    _git(repo, "add", "-A")
    paths = secret_paths(repo)
    assert any(p == "é/.env" for p in paths), f"secret .env not detected, got {paths!r}"


@pytest.mark.asyncio
async def test_B02_tester_path_outside_repo_stays_untouched(tmp_path: Path):
    repo = _repo(tmp_path)
    ext_dir = tmp_path.parent / (tmp_path.name + "_ext")
    ext_dir.mkdir(parents=True, exist_ok=True)
    ext_file = ext_dir / "test_evil.py"
    sentinel = "def test_evil():\n    assert True\n"
    ext_file.write_text(sentinel)
    rel = os.path.relpath(ext_file, repo)

    def tester_returns_outside(role, kwargs):
        assert role == "tester"
        return {"tests": [{"finding_id": "F1", "path": rel, "name": "test_evil"}], "skipped": []}

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester", problem="returns nope")])),
            tester_returns_outside,
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    await _run_phase(repo, agents)
    assert ext_file.exists(), f"outside tester file was deleted: {rel}"
    assert ext_file.read_text() == sentinel, "outside tester file was modified"


def test_B04_gate_timeout_returns_timed_out(tmp_path: Path):
    from src.loop.gates import _run

    try:
        result = _run(["sh", "-c", "echo hi; sleep 5"], "pytest", tmp_path, 1)
    except TypeError as exc:
        pytest.fail(f"gate crashed with TypeError instead of GateResult(timed_out=True): {exc}")
    assert result.timed_out is True, f"expected timed_out=True, got {result!r}"
    assert isinstance(result.output, str) and "hi" in result.output, f"expected str output with 'hi', got {result.output!r}"


@pytest.mark.asyncio
async def test_B08_resume_does_not_rerun_shipped_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _repo(tmp_path, PLAN_TWO)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    gate = {"test_command": "python -m pytest -q -p no:cacheprovider"}
    orig = current_branch(repo).strip() or "master"

    def player_one(role, kwargs):
        assert role == "player"
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    def player_two(role, kwargs):
        assert role == "player"
        write_file(repo, "src/other.py", "X = 1\n")
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    agents1 = FakeAgents(
        repo,
        [
            player_one,
            judge_returns(ship()),
            player_two,
            judge_returns({"verdict": "NEEDS_USER", "previous": [], "findings": [], "user_question": "q?"}),
        ],
    )
    outcome1 = await run(_run_cfg(repo, **gate), agents=agents1)
    assert outcome1.phases_shipped == 1, f"setup failed, first run should ship phase 1: {outcome1!r}"
    subprocess.run(["git", "clean", "-fd"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", orig], cwd=repo, check=True, capture_output=True, text=True)

    agents2 = FakeAgents(
        repo,
        [
            player_two,
            judge_returns(ship()),
            player_two,
            judge_returns(ship()),
        ],
    )
    await run(_run_cfg(repo, **gate), agents=agents2)
    players = [c for c in agents2.calls if c == "player"]
    assert players == ["player"], f"shipped phase 1 ran again, player calls: {agents2.calls}"


def test_B09_error_without_failed_ids_has_no_fingerprint():
    result = GateResult(ok=False, exit_code=1, output="ERROR tests/a.py - boom\n1 error", command_name="pytest")
    assert fingerprint(result) is None, f"expected None fingerprint, got {fingerprint(result)!r}"


@pytest.mark.asyncio
async def test_B10_outside_plan_keeps_checkbox_on_secrets(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tero@test")
    _git(repo, "config", "user.name", "tero")
    write_file(repo, "src/__init__.py", "")
    write_file(repo, "tests/test_smoke.py", "def test_smoke():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    plan_path = tmp_path / "plan_outside.md"
    plan_path.write_text(PLAN)

    def player_with_env(_role, _kw):
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, ".env", "SECRET=1\n")
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    agents = FakeAgents(repo, [player_with_env, judge_returns(ship())])
    cfg = _cfg()
    outcome, _ = await _run_phase(repo, agents, cfg, plan_path=plan_path)
    assert outcome.reason == "secrets", f"setup failed, expected secrets stop: {outcome!r}"
    text = plan_path.read_text()
    assert "- [x]" not in text and "- [ ]" in text, f"checkbox was marked done before secrets stop: {text!r}"


@pytest.mark.asyncio
async def test_B12_empty_model_still_escalates_on_provider(tmp_path: Path):
    repo = _repo(tmp_path)
    stuck = no_ship([finding(problem="still broken")], previous=[{"id": "F1", "state": "still_open"}])
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(no_ship([finding(problem="still broken")])),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(stuck),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(stuck),
        ],
    )
    outcome, ledger = await _run_phase(
        repo, agents, _cfg(escalation_provider="codex", escalation_model="")
    )
    kinds = [json.loads(line)["kind"] for line in ledger.path.read_text().splitlines()]
    assert "escalate" in kinds, f"expected escalate event with provider set, got {kinds}"
    assert outcome.escalated is True, f"expected escalated=True, got {outcome!r}"
    assert outcome.reason == "stuck", f"expected stuck after escalation, got {outcome!r}"


@pytest.mark.asyncio
async def test_B16_unrecognized_checkboxes_are_an_error(tmp_path: Path):
    repo = _repo(tmp_path, "* [ ] one\n  - [ ] two\n")
    outcome = await run(_run_cfg(repo), agents=FakeAgents(repo, []))
    assert outcome.exit_code == 1, f"expected exit 1 for bad plan, got {outcome!r}"
