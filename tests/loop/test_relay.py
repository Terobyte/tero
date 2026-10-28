"""Фаза 06: RelayClient и ответ с телефона."""

from __future__ import annotations

import json
import time
from pathlib import Path


def _acked(relay, uuid, timeout=3.0):
    """ack уходит в фоне: ждём его, не дольше timeout."""
    deadline = time.time() + timeout
    while uuid not in relay.acked and time.time() < deadline:
        time.sleep(0.01)
    return uuid in relay.acked


def _ctx(tmp_path, relay=None):
    from src.config import Config
    from src.loop.ledger import Ledger
    from src.loop.phase_ctx import PhaseContext, PhaseState
    from src.loop.plan import Phase
    from src.loop.state import RunState
    from src.loop.stream import Stream
    run = tmp_path / "run"
    (run / "inbox").mkdir(parents=True, exist_ok=True)
    return PhaseContext(
        phase=Phase(index=0, title="p", body="", checkboxes=[], start_line=0,
                    end_line=1),
        total_phases=1, repo=tmp_path, plan_path=tmp_path / "plan.md",
        preamble="", cfg=Config(decisions=True), agents=None,
        test_command=None, tester_enabled=False, ledger=Ledger(run),
        run_dir=run, state_io=RunState(run / "state.json"),
        stream=Stream(run / "stream.jsonl"), pulse=None,
        checkpoint="", contract="", suite_hashes={}, guarded={},
        link_targets=set(), st=PhaseState(), relay=relay,
    )


def test_relay_reaches_all_decision_steps(tmp_path):
    """Во всех трёх местах DecisionStep.relay is ctx.relay."""
    from src.loop.decision.step import DecisionStep
    from src.loop.phase import _PhaseRunner
    from src.loop import pending as pending_mod
    from src.loop.resume import wait as resume_wait_mod
    from src.relay.iface import FakeRelay
    relay = FakeRelay()
    ctx = _ctx(tmp_path, relay=relay)
    runner = _PhaseRunner.__new__(_PhaseRunner)
    runner.ctx = ctx
    runner._rollback_with_snapshot = lambda reason: None
    runner.round_limit = lambda: 15
    assert runner._decider().relay is relay
    from src.loop.phase_ctx import PhaseState
    runner.start_question = {"status": "waiting_human", "reason": "x", "qid": "1-1"}
    runner.state = PhaseState()
    resumed = resume_wait_mod.resume_waiting_outcome(runner)
    assert resumed.decider.relay is relay

    import subprocess
    import src.loop.decision.step as ds_mod

    class _Step:
        def __init__(self, ctx):
            self.ctx = ctx
            self.rollback_with_snapshot = lambda reason: None
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=False)
    (tmp_path / "state.json").write_text("{}")
    orig_tree = ds_mod.DecisionStep.tree_sha
    ds_mod.DecisionStep.tree_sha = lambda self, reason: "abc123"
    try:
        made = pending_mod.stop_judge_pending(_Step(ctx), reason="judge_failed")
    finally:
        ds_mod.DecisionStep.tree_sha = orig_tree
    assert made.decider is not None and made.decider.relay is relay
    assert isinstance(DecisionStep.__init__.__defaults__, tuple)


def test_phone_answer_applied_in_memory(tmp_path):
    """Ответ who:human применён, файла в inbox/ нет, ack вызван."""
    from src.loop.inbox import Inbox
    from src.loop.state import RunState
    from src.relay.iface import Answer, FakeRelay
    run = tmp_path / "run"
    (run / "inbox").mkdir(parents=True, exist_ok=True)
    RunState(run / "state.json").update(
        status="waiting_human", question={"id": "1-1", "actions": ["skip", "stop"]},
        question_id="1-1")
    relay = FakeRelay([Answer(question_id="1-1", action="skip",
                              text="t", who="human", uuid="u1")])
    got = Inbox(run).take("1-1", relay)
    assert got is not None and got.who == "human"
    assert not (run / "inbox" / "u1.json").exists()
    assert list((run / "inbox").glob("*")) == []
    assert _acked(relay, "u1")
    assert RunState.read(run / "state.json")["applied_uuids"] == ["u1"]


