"""Pulse heartbeat: <run_dir>/pulse plus hang_after_s in state.json."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from src.config import Config
from src.loop.pulse import Pulse
from src.loop.run import run
from tests.loop import fakes as scripted


def _read_pulse(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_pulse_alive_ticks(tmp_path: Path):
    pulse = Pulse(tmp_path, interval_s=0.05)
    try:
        first = _read_pulse(tmp_path / "pulse")["alive"]
        deadline = time.time() + 5
        second = first
        while time.time() < deadline:
            second = _read_pulse(tmp_path / "pulse")["alive"]
            if second > first:
                break
            time.sleep(0.01)
        assert second > first
        assert (tmp_path / "pulse").read_text() and True
        data = _read_pulse(tmp_path / "pulse")
        assert set(data) == {"alive", "progress", "progress_ts"}
        assert data["progress"] == 0
    finally:
        pulse.stop()
    assert not pulse._thread.is_alive()


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


def _init(tmp_path: Path, plan: str) -> Path:
    scripted.git(tmp_path, "init")
    scripted.git(tmp_path, "config", "user.email", "tero@test")
    scripted.git(tmp_path, "config", "user.name", "tero")
    scripted.write_file(tmp_path, "plan.md", plan)
    scripted.write_file(tmp_path, "src/__init__.py", "")
    scripted.write_file(
        tmp_path, "tests/test_smoke.py", "def test_smoke():\n    assert True\n"
    )
    scripted.git(tmp_path, "add", "-A")
    scripted.git(tmp_path, "commit", "-m", "init")
    return tmp_path


@pytest.mark.asyncio
async def test_pulse_progress_after_each_step(tmp_path: Path):
    repo = _init(tmp_path, PLAN_ONE)
    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            ),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    started = time.time()
    outcome = await run(_cfg(repo), agents=agents)
    assert outcome.exit_code == 0
    data = _read_pulse(Path(outcome.run_dir) / "pulse")
    # player call + gate + judge + commit-phase = 4 completed steps
    assert data["progress"] == 4
    assert data["progress_ts"] >= started


@pytest.mark.asyncio
async def test_pulse_write_error_does_not_crash_run(tmp_path: Path):
    # Every pulse write fails (read-only run dir): the run still finishes.
    repo = _init(tmp_path, PLAN_ONE)
    import src.loop.pulse as pulse_mod

    real_cls = pulse_mod.Pulse

    class BrokenPulse(real_cls):  # type: ignore[valid-type, misc]
        def _write(self, *a, **k):
            raise OSError("read-only run dir")

    pulse_mod.Pulse = BrokenPulse  # type: ignore[assignment]
    try:
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
    finally:
        pulse_mod.Pulse = real_cls  # type: ignore[assignment]
    assert outcome.exit_code == 0


@pytest.mark.asyncio
async def test_state_has_hang_after_s(tmp_path: Path):
    from src.loop.state import RunState

    repo = _init(tmp_path, PLAN_ONE)
    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            ),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    cfg = _cfg(
        repo,
        player_timeout_s=111,
        judge_timeout_s=222,
        tester_timeout_s=333,
        test_timeout_s=44,
        stall_timeout_s=55,
    )
    outcome = await run(cfg, agents=agents)
    assert outcome.exit_code == 0
    saved = RunState.read(Path(outcome.run_dir) / "state.json") or {}
    assert saved.get("hang_after_s") == max(111, 222, 333, 44, 55) + 300


def test_pulse_write_error_real_readonly_dir(tmp_path: Path):
    target = tmp_path / "runs" / "x"
    target.mkdir(parents=True)
    pulse = Pulse(target, interval_s=60)
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IXUSR)
        # Never raises even when the dir cannot be written.
        try:
            pulse.progress()
        finally:
            os.chmod(target, 0o755)
        assert pulse._thread.is_alive()
    finally:
        pulse.stop()
