"""Поток опроса ретранслятора (план 09, фаза 1): один GET, сообщения, чужие ответы."""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.loop.inbox import Inbox
from src.loop.state import RunState
from src.relay.iface import Answer, FakeRelay
from src.relay.poller import Poller


def _run(tmp_path: Path, monkeypatch, qid: str = "q1") -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    run_dir = tmp_path / "repo" / ".tero" / "runs" / "20200101-000000-demo"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "status": "waiting_human",
        "question": {"id": qid, "actions": ["skip", "stop"], "all_actions": ["skip", "stop"]},
    }), encoding="utf-8")
    return run_dir


def _ans(uuid: str, qid: str = "q1", kind: str = "answer", text: str = "", action: str = "skip") -> Answer:
    return Answer(question_id=qid, action=action, text=text, who="human", uuid=uuid, via="relay", kind=kind)


def test_poller_single_get_and_take_offline(tmp_path, monkeypatch):
    run_dir = _run(tmp_path, monkeypatch)
    relay = FakeRelay()
    poller = Poller(run_dir, relay)
    assert poller.tick(0.0) is True
    relay.answers.append(_ans("u1"))
    assert poller.tick(5.0) is False  # два GET на 10 с не бывает
    assert relay.gets == 1
    assert poller.tick(10.0) is True
    assert relay.gets == 2
    got = Inbox(run_dir).take("q1", poller.adapter)
    assert got is not None and got.uuid == "u1"
    assert relay.gets == 2  # take не ходил в сеть
    deadline = time.monotonic() + 3
    while "u1" not in relay.acked and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "u1" in relay.acked
    assert poller.tick(20.0) is True
    assert relay.gets == 3
    assert Inbox(run_dir).take("q1", poller.adapter) is None
    assert (RunState.read(run_dir / "state.json") or {})["applied_uuids"] == ["u1"]


def test_poller_acks_stale_answers(tmp_path, monkeypatch):
    run_dir = _run(tmp_path, monkeypatch, qid="q2")
    relay = FakeRelay([_ans("old", qid="q1")])
    poller = Poller(run_dir, relay)
    poller.tick(0.0)
    assert "old" in relay.acked
    assert relay.reasons["old"] == "не тот вопрос"
    assert poller.adapter.fetch_answers("q1") == []


def test_message_written_signed_not_in_player_prompt(tmp_path, monkeypatch):
    from src.loop.prompts import player_prompt

    run_dir = _run(tmp_path, monkeypatch)
    before = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    relay = FakeRelay([_ans("m1", qid="", kind="message", text="привет хозяин", action="")])
    Poller(run_dir, relay).tick(0.0)
    data = json.loads((run_dir / "inbox" / "msg-m1.json").read_text(encoding="utf-8"))
    assert data["text"] == "привет хозяин" and data["sig"]
    assert "m1" in relay.acked
    after = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert "привет хозяин" not in json.dumps(after, ensure_ascii=False)
    stripped_before = {k: v for k, v in before.items() if k not in ("decisions", "updated_at")}
    stripped_after = {k: v for k, v in after.items() if k not in ("decisions", "updated_at")}
    assert stripped_after == stripped_before, "сообщение меняет только decisions/updated_at"
    assert len(after.get("decisions") or []) == len(before.get("decisions") or []) + 1
    # F2 (план 09, фаза 2): хозяина нет — wake(reason=message) пишет отказ в decisions.
    wakes = [d for d in (after.get("decisions") or []) if d.get("kind") == "wake"]
    assert len(wakes) == 1 and wakes[0]["reason"] == "message"
    # F14: промпт игрока следующего раунда собирается из state.answer —
    # сообщение туда не попадает ни через answer, ни напрямую.
    saved = RunState.read(run_dir / "state.json") or {}
    assert not saved.get("answer")
    prompt = player_prompt(preamble="p", contract="c", answer=str(saved.get("answer") or ""),
                           phone=bool(saved.get("answer_from_phone")))
    assert "привет хозяин" not in prompt


def test_phone_text_off_rejects_message(tmp_path, monkeypatch):
    run_dir = _run(tmp_path, monkeypatch)
    monkeypatch.setattr("src.config_env.phone_text_allowed", lambda: False)
    relay = FakeRelay([_ans("m1", qid="", kind="message", text="привет", action="")])
    Poller(run_dir, relay).tick(0.0)
    assert not (run_dir / "inbox" / "msg-m1.json").exists()
    assert "m1" in relay.acked and relay.reasons["m1"] == "текст с телефона выключен"


def test_wake_acked_and_result_logged(tmp_path, monkeypatch):
    run_dir = _run(tmp_path, monkeypatch)
    relay = FakeRelay([_ans("w1", qid="", kind="wake", action="")])
    Poller(run_dir, relay).tick(0.0)
    assert "w1" in relay.acked
    last = (RunState.read(run_dir / "state.json") or {})["decisions"][-1]
    assert last["kind"] == "wake" and last["reason"] == "manual"
    assert "host" in last["result"] and "at" in last and last["uuid"] == "w1"  # команды нет → отказ с подсказкой


class _FlakyAckRelay(FakeRelay):
    """Первый ack падает, дальше успешно: временный сбой сети."""

    def __init__(self, answers=None) -> None:
        super().__init__(answers)
        self.fail_next = True

    def ack(self, uuid: str, reason: str = "") -> bool:
        if self.fail_next:
            self.fail_next = False
            return False
        return super().ack(uuid, reason)