def test_rejected_phone_answer_acked(tmp_path):
    """Отклонённый (не тот вопрос) ответ тоже ack — не крутится."""
    from src.loop.inbox import Inbox
    from src.loop.state import RunState
    from src.relay.iface import Answer, FakeRelay
    run = tmp_path / "run"
    (run / "inbox").mkdir(parents=True, exist_ok=True)
    RunState(run / "state.json").update(
        status="waiting_human", question={"id": "1-1", "actions": ["skip"]},
        question_id="1-1")
    relay = FakeRelay([Answer(question_id="9-9", action="skip",
                              text="t", who="human", uuid="u9")])
    assert Inbox(run).take("9-9", relay) is None
    assert _acked(relay, "u9")


def test_inbox_file_human_is_agent(tmp_path):
    """Файл human без подписи — агент; relay human — человек (B7 цела)."""
    from src.loop.inbox import Inbox
    from src.loop.state import RunState
    from src.relay.iface import Answer, FakeRelay, NoopRelay
    run = tmp_path / "run"
    (run / "inbox").mkdir(parents=True, exist_ok=True)
    RunState(run / "state.json").update(
        status="waiting_human", question={"id": "1-1", "actions": ["skip", "stop"],
                                          "all_actions": ["skip", "stop"]},
        question_id="1-1")
    (run / "inbox" / "x.json").write_text(json.dumps(
        {"uuid": "x", "question_id": "1-1", "action": "skip", "who": "human"}))
    got = Inbox(run).take("1-1", NoopRelay())
    assert got is not None and got.who == "agent"
    run2 = tmp_path / "run2"
    (run2 / "inbox").mkdir(parents=True, exist_ok=True)
    RunState(run2 / "state.json").update(
        status="waiting_human", question={"id": "1-1", "actions": ["skip", "stop"]},
        question_id="1-1")
    relay = FakeRelay([Answer(question_id="1-1", action="skip",
                              text="t", who="human", uuid="u9")])
    got2 = Inbox(run2).take("1-1", relay)
    assert got2 is not None and got2.who == "human"


def test_phone_text_capped_and_not_in_judge():
    """Текст 5000 → ровно 2000; в промпте судьи нет; ## и ``` экранированы."""
    from src.loop import prompts as p
    long = "## x\n```code```\n" + "y" * 5000
    assert len(long) > 5000
    block = p.answer_block(long, phone=True)
    assert "## x" not in block and "```code" not in block
    clipped = p._phone_data(long[:2000])
    assert len(long[:2000]) == 2000
    assert len(clipped) >= 2000
    head, _, body = block.partition("\n")
    assert body.rstrip("\n") == clipped
    assert "y" * 2001 not in block
    pt = p.player_prompt(preamble="P", contract="C", answer=long, phone=True)
    # F10: та же длинная строка через player; ровно 2000 исходных до экранирования
    assert "y" * 2001 not in pt
    assert clipped in pt
    assert long[2050:] not in pt
    # телефонный ответ — в промпт судьи не попадает; CLI-ответ — как раньше
    jt = p.judge_prompt(preamble="p", contract="c", answer=long, from_phone=True)
    assert long[:2000] not in jt and long not in jt and "y" * 2001 not in jt
    assert "Ответ на вопрос фазы" not in jt
    jt_cli = p.judge_prompt(preamble="p", contract="c", answer="cli-resume-answer")
    assert "cli-resume-answer" in jt_cli


def test_cli_answer_block_unchanged():
    """CLI resume-ответ: разметка и длина как до плана (F14)."""
    from src.loop import prompts as p
    cli = "## план\n```py\nx\n```\n" + "z" * 3000
    block = p.answer_block(cli)
    assert cli in block
    assert cli in p.player_prompt(preamble="P", contract="C", answer=cli)
    assert cli in p.judge_prompt(preamble="p", contract="c", answer=cli)


