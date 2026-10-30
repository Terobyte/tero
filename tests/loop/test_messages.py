"""`tero wait --messages` (код 7) и `tero inbox` (план 09, фаза 1)."""

from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path

from src.commands.inbox_cmds import run_inbox
from src.hud.registry import current_pid_started
from src.loop import host_id
from src.loop.human_key import sign_message
from src.loop.wait import wait_run


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


def _setup(tmp_path: Path, monkeypatch, **state) -> tuple[Path, Path]:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    run_dir = repo / ".tero" / "runs" / "20200101-000000-demo"
    run_dir.mkdir(parents=True, exist_ok=True)
    for old in (run_dir / "inbox").glob("*") if (run_dir / "inbox").is_dir() else []:
        old.unlink()
    base = {"status": "running", "pid": os.getpid(), "pid_started": current_pid_started()}
    base.update(state)
    (run_dir / "state.json").write_text(json.dumps(base), encoding="utf-8")
    return repo, run_dir


def _msg(run_dir: Path, uid: str, text: str, signed: bool = True) -> Path:
    (run_dir / "inbox").mkdir(exist_ok=True)
    payload = {"uuid": uid, "kind": "message", "text": text, "ts": 1.0}
    if signed:
        sign_message(run_dir.name, payload)
    path = run_dir / "inbox" / f"msg-{uid}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _wait(repo, run_dir, out, timeout_s=0, host=None):
    c = _Clock()
    return wait_run(run_dir, repo, timeout_s=timeout_s, clock=c.clock, sleep=c.sleep,
                    host=host or host_id.Host(""), out=out.append, messages=True)


def test_wait_messages_code7_prints_and_renames(tmp_path, monkeypatch):
    repo, run_dir = _setup(tmp_path, monkeypatch)
    _msg(run_dir, "m1", "привет хозяин")
    out: list[str] = []
    assert _wait(repo, run_dir, out) == 7
    assert any("привет хозяин" in line for line in out)
    assert (run_dir / "inbox" / "msg-m1.read.json").is_file()
    assert not (run_dir / "inbox" / "msg-m1.json").exists()
    assert _wait(repo, run_dir, []) == 6  # второй wait 7 не даёт


def test_two_parallel_waits_print_once(tmp_path, monkeypatch):
    repo, run_dir = _setup(tmp_path, monkeypatch)
    _msg(run_dir, "m1", "ровно один раз")
    outs: list[list[str]] = [[], []]
    codes: list[int] = [0, 0]

    def go(i: int) -> None:
        codes[i] = _wait(repo, run_dir, outs[i])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    printed = sum(1 for o in outs for line in o if "ровно один раз" in line)
    assert printed == 1


def test_unsigned_msg_ignored(tmp_path, monkeypatch, capsys):
    repo, run_dir = _setup(tmp_path, monkeypatch)
    _msg(run_dir, "x", "подложено", signed=False)
    out: list[str] = []
    assert _wait(repo, run_dir, out) == 6
    assert not any("подложено" in line for line in out)
    args = argparse.Namespace(working_dir=str(repo), run=str(run_dir), wait=False, all=False)
    assert run_inbox(args) == 0
    assert "подложено" not in capsys.readouterr().out
    logged = (run_dir / "hud.log").read_text(encoding="utf-8")
    assert "нет верной подписи" in logged
    assert "подложено" not in logged


def test_wait_code_priority(tmp_path, monkeypatch):
    # 8 раньше 7
    repo, run_dir = _setup(tmp_path, monkeypatch)
    _msg(run_dir, "m1", "t")
    gone = host_id.Host("claude", pid=999999999, started=0.0)
    assert _wait(repo, run_dir, [], host=gone) == 8
    # 3 раньше 7
    repo, run_dir = _setup(tmp_path, monkeypatch, status="waiting_agent")
    _msg(run_dir, "m1", "t")
    assert _wait(repo, run_dir, []) == 3
    # 7 раньше 5 (владелец мёртв) и 6 (таймаут)
    repo, run_dir = _setup(tmp_path, monkeypatch, pid=999999999, pid_started=0.0)
    _msg(run_dir, "m1", "t")
    assert _wait(repo, run_dir, []) == 7


def test_tero_inbox_all_keeps_unread(tmp_path, monkeypatch, capsys):
    repo, run_dir = _setup(tmp_path, monkeypatch)
    _msg(run_dir, "m1", "непрочитанное")
    args = argparse.Namespace(working_dir=str(repo), run=str(run_dir), wait=False, all=True)
    assert run_inbox(args) == 0
    assert "непрочитанное" in capsys.readouterr().out
    assert (run_dir / "inbox" / "msg-m1.json").is_file()  # --all не помечает
    args.all = False
    assert run_inbox(args) == 0
    assert "непрочитанное" in capsys.readouterr().out
    assert (run_dir / "inbox" / "msg-m1.read.json").is_file()
