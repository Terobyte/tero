"""Фаза 8: inbox, вопрос и tero answer."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from src.loop.state import RunState
from src.relay.iface import Answer, FakeRelay, NoopRelay

ROOT = Path(__file__).resolve().parent.parent.parent

QUESTION = {
    "id": "1-1",
    "text": "нужен человек",
    "actions": ["answer", "skip", "stop", "more", "model", "retry", "wait", "allow"],
}


def _run_dir(repo: Path, name: str = "20200101-000000-demo") -> Path:
    run = repo / ".tero" / "runs" / name
    run.mkdir(parents=True)
    return run


def _state(run: Path, question: dict | None = None) -> dict:
    RunState(run / "state.json").update(
        status="waiting_human",
        question=question if question is not None else dict(QUESTION),
    )
    state = RunState.read(run / "state.json")
    assert state is not None
    return state


def _answer_file(
    run: Path,
    name: str,
    uid: str,
    qid: str,
    action: str,
    text: str = "",
    who: str = "agent",
    why: str = "потому",
) -> Path:
    inbox = run / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {
        "uuid": uid,
        "question_id": qid,
        "action": action,
        "text": text,
        "why": why,
        "who": who,
        "ts": time.time(),
    }
    path = inbox / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _result(run: Path, uid: str) -> str | None:
    path = run / "inbox" / f"{uid}.result"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def test_answer_writes_inbox_and_prints_result(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = _run_dir(repo)
    _state(run)

    def owner():
        inbox = run / "inbox"
        for _ in range(300):
            if inbox.is_dir():
                for found in sorted(inbox.glob("*.json")):
                    res = found.with_suffix(".result")
                    if not res.exists():
                        res.write_text("принято", encoding="utf-8")
                        return
            time.sleep(0.1)

    thread = threading.Thread(target=owner, daemon=True)
    thread.start()
    proc = subprocess.run(
        [
            sys.executable, "-m", "src.cli_entry",
            "answer", "1-1", "skip", "--why", "потому", "--run", str(run),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    thread.join(timeout=10)
    assert proc.returncode == 0
    assert "принято" in proc.stdout
    files = list((run / "inbox").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["question_id"] == "1-1"
    assert payload["action"] == "skip"
    assert payload["who"] == "agent"
    assert payload["why"] == "потому"
    assert payload["uuid"]
    assert payload["ts"]


def test_two_answers_one_wins(tmp_path):
    from src.loop.inbox import Inbox

    repo = tmp_path / "repo"
    repo.mkdir()
    run = _run_dir(repo)
    _state(run)
    _answer_file(run, "a.json", "u-1", "1-1", "skip", why="w1")
    _answer_file(run, "b.json", "u-2", "1-1", "answer", text="idea", why="w2")
    won = Inbox(run).take("1-1", NoopRelay())
    assert won is not None
    assert won.uuid == "u-1"
    assert won.action == "skip"
    assert _result(run, "u-1") == "принято"
    assert _result(run, "u-2") == "отклонено: уже отвечено: agent, skip"
    state = RunState.read(run / "state.json")
    assert state is not None
    assert state["applied_uuids"] == ["u-1"]
    assert len(state["decisions"]) == 1

    # Человек из ретранслятора выигрывает, хотя локальный файл старше.
    run2 = _run_dir(repo, "20200101-000001-demo")
    _state(run2)
    _answer_file(run2, "old.json", "u-9", "1-1", "skip", why="w")
    relay = FakeRelay(
        [Answer(question_id="1-1", action="answer", text="делай так",
                who="human", why="я", uuid="r-1")]
    )
    won2 = Inbox(run2).take("1-1", relay)
    assert won2 is not None
    assert won2.who == "human"
    assert _result(run2, "u-9") == "отклонено: уже отвечено: human, answer"
    state2 = RunState.read(run2 / "state.json")
    assert state2 is not None
    assert state2["applied_uuids"] == ["r-1"]


def test_answer_rejects(tmp_path):
    from src.loop.inbox import Inbox

    repo = tmp_path / "repo"
    repo.mkdir()

    def fresh(name: str):
        run = _run_dir(repo, name)
        _state(run)
        return run, (run / "state.json").read_bytes()

    run, snap = fresh("20200101-000000-a")
    _answer_file(run, "x.json", "u-x", "9-9", "skip")
    assert Inbox(run).take("9-9", NoopRelay()) is None
    assert _result(run, "u-x") == "отклонено: вопрос 9-9 не открыт"
    assert (run / "state.json").read_bytes() == snap

    run, snap = fresh("20200101-000000-b")
    _answer_file(run, "x.json", "u-x", "1-1", "dance")
    assert Inbox(run).take("1-1", NoopRelay()) is None
    assert _result(run, "u-x") == "отклонено: действие недопустимо"
    assert (run / "state.json").read_bytes() == snap

    run, snap = fresh("20200101-000000-c")
    _answer_file(run, "x.json", "u-x", "1-1", "model", text="judge foo")
    assert Inbox(run).take("1-1", NoopRelay()) is None
    assert _result(run, "u-x") == "отклонено: судью менять нельзя"
    assert (run / "state.json").read_bytes() == snap

    run, snap = fresh("20200101-000000-d")
    _answer_file(run, "x.json", "u-x", "1-1", "allow", text="sec/keys.env", who="agent")
    assert Inbox(run).take("1-1", NoopRelay()) is None
    assert _result(run, "u-x") == "отклонено: allow только человек"
    assert (run / "state.json").read_bytes() == snap

    # allow от человека принимается.
    run, _ = fresh("20200101-000000-e")
    _answer_file(run, "x.json", "u-x", "1-1", "allow", text="sec/keys.env", who="human")
    won = Inbox(run).take("1-1", NoopRelay())
    assert won is not None
    assert _result(run, "u-x") == "принято"


def test_applied_uuid_not_reapplied(tmp_path):
    from src.loop.inbox import Inbox

    repo = tmp_path / "repo"
    repo.mkdir()
    run = _run_dir(repo)
    _state(run)
    _answer_file(run, "a.json", "u-1", "1-1", "skip")
    box = Inbox(run)
    assert box.take("1-1", NoopRelay()) is not None
    state = RunState.read(run / "state.json")
    assert state is not None
    assert len(state["decisions"]) == 1
    (run / "inbox" / "u-1.result").unlink()
    assert box.take("1-1", NoopRelay()) is None
    again = RunState.read(run / "state.json")
    assert again is not None
    assert len(again["decisions"]) == 1
    assert again["applied_uuids"] == ["u-1"]


def test_answer_text_capped(tmp_path):
    from src.loop.inbox import Inbox

    repo = tmp_path / "repo"
    repo.mkdir()
    run = _run_dir(repo)
    _state(run)
    _answer_file(run, "a.json", "u-1", "1-1", "answer", text="x" * 5000)
    won = Inbox(run).take("1-1", NoopRelay())
    assert won is not None
    state = RunState.read(run / "state.json")
    assert state is not None
    assert len(state["decisions"][0]["text"]) == 2000