def test_relay_no_result_file_in_inbox(tmp_path):
    """relay-ответ не создаёт в inbox ни .json, ни .result (F16)."""
    from src.loop.inbox import Inbox
    from src.loop.state import RunState
    from src.relay.iface import Answer, FakeRelay
    run = tmp_path / "run"
    (run / "inbox").mkdir(parents=True, exist_ok=True)
    RunState(run / "state.json").update(
        status="waiting_human", question={"id": "1-1", "actions": ["skip", "stop"]},
        question_id="1-1")
    relay = FakeRelay([Answer(question_id="1-1", action="skip",
                              text="t", who="human", uuid="u1")])
    got = Inbox(run).take("1-1", relay)
    assert got is not None and got.who == "human"
    assert list((run / "inbox").glob("*")) == []
    assert _acked(relay, "u1")


def test_phone_text_disabled_rejected(tmp_path):
    """action == text при phone_text_answers: false отклоняется и в tero."""
    from src.loop.inbox import Inbox
    from src.loop.state import RunState
    from src.relay.iface import Answer, FakeRelay
    run = tmp_path / "run"
    (run / "inbox").mkdir(parents=True, exist_ok=True)
    RunState(run / "state.json").update(
        status="waiting_human",
        question={"id": "1-1", "actions": ["answer", "skip"]},
        question_id="1-1",
        config={"phone_text_answers": False})
    relay = FakeRelay([Answer(question_id="1-1", action="text",
                              text="подсказка", who="human", uuid="ut")])
    assert Inbox(run).take("1-1", relay) is None
    assert _acked(relay, "ut")
    assert RunState.read(run / "state.json").get("applied_uuids", []) == []


def test_phone_answer_text_disabled_rejected(tmp_path):
    """action == answer с текстом при phone_text_answers: false отклоняется."""
    from src.loop.inbox import Inbox
    from src.loop.state import RunState
    from src.relay.iface import Answer, FakeRelay
    run = tmp_path / "run"
    (run / "inbox").mkdir(parents=True, exist_ok=True)
    RunState(run / "state.json").update(
        status="waiting_human",
        question={"id": "1-1", "actions": ["answer", "skip"]},
        question_id="1-1",
        config={"phone_text_answers": False})
    relay = FakeRelay([Answer(question_id="1-1", action="answer",
                              text="подсказка", who="human", uuid="ua")])
    assert Inbox(run).take("1-1", relay) is None
    assert _acked(relay, "ua")
    assert RunState.read(run / "state.json").get("applied_uuids", []) == []


def _cfg():
    return type("C", (), {"relay_url": "https://x", "relay_write_key": "k",
                          "phone_text_answers": True})()


def test_relay_client_pushes_on_status_change():
    """push_state отправляет состояние; секреты проходят redact."""
    from src.relay.client import RelayClient
    seen = {}

    def fake_fetch(method, path, body=None, token=""):
        seen.update(method=method, path=path, body=body)

        class R:
            status = 200

            def json(self): return {"ok": True}
        return R()
    c = RelayClient(_cfg(), fetch=fake_fetch)
    secret = "sk-" + "a" * 40
    assert c.push_state({"status": "running", "question": {"id": "1-1"},
                         "note": secret}) is True
    assert seen["path"] == "/state" and seen["body"]["question"]["id"] == "1-1"
    assert secret not in json.dumps(seen["body"])


def test_relay_down_does_not_stop_run():
    """Сбой сети: False/[] вместо исключения, прогон жив."""
    from src.relay.client import RelayClient

    def boom(method, path, body=None, token=""):
        raise RuntimeError("down")
    c = RelayClient(_cfg(), fetch=boom)
    assert c.push_state({"status": "running"}) is False
    assert c.fetch_answers("1-1") == []
    assert c.ack("u1") is False
    assert c.notify("hi") is False




