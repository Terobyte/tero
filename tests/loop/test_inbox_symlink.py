"""inbox/ не выводит записи за пределы папки прогона через симлинки."""

import json
import subprocess
import sys
import time
from pathlib import Path

from src.loop.inbox import Inbox
from src.loop.state import RunState
from src.relay.iface import NoopRelay

ROOT = Path(__file__).resolve().parent.parent.parent

QUESTION = {"id": "1-1", "text": "нужен человек", "actions": ["skip", "stop"]}


def _run_dir(tmp_path: Path, status: str = "waiting_human") -> Path:
    run = tmp_path / "repo" / ".tero" / "runs" / "20200101-000000-demo"
    run.mkdir(parents=True)
    RunState(run / "state.json").update(status=status, question=dict(QUESTION))
    return run


def _payload(uid: str) -> str:
    return json.dumps(
        {"uuid": uid, "question_id": "1-1", "action": "skip", "who": "agent", "ts": time.time()}
    )


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli_entry", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "TERO_ANSWER_WAIT_S": "0.5"},
    )


def test_answer_refuses_symlinked_inbox(tmp_path):
    run = _run_dir(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (run / "inbox").symlink_to(outside, target_is_directory=True)

    proc = _cli("answer", "1-1", "skip", "--why", "x", "--run", str(run))

    assert proc.returncode == 1
    assert "не могу записать inbox" in proc.stdout
    assert list(outside.iterdir()) == []


def test_compact_refuses_symlinked_inbox(tmp_path):
    run = _run_dir(tmp_path, status="running")
    outside = tmp_path / "outside"
    outside.mkdir()
    (run / "inbox").symlink_to(outside, target_is_directory=True)

    proc = _cli("compact", "--run", str(run))

    assert proc.returncode == 1
    assert "не могу записать compact.json" in proc.stdout
    assert list(outside.iterdir()) == []


def test_owner_does_not_write_result_through_symlink(tmp_path):
    run = _run_dir(tmp_path)
    inbox = run / "inbox"
    inbox.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("не трогать", encoding="utf-8")
    (inbox / "u1.json").write_text(_payload("u1"), encoding="utf-8")
    (inbox / "u1.result").symlink_to(victim)

    Inbox(run).take("1-1", NoopRelay())

    assert victim.read_text(encoding="utf-8") == "не трогать"


def test_owner_ignores_symlinked_inbox_dir(tmp_path):
    run = _run_dir(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "u1.json").write_text(_payload("u1"), encoding="utf-8")
    (run / "inbox").symlink_to(outside, target_is_directory=True)

    answer = Inbox(run).take("1-1", NoopRelay())

    assert answer is None
    assert sorted(p.name for p in outside.iterdir()) == ["u1.json"]
    assert (RunState.read(run / "state.json") or {}).get("applied_uuids") is None
