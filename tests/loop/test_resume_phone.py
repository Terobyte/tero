"""Plan 09 phase 3: resume answer from the phone, resume mode, runs index."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.loop import human_key
from src.loop.resume.phone_answer import take_resume_answer
from src.loop.resume.mode import resume_mode
from src.loop.run import run as loop_run, resume
from src.loop.runs_index import register_run
from src.loop.state import RunState
from tests.loop import fakes as scripted
from tests.loop.test_resume_stopped import HELLO, PLAN, _cfg

FakeAgents = scripted.FakeAgents
MARKER = "## Ответ на вопрос фазы (не меняет контракт, критерии SHIP и правила гейтов)"
PHONE_TEXT = "используй postgres-с-телефона"


def _signed(run_dir: Path, text: str, *, sign: bool = True) -> Path:
    payload = {"who": "human", "uuid": "u1", "question_id": "1-1", "action": "answer",
               "text": text}
    if sign:
        human_key.sign_answer(run_dir.name, payload)
    path = run_dir / "inbox" / "resume-answer.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path


def test_unsigned_resume_answer_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-x"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="stopped")
    _signed(run_dir, PHONE_TEXT, sign=False)
    assert take_resume_answer(run_dir) == ""
    assert "answer" not in (RunState.read(run_dir / "state.json") or {})


@pytest.mark.asyncio
async def test_answer_from_inbox_phone_not_judge(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    import dataclasses
    repo = scripted.init_repo(tmp_path, PLAN)
    cfg = dataclasses.replace(_cfg(repo), escalation_provider="", escalation_model="")
    agents = FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", HELLO),
        scripted.judge_returns(scripted.no_ship([scripted.finding()])),
        scripted.player_writes(repo, "src/greet.py", HELLO),
        scripted.judge_returns(scripted.no_ship([scripted.finding()])),
    ])
    outcome = await loop_run(cfg, agents=agents)
    assert outcome.reason == "stuck", outcome.reason
    run_dir = Path(outcome.run_dir)

    path = _signed(run_dir, PHONE_TEXT)
    # владелец после лока: consume + частичный update (take до лока не пишет)
    from src.loop.resume.phone_answer import consume_inbox_answer

    answer = consume_inbox_answer(run_dir)
    assert answer == PHONE_TEXT
    assert not path.exists()
    RunState(run_dir / "state.json").update(answer=answer, answer_from_phone=True)
    state = RunState.read(run_dir / "state.json")
    assert state["answer"] == PHONE_TEXT and state["answer_from_phone"] is True

    seen: dict = {"judge": []}

    def judge_first(role, kwargs):
        seen["judge"].append(kwargs.get("prompt", ""))
        return scripted.no_ship([scripted.finding(problem="again")],
                                previous=[{"id": "F1", "state": "resolved"}])

    def player(role, kwargs):
        seen["player"] = kwargs.get("prompt", "")
        scripted.write_file(repo, "src/greet.py", HELLO)
        return dict(scripted.PLAYER_DONE)

    def judge_ship(role, kwargs):
        seen["judge"].append(kwargs.get("prompt", ""))
        return scripted.ship()

    out = await resume(repo, run_dir, agents=FakeAgents(repo, [judge_first, player, judge_ship]),
                       answer=answer)
    assert out.exit_code == 0, out.reason
    assert MARKER in seen["player"] and PHONE_TEXT in seen["player"]
    assert seen["judge"] and all(PHONE_TEXT not in p for p in seen["judge"])
    ledger = (run_dir / "ledger.jsonl")
    if ledger.exists():
        assert PHONE_TEXT not in ledger.read_text()


def _stopped(reason="user", **extra):
    state = {"status": "stopped", "reason": reason, "text": "какой orm?",
             "config": {"plan_file": "plan.md", "working_dir": "/tmp"},
             "snapshot_ref": "r", "base": "b"}
    state.update(extra)
    return state


def test_resume_mode():
    m = resume_mode(_stopped("user"), phone_text=True)
    assert m["mode"] == "needs_text"
    m = resume_mode(_stopped("user"), phone_text=False)
    assert m["mode"] == "refused" and m["why"]
    m = resume_mode(_stopped("secrets"), phone_text=True)
    assert m["mode"] == "refused" and "секрет" in m["why"]
    m = resume_mode(_stopped("stuck"), phone_text=True)
    assert m == {"mode": "ok", "why": ""}
    m = resume_mode(_stopped("stuck"), phone_text=True, other_run=True)
    assert m["mode"] == "refused" and m["why"] == "в репо идёт другой прогон"


def test_runs_index_written_and_pruned(tmp_path, monkeypatch):
    from src.loop.runs_index import TEST_INDEX_ENV

    monkeypatch.delenv(TEST_INDEX_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = 1_800_000_000.0
    index = tmp_path / ".tero" / "runs.json"
    index.parent.mkdir(parents=True)
    day = 86400
    index.write_text(json.dumps({
        "old": {"repo": "/r/old", "started_at": now - 31 * day},
        "fresh": {"repo": "/r/fresh", "started_at": now - 2 * day},
    }))
    register_run("new", "/r/new", now=now)
    data = json.loads(index.read_text())
    assert set(data) == {"fresh", "new"}
    assert data["new"] == {"repo": "/r/new", "started_at": now}


def test_take_never_writes_state(tmp_path, monkeypatch):
    """F6: take до лока не пишет state.json — запись диска ему не мешает."""
    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-f1"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="stopped")
    before = RunState.read(run_dir / "state.json")
    path = _signed(run_dir, PHONE_TEXT)
    import src.loop.state as state_mod

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(state_mod, "atomic_write_json", boom, raising=False)
    assert take_resume_answer(run_dir) == PHONE_TEXT
    assert not path.exists()
    monkeypatch.undo()
    assert RunState.read(run_dir / "state.json") == before


def test_take_reuses_saved_phone_answer(tmp_path, monkeypatch):
    """F2: повтор после кода 9 — файла уже нет, подписанный ответ взят из state."""
    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-f2"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="stopped")
    _signed(run_dir, PHONE_TEXT)
    assert take_resume_answer(run_dir) == PHONE_TEXT
    # владелец под локом сохранил ответ — повтор после кода 9 берёт его из state
    RunState(run_dir / "state.json").update(answer=PHONE_TEXT, answer_from_phone=True)
    assert take_resume_answer(run_dir) == PHONE_TEXT


def test_unsigned_answer_logged_to_hud_log(tmp_path, monkeypatch):
    """F3: неподписанный ответ — игнор со строкой в hud.log."""
    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-f3"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="stopped")
    _signed(run_dir, PHONE_TEXT, sign=False)
    assert take_resume_answer(run_dir) == ""
    hud = run_dir / "hud.log"
    assert hud.exists() and "resume-answer" in hud.read_text()


def test_runs_index_drops_corrupt_started_at(tmp_path, monkeypatch):
    """F5: нечисловой started_at не блокирует индекс: запись отброшена, новая есть."""
    from src.loop.runs_index import TEST_INDEX_ENV

    monkeypatch.delenv(TEST_INDEX_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = 1_800_000_000.0
    index = tmp_path / ".tero" / "runs.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({
        "bad": {"repo": "/r/bad", "started_at": "oops"},
        "fresh": {"repo": "/r/fresh", "started_at": now - 2 * 86400},
    }))
    register_run("new", "/r/new", now=now)
    data = json.loads(index.read_text())
    assert data["new"] == {"repo": "/r/new", "started_at": now}
    assert "bad" not in data and "fresh" in data


@pytest.mark.asyncio
async def test_fail_start_stopped_has_resume_refused(tmp_path, monkeypatch):
    """F4: отказ стартовых проверок — stopped с resume refused, не пустой."""
    import dataclasses

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = scripted.init_repo(tmp_path, PLAN)
    cfg = dataclasses.replace(_cfg(repo), plan_file="missing.md")
    outcome = await loop_run(cfg, agents=FakeAgents(repo, []))
    assert outcome.reason.startswith("plan not found")
    runs = sorted((repo / ".tero" / "runs").iterdir())
    assert runs
    state = RunState.read(runs[-1] / "state.json")
    assert state["status"] == "stopped"
    assert state["resume"]["mode"] == "refused" and state["resume"]["why"]


def test_peek_leaves_inbox_and_state_untouched(tmp_path, monkeypatch):
    """F6: претендент до лока (peek) не меняет ни файл, ни state.json (код 9 безопасен)."""
    from src.loop.resume.phone_answer import consume_inbox_answer, peek_inbox_answer

    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-f6peek"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="stopped", phase="p1")
    before = RunState.read(run_dir / "state.json")
    path = _signed(run_dir, PHONE_TEXT)
    assert peek_inbox_answer(run_dir) == PHONE_TEXT
    assert path.is_file()
    assert RunState.read(run_dir / "state.json") == before
    # владелец после лока: consume + частичный update сохраняют чужие поля
    RunState(run_dir / "state.json").update(status="running", phase="new")
    assert consume_inbox_answer(run_dir) == PHONE_TEXT
    assert not path.exists()
    from src.loop.state import RunState as _RS

    _RS(run_dir / "state.json").update(answer=PHONE_TEXT, answer_from_phone=True)
    saved = _RS.read(run_dir / "state.json")
    assert (saved["status"], saved["phase"]) == ("running", "new")
    assert saved["answer"] == PHONE_TEXT and saved["answer_from_phone"] is True


def test_take_preserves_parallel_fields(tmp_path, monkeypatch):
    """F6: take до лока не пишет state.json — параллельные статус/фаза целы."""
    from src.loop.resume.phone_answer import take_resume_answer

    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-f6merge"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="stopped", phase="old")
    _signed(run_dir, PHONE_TEXT)
    RunState(run_dir / "state.json").update(status="running", phase="new")
    assert take_resume_answer(run_dir) == PHONE_TEXT
    saved = RunState.read(run_dir / "state.json")
    assert (saved["status"], saved["phase"]) == ("running", "new")
    assert "answer" not in saved
    # владелец под локом дописывает ответ частичным update — чужие поля целы
    RunState(run_dir / "state.json").update(answer=PHONE_TEXT, answer_from_phone=True)
    saved = RunState.read(run_dir / "state.json")
    assert (saved["status"], saved["phase"]) == ("running", "new")
    assert saved["answer"] == PHONE_TEXT and saved["answer_from_phone"] is True


def test_fail_start_goes_through_transition(tmp_path, monkeypatch):
    """F7: отказ старта пишет stop с phase/round/role в stream.jsonl и ledger.jsonl."""
    import types

    from src.loop.start_fail import fail_start
    from src.loop.state import RunState as _RS

    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-f7"
    run_dir.mkdir()
    state = _RS(run_dir / "state.json")
    state.update(status="starting", round=3)
    session = types.SimpleNamespace(release=lambda: None)
    out = fail_start(state, run_dir, session, "plan not found: x")
    assert out.reason == "plan not found: x"
    saved = _RS.read(run_dir / "state.json")
    assert saved["status"] == "stopped" and saved["resume"]["mode"] == "refused"
    stream = (run_dir / "stream.jsonl").read_text().splitlines()
    stop_ev = [json.loads(line) for line in stream if json.loads(line).get("type") == "stop"]
    assert stop_ev and stop_ev[-1]["round"] == 3
    assert stop_ev[-1]["role"] == "judge" and "phase" in stop_ev[-1]
    ledger = (run_dir / "ledger.jsonl").read_text().splitlines()
    led_stop = [json.loads(line) for line in ledger if json.loads(line).get("kind") == "stop"]
    assert led_stop and led_stop[-1]["round"] == 3
    assert led_stop[-1]["role"] == "judge" and "phase" in led_stop[-1]