class _SlowAckRelay:
    """FakeRelay, у которого ack висит: take не должен его ждать."""

    def __init__(self, answers):
        from src.relay.iface import FakeRelay
        self._inner = FakeRelay(answers)
        self.acked = self._inner.acked

    def fetch_answers(self, qid):
        return self._inner.fetch_answers(qid)

    def ack(self, uuid, reason=""):
        time.sleep(3)
        return self._inner.ack(uuid, reason)

    def push_state(self, state):
        return True

    def notify(self, text):
        return True


def _waiting(run, actions=("skip", "stop"), **extra):
    from src.loop.state import RunState
    (run / "inbox").mkdir(parents=True, exist_ok=True)
    RunState(run / "state.json").update(
        status="waiting_human", question={"id": "1-1", "actions": list(actions)},
        question_id="1-1", **extra)


def test_phone_stop_not_delayed_by_ack(tmp_path):
    """ack в фоне: phone stop принимается без ожидания второго таймаута."""
    from src.loop.inbox import Inbox
    from src.relay.iface import Answer
    run = tmp_path / "run"
    _waiting(run)
    relay = _SlowAckRelay([Answer(question_id="1-1", action="stop", who="human", uuid="us")])
    t0 = time.monotonic()
    got = Inbox(run).take("1-1", relay)
    assert got is not None and got.action == "stop"
    assert time.monotonic() - t0 < 1.5


def test_all_phone_answers_acked(tmp_path):
    """Победитель и остальные ответы на вопрос — все ack, в D1 не копятся."""
    from src.loop.inbox import Inbox
    from src.relay.iface import Answer, FakeRelay
    run = tmp_path / "run"
    _waiting(run)
    relay = FakeRelay([
        Answer(question_id="1-1", action="skip", who="human", uuid="u1"),
        Answer(question_id="1-1", action="stop", who="human", uuid="u2"),
    ])
    got = Inbox(run).take("1-1", relay)
    assert got is not None and got.uuid == "u1" and got.via == "relay"
    assert _acked(relay, "u1") and _acked(relay, "u2")


def test_signed_local_human_text_not_phone(tmp_path, monkeypatch):
    """Подписанный tero answer --human: не телефон, текст при phone_text_answers false принят."""
    from src.loop.human_key import sign_answer
    from src.loop.inbox import Inbox
    from src.relay.iface import NoopRelay
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    run = tmp_path / "run"
    _waiting(run, actions=("answer", "skip"), config={"phone_text_answers": False})
    payload = sign_answer(run.name, {"uuid": "h1", "question_id": "1-1",
                                     "action": "answer", "text": "локальная подсказка",
                                     "who": "human"})
    (run / "inbox" / "h1.json").write_text(json.dumps(payload, ensure_ascii=False))
    got = Inbox(run).take("1-1", NoopRelay())
    assert got is not None and got.who == "human" and got.via == ""
    assert got.text == "локальная подсказка"


def test_phone_flag_survives_next_runner(tmp_path):
    """run_phases передаёт только start_answer: флаг телефона — из state.json."""
    from src.loop.state import RunState
    from src.loop.judge_prompt import build_prompt
    run = tmp_path / "run"
    run.mkdir()
    RunState(run / "state.json").update(answer="секрет-с-телефона", answer_from_phone=True)
    from src.loop.phase_ctx import PhaseState
    import tests.loop.test_relay as me
    ctx = me._ctx(tmp_path)
    assert ctx.st.answer_from_phone is False  # пустой ответ — флаг не ставится
    st = PhaseState()
    st.answer = "секрет-с-телефона"
    ctx2 = _ctx_with_state(tmp_path, st)
    assert ctx2.st.answer_from_phone is True
    assert "секрет-с-телефона" not in build_prompt(ctx2, "")
    st3 = PhaseState()
    st3.answer = "другой cli-ответ"
    assert _ctx_with_state(tmp_path, st3).st.answer_from_phone is False


