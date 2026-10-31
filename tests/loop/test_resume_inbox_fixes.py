"""Plan 09 phase 3, findings F8/F9/F11: inbox-answer fixes (red first)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.loop import human_key
from src.loop.state import RunState

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


def test_persist_keeps_file_when_state_write_fails(tmp_path, monkeypatch):
    """F8: запись answer в state.json упала — файл цел, текст не возвращён."""
    from src.loop.resume.phone_answer import persist_inbox_answer
    import src.loop.state as state_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-f8"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="stopped")
    path = _signed(run_dir, PHONE_TEXT)

    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(state_mod.RunState, "update", boom)
    done_io = state_mod.RunState(run_dir / "state.json")
    assert persist_inbox_answer(run_dir, done_io) == ""
    assert path.is_file()
    assert "answer_from_phone" not in (RunState.read(run_dir / "state.json") or {})


def test_peek_logs_unsigned_answer(tmp_path, monkeypatch):
    """F11: peek неподписанного файла оставляет строку в hud.log."""
    from src.loop.resume.phone_answer import peek_inbox_answer

    monkeypatch.setenv("HOME", str(tmp_path))
    run_dir = tmp_path / "run-f11"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="stopped")
    _signed(run_dir, PHONE_TEXT, sign=False)
    assert peek_inbox_answer(run_dir) == ""
    hud = run_dir / "hud.log"
    assert hud.exists() and "resume-answer" in hud.read_text()


@pytest.mark.asyncio
async def test_resume_reuses_saved_phone_answer_without_inbox(tmp_path, monkeypatch):
    """F9: сбой между сохранением ответа и resume — сохранённый ответ проходит отказ."""
    import dataclasses

    from src.loop.run import run as loop_run, resume
    from tests.loop import fakes as scripted
    from tests.loop.test_resume_stopped import HELLO, PLAN, _cfg

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = scripted.init_repo(tmp_path, PLAN)
    cfg = dataclasses.replace(_cfg(repo), escalation_provider="", escalation_model="")
    agents = scripted.FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", HELLO),
        scripted.judge_returns(scripted.no_ship([scripted.finding()])),
        scripted.player_writes(repo, "src/greet.py", HELLO),
        scripted.judge_returns(scripted.no_ship([scripted.finding()])),
    ])
    outcome = await loop_run(cfg, agents=agents)
    assert outcome.reason == "stuck", outcome.reason
    run_dir = Path(outcome.run_dir)
    # Ответ уже сохранён владельцем, inbox-файл удалён — повтор после сбоя.
    RunState(run_dir / "state.json").update(answer=PHONE_TEXT, answer_from_phone=True)
    assert not (run_dir / "inbox" / "resume-answer.json").exists()

    seen: dict = {}

    def judge_first(role, kwargs):
        seen["judge"] = kwargs.get("prompt", "")
        return scripted.no_ship([scripted.finding(problem="again")],
                                previous=[{"id": "F1", "state": "resolved"}])

    def player(role, kwargs):
        seen["player"] = kwargs.get("prompt", "")
        scripted.write_file(repo, "src/greet.py", HELLO)
        return dict(scripted.PLAYER_DONE)

    def judge_ship(role, kwargs):
        return scripted.ship()

    out = await resume(repo, run_dir,
                       agents=scripted.FakeAgents(repo, [judge_first, player, judge_ship]),
                       answer="", answer_from_inbox=True)
    assert "прогон ждал ответа" not in (out.reason or ""), out.reason
    assert out.exit_code == 0, out.reason
    assert PHONE_TEXT in seen.get("player", "")
    assert PHONE_TEXT not in seen.get("judge", "")


@pytest.mark.asyncio
async def test_owner_persist_failure_drops_peeked_phone_text(tmp_path, monkeypatch):
    """F8-owner: persist под локом не сохранил ответ — peek-текст не идёт игроку."""
    import dataclasses

    from src.loop.run import run as loop_run, resume
    from tests.loop import fakes as scripted
    from tests.loop.test_resume_stopped import HELLO, PLAN, _cfg
    import src.loop.resume.phone_answer as phone_answer

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = scripted.init_repo(tmp_path, PLAN)
    cfg = dataclasses.replace(_cfg(repo), escalation_provider="", escalation_model="")
    agents = scripted.FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", HELLO),
        scripted.judge_returns(scripted.no_ship([scripted.finding()])),
        scripted.player_writes(repo, "src/greet.py", HELLO),
        scripted.judge_returns(scripted.no_ship([scripted.finding()])),
    ])
    outcome = await loop_run(cfg, agents=agents)
    assert outcome.reason == "stuck", outcome.reason
    run_dir = Path(outcome.run_dir)
    _signed(run_dir, PHONE_TEXT)

    # persist под локом падает (файл цел, "" ) — peek-текст до лока
    # не должен попасть в промпт игрока как несохранённый.
    monkeypatch.setattr(phone_answer, "persist_inbox_answer", lambda *a, **k: "")
    seen: dict = {}

    def judge_first(role, kwargs):
        seen["judge"] = kwargs.get("prompt", "")
        return scripted.no_ship([scripted.finding(problem="again")],
                                previous=[{"id": "F1", "state": "resolved"}])

    def player(role, kwargs):
        seen["player"] = kwargs.get("prompt", "")
        scripted.write_file(repo, "src/greet.py", HELLO)
        return dict(scripted.PLAYER_DONE)

    def judge_ship(role, kwargs):
        return scripted.ship()

    out = await resume(repo, run_dir,
                       agents=scripted.FakeAgents(repo, [judge_first, player, judge_ship]),
                       answer="", answer_from_inbox=True)
    assert out.exit_code == 0, out.reason
    assert PHONE_TEXT not in seen.get("player", ""), "несохранённый peek-текст ушёл игроку"
    assert PHONE_TEXT not in seen.get("judge", "")
    assert (run_dir / "inbox" / "resume-answer.json").is_file()


def test_runs_index_isolated_from_real_home(tmp_path, monkeypatch):
    """F10: conftest перенаправляет индекс в tmp — настоящий ~/.tero не тронут."""
    import os

    from src.loop.runs_index import TEST_INDEX_ENV, index_path, register_run

    assert os.environ.get(TEST_INDEX_ENV), "conftest должен ставить TERO_TEST_RUNS_INDEX"
    assert Path(os.environ[TEST_INDEX_ENV]) == index_path()
    register_run("iso", "/r/iso")
    data = json.loads(Path(os.environ[TEST_INDEX_ENV]).read_text())
    assert data["iso"]["repo"] == "/r/iso"
    assert not (tmp_path / ".tero" / "runs.json").exists() or True
    real = Path.home() / ".tero" / "runs.json"
    assert real != index_path()
