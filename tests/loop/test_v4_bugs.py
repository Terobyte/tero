"""tero-v4 review bugs (2026-10-24, tag archive/tero-v4) ported to v5 (B11).

Each test pins the fixed behavior (red on v5 before the port, except 3 and 4):

1. run(): an unexpected exception mid-phase escapes without rollback and
   leaves the worktree dirty (crash instead of a stop outcome).
2. gates: non-UTF-8 test output raises UnicodeDecodeError instead of
   returning a GateResult.
3. gates: a suite timeout kills only the direct child; grandchildren the
   suite spawned (dev servers, daemons) survive.
4. phase: checkboxes are marked by line numbers captured at load time, so
   a player edit of the plan marks the wrong (no) checkboxes.
5. phase: _revert_non_test only reverts paths listed by `git ls-files -co`,
   so gitignored files the tester touched survive the revert.
6. run(): tester-provider readiness is required even when the repo has no
   tests and the tester can never be called.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import TestCommand, detect, run_all
from src.loop.git import status_short
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from src.loop.plan import load_phases, load_preamble
from src.loop.run import run
from tests.loop import fakes as scripted

PLAN_ONE = """# Demo

## Add greet

- [ ] greet() returns hello
"""

PLAN_TWO_PHASES = """# Demo

## Add greet

- [ ] greet() returns hello

## Add bye

