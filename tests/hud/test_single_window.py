"""One HUD window per machine (phase 9): lock, hud-target, `+N` badge."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

from src.hud import app as hud_app
from src.hud import registry, single
from src.hud import view as hud_view
from tests.hud.test_view import _install_fake_webview, _run_state


def _run(tmp_path, name: str, header_round: int = 1):
    run_dir = tmp_path / ".tero" / "runs" / name
    run_dir.mkdir(parents=True)
    state = _run_state(time.time())
    state["round"] = header_round
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return run_dir


def _hold_lock():
    single.lock_path().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(single.lock_path(), os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def _lock_is_busy() -> bool:
    fd = os.open(single.lock_path(), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def test_second_hud_writes_target_and_exits(monkeypatch, tmp_path):
    calls, _, _ = _install_fake_webview(monkeypatch)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    run_dir = _run(tmp_path, "b")
    fd = _hold_lock()
    try:
        assert hud_app.main(["--run", str(run_dir)]) == 0
    finally:
        os.close(fd)
    assert "create_window" not in calls
    assert single.target_path().read_text(encoding="utf-8") == str(run_dir)


def test_window_switches_on_target(monkeypatch, tmp_path):
    monkeypatch.setattr(hud_view, "is_dead", lambda s: False)
    first = _run(tmp_path, "a", header_round=1)
    second = _run(tmp_path, "b", header_round=7)
    bridge = hud_view.Bridge(first)
    assert bridge.view()["round"] == 1
    single.write_target(second)
    assert bridge.view()["round"] == 7
    assert bridge.run_dir == second
    assert not single.target_path().exists()
    assert bridge.view()["round"] == 7


def test_free_lock_opens_window(monkeypatch, tmp_path):
    calls, _, _ = _install_fake_webview(monkeypatch)
    seen: list = []
    real_start = sys.modules["webview"].start

    def spy_start(*args, **kwargs):
        seen.append(_lock_is_busy())
        return real_start(*args, **kwargs)

    sys.modules["webview"].start = spy_start
    assert hud_view.run(_run(tmp_path, "a")) == 0
    assert "create_window" in calls
    assert seen == [True]


def test_plus_n_label_from_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(hud_view, "is_dead", lambda s: False)
    monkeypatch.setattr(registry, "pid_is_alive", lambda pid, started: True)
    first = _run(tmp_path, "a")
    second = _run(tmp_path, "b", header_round=7)
    reg = registry.Registry()
    reg.register("a", first / "state.json", pid=1, pid_started=1.0)
    bridge = hud_view.Bridge(first)
    assert bridge.view()["plus"] == ""
    reg.register("b", second / "state.json", pid=2, pid_started=1.0)
    assert bridge.view()["plus"] == "+1"
    assert bridge.switch("") == str(second.resolve())
    assert bridge.view()["round"] == 7
    assert bridge.view()["plus"] == "+1"
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    assert 'id="plus"' in html and "switch(" in html


def test_closed_window_frees_lock(monkeypatch, tmp_path):
    calls, _, _ = _install_fake_webview(monkeypatch)
    assert hud_view.run(_run(tmp_path, "a")) == 0
    assert not _lock_is_busy()
    calls.clear()
    assert hud_view.run(_run(tmp_path, "b")) == 0
    assert "create_window" in calls


def test_plus_n_cycles_through_all_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(hud_view, "is_dead", lambda s: False)
    monkeypatch.setattr(registry, "pid_is_alive", lambda pid, started: True)
    runs = [_run(tmp_path, name) for name in ("a", "b", "c")]
    reg = registry.Registry()
    for i, run_dir in enumerate(runs):
        reg.register(run_dir.name, run_dir / "state.json", pid=i + 1, pid_started=1.0)
    bridge = hud_view.Bridge(runs[1])
    assert bridge.view()["plus"] == "+2"
    seen = [bridge.switch("") for _ in range(3)]
    assert seen == [str(runs[2].resolve()), str(runs[0].resolve()), str(runs[1].resolve())]