def _ctx_with_state(tmp_path, st):
    ctx = _ctx(tmp_path)
    from dataclasses import replace
    return replace(ctx, st=st, relay=None)


def test_answer_restored_after_crash_rerun():
    """Краш после применённого ответа: rerun получает start_answer той же фазы."""
    import inspect
    from src.loop.resume import recover as resume_recover
    src = inspect.getsource(resume_recover._continue_after_crash)
    assert 'state.get("answer_phase") == cur' in src
    assert 'first["start_answer"]' in src


def test_phone_text_answers_in_config(tmp_path, monkeypatch):
    """phone_text_answers — поле Config: yaml/env выключают, _public_config несёт."""
    from src.config import Config, resolve_config
    from src.loop.session import _public_config
    assert Config().phone_text_answers is True
    monkeypatch.setenv("TERO_PHONE_TEXT_ANSWERS", "false")
    cfg = resolve_config({"working_dir": str(tmp_path)})
    assert cfg.phone_text_answers is False
    assert _public_config(cfg)["phone_text_answers"] is False


def test_transition_ledger_has_phase_role_and_never_raises(tmp_path):
    """Событие ledger несёт phase/round/role; сбой ledger не отменяет переход."""
    from src.loop.state import RunState
    from src.loop.transition import phase_transition
    ctx = _ctx(tmp_path)
    phase_transition(ctx, status="running", reason="r", text="t", role="player",
                     event="answer", kind="answer")
    rows = [json.loads(x) for x in (ctx.run_dir / "ledger.jsonl").read_text().splitlines()]
    last = rows[-1]
    assert last["kind"] == "answer" and last["phase"] == "p" and last["role"] == "player"
    assert "round" in last

    class _Boom:
        def event(self, *a, **k):
            raise OSError("disk full")
    from dataclasses import replace
    broken = replace(ctx, ledger=_Boom(), relay=None)
    phase_transition(broken, status="waiting_human", reason="r2", text="t2")
    assert RunState.read(ctx.run_dir / "state.json")["status"] == "waiting_human"


class _Recorder:
    def __init__(self, delay=0.0):
        self.pushed = []
        self.delay = delay
        self.lock_free = []

    def push_state(self, state):
        import threading
        from src.loop.state import owner_lock
        box = {}

        def probe():  # третий поток: свободен ли owner_lock в момент отправки
            lock = owner_lock(self.path)
            box["free"] = lock.acquire(blocking=False)
            if box["free"]:
                lock.release()
        t = threading.Thread(target=probe)
        t.start()
        t.join()
        self.lock_free.append(box["free"])
        time.sleep(self.delay)
        self.pushed.append(dict(state))
        return True

    def fetch_answers(self, qid):
        return []

    def ack(self, uuid, reason=""):
        return True

    def notify(self, text):
        return True


def _wait(cond, timeout=5.0):
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        time.sleep(0.02)
    return cond()


def test_pusher_sends_fresh_state_not_under_lock(tmp_path):
    """Стартовое состояние уходит сразу; смена фазы без phase_transition тоже; lock свободен."""
    from src.loop.state import RunState
    from src.relay.pusher import Pusher, relay_status
    run = tmp_path / "run"
    run.mkdir()
    RunState(run / "state.json").update(status="running", phase="p1", round=1)
    rec = _Recorder()
    rec.path = run / "state.json"
    Pusher(run, rec)
    assert _wait(lambda: rec.pushed and rec.pushed[-1].get("phase") == "p1")
    time.sleep(0.05)
    RunState(run / "state.json").update(phase="p2", round=3)
    assert _wait(lambda: rec.pushed[-1].get("phase") == "p2")
    assert rec.pushed[-1]["round"] == 3
    assert all(rec.lock_free)
    assert relay_status(run) == ""


