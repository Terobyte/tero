"""Спек пульта 2, п. 1: аренда подписана, с pid_started, сливается под flock;
agent в status/HUD/пульте; ready в трее; machine_id в файле."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from src.hud.registry import current_pid_started
from src.loop import lease as lease_mod


def _run(tmp_path: Path) -> Path:
    run_dir = tmp_path / ".tero" / "runs" / "20261029-demo"
    run_dir.mkdir(parents=True)
    return run_dir


def _me() -> tuple[int, float]:
    return os.getpid(), current_pid_started()


def test_write_read_sign_and_fields(tmp_path: Path):
    run_dir = _run(tmp_path)
    pid, started = _me()
    assert lease_mod.write(run_dir, until=time.time() + 60, pid=pid, pid_started=started,
                           host="claude", session="s1")
    raw = json.loads((run_dir / "lease").read_text())
    assert set(raw) == {"until", "pid", "pid_started", "host", "session", "written_at", "sig"}
    assert (raw["pid"], raw["pid_started"], raw["host"], raw["session"]) == (pid, started, "claude", "s1")
    assert lease_mod.read(run_dir) == raw
    assert lease_mod.on_duty(run_dir)
    assert (run_dir / "lease").stat().st_mode & 0o077 == 0


def test_tampered_or_unsigned_lease_is_dead_and_logged_once(tmp_path: Path):
    run_dir = _run(tmp_path)
    pid, started = _me()
    lease_mod.write(run_dir, until=time.time() + 60, pid=pid, pid_started=started)
    raw = json.loads((run_dir / "lease").read_text())
    raw["until"] = time.time() + 99999
    (run_dir / "lease").write_text(json.dumps(raw))
    assert lease_mod.read(run_dir) is None, "правленная аренда не верифицируется"
    assert not lease_mod.on_duty(run_dir)
    lease_mod.read(run_dir)
    log = (tmp_path / ".tero" / "hud.log").read_text()
    assert log.count("подпись не сошлась") == 1, "строка в hud.log один раз, не каждую секунду"
    del raw["sig"]
    (run_dir / "lease").write_text(json.dumps(raw))
    assert lease_mod.read(run_dir) is None
    # another run's signed lease copied over does not verify here
    other = tmp_path / ".tero" / "runs" / "20261029-other"
    other.mkdir()
    lease_mod.write(other, until=time.time() + 60, pid=pid, pid_started=started)
    (run_dir / "lease").write_bytes((other / "lease").read_bytes())
    assert lease_mod.read(run_dir) is None


def test_expired_lease_is_alive_but_off_duty(tmp_path: Path):
    run_dir = _run(tmp_path)
    pid, started = _me()
    lease_mod.write(run_dir, until=time.time() - 5, pid=pid, pid_started=started, host="claude")
    view = lease_mod.agent_view(run_dir)
    assert (view["alive"], view["on_duty"], view["host"]) == (True, False, "claude")
    assert view["lease_until"] < time.time()
    lease_mod.write(run_dir, until=time.time() + 600, pid=999999999, pid_started=1.0)
    view = lease_mod.agent_view(run_dir)
    assert (view["alive"], view["on_duty"]) == (False, False)
    assert lease_mod.agent_view(tmp_path / "nowhere") == {
        "host": "", "alive": False, "on_duty": False, "pid_started_at": None,
        "lease_until": None, "lease_written_at": None}


def test_extend_keeps_pid_and_refuses_dead(tmp_path: Path):
    run_dir = _run(tmp_path)
    pid, started = _me()
    lease_mod.write(run_dir, until=time.time() + 10, pid=pid, pid_started=started, host="claude")
    assert lease_mod.extend(run_dir, time.time() + 1000)
    got = lease_mod.read(run_dir)
    assert got["pid"] == pid and got["until"] > time.time() + 900
    assert lease_mod.read(run_dir)["host"] == "claude"
    lease_mod.write(run_dir, until=time.time() + 10, pid=999999999, pid_started=1.0)
    assert not lease_mod.extend(run_dir, time.time() + 1000)
    assert not lease_mod.extend(tmp_path / "nowhere", time.time() + 1000)


def test_may_take_rules(tmp_path: Path):
    run_dir = _run(tmp_path)
    pid, started = _me()
    assert lease_mod.may_take(run_dir, pid), "нет аренды — можно"
    lease_mod.write(run_dir, until=time.time() + 600, pid=pid, pid_started=started)
    assert lease_mod.may_take(run_dir, pid), "своя"
    assert not lease_mod.may_take(run_dir, pid + 1), "чужая живая — нельзя"
    lease_mod.write(run_dir, until=time.time() - 1, pid=pid, pid_started=started)
    assert lease_mod.may_take(run_dir, pid + 1), "истекшая — можно"
    lease_mod.write(run_dir, until=time.time() + 600, pid=999999999, pid_started=1.0)
    assert lease_mod.may_take(run_dir, pid + 1), "мёртвый pid — можно"


def test_concurrent_writers_merge_under_flock(tmp_path: Path):
    run_dir = _run(tmp_path)
    pid, started = _me()
    errors: list = []

    def worker(n: int) -> None:
        for i in range(30):
            if not lease_mod.write(run_dir, until=1000 + n * 100 + i, pid=pid, pid_started=started,
                                   host=f"h{n}"):
                errors.append(n)
            if lease_mod.read(run_dir) is None:
                errors.append(("torn", n))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert lease_mod.read(run_dir) is not None


def test_status_payload_carries_agent_lease_and_push_ok(tmp_path: Path):
    from src.loop.wait import agent_line, status_payload

    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _run(tmp_path)
    (run_dir / "state.json").write_text(json.dumps({"status": "waiting_agent"}))
    payload = status_payload(run_dir, repo)
    assert payload["lease"] is None and payload["push_ok"] is False
    assert payload["agent"]["on_duty"] is False
    assert payload["view"] == {"agent": payload["agent"]}
    assert agent_line(payload["agent"]) == "агент: не известен"
    pid, started = _me()
    lease_mod.write(run_dir, until=time.time() + 600, pid=pid, pid_started=started, host="claude")
    (run_dir / "relay.json").write_text(json.dumps({"ok": True, "push_ok": True}))
    payload = status_payload(run_dir, repo)
    assert payload["lease"] == {"until": lease_mod.read(run_dir)["until"], "pid": pid, "host": "claude"}
    assert "pid_started" not in payload["lease"]
    assert payload["push_ok"] is True
    assert payload["agent"]["on_duty"] is True
    assert agent_line(payload["agent"]).startswith("агент: claude · дежурит · аренда до ")
    lease_mod.write(run_dir, until=time.time() - 60, pid=999999999, pid_started=1.0, host="claude")
    assert agent_line(status_payload(run_dir, repo)["agent"]).startswith("агент: claude · умер · аренда истекла ")
    (run_dir / "relay.json").write_text(json.dumps({"ok": False, "push_ok": True}))
    assert status_payload(run_dir, repo)["push_ok"] is False


def test_relay_payload_has_agent_without_pid(tmp_path: Path):
    from src.relay.client import RelayClient

    run_dir = _run(tmp_path)
    pid, started = _me()
    lease_mod.write(run_dir, until=time.time() + 600, pid=pid, pid_started=started, host="claude")
    client = RelayClient(fetch=lambda *a: None, run_id=run_dir.name, run_dir=run_dir)
    out = client._redacted_state({"status": "running", "config": {"phone_text_answers": False}})
    assert out["agent"]["on_duty"] is True and out["agent"]["host"] == "claude"
    assert "pid" not in out["agent"]
    assert out["phone_text_answers"] is True, "из state.json флаг не читается"
    assert '"pid"' not in json.dumps(out) and "4242" not in json.dumps(out).replace(str(pid), "")


def test_machine_id_is_random_stable_and_in_a_file(monkeypatch):
    from src.relay.machine import machine_id, machine_id_path

    first = machine_id()
    assert first and first.isalnum() and len(first) >= 16
    assert machine_id_path().read_text().strip() == first
    assert machine_id() == first
    assert machine_id_path().stat().st_mode & 0o077 == 0
    import socket

    assert first != socket.gethostname()[:64]


def test_ready_light_in_tray_and_html():
    from src.hud import tray, view as hud_view

    assert tray.DOT_RGB["ready"] not in (tray.DOT_RGB["green"], tray.DOT_RGB["yellow"])
    assert tray.dot_alpha("ready", tray.BREATHE_S / 2) == 1.0, "ready не дышит"
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    assert "#light.ready" in html
    assert 'id="close"' in html and 'call("close")' in html
    assert "breathe" not in html.split("#light.ready")[1].split("\n")[0]


def test_bridge_close_destroys_window(tmp_path: Path):
    import types

    from src.hud import view as hud_view

    bridge = hud_view.Bridge(tmp_path)
    seen: list = []
    bridge._window = types.SimpleNamespace(destroy=lambda: seen.append("destroy"))
    assert bridge.close() is True
    assert seen == ["destroy"]


def test_pulse_tick_pokes_pusher_when_agent_changes(tmp_path: Path, monkeypatch):
    from src.loop import session as session_mod

    run_dir = _run(tmp_path)
    pokes: list = []
    monkeypatch.setattr("src.relay.pusher.poke", lambda rd: pokes.append(str(rd)))
    s = session_mod.RunSession.__new__(session_mod.RunSession)
    s.run_dir = run_dir
    s._agent_tick()
    s._agent_tick()
    assert len(pokes) == 1, "первый пульс даёт исходное состояние"
    pid, started = _me()
    lease_mod.write(run_dir, until=time.time() + 600, pid=pid, pid_started=started)
    s._agent_tick()
    s._agent_tick()
    assert len(pokes) == 2, "смена on_duty будит пушер один раз"
