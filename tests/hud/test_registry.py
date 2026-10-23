"""Registry of live runs (spec part A, T4a).

`Registry` lives in `~/.tero/registry/<run_id>.json` (pid, pid_started,
path to `state.json`); every run writes it; HUD takes "+1 run" from it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest

import psutil

from src.hud import registry as registry_mod
from src.hud.registry import Registry, is_dead, pid_is_alive


def _entry(pid, pid_started, state_path="some/state.json", run_id="r1"):
    return {
        "run_id": run_id,
        "pid": pid,
        "pid_started": pid_started,
        "state_path": state_path,
    }


def test_pid_is_alive_matches_start_time(monkeypatch):
    class FakeProc:
        def __init__(self, create_time):
            self._ct = create_time

        def create_time(self):
            return self._ct

    started = 1000.5
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: pid == 4242)
    monkeypatch.setattr(
        registry_mod.psutil, "Process", lambda pid=None: FakeProc(started)
    )
    assert pid_is_alive(4242, started) is True
    assert pid_is_alive(4242, started + 1.0) is False
    assert pid_is_alive(4243, started) is False


def test_pid_is_alive_returns_false_for_bad_pid(monkeypatch):
    called = []
    monkeypatch.setattr(
        psutil, "pid_exists", lambda pid: called.append(pid) or True
    )
    assert pid_is_alive("x", 1.0) is False
    assert pid_is_alive(-1, 1.0) is False
    assert called == []


def test_is_dead_active_status_without_process(monkeypatch):
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    assert is_dead({"status": "starting", "pid": 4242, "pid_started": 1.0}) is True
    assert is_dead({"status": "running", "pid": 4242, "pid_started": 1.0}) is True
    assert is_dead({"status": "done", "pid": 4242, "pid_started": 1.0}) is False
    assert is_dead({"status": "stopped", "pid": 4242, "pid_started": 1.0}) is False


def test_is_dead_reused_pid_is_dead(monkeypatch):
    """A reused pid (same number, different start time) is not our process."""

    class FakeProc:
        def create_time(self):
            return 2000.0

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(registry_mod.psutil, "Process", lambda pid=None: FakeProc())
    assert is_dead({"status": "running", "pid": 4242, "pid_started": 1000.0}) is True
    assert is_dead({"status": "running", "pid": 4242, "pid_started": 2000.0}) is False


def test_is_dead_live_process_is_not_dead():
    pid = os.getpid()
    started = psutil.Process(pid).create_time()
    assert is_dead({"status": "running", "pid": pid, "pid_started": started}) is False
    assert is_dead({"status": "starting", "pid": pid, "pid_started": started}) is False


def test_registry_register_and_unregister(tmp_path):
    reg = Registry(tmp_path / "registry")
    path = reg.register("run-1", tmp_path / "state.json")
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-1"
    assert data["pid"] == os.getpid()
    assert isinstance(data["pid_started"], float)
    assert data["state_path"] == str(tmp_path / "state.json")
    reg.unregister("run-1")
    assert not path.exists()


def test_registry_lists_live_runs_only(tmp_path):
    reg = Registry(tmp_path / "registry")
    reg.register("live", tmp_path / "live" / "state.json")
    dead = _entry(2**31 - 1, 1000.0, run_id="dead")
    (reg.base_dir).mkdir(parents=True, exist_ok=True)
    (reg.base_dir / "dead.json").write_text(
        json.dumps(dead), encoding="utf-8"
    )
    (reg.base_dir / "junk.json").write_text("not json\n", encoding="utf-8")
    live = reg.list_live()
    assert [e["run_id"] for e in live] == ["live"]
    assert not (reg.base_dir / "dead.json").exists()
    assert not (reg.base_dir / "junk.json").exists()
    assert (reg.base_dir / "live.json").exists()


@pytest.mark.asyncio
async def test_run_registers_and_unregisters_own_file(tmp_path):
    """run() writes its registry file; it is gone when run() returns."""
    from src.config import Config
    from src.hud.registry import registry_dir
    from src.loop.run import run
    from tests.loop import fakes as scripted

    repo = scripted.init_repo(
        tmp_path,
        "# Demo\n\n## Add greet\n\n- [ ] greet() returns hello\n",
    )
    observed: dict = {}

    def player(role: str, kwargs: dict):
        assert role == "player"
        observed["files"] = sorted(
            p.name for p in registry_dir().glob("*.json")
        )
        scripted.write_file(
            repo, "src/greet.py", "def greet():\n    return 'hello'\n"
        )
        return scripted.PLAYER_DONE

    agents = scripted.FakeAgents(
        repo, [player, scripted.judge_returns(scripted.ship())]
    )
    cfg = Config(
        plan_file="plan.md",
        working_dir=str(repo),
        max_rounds=8,
        test_timeout_s=30,
    )
    outcome = await run(cfg, agents=agents)
    assert outcome.exit_code == 0
    assert observed.get("files"), "run() never wrote its registry file"
    leftovers = [
        p
        for p in registry_dir().glob("*.json")
        if repo.as_posix() in p.read_text(encoding="utf-8")
    ]
    assert leftovers == []
    data = json.loads((repo / outcome.run_dir / "state.json").read_text())
    assert data["pid"] == os.getpid()
    assert isinstance(data["pid_started"], float)


def test_registry_entry_of_killed_process_is_removed(tmp_path):
    """Leftovers after kill -9: list_live drops the dead file."""
    reg = Registry(tmp_path / "registry")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        started = psutil.Process(proc.pid).create_time()
    except psutil.NoSuchProcess:
        proc.wait(timeout=5)
        pytest.fail("child died before we could record it")
    reg.register("victim", tmp_path / "state.json", pid=proc.pid, pid_started=started)
    victim = reg.base_dir / "victim.json"
    assert victim.is_file()
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    for _ in range(100):
        if not psutil.pid_exists(proc.pid):
            break
        time.sleep(0.05)
    assert reg.list_live() == []
    assert not victim.exists()