def test_message_text_scrubbed_no_secret_in_inbox(tmp_path, monkeypatch):
    """F8: текст с телефона проходит scrub — секрет из secrets.env не в файле."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".tero").mkdir(parents=True)
    (home / ".tero" / "secrets.env").write_text("CUSTOM_TOKEN=opaque-secret-value!\n")
    run_dir = _run(tmp_path, monkeypatch)
    relay = FakeRelay([_ans("m1", qid="", kind="message", text="токен opaque-secret-value! в тексте",
                            action="")])
    Poller(run_dir, relay).tick(0.0)
    body = (run_dir / "inbox" / "msg-m1.json").read_text(encoding="utf-8")
    assert "opaque-secret-value!" not in body


def test_flaky_ack_no_unread_copy(tmp_path, monkeypatch):
    """F9: ack упал после записи — следующий poll не создаёт непрочитанную копию."""
    from src.loop.messages import take_unread

    run_dir = _run(tmp_path, monkeypatch)
    relay = _FlakyAckRelay([_ans("m1", qid="", kind="message", text="привет", action="")])
    Poller(run_dir, relay).tick(0.0)
    assert (run_dir / "inbox" / "msg-m1.json").is_file()
    assert take_unread(run_dir) == ["привет"]
    poller = Poller(run_dir, relay)
    poller.tick(20.0)
    assert not (run_dir / "inbox" / "msg-m1.json").exists()
    assert (run_dir / "inbox" / "msg-m1.read.json").is_file()
    assert "m1" in relay.acked


def test_wake_double_poll_single_decision(tmp_path, monkeypatch):
    """F9: два poll для wake при падении первого ack — одна запись в decisions."""
    run_dir = _run(tmp_path, monkeypatch)
    relay = _FlakyAckRelay([_ans("w1", qid="", kind="wake", action="")])
    poller = Poller(run_dir, relay)
    poller.tick(0.0)
    poller.tick(20.0)
    decisions = (RunState.read(run_dir / "state.json") or {}).get("decisions") or []
    assert len([d for d in decisions if d.get("kind") == "wake"]) == 1
    assert "w1" in relay.acked


def test_message_length_capped_after_scrub(tmp_path, monkeypatch):
    """F20: лимит применяется после очистки — замена секрета не раздувает текст за 2000."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".tero").mkdir(parents=True)
    (home / ".tero" / "secrets.env").write_text("CUSTOM_TOKEN=secret8x\n")
    run_dir = _run(tmp_path, monkeypatch)
    relay = FakeRelay([_ans("m1", qid="", kind="message", text="a" * 1992 + "secret8x",
                            action="")])
    Poller(run_dir, relay).tick(0.0)
    body = (run_dir / "inbox" / "msg-m1.json").read_text(encoding="utf-8")
    assert "secret8x" not in body
    assert len(json.loads(body)["text"]) <= 2000


def test_owner_poller_polls_during_setup(tmp_path, monkeypatch):
    """F19: поток владельца опрашивает сразу при старте — сообщение setup не ждёт тика."""
    from src.relay.poller import start_owner_poller, stop_poller

    run_dir = _run(tmp_path, monkeypatch)
    relay = FakeRelay([_ans("m1", qid="", kind="message", text="привет", action="")])
    monkeypatch.setattr("src.relay.poller.make_relay", lambda *a, **k: relay)
    try:
        assert start_owner_poller(run_dir, object()) is not None
        assert (run_dir / "inbox" / "msg-m1.json").is_file()  # немедленный GET, без сна
        assert "m1" in relay.acked
    finally:
        stop_poller(run_dir)


def test_owner_exit_stops_gets(tmp_path, monkeypatch):
    """F21: владелец завершается через RunSession — поток остановлен, GET не растут."""
    import time as _time

    from src.loop.session import RunSession
    from src.relay.poller import ensure_poller

    run_dir = _run(tmp_path, monkeypatch)
    relay = FakeRelay([_ans("u1")])
    assert ensure_poller(run_dir, relay) is not None
    deadline = _time.monotonic() + 5
    while relay.gets < 1 and _time.monotonic() < deadline:
        _time.sleep(0.05)
    assert relay.gets >= 1  # поток сделал хотя бы один GET до выхода владельца

    class _Lock:
        def release(self) -> None:
            pass

    session = RunSession(_Lock(), run_dir, pid_started=None, release_on_exit=False)
    session.register()
    with session:
        pass
    # Стоп — только из lifecycle владельца (без прямого stop_poller):
    # запись реестра убрана, поток мёртв.
    from src.relay import poller as poller_mod

    assert str(run_dir.absolute()) not in poller_mod._POLLERS
    frozen = relay.gets
    _time.sleep(0.6)  # один TICK_S проходит без потока
    assert relay.gets == frozen


def test_dead_poller_reaped(tmp_path, monkeypatch):
    """Терминально умерший поток не выдаётся за живой: ensure заводит новый."""
    from src.relay import poller as poller_mod
    from src.relay.poller import ensure_poller, stop_poller

    run_dir = _run(tmp_path, monkeypatch)
    relay = FakeRelay()
    try:
        first = ensure_poller(run_dir, relay)
        assert first is not None
        first.stop()  # поток мёртв, запись в реестре осталась
        assert not first._thread.is_alive()
        second = ensure_poller(run_dir, relay)
        assert second is not None and second is not first
        assert second._thread is not None and second._thread.is_alive()
    finally:
        stop_poller(run_dir)
    assert str(run_dir.absolute()) not in poller_mod._POLLERS
