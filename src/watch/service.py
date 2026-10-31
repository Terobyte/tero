"""The watcher loop: poll = pulse, marks, `resume <run_id>` commands from the phone."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from src.hud.registry import pid_is_alive
from src.loop.human_key import sign_answer
from src.fsio import atomic_write_json, read_json_dict
from src.watch import marks as marks_mod

POLL_S = 30
FULL_MARKS_S = 300
MAX_TEXT = 2000
BUSY_CODE = 9
RETRIES = 3
RETRY_PAUSE_S = 10
RESUME_TIMEOUT_S = 120


def watch_lock_path() -> Path:
    return Path.home() / ".tero" / "watch.lock"


def acquire_watch_lock(path: Path | None = None):
    """Open file holding the flock, or None when another watcher has it."""
    import fcntl

    path = path or watch_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def resume_argv(repo: Path, run_dir: Path, answer_from_inbox: bool) -> list[str]:
    """Argv list with full paths; phone text never goes in here."""
    argv = [sys.executable, "-m", "src.cli_entry", "resume", "--detach", "-w", str(repo), "--run", str(run_dir)]
    if answer_from_inbox:
        argv.append("--answer-from-inbox")
    return argv


def default_runner(argv: list[str], **kw: Any) -> tuple[int, str]:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=RESUME_TIMEOUT_S, **kw)
    return done.returncode, (done.stdout or "")[-500:] + (done.stderr or "")[-500:]


class Watcher:
    def __init__(self, post: Callable[[str, dict], Any], *, machine_id: str,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 runner: Callable[..., tuple[int, str]] = default_runner,
                 alive: Callable[[Any, Any], bool] = pid_is_alive) -> None:
        self._post, self._machine = post, machine_id
        self._clock, self._sleep, self._runner, self._alive = clock, sleep, runner, alive
        self._last: dict[str, bool] = {}
        self._full_at = float("-inf")
        self._threads: list[threading.Thread] = []

    def cycle(self) -> None:
        now = self._clock()
        marks = marks_mod.collect(now, self._alive)
        full = now - self._full_at >= FULL_MARKS_S
        send = [m for m in marks if full or self._last.get(m["run_id"]) != m["owner_alive"]]
        got: Any = None
        pending: list = []
        for i in range(0, max(len(send), 1), 200):
            chunk = send[i:i + 200] if send else []
            if not send:
                got = self._post("/commands/poll", {"machine_id": self._machine, "marks": []})
            else:
                got = self._post("/commands/poll", {"machine_id": self._machine, "marks": chunk})
            if got is None:
                return
            if isinstance(got, dict):
                pending.extend(got.get("pending") or [])
        if full:
            self._full_at = now
        self._last = {m["run_id"]: m["owner_alive"] for m in marks}
        for cmd in pending:
            self._take(cmd)

    def _take(self, cmd: dict) -> None:
        taken = self._post("/commands/take", {"id": cmd.get("id"), "machine_id": self._machine})
        if not isinstance(taken, dict):
            return
        thread = threading.Thread(target=self._execute, args=(taken,), daemon=True, name="tero-watch-exec")
        self._threads.append(thread)
        thread.start()

    def join(self) -> None:
        for thread in self._threads:
            thread.join(timeout=5)

    def _execute(self, cmd: dict) -> None:
        try:
            code, text = self._resume(str(cmd.get("run_id") or ""), str(cmd.get("text") or "")[:MAX_TEXT])
        except BaseException as exc:  # SystemExit runner-а — тоже ошибка команды, не смерть вотчера
            from src.errors import scrub_secrets

            try:
                text = scrub_secrets(f"{type(exc).__name__}: {exc}")[:500] or "watcher error"
            except Exception:
                text = "watcher error"
            self._post("/commands/result", {"id": cmd.get("id"), "machine_id": self._machine,
                                            "code": 1, "text": text})
            return
        self._post("/commands/result", {"id": cmd.get("id"), "machine_id": self._machine,
                                        "code": code, "text": text[:500]})

    def _resume(self, run_id: str, phone_text: str) -> tuple[int, str]:
        entry = marks_mod._index().get(run_id)
        run_dir = marks_mod.run_dir_of(entry, run_id)
        state = read_json_dict(run_dir / "state.json") if run_dir else None
        if run_dir is None or state is None:
            return 2, "прогон не найден на этой машине"
        needs = marks_mod.resume_mark(state)["mode"] == "needs_text"
        if needs and phone_text.strip():
            payload = sign_answer(run_id, {"who": "human", "text": phone_text})
            (run_dir / "inbox").mkdir(parents=True, exist_ok=True)
            atomic_write_json(run_dir / "inbox" / "resume-answer.json", payload)
        argv = resume_argv(Path(str(entry["repo"])), run_dir, needs and bool(phone_text.strip()))
        code, out = 0, ""
        for attempt in range(RETRIES + 1):
            code, out = self._runner(argv)
            if code != BUSY_CODE or attempt == RETRIES:
                break
            self._sleep(RETRY_PAUSE_S)
        return code, out
