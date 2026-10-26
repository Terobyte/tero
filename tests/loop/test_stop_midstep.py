"""Phase 1 (plan 07): tero stop during a step, stale index.lock, decision point, CLI refuse."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.loop import watchdog as wd_mod
from tests.loop import fakes as scripted

PLAN = """# Demo

## Add greet

- [ ] greet() returns hello
"""

HELLO = "def greet():\n    return 'hello'\n"


def _cfg(**overrides):
    from src.config import Config

    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 5,
            "decisions": True}
    data.update(overrides)
    return Config(**data)


@pytest.fixture(autouse=True)
def _fast_paths(monkeypatch):
    monkeypatch.setattr(wd_mod, "STOP_POLL_S", 0.05)
    monkeypatch.setattr(wd_mod, "KILL_GRACE_S", 0.5)


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


async def _run_phase(repo, agents, cfg, ledger, pulse=None):
    from src.loop.gates import detect
    from src.loop.phase import run_phase
    from src.loop.plan import load_phases, load_preamble

    phases = load_phases(repo / "plan.md")
    return await run_phase(
        phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=agents, test_command=detect(repo), ledger=ledger,
        pulse=pulse,
    )


def _write_stop(run_dir: Path, payload: dict | None = None) -> None:
    (run_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (run_dir / "inbox" / "stop.json").write_text(
        json.dumps(payload or {"who": "human", "ts": time.time()}))


@pytest.mark.asyncio
async def test_stop_old_stop_json_ignored(tmp_path: Path):
    """stop.json of a finished run does not stop a new run (flag is live)."""
    repo = _repo(tmp_path)
    from src.loop.ledger import Ledger, new_run_dir

    old = Ledger(new_run_dir(repo, "demo"))
    (old.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
    _write_stop(old.run_dir, {"who": "human", "ts": 1.0})
    from src.loop import stop_flag as _flag

    from src.loop.state import RunState

    RunState(old.run_dir / "state.json").update(status="stopped")
    assert _flag.live(old.run_dir) is False
    ledger = Ledger(new_run_dir(repo, "demo"))
    (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
    assert _flag.live(ledger.run_dir) is False
    agents = scripted.FakeAgents(
        repo,
        [scripted.player_writes(repo, "src/greet.py", HELLO),
         scripted.judge_returns(scripted.ship())],
    )
    outcome = await _run_phase(repo, agents, _cfg(), ledger)
    assert outcome.status == "ship", outcome


def _pid_dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def test_watchdog_stop_requested_contract(monkeypatch):
    import src.loop.watchdog as _w

    monkeypatch.undo()
    assert _w.STOP_POLL_S == 5
    assert _w.stop_requested is not None
    assert hasattr(_w, "Watchdog")


def test_stop_cmd_module_contract():
    from src.commands import stop_cmd

    assert callable(stop_cmd.run_stop)


@pytest.mark.asyncio
async def test_stop_midstep_kills_fast(tmp_path: Path, monkeypatch):
    """stop.json during a long stub-subprocess step -> stopped, child dead, snapshot kept."""
    import src.loop.watchdog as w

    monkeypatch.setattr(w, "STOP_POLL_S", 0.1)
    repo = _repo(tmp_path)
    from src.loop.ledger import Ledger, new_run_dir

    ledger = Ledger(new_run_dir(repo, "demo"))
    (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
    holder: dict = {}

    async def sleeping_player(role: str, kwargs: dict) -> dict:
        assert role == "player"
        from src.loop import watchdog as _w
        from src.process_guard import ProcessGuard

        before = ProcessGuard().snapshot_pids()
        _w.note_step_before(before)
        provider = scripted.sleep_provider(tmp_path, 6.0)
        holder["provider"] = provider
        try:
            stream = provider.run(
                prompt="p", system_prompt="", working_dir=str(ledger.run_dir))
            async for _msg in stream:
                pass
            raise AssertionError("watchdog must kill the sleeping step")
        except GeneratorExit:
            raise
        except BaseException as exc:
            if isinstance(exc, AssertionError) and not provider.proc:
                raise
            holder["proc"] = provider.proc
            holder["exc"] = exc
            scripted.write_file(repo, "src/greet.py", HELLO)
            return dict(scripted.PLAYER_DONE)
        finally:
            _w.note_step_before(None)

    class _Agents:
        async def run_role(self, role: str, **kw):
            if role == "player":
                return await sleeping_player(role, kw)
            return scripted.ship()

    from src.loop.pulse import Pulse

    pulse = Pulse(ledger.run_dir, interval_s=60)
    try:
        written = threading.Event()

        def _watch_spawned() -> None:
            deadline = time.monotonic() + 4.0
            while "provider" not in holder and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)
            _write_stop(ledger.run_dir)
            written.set()

        t = threading.Thread(target=_watch_spawned, daemon=True)
        t0 = time.monotonic()
        t.start()
        outcome = await _run_phase(repo, _Agents(), _cfg(), ledger, pulse=pulse)
        dt = time.monotonic() - t0
        t.join(timeout=4)
        assert written.is_set()
        assert not t.is_alive()
    finally:
        pulse.stop()
    assert outcome.status == "stop", outcome
    assert outcome.reason == "user_stop", outcome
    assert dt < 5, dt
    proc = holder["proc"]
    assert proc.poll() is not None
    assert _pid_dead(proc.pid)
    from src.loop.state import RunState

    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert state.get("status") == "stopped"
    assert state.get("snapshot_ref"), "снимок после стопа обязателен"


@pytest.mark.asyncio
async def test_stop_midstep_via_live_agents(tmp_path: Path, monkeypatch):
    """Same contract through the real wiring: Pulse + LiveAgents + a stub CLI subprocess.

    Unlike test_stop_midstep_kills_fast (whose player calls note_step_before
    itself), the baseline here comes from LiveAgents._call, same as a real run.
    """
    import src.loop.watchdog as w
    from src.loop.agents import LiveAgents

    monkeypatch.setattr(w, "STOP_POLL_S", 0.1)
    repo = _repo(tmp_path)
    from src.loop.ledger import Ledger, new_run_dir

    ledger = Ledger(new_run_dir(repo, "demo"))
    (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
    provider = scripted.sleep_provider(tmp_path, 6.0)
    live = LiveAgents(str(ledger.run_dir), factory=lambda _n: provider, retry_wait_s=0)

    class _Agents:
        async def run_role(self, role: str, **kw):
            if role == "player":
                return await live.run_role(role, **kw)
            return scripted.ship()

    from src.loop.pulse import Pulse

    pulse = Pulse(ledger.run_dir, interval_s=60)
    try:
        written = threading.Event()

        def _watch_spawned() -> None:
            deadline = time.monotonic() + 4.0
            while provider.proc is None and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)
            _write_stop(ledger.run_dir)
            written.set()

        t = threading.Thread(target=_watch_spawned, daemon=True)
        t0 = time.monotonic()
        t.start()
        outcome = await _run_phase(repo, _Agents(), _cfg(), ledger, pulse=pulse)
        dt = time.monotonic() - t0
        t.join(timeout=4)
        assert written.is_set()
        assert not t.is_alive()
    finally:
        pulse.stop()
    assert outcome.status == "stop", outcome
    assert outcome.reason == "user_stop", outcome
    assert dt < 5, dt
    assert provider.proc.poll() is not None
    assert _pid_dead(provider.proc.pid)
    from src.loop.state import RunState

    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert state.get("status") == "stopped"
    assert state.get("snapshot_ref"), "снимок после стопа обязателен"


def test_watchdog_kills_only_current_step(tmp_path: Path):
    """A process older than the step baseline (HUD) survives the stop kill."""
    from src.loop.watchdog import Watchdog, note_step_before
    from src.process_guard import ProcessGuard

    run_dir = tmp_path / "run"
    (run_dir / "inbox").mkdir(parents=True)
    hud = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    step = None
    wd = None
    try:
        note_step_before(ProcessGuard().snapshot_pids())
        step = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        assert step.pid != hud.pid
        _write_stop(run_dir)
        wd = Watchdog(run_dir, poll_s=0.05)
        wd.start()
        deadline = time.monotonic() + 4.0
        while step.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert step.poll() is not None, "step child must die on stop"
        assert not _pid_dead(hud.pid), "pre-step child (HUD) must survive"
        assert wd.cancel.is_set()
    finally:
        note_step_before(None)
        if wd is not None:
            wd.stop()
        for proc in (step, hud):
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass


def _live_stop_agents(tmp_path: Path, provider):
    import os as _os

    import src.loop.agents as agents_mod
    from src.hud import registry as _reg

    run_dir = tmp_path / "run"
    (run_dir / "inbox").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(
        {"status": "running", "pid": _os.getpid(),
         "pid_started": _reg.current_pid_started()}))
    (run_dir / "inbox" / "stop.json").write_text(
        json.dumps({"who": "human", "ts": time.time()}))
    agents = agents_mod.LiveAgents(str(run_dir), factory=lambda _n: provider)
    agents.run_dir = str(run_dir)
    assert agents_mod.stop_requested_flag(str(run_dir)) is True
    return agents


@pytest.mark.asyncio
async def test_stop_no_network_retry_while_flag_live(tmp_path: Path, monkeypatch):
    """A live stop.json suppresses the network retry branch (one call)."""
    import src.loop.agents as agents_mod

    class _NetProvider:
        calls = 0

        async def run(self, prompt, system_prompt, working_dir, model="", **kw):
            type(self).calls += 1
            raise ConnectionError("reset by peer")
            yield  # async generator like a real provider

    provider = _NetProvider()
    agents = _live_stop_agents(tmp_path, provider)
    monkeypatch.setattr(agents_mod, "RETRY_DELAYS_S", (60,))
    out = await agents._call(
        "p", "m", "prompt", {"type": "object"}, False, 60, role="player")
    assert out is None
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_stop_no_other_retry_while_flag_live(tmp_path: Path):
    """A live stop.json suppresses the other-error retry branch (one call)."""
    class _OtherProvider:
        calls = 0

        async def run(self, prompt, system_prompt, working_dir, model="", **kw):
            type(self).calls += 1
            raise RuntimeError("boom")
            yield  # async generator like a real provider

    provider = _OtherProvider()
    agents = _live_stop_agents(tmp_path, provider)
    agents.retry_wait_s = 0
    out = await agents._call(
        "p", "m", "prompt", {"type": "object"}, False, 60, role="player")
    assert out is None
    assert provider.calls == 1


def test_stop_flag_ignores_finished_runs(tmp_path: Path, monkeypatch):
    """stop.json of a finished run does not suppress retries of a later run."""
    from src.hud import registry as reg_mod
    from src.loop import agents as agents_mod

    monkeypatch.setattr(reg_mod, "pid_is_alive", lambda pid, started: True)
    runs = tmp_path / ".tero" / "runs"
    old = runs / "run-old"
    (old / "inbox").mkdir(parents=True)
    (old / "state.json").write_text(json.dumps({"status": "done"}))
    (old / "inbox" / "stop.json").write_text(json.dumps({"who": "human", "ts": 1.0}))
    assert agents_mod.stop_requested_flag(str(old)) is False
    assert agents_mod.stop_requested_flag(str(tmp_path)) is False
    new = runs / "run-new"
    import os as _os

    (new / "inbox").mkdir(parents=True)
    (new / "state.json").write_text(
        json.dumps({"status": "running", "pid": _os.getpid(),
                    "pid_started": reg_mod.current_pid_started()}))
    assert agents_mod.stop_requested_flag(str(new)) is False
    assert agents_mod.stop_requested_flag(str(tmp_path)) is False
    (new / "inbox" / "stop.json").write_text(json.dumps({"who": "human", "ts": 2.0}))
    assert agents_mod.stop_requested_flag(str(new)) is True
    assert agents_mod.stop_requested_flag(str(tmp_path)) is True


@pytest.mark.asyncio
async def test_stop_removes_stale_index_lock_from_killed_subprocess(tmp_path: Path, monkeypatch):
    """A step whose killed child process left .git/index.lock: stop drops it too."""
    import src.loop.watchdog as w

    monkeypatch.setattr(w, "STOP_POLL_S", 0.1)
    repo = _repo(tmp_path)
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.state import RunState

    ledger = Ledger(new_run_dir(repo, "demo"))
    (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
    provider = scripted.sleep_provider(tmp_path, 6.0)

    from src.loop.agents import LiveAgents

    live = LiveAgents(str(ledger.run_dir), factory=lambda _n: provider, retry_wait_s=0)

    class _Agents:
        async def run_role(self, role: str, **kw):
            if role == "player":
                (repo / ".git" / "index.lock").write_text("stale-from-child")
                return await live.run_role(role, **kw)
            return scripted.ship()

    from src.loop.pulse import Pulse

    pulse = Pulse(ledger.run_dir, interval_s=60)
    try:
        written = threading.Event()

        def _watch_spawned() -> None:
            deadline = time.monotonic() + 4.0
            while provider.proc is None and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)
            _write_stop(ledger.run_dir)
            written.set()

        t = threading.Thread(target=_watch_spawned, daemon=True)
        t.start()
        outcome = await _run_phase(repo, _Agents(), _cfg(), ledger, pulse=pulse)
        t.join(timeout=4)
        assert written.is_set()
    finally:
        pulse.stop()
    assert outcome.status == "stop" and outcome.reason == "user_stop", outcome
    assert not (repo / ".git" / "index.lock").exists()
    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert state.get("snapshot_ref")


@pytest.mark.asyncio
async def test_stop_removes_stale_index_lock(tmp_path: Path):
    """Killed step leaves .git/index.lock: stop still drops it and rolls back."""
    repo = _repo(tmp_path)
    (repo / ".git" / "index.lock").write_text("stale")
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.state import RunState

    ledger = Ledger(new_run_dir(repo, "demo"))
    (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)

    def _player_writes_stale_lock(role: str, kwargs: dict) -> dict:
        assert role == "player"
        (repo / ".git" / "index.lock").write_text("stale-from-step")
        (ledger.run_dir / "inbox" / "stop.json").write_text(
            json.dumps({"who": "human", "ts": time.time()}))
        scripted.write_file(repo, "src/greet.py", HELLO)
        return dict(scripted.PLAYER_DONE)

    agents = scripted.FakeAgents(
        repo,
        [_player_writes_stale_lock,
         scripted.judge_returns(scripted.ship())],
    )
    outcome = await _run_phase(repo, agents, _cfg(), ledger)
    assert outcome.status == "stop" and outcome.reason == "user_stop"
    assert not (repo / ".git" / "index.lock").exists()
    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert state.get("snapshot_ref")


@pytest.mark.asyncio
async def test_stop_json_answers_decision_point(tmp_path: Path, monkeypatch):
    import src.loop.decision_step as ds_mod

    monkeypatch.setattr(ds_mod, "DECISION_POLL_S", 0.01)
    repo = _repo(tmp_path)
    agents = scripted.FakeAgents(
        repo,
        [scripted.player_writes(repo, "src/greet.py", HELLO),
         scripted.judge_returns(
             {"verdict": "NEEDS_USER", "previous": [], "findings": [],
              "user_question": "which greeting?"})],
    )
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.state import RunState

    ledger = Ledger(new_run_dir(repo, "demo"))
    (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)

    def _ask_stop() -> None:
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            try:
                state = RunState.read(ledger.run_dir / "state.json") or {}
                if state.get("status") == "waiting_human":
                    _write_stop(ledger.run_dir)
                    return
            except Exception:
                pass
            time.sleep(0.01)

    from src.loop.session import run_phases
    from src.loop.phase import run_phase
    from src.loop.gates import detect
    from src.loop.plan import load_phases, load_preamble

    t = threading.Thread(target=_ask_stop, daemon=True)
    t.start()
    outcome = await run_phases(
        run_phase, load_phases(repo / "plan.md"),
        ledger=lambda: ledger,
        phase_kwargs=dict(
            repo=repo, plan_path=repo / "plan.md",
            preamble=load_preamble(repo / "plan.md"), cfg=_cfg(),
            agents=agents, test_command=detect(repo),
        ),
        run_dir=ledger.run_dir, on_pending=lambda: None,
        on_stop=lambda o: None, on_done=lambda s: None,
    )
    t.join(timeout=4)
    assert not t.is_alive()
    assert outcome.status == "stop", outcome
    assert outcome.reason == "user_stop", outcome


def test_stop_cli_refuses_when_not_running(tmp_path: Path):
    run_dir = tmp_path / "run"
    (run_dir / "inbox").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({"status": "done"}))
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli_entry", "stop",
         "--run", str(run_dir), "--working-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=Path.cwd(), timeout=5,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "прогон не идёт" in proc.stdout


@pytest.mark.asyncio
async def test_stop_after_judge_blocks_ship(tmp_path: Path):
    """Stop raised inside the judge call blocks SHIP: no commit, stopped."""
    repo = _repo(tmp_path)
    from src.loop.ledger import Ledger, new_run_dir

    ledger = Ledger(new_run_dir(repo, "demo"))
    (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)

    class _Agents:
        async def run_role(self, role: str, **kw):
            if role == "player":
                scripted.write_file(repo, "src/greet.py", HELLO)
                return dict(scripted.PLAYER_DONE)
            _write_stop(ledger.run_dir)
            return scripted.ship()

    from src.loop.git import head

    before = head(repo)
    outcome = await _run_phase(repo, _Agents(), _cfg(), ledger)
    assert outcome.status == "stop", outcome
    assert outcome.reason == "user_stop", outcome
    assert head(repo) == before
