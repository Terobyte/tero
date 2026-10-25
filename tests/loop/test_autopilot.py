"""Фаза 4: таймауты ожидания, аренда, автопилот."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from src.config import Config
from src.loop import autopilot as auto
from src.relay.iface import FakeRelay, NoopRelay

PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""

HELLO = "def greet():\n    return 'hello'\n"


def test_autopilot_contract_exists():
    c = Config()
    assert c.human_timeout_s == 900
    assert c.agent_timeout_s == 1800
    assert c.autopilot_grace_s == 900
    assert c.wake_command == ""
    assert hasattr(auto, "choose")


def _repo(tmp_path: Path, tag: str = "repo") -> Path:
    from tests.loop import fakes as scripted

    root = tmp_path / tag
    root.mkdir(parents=True, exist_ok=True)
    return scripted.init_repo(root, PLAN)


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


def _ctx(tmp_path: Path, repo: Path, cfg: Config, relay=None):
    from src.loop.decision_step import DecisionStep
    from src.loop.git import head
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.phase_ctx import PhaseContext, PhaseState
    from src.loop.plan import load_phases, load_preamble
    from src.loop.state import RunState
    from src.loop.stream import Stream
    from tests.loop import fakes as scripted

    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox_dir(ledger.run_dir, create=True)
    phases = load_phases(repo / "plan.md")
    ctx = PhaseContext(
        phase=phases[0], total_phases=1, repo=repo, plan_path=repo / "plan.md",
        preamble="", cfg=cfg, agents=scripted.FakeAgents(repo, []),
        test_command=None, tester_enabled=False, ledger=ledger,
        run_dir=ledger.run_dir, state_io=RunState(ledger.run_dir / "state.json"),
        stream=Stream(ledger.run_dir / "stream.jsonl"), pulse=None,
        checkpoint=head(repo), contract="", suite_hashes={}, guarded={},
        link_targets=set(), st=PhaseState(),
    )
    from src.loop import snapshot as _snap
    from src.loop.git import rollback as _rollback

    def _real_rollback(reason: str) -> str | None:
        run_id = ledger.run_dir.name
        ref = _snap.ref_name(run_id, ctx.phase.index, ctx.st.round)
        run_rel = ledger.run_dir.relative_to(repo).as_posix()
        try:
            _snap.take_snapshot(repo, ctx.checkpoint, ref)
            if not _snap.verify(repo, ref):
                return None
        except Exception:
            return None
        try:
            _rollback(repo, ctx.checkpoint)
        finally:
            # откат `git clean -fd` сносит незаигноренный run_dir:
            # пересоздаём каркас прогона (F11: дерево чинится, журнал жив).
            (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
            ledger.path.touch(exist_ok=True)
            try:
                from src.loop.stream import Stream as _S

                if not (ledger.run_dir / "stream.jsonl").exists():
                    _S(ledger.run_dir / "stream.jsonl")
            except Exception:
                pass
        return ref

    step = DecisionStep(ctx, _real_rollback, poll_s=0.01,
                        relay=relay, now=time.time)
    (repo / "src" / "greet.py").write_text(HELLO)
    import subprocess as _git2

    _git2.run(["git", "add", "-A"], cwd=repo, check=False,
              capture_output=True)
    halted = step.halt("stuck")
    assert halted.status == "waiting_human"
    state = RunState.read(ledger.run_dir / "state.json") or {}
    return step, ledger, state.get("question", {}).get("id")


def _resolve_in_thread(step, ledger, timeout=5, tick=None):
    from src.loop.state import RunState

    out = {}

    def _go() -> None:
        try:
            out["r"] = step.resolve()
        except Exception as exc:  # noqa: BLE001
            out["e"] = exc

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    if tick is not None:
        deadline = time.time() + timeout
        while t.is_alive() and time.time() < deadline:
            try:
                tick()
            except Exception:
                pass
            time.sleep(0.005)
    t.join(timeout=1)
    assert not t.is_alive(), "resolve hung"
    assert "e" not in out, out.get("e")
    return out["r"], RunState.read(ledger.run_dir / "state.json") or {}


def test_human_timeout_to_waiting_agent(tmp_path: Path, monkeypatch):
    from src.loop.state import RunState
    from src.loop.wait import wait_run
    from tests.loop.test_wait import _repo as _wait_repo

    repo = _repo(tmp_path, "repo_ht")
    cfg = _cfg(decisions=True, human_timeout_s=0.05, agent_timeout_s=3600,
               autopilot_grace_s=3600)
    step, ledger, qid = _ctx(tmp_path, repo, cfg)
    monkeypatch.setattr(auto, "NO_RELAY_GRACE_S", 3600)
    seen = []

    real_resolve = step.resolve

    def _spy_resolve():
        seen.append(RunState.read(ledger.run_dir / "state.json") or {})
        return real_resolve()

    step.resolve = _spy_resolve

    def _answerer_we_wait():
        deadline = time.time() + 5
        while time.time() < deadline:
            state = RunState.read(ledger.run_dir / "state.json") or {}
            if state.get("status") == "waiting_agent":
                return
            time.sleep(0.01)

    import threading as _t

    t = _t.Thread(target=_answerer_we_wait, daemon=True)
    t.start()
    out = {}
    rt = _t.Thread(target=lambda: out.update(r=step.resolve()), daemon=True)
    rt.start()
    from src.loop.inbox import write_inbox_file

    deadline = time.time() + 5
    while time.time() < deadline:
        state = RunState.read(ledger.run_dir / "state.json") or {}
        if (state.get("question") or {}).get("id") and not out:
            write_inbox_file(ledger.run_dir, "a.json", json.dumps({
                "uuid": "h1", "question_id": state["question"]["id"],
                "action": "skip", "who": "human"}))
            break
        time.sleep(0.01)
    rt.join(timeout=5)
    assert not rt.is_alive(), "resolve hung"
    assert out["r"].status == "skip"
    state = RunState.read(ledger.run_dir / "state.json") or {}
    decisions = state.get("decisions") or []
    assert decisions and decisions[-1].get("who") == "human"

    (tmp_path / "w2c").mkdir(exist_ok=True)
    repo2 = _wait_repo(tmp_path / "w2c")
    run2 = tmp_path / "run2"
    (run2).mkdir()
    (run2 / "state.json").write_text(json.dumps({"status": "waiting_agent"}))
    assert wait_run(run2, repo2) == 3

    (tmp_path / "w3c").mkdir(exist_ok=True)
    repo3 = _repo(tmp_path / "w3c")
    cfg3 = _cfg(decisions=True, human_timeout_s=0.01, agent_timeout_s=3600,
                autopilot_grace_s=3600)
    step3, ledger3, qid3 = _ctx(tmp_path / "w3c", repo3, cfg3)
    monkeypatch.setattr(auto, "NO_RELAY_GRACE_S", 3600)
    t3 = _t.Thread(target=step3.resolve, daemon=True)
    t3.start()
    deadline = time.time() + 5
    saw_agent = False
    while time.time() < deadline:
        st3 = RunState.read(ledger3.run_dir / "state.json") or {}
        if st3.get("status") == "waiting_agent" and (
                st3.get("question") or {}).get("id") == qid3:
            saw_agent = True
            rc = wait_run(ledger3.run_dir, repo3, timeout_s=2, poll_s=0)
            assert rc == 3, "wait_run на том же прогоне возвращает 3"
            write_inbox_file(ledger3.run_dir, "ans.json", json.dumps({
                "uuid": "stop-1", "question_id": qid3,
                "action": "stop", "who": "human"}))
            break
        time.sleep(0.01)
    assert saw_agent, "вопрос остался открытым в waiting_agent"
    t3.join(timeout=5)
    assert not t3.is_alive(), "resolve hung"


def test_lease_written_by_wait(tmp_path: Path):
    import os

    from src.loop.wait import _AGENT_STATUSES, wait_run
    from tests.loop.test_wait import _repo as _wait_repo

    repo = _wait_repo(tmp_path)
    run_dir = tmp_path / "run"
    (run_dir).mkdir()
    from src.hud.registry import current_pid_started

    (run_dir / "state.json").write_text(json.dumps(
        {"status": "running", "pid": os.getpid(),
         "pid_started": current_pid_started()}))
    code = wait_run(run_dir, repo, timeout_s=0, poll_s=0,
                    agent_pid=os.getpid())
    assert code == 6
    assert (run_dir / "lease").is_file(), "выход с 6 пишет аренду now+300"
    lease = json.loads((run_dir / "lease").read_text())
    assert lease["until"] > time.time()
    assert lease["pid"] == os.getpid()
    assert not auto.lease_valid(run_dir, alive=lambda pid: False)
    assert auto.lease_valid(run_dir, alive=lambda pid: True)


def test_autopilot_after_lease_expired(tmp_path: Path):
    import os as _os

    from src.loop.state import RunState as _RS

    repo = _repo(tmp_path, "repo_lease")
    relay = FakeRelay()
    cfg = _cfg(decisions=True, human_timeout_s=0.05, agent_timeout_s=60,
               autopilot_grace_s=0.15)
    step, ledger, qid = _ctx(tmp_path, repo, cfg, relay=relay)
    # F22: живая аренда задерживает автопилот до истечения срока.
    (ledger.run_dir / "lease").write_text(json.dumps(
        {"until": time.time() + 3600, "pid": _os.getpid()}))
    assert auto.lease_valid(ledger.run_dir)
    import threading as _t

    out: dict = {}
    t = _t.Thread(target=lambda: out.update(r=step.resolve()), daemon=True)
    t.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            st = _RS.read(ledger.run_dir / "state.json") or {}
            if st.get("status") == "waiting_agent":
                break
            time.sleep(0.01)
        # живая аренда: ждём дольше human+grace — notify и решения нет.
        time.sleep(0.3)
        assert t.is_alive(), "resolve завершился при живой аренде"
        assert relay.notified == [], "нет notify при живой аренде"
        mid = _RS.read(ledger.run_dir / "state.json") or {}
        assert (mid.get("decisions") or []) == []
        # истекаем аренду — дальше notify и skip от автопилота.
        (ledger.run_dir / "lease").write_text(json.dumps(
            {"until": time.time() - 1, "pid": _os.getpid()}))
        t.join(timeout=5)
        assert not t.is_alive(), "resolve hung"
    finally:
        t.join(timeout=1)
    res = out["r"]
    state = _RS.read(ledger.run_dir / "state.json") or {}
    assert res.status == "skip"
    assert any("агента нет" in n for n in relay.notified)
    decisions = state.get("decisions") or []
    assert decisions and decisions[-1].get("who") == "autopilot"
    assert decisions[-1].get("action") == "skip"


def test_autopilot_no_relay_no_agent_10min(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path, "repo_noop")
    monkeypatch.setattr(auto, "NO_RELAY_GRACE_S", 0.05)
    cfg = _cfg(decisions=True, human_timeout_s=60, agent_timeout_s=60,
               autopilot_grace_s=60)
    step, ledger, qid = _ctx(tmp_path, repo, cfg, relay=NoopRelay())
    res, state = _resolve_in_thread(step, ledger)
    assert res.status == "skip"
    decisions = state.get("decisions") or []
    assert decisions and decisions[-1].get("who") == "autopilot"


def _not_chain_repo(tmp_path: Path, tag: str = "repo_nc"):
    from tests.loop import fakes as scripted

    repo = tmp_path / tag
    repo.mkdir(parents=True, exist_ok=True)
    scripted.init_repo(repo, PLAN)
    return repo


def test_autopilot_not_chain(tmp_path: Path):
    import subprocess as _git

    ans = auto.choose(
        {"reason": "stuck", "question_id": "2-1", "phase": 2},
        {"question_id": "2-1",
         "decisions": [{"who": "autopilot", "action": "skip",
                        "question_id": "1-3"}]})
    assert ans.action == "stop"
    assert "две фазы подряд" in ans.text
    # F11: полный прогон: фаза 1 пропущена автопилотом в долг, фаза 2
    # в точке решения — реальный откат дерева и запись решения в журнале.
    repo = _not_chain_repo(tmp_path, "repo_nc2")
    cfg = _cfg(decisions=True, human_timeout_s=0.01, agent_timeout_s=3600,
               autopilot_grace_s=0.01)
    step, ledger, qid = _ctx(tmp_path, repo, cfg,
                             relay=FakeRelay())
    from src.loop.state import RunState

    res1 = step.resolve()
    assert res1.status == "skip"
    debt = (RunState.read(ledger.run_dir / "state.json") or {}).get("debt") or []
    assert debt and debt[-1].get("who") == "autopilot"
    import dataclasses as _dc

    marker = repo / "src" / "phase2.txt"
    marker.write_text("phase2 work\n")
    fresh = RunState.read(ledger.run_dir / "state.json") or {}
    RunState(ledger.run_dir / "state.json").update(
        decisions=[{"who": "autopilot", "action": "skip",
                    "question_id": "1-1", "ts": 1.0}],
        decision_seq={"1": 1})
    step.ctx = _dc.replace(step.ctx, phase=_dc.replace(step.ctx.phase, index=1))

    from src.loop.state import Status

    qid2 = step.open_question(
        (fresh.get("question") or {}).get("reason") or "stuck",
        (fresh.get("question") or {}).get("text") or "нужен человек")
    assert qid2 and qid2.startswith("2-")
    got = auto.autopilot_answer(
        step, qid2, (fresh.get("question") or {}).get("reason") or "stuck")
    assert getattr(got, "status", None) == "stop"
    st2 = RunState.read(ledger.run_dir / "state.json") or {}
    assert st2.get("status") == Status.STOPPED.value
    assert not marker.exists(), "откат дерева"
    decisions = st2.get("decisions") or []
    assert decisions and decisions[-1].get("who") == "autopilot"
    assert decisions[-1].get("why") == "not_chain"
    assert decisions[-1].get("action") == "stop"
    assert decisions[-1].get("question_id") == qid2
    relay = step.relay
    assert isinstance(relay, FakeRelay)
    assert any("две фазы подряд" in (n or "") for n in relay.notified)


def test_wake_command_once_when_lease_expired(tmp_path: Path):
    from src.loop.inbox import Inbox

    repo = _repo(tmp_path, "repo_wake")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"question": {"id": "1-1"}}))
    calls: list[list[str]] = []

    class _Popen:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
            self.pid = 424242

    pid = auto.maybe_wake(run_dir, "1-1", "echo {run_dir} {question_id}",
                          60, popen=_Popen, now=time.time(),
                          alive=lambda pid: False)
    assert pid == 424242
    assert calls and str(run_dir) in " ".join(calls[0])
    assert "1-1" in " ".join(calls[0])
    state = json.loads((run_dir / "state.json").read_text())
    assert state["woken"]["1-1"] == 424242
    before = len(calls)
    auto.maybe_wake(run_dir, "1-1", "echo {run_dir} {question_id}", 60,
                    popen=_Popen, now=time.time(), alive=lambda pid: False)
    assert len(calls) == before, "один раз на вопрос"
    (run_dir / "inbox").mkdir(exist_ok=True)
    (run_dir / "lease").write_text(json.dumps({"until": time.time() + 600,
                                               "pid": 424242}))
    auto.maybe_wake(run_dir, "2-1", "echo hi", 60, popen=_Popen,
                    now=time.time(), alive=lambda pid: True)
    assert len(calls) == before, "при живой аренде не запущен"

    (run_dir / "state.json").write_text(json.dumps({
        "question": {"id": "1-1", "reason": "stuck",
                     "actions": ["retry", "more", "skip", "stop"],
                     "all_actions": ["retry", "more", "skip", "stop"]}}))
    (run_dir / "inbox" / "w.json").write_text(json.dumps(
        {"uuid": "w1", "question_id": "1-1", "action": "more",
         "who": "agent", "woken": True}))
    assert Inbox(run_dir).take("1-1", FakeRelay()) is None
    assert "retry|skip|stop" in (run_dir / "inbox" / "w1.result").read_text()
    # F8: чужой pid не считается разбуженным — more от него принимается.
    (run_dir / "inbox" / "o.json").write_text(json.dumps(
        {"uuid": "o1", "question_id": "1-1", "action": "more",
         "who": "agent", "agent_pid": 999999}))
    assert Inbox(run_dir).take("1-1", FakeRelay()) is not None
    assert (run_dir / "inbox" / "o1.result").read_text() == "принято"


def test_choose_table_and_budget():
    assert auto.choose({"reason": "user", "question_id": "1-1", "phase": 1},
                       {}).action == "skip"
    assert auto.choose({"reason": "stuck", "question_id": "1-1", "phase": 1},
                       {}).action == "skip"
    assert auto.choose({"reason": "fuse", "question_id": "1-1", "phase": 1},
                       {}).action == "skip"
    assert auto.choose({"reason": "secrets", "question_id": "1-1", "phase": 1},
                       {}).action == "skip"
    assert auto.choose({"reason": "provider_unavailable", "question_id": "1-1",
                        "phase": 1}, {}).action == "wait"
    full = {"budget": {"1": 3}}
    assert auto.choose({"reason": "provider_quota", "question_id": "1-1",
                        "phase": 1}, full).action == "skip"


def test_notify_cooldown_30min(tmp_path: Path):
    relay = FakeRelay()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert auto.notify_once(relay, run_dir, "stuck", "ping", now=1000.0)
    assert not auto.notify_once(relay, run_dir, "stuck", "ping", now=1000.0 + 60)
    assert auto.notify_once(relay, run_dir, "stuck", "ping",
                            now=1000.0 + auto.NOTIFY_COOLDOWN_S)
    assert len(relay.notified) == 2


def test_pid_alive_zombie_is_dead(monkeypatch):
    import os as _os

    monkeypatch.setattr(_os, "kill", lambda pid, sig: None)
    try:
        import psutil as _psutil

        class _P:
            def __init__(self, pid):
                pass

            def status(self):
                return _psutil.STATUS_ZOMBIE

        monkeypatch.setattr(_psutil, "Process", _P)
    except Exception:
        pytest.skip("no psutil")
    assert auto.pid_alive(424242) is False


def test_extend_from_answer_uses_agent_pid(tmp_path: Path):
    import os as _os

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "lease").write_text(json.dumps(
        {"until": time.time() + 600, "pid": 111}))
    auto.extend_from_answer(run_dir, 600, agent_pid=222)
    data = json.loads((run_dir / "lease").read_text())
    assert int(data["pid"]) == 222
    # без pid ответа живая аренда сохраняет свой pid
    auto.extend_from_answer(run_dir, 600, agent_pid=None,
                            alive=lambda pid: True)
    data2 = json.loads((run_dir / "lease").read_text())
    assert int(data2["pid"]) == 222


def test_woken_stamp_persisted_to_file(tmp_path: Path):
    from src.loop.inbox import Inbox
    from src.loop.state import RunState

    run_dir = tmp_path / "run"
    (run_dir / "inbox").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "question": {"id": "1-1", "reason": "stuck",
                     "actions": ["retry", "skip", "stop"],
                     "all_actions": ["retry", "more", "skip", "stop"]},
        "woken": {"1-1": 424242}}))
    # woken ставит только tero answer после проверки сессии (F8/F20):
    # владелец читает маркер как есть, сам не дописывает.
    (run_dir / "inbox" / "a.json").write_text(json.dumps(
        {"uuid": "a1", "question_id": "1-1", "action": "more",
         "who": "agent", "agent_pid": 424242, "woken": True}))
    assert Inbox(run_dir).take("1-1", FakeRelay()) is None
    raw = json.loads((run_dir / "inbox" / "a.json").read_text())
    assert raw.get("woken") is True


def test_answer_cli_marks_agent_pid(tmp_path: Path, monkeypatch):
    import sys

    from src.commands import inbox_cmds as _cmds

    run_dir = tmp_path / "run"
    (run_dir / "inbox").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({"status": "running"}))
    monkeypatch.setenv("TERO_ANSWER_WAIT_S", "0")
    monkeypatch.setenv("TERO_ANSWER_POLL_S", "0.001")
    args = type("A", (), {"question_id": "1-1", "action": "skip",
                          "text": "", "why": "", "human": False,
                          "working_dir": str(tmp_path),
                          "run": str(run_dir), "agent_pid": None})()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert _cmds.run_answer(args) == 1  # владелец не ответил, но файл записан
    files = list((run_dir / "inbox").glob("*.json"))
    assert files, "inbox файл записан"
    payload = json.loads(files[0].read_text())
    assert int(payload.get("agent_pid") or 0) > 0


def test_wake_only_after_waiting_agent(tmp_path: Path, monkeypatch):
    """F14: wake_command молчит в waiting_human, запускается один раз после
    перехода в waiting_agent, и сразу после запуска нет notify «агента нет»."""
    import threading as _t

    from src.loop.inbox import write_inbox_file
    from src.loop.state import RunState

    repo = _repo(tmp_path, "repo_wake_gate")
    relay = FakeRelay()
    cfg = _cfg(decisions=True, human_timeout_s=0.15, agent_timeout_s=3600,
               autopilot_grace_s=3600,
               wake_command="echo {run_dir} {question_id}")
    step, ledger, qid = _ctx(tmp_path, repo, cfg, relay=relay)
    monkeypatch.setattr(auto, "NO_RELAY_GRACE_S", 3600)
    calls: list[list[str]] = []

    class _Popen:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
            self.pid = 555001

    step._popen = _Popen
    out = {}
    t = _t.Thread(target=lambda: out.update(r=step.resolve()), daemon=True)
    t.start()
    try:
        time.sleep(0.05)
        assert calls == [], "wake_command до human_timeout"
        deadline = time.time() + 5
        while time.time() < deadline:
            st = RunState.read(ledger.run_dir / "state.json") or {}
            if st.get("status") == "waiting_agent":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("нет перехода в waiting_agent")
        deadline = time.time() + 5
        while not calls and time.time() < deadline:
            time.sleep(0.01)
        assert len(calls) == 1, "wake один раз после перехода"
        assert str(ledger.run_dir) in " ".join(calls[0])
        assert qid in " ".join(calls[0])
        assert not any("агента нет" in n for n in relay.notified)
        write_inbox_file(ledger.run_dir, "fin.json", json.dumps({
            "uuid": "fin1", "question_id": qid,
            "action": "skip", "who": "human"}))
    finally:
        t.join(timeout=5)
    assert not t.is_alive(), "resolve hung"
    assert out["r"].status == "skip"


def test_f19_human_with_matching_agent_pid_not_woken(tmp_path: Path):
    """F19 red: human more с agent_pid==woken не должен отклоняться."""
    from src.loop.inbox import Inbox

    run_dir = tmp_path / "run_f19"
    (run_dir / "inbox").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "question": {"id": "1-1", "reason": "stuck",
                     "actions": ["retry", "more", "skip", "stop"],
                     "all_actions": ["retry", "more", "skip", "stop"]},
        "woken": {"1-1": 424242}}))
    (run_dir / "inbox" / "h.json").write_text(json.dumps(
        {"uuid": "h1", "question_id": "1-1", "action": "more",
         "who": "human", "agent_pid": 424242}))
    got = Inbox(run_dir).take("1-1", FakeRelay())
    assert got is not None, "человеческий more не ограничивается woken"
    raw = json.loads((run_dir / "inbox" / "h.json").read_text())
    assert raw.get("woken") is not True


def test_f18_woken_via_ancestor_chain_ignores_claimed_pid(
        tmp_path: Path, monkeypatch):
    """F17/F18 red: разбуженная сессия через дочерний shell помечается woken,
    заявленный --agent-pid игнорируется."""
    import os as _os
    import sys

    from src.commands import inbox_cmds as _cmds

    run_dir = tmp_path / "run_f18"
    (run_dir / "inbox").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "status": "waiting_agent",
        "question": {"id": "1-1"},
        "woken": {"1-1": 777}}))
    monkeypatch.setenv("TERO_ANSWER_WAIT_S", "0")
    monkeypatch.setenv("TERO_ANSWER_POLL_S", "0.001")
    monkeypatch.setattr(_os, "getppid", lambda: 888)

    class _FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def parents(self):
            return [_FakeProc(777), _FakeProc(1)]

    import psutil as _psutil

    monkeypatch.setattr(_psutil, "Process", _FakeProc)
    args = type("A", (), {"question_id": "1-1", "action": "skip",
                          "text": "", "why": "", "human": False,
                          "working_dir": str(tmp_path),
                          "run": str(run_dir), "agent_pid": 999999})()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert _cmds.run_answer(args) == 1
    files = list((run_dir / "inbox").glob("*.json"))
    assert files, "inbox файл записан"
    payload = json.loads(files[0].read_text())
    assert payload.get("woken") is True, "ответ разбуженной сессии помечен"
    assert int(payload.get("agent_pid") or 0) == 777


def test_answer_cli_marks_woken_by_real_ppid(tmp_path: Path, monkeypatch):
    """F6+F17: разбуженная сессия помечается woken по реальному ppid,
    а не по заявленному --agent-pid."""
    import os as _os
    import sys

    from src.commands import inbox_cmds as _cmds

    run_dir = tmp_path / "run"
    (run_dir / "inbox").mkdir(parents=True)
    real_ppid = 777001
    (run_dir / "state.json").write_text(json.dumps({
        "status": "waiting_agent",
        "question": {"id": "1-1"},
        "woken": {"1-1": real_ppid}}))
    monkeypatch.setenv("TERO_ANSWER_WAIT_S", "0")
    monkeypatch.setenv("TERO_ANSWER_POLL_S", "0.001")
    monkeypatch.setattr(_os, "getppid", lambda: real_ppid)
    args = type("A", (), {"question_id": "1-1", "action": "skip",
                          "text": "", "why": "", "human": False,
                          "working_dir": str(tmp_path),
                          "run": str(run_dir), "agent_pid": 999999})()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert _cmds.run_answer(args) == 1
    files = list((run_dir / "inbox").glob("*.json"))
    assert files, "inbox файл записан"
    payload = json.loads(files[0].read_text())
    assert payload.get("woken") is True, "ответ разбуженной сессии помечен"


def test_f20_claimed_pid_not_woken(tmp_path: Path):
    """F20 red: заявленный agent_pid без woken:true — не доказательство.
    more от чужого агента принимается, woken в файл не дописывается."""
    from src.loop.inbox import Inbox

    run_dir = tmp_path / "run_f20"
    (run_dir / "inbox").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "question": {"id": "1-1", "reason": "stuck",
                     "actions": ["retry", "more", "skip", "stop"],
                     "all_actions": ["retry", "more", "skip", "stop"]},
        "woken": {"1-1": 424242}}))
    (run_dir / "inbox" / "c.json").write_text(json.dumps(
        {"uuid": "c1", "question_id": "1-1", "action": "more",
         "who": "agent", "agent_pid": 424242}))
    got = Inbox(run_dir).take("1-1", FakeRelay())
    assert got is not None, "чужой agent_pid не делает ответ woken"
    raw = json.loads((run_dir / "inbox" / "c.json").read_text())
    assert raw.get("woken") is not True


def test_f23_pid_states(monkeypatch):
    import os as _os

    try:
        import psutil as _p
    except Exception:
        import pytest as _pt

        _pt.skip("no psutil")

    class _Up:
        def __init__(self, pid):
            pass

        def status(self):
            return _p.STATUS_RUNNING

    # EPERM от kill(0) при живом процессе — аренда жива.
    monkeypatch.setattr(_p, "Process", _Up)
    monkeypatch.setattr(_os, "kill",
                        lambda pid, sig: (_ for _ in ()).throw(PermissionError(1, "x")))
    assert auto.pid_alive(424242) is True

    class _Gone:
        def __init__(self, pid):
            pass

        def status(self):
            raise _p.NoSuchProcess(424242)

    # NoSuchProcess после успешного kill — процесс умер между проверками.
    monkeypatch.setattr(_p, "Process", _Gone)
    monkeypatch.setattr(_os, "kill", lambda pid, sig: None)
    assert auto.pid_alive(424242) is False
    # ESRCH от kill(0) — мёртв без psutil.
    monkeypatch.setattr(_os, "kill",
                        lambda pid, sig: (_ for _ in ()).throw(
                            ProcessLookupError(3, "x")))
    assert auto.pid_alive(424242) is False


def test_f24_wake_once_on_failure(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"question": {"id": "1-1"}}))
    calls: list = []

    def _boom(cmd, **kw):
        calls.append(cmd)
        raise RuntimeError("nope")

    assert auto.maybe_wake(run_dir, "1-1", "echo {run_dir} {question_id}",
                           60, popen=_boom, now=time.time(),
                           alive=lambda pid: False) is None
    assert auto.maybe_wake(run_dir, "1-1", "echo {run_dir} {question_id}",
                           60, popen=_boom, now=time.time(),
                           alive=lambda pid: False) is None
    assert len(calls) == 1, f"wake retried after failure: {len(calls)}"


def test_f25_run_dir_with_spaces(tmp_path: Path):
    run_dir = tmp_path / "a b"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"question": {"id": "1-1"}}))
    calls: list = []

    class _P:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
            self.pid = 424242

    auto.maybe_wake(run_dir, "1-1", "echo {run_dir} {question_id}",
                    60, popen=_P, now=time.time(), alive=lambda pid: False)
    assert calls, "wake not launched"
    assert str(run_dir) in calls[0], f"run_dir split: {calls[0]!r}"


def test_f26_pid_eintr_keeps_lease(monkeypatch):
    import errno as _errno
    import os as _os

    try:
        import psutil as _p
    except Exception:
        import pytest as _pt

        _pt.skip("no psutil")

    class _Up:
        def __init__(self, pid):
            pass

        def status(self):
            return _p.STATUS_RUNNING

    monkeypatch.setattr(_p, "Process", _Up)
    monkeypatch.setattr(_os, "kill",
                        lambda pid, sig: (_ for _ in ()).throw(
                            OSError(_errno.EINTR, "interrupted")))
    assert auto.pid_alive(424242) is True


def test_f27_wake_not_launched_without_marker(tmp_path: Path, monkeypatch):
    from src.loop import autopilot as _auto

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"question": {"id": "1-1"}}))
    calls: list = []

    def _boom(cmd, **kw):
        calls.append(cmd)
        raise AssertionError("Popen must not run without persisted marker")

    class _BrokenState:
        @staticmethod
        def read(path):
            return {}

        def __init__(self, path):
            pass

        def update(self, **fields):
            return None

    monkeypatch.setattr(_auto, "RunState", _BrokenState)
    assert _auto.maybe_wake(run_dir, "1-1", "echo {run_dir} {question_id}",
                            60, popen=_boom, now=time.time(),
                            alive=lambda pid: False) is None
    assert calls == []


def test_f28_resolve_handles_not_chain_outcome(tmp_path: Path):
    from src.loop.phase_ctx import PhaseOutcome as _Outcome

    repo = _repo(tmp_path, "repo_f28")
    cfg = _cfg(decisions=True)
    step, ledger, qid = _ctx(tmp_path, repo, cfg)
    stop = _Outcome(status="stop", reason="stuck", stop_text="две фазы подряд")
    step.wait_answer = lambda _qid, _opened=None: stop  # type: ignore[assignment]
    got = step.resolve()
    assert getattr(got, "status", None) == "stop"
    assert "решение не применено" not in (getattr(got, "stop_text", "") or "")


def test_f30_wake_marker_update_holds_owner_lock(tmp_path: Path, monkeypatch):
    from src.loop import autopilot as _auto
    from src.loop import state as _state

    run_dir = tmp_path / "run_f30"
    run_dir.mkdir()
    fp = run_dir / "state.json"
    fp.write_text(json.dumps({"woken": {}}))
    lock = _state.owner_lock(fp)
    real_update = _state.RunState.update
    held: dict = {}

    def _spy_update(self, **fields):
        held["owned"] = lock._is_owned()
        return real_update(self, **fields)

    monkeypatch.setattr(_state.RunState, "update", _spy_update)
    assert _auto._remember_wake(run_dir, "1-1", 5) is True
    assert held.get("owned") is True, "чтение-обновление-проверка без общего owner_lock"
    cur = json.loads(fp.read_text())
    assert (cur.get("woken") or {}).get("1-1") == 5


def test_f29_failed_wake_marker_never_rewrites_state(tmp_path: Path, monkeypatch):
    from src.loop import autopilot as _auto

    run_dir = tmp_path / "run_f29"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"woken": {}}))
    monkeypatch.setattr(_auto, "_wake_marker", lambda *a, **k: None)
    rewrites: list = []
    monkeypatch.setattr("src.loop.autopilot.atomic_write_text",
                        lambda p, t, **k: rewrites.append(str(p)), raising=False)
    assert _auto._remember_wake(run_dir, "1-1", 5) is False
    assert rewrites == [], "неудачный маркер не восстанавливает state.json целиком"