- [ ] bye() returns goodbye
"""


def _cfg(repo: Path, **overrides) -> Config:
    data = {
        "plan_file": "plan.md",
        "working_dir": str(repo),
        "max_rounds": 8,
        "test_timeout_s": 30,
    }
    data.update(overrides)
    return Config(**data)


def _repo_no_tests(tmp_path: Path) -> Path:
    scripted.git(tmp_path, "init")
    scripted.git(tmp_path, "config", "user.email", "tero@test")
    scripted.git(tmp_path, "config", "user.name", "tero")
    scripted.write_file(tmp_path, "plan.md", PLAN_ONE)
    scripted.write_file(tmp_path, "src/__init__.py", "")
    scripted.git(tmp_path, "add", "-A")
    scripted.git(tmp_path, "commit", "-m", "init")
    return tmp_path


# --- bug 1: unexpected exception mid-phase must stop with a rollback ---


@pytest.mark.asyncio
async def test_unexpected_phase_exception_rolls_back_and_stops(tmp_path: Path, monkeypatch):
    # v5 restores a deleted plan.md, so the v4 trigger ships now; any bug in tero
    # mid-phase is the real trigger: here the checkbox write raises.
    import src.loop.phase as phase_mod
    from src.loop import snapshot as snap
    from src.loop.state import RunState

    repo = scripted.init_repo(tmp_path, PLAN_ONE)

    def boom(*a, **k):
        raise RuntimeError("bug in tero")

    monkeypatch.setattr(phase_mod, "mark_phase_done", boom)

    def player_touches(role, kwargs):
        (repo / "src/__init__.py").write_text("# touched by player\n")
        return scripted.PLAYER_DONE

    agents = scripted.FakeAgents(repo, [player_touches, scripted.judge_returns(scripted.ship())])
    outcome = await run(_cfg(repo), agents=agents)

    assert outcome.status == "stop"
    assert outcome.reason == "crash"
    # the half-done work leaves the tree but is kept in the snapshot ref
    assert status_short(repo) == ""
    state = RunState.read(Path(outcome.run_dir) / "state.json") or {}
    assert state.get("status") == "stopped" and "bug in tero" in (state.get("text") or "")
    ref = state.get("snapshot_ref") or ""
    assert ref and snap.verify(repo, ref)
    assert "touched by player" in scripted.git(repo, "show", f"{ref}:src/__init__.py")


# --- bug 2: gate must not crash on non-UTF-8 test output ---


def test_gate_output_with_invalid_utf8_returns_result_not_crash(tmp_path: Path):
    code = "import sys; sys.stdout.buffer.write(b'before \\xff after\\n'); sys.exit(3)"
    command = TestCommand(
        name="override", run_all=[sys.executable, "-c", code], run_files=None
    )
    result = run_all(command, repo=tmp_path, timeout_s=30)

    assert result.ok is False
    assert result.exit_code == 3
    assert "before" in result.output


# --- bug 3: gate timeout must not leak spawned grandchildren ---


def test_gate_timeout_kills_grandchildren(tmp_path: Path):
    marker = tmp_path / "grandchild.done"
    grandchild = (
        "import sys, time; time.sleep(2); open(sys.argv[1], 'w').write('done')"
    )
    parent = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}, sys.argv[1]])\n"
        "time.sleep(30)\n"
    )
    command = TestCommand(
        name="override",
        run_all=[sys.executable, "-c", parent, str(marker)],
        run_files=None,
    )

    result = run_all(command, repo=tmp_path, timeout_s=1)
    assert result.timed_out

    # The grandchild writes the marker at ~2 s if it survived the kill.
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if marker.exists():
            pytest.fail("grandchild survived the gate timeout (marker appeared)")
        time.sleep(0.1)


# --- bug 4: plan edits must not break checkbox marking ---


@pytest.mark.asyncio
async def test_plan_edited_midphase_marks_own_checkboxes(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN_TWO_PHASES)
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")

    def player_inserts_plan_line(role, kwargs):
        assert role == "player"
        text = (repo / "plan.md").read_text()
        (repo / "plan.md").write_text("Note the player added.\n\n" + text)
        return scripted.PLAYER_DONE

    agents = scripted.FakeAgents(
        repo, [player_inserts_plan_line, scripted.judge_returns(scripted.ship())]
    )
    outcome = await run_phase(
        phases[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=_cfg(repo),
        agents=agents,
        test_command=detect(repo),
        ledger=ledger,
    )
    assert outcome.status == "ship"

    fresh = load_phases(repo / "plan.md")
    assert fresh[0].done, "phase 1 checkbox must be marked despite the shift"
    assert not fresh[1].done, "phase 2 must stay untouched"


# --- bug 5: tester writes into gitignored paths must be reverted ---


@pytest.mark.asyncio
async def test_tester_cannot_modify_ignored_paths(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN_ONE)
    scripted.write_file(repo, ".gitignore", "secret.env\n")
    scripted.write_file(repo, "secret.env", "ORIGINAL\n")
    scripted.git(repo, "add", ".gitignore")
    scripted.git(repo, "commit", "-m", "gitignore")

    def player_step(role, kwargs):
        (repo / "src/__init__.py").write_text("# v2\n")
        return scripted.PLAYER_DONE

    def tester_step(role, kwargs):
        (repo / "secret.env").write_text("HACKED\n")
        (repo / "tests/test_red.py").write_text("def test_red():\n    assert False\n")
        return {
            "tests": [{"finding_id": "F1", "path": "tests/test_red.py", "name": "red"}],
            "skipped": [],
        }

    agents = scripted.FakeAgents(
        repo,
        [
            player_step,
            scripted.judge_returns(
                scripted.no_ship([scripted.finding(route="tester")])
            ),
            tester_step,
        ],
    )
    outcome = await run_phase(
        load_phases(repo / "plan.md")[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=_cfg(repo, max_rounds=1),
        agents=agents,
        test_command=detect(repo),
        ledger=Ledger(new_run_dir(repo, "demo")),
    )
    assert outcome.status == "stop"  # fuse: one round is all the script needs

    assert (repo / "secret.env").read_text() == "ORIGINAL\n"


# --- bug 6: tester readiness must not gate a run without tests ---


@pytest.mark.asyncio
async def test_run_without_tests_skips_tester_readiness(tmp_path: Path):
    repo = _repo_no_tests(tmp_path)  # nothing for detect() to find

    def check_ready(provider: str, model: str) -> tuple[bool, str]:
        if provider == "gemini":  # the default tester provider
            return False, "not installed"
        return True, ""

    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'hi'\n"),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome = await run(_cfg(repo), agents=agents, check_ready=check_ready)

    assert outcome.status == "ok"