def test_pusher_relay_down_shows_in_status(tmp_path):
    from src.loop.state import RunState
    from src.relay.pusher import Pusher, relay_status
    run = tmp_path / "run"
    run.mkdir()
    RunState(run / "state.json").update(status="running")

    class Down(_Recorder):
        def push_state(self, state):
            raise RuntimeError("down")
    Pusher(run, Down())
    assert _wait(lambda: relay_status(run) == "нет связи с ретранслятором")


def test_final_state_flushed_at_exit(tmp_path):
    """done записан и процесс выходит: atexit досылает финал, демон не обрывает."""
    import subprocess
    import sys
    run = tmp_path / "run"
    run.mkdir()
    out = tmp_path / "pushed.json"
    code = f"""
import json, time
from pathlib import Path
from src.loop.state import RunState
from src.relay.pusher import ensure_pusher
run = Path({str(run)!r})
RunState(run / "state.json").update(status="running")
class R:
    def __init__(self): self.seen = []
    def push_state(self, state):
        time.sleep(0.3)
        self.seen.append(state["status"])
        Path({str(out)!r}).write_text(json.dumps(self.seen))
        return True
ensure_pusher(run, R())
time.sleep(0.05)
RunState(run / "state.json").update(status="done")
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30,
                   cwd=str(Path(__file__).resolve().parents[2]))
    assert json.loads(out.read_text())[-1] == "done"


def test_secrets_env_value_not_pushed(tmp_path, monkeypatch):
    """Произвольный секрет из ~/.tero/secrets.env в тексте решения не уходит в relay."""
    from src.relay.client import RelayClient
    from src.relay.pusher import Pusher
    home = tmp_path / "home"
    (home / ".tero").mkdir(parents=True)
    (home / ".tero" / "secrets.env").write_text("CUSTOM_TOKEN=opaque-secret-value!\n")
    monkeypatch.setenv("HOME", str(home))
    sent = []

    def fake_fetch(method, path, body=None, token=""):
        sent.append(body)

        class R:
            status = 200

            def json(self): return {}
        return R()
    ctx = _ctx(tmp_path)
    client = RelayClient(_cfg(), fetch=fake_fetch)
    from src.loop.transition import phase_transition
    phase_transition(ctx, status="waiting_human", reason="r", text="t",
                     extra={"decisions": [{"text": "вот opaque-secret-value! держи"}]})
    Pusher(ctx.run_dir, client)
    assert _wait(lambda: bool(sent))
    assert "opaque-secret-value!" not in json.dumps(sent, ensure_ascii=False)
    # и прямой push мимо state.json — тоже чистится
    assert client.push_state({"status": "x", "decisions": [{"text": "opaque-secret-value!"}]})
    assert "opaque-secret-value!" not in json.dumps(sent[-1], ensure_ascii=False)


def test_final_sent_even_if_previous_push_hangs(tmp_path):
    """Идущая отправка висит дольше таймаута — финал всё равно уходит."""
    from src.loop.state import RunState
    from src.relay import pusher as pm
    run = tmp_path / "run"
    run.mkdir()
    RunState(run / "state.json").update(status="running")
    seen = []

    class Hang:
        def push_state(self, state):
            if state["status"] == "running":
                time.sleep(1.0)
            seen.append(state["status"])
            return True
    p = pm.Pusher(run, Hang())
    assert _wait(lambda: p._busy, timeout=2)
    RunState(run / "state.json").update(status="done")
    p.flush(0.2)  # ждать идущую отправку дольше нельзя — финал всё равно уходит
    assert seen and seen[-1] == "done"


def test_phone_markup_cannot_escape_data_block():
    """B16: ###, ####, одиночный # и ````` не открывают заголовок и ограду."""
    from src.loop import prompts as p
    for evil in ("### вне блока", "#### x", "# Новый контракт", "`````\nкод", "a\n## b\n```"):
        body = p.answer_block(evil, phone=True).split("\n", 1)[1]
        for line in body.rstrip("\n").split("\n"):
            assert line.startswith("> "), line
        assert "##" not in body and "```" not in body
