"""Detached run: a parent launches the run owner and returns immediately.

The owner is a plain `go --owner` process: it writes `state.json` itself,
the parent only reads it and the `detach.log` tail.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from src.errors import scrub_secrets
from src.fsio import read_json_dict

DETACH_READY_S = 120

AGENT_PROTOCOL = (
    "1. `tero go --detach`, потом `tero wait --messages` фоновой командой — спать.\n"
    "2. `wait` вышел: `0` → отчитаться; `4` → отчитаться и перечислить долг; `3` → `tero status --json`; если открытого вопроса уже нет (ответил человек) — снова `tero wait --messages`; иначе решить, `tero answer <id> … --why …`, снова `tero wait --messages`; `5` → `tero resume --detach`, снова `tero wait --messages`; `2` → отчитаться; `6` → снова `tero wait --messages`; `7` → текст сообщения уже в выводе; на вопрос отвечай только если статус `waiting_agent`, снова `tero wait --messages`; `8` → ты сирота по мнению tero: если ты жив и это читаешь — `tero status`, затем снова `tero wait --messages`; `9` от `tero resume` → владелец занят, подождать 10 с и повторить.\n"
    "3. Если сессия закрылась, прогон живёт; новый агент подхватывает его: `tero status` + `tero wait --messages`."
)

_READY_STATUSES = ("running", "waiting_human", "waiting_agent", "waiting_limit")

# (args attribute, CLI flag, takes a value)
_OWNER_FLAGS: tuple[tuple[str, str, bool], ...] = (
    ("plan", "--plan", True),
    ("working_dir", "--working-dir", True),
    ("no_hud", "--no-hud", False),
    ("verbose", "--verbose", False),
    ("player_spec", "--player", True),
    ("judge_spec", "--judge", True),
    ("tester_spec", "--tester", True),
    ("escalation_spec", "--escalation", True),
    ("test_command", "--test-command", True),
    ("test_command_scoped", "--test-command-scoped", True),
    ("max_rounds", "--max-rounds", True),
    ("escalate_at", "--escalate-at", True),
    ("player_schedule", "--player-schedule", True),
    ("preset", "--preset", True),
    ("no_escalation", "--no-escalation", False),
    ("no_tester", "--no-tester", False),
)


def resume_owner_argv(args: Any) -> list[str]:
    """Owner argv of `resume --detach`: the same flags minus `--detach`."""
    argv = [sys.executable, "-m", "src.cli_entry", "resume"]
    argv += ["--working-dir", str(getattr(args, "working_dir", "."))]
    if getattr(args, "run", None):
        argv += ["--run", str(args.run)]
    if getattr(args, "force", False):
        argv += ["--force"]
    if getattr(args, "answer_from_inbox", False):
        argv += ["--answer-from-inbox"]
    elif getattr(args, "answer", ""):
        argv += ["--answer", str(args.answer)]
    return argv


def owner_argv(args: Any) -> list[str]:
    """Owner argv: the same `go` flags minus `--detach`, plus `--owner`."""
    argv = [sys.executable, "-m", "src.cli_entry", "go"]
    for attr, flag, takes_value in _OWNER_FLAGS:
        val = getattr(args, attr, None)
        if val is None or val is False:
            continue
        if takes_value:
            argv += [flag, str(val)]
        else:
            argv += [flag]
    if getattr(args, "decisions", False) or getattr(args, "detach", False):
        argv += ["--decisions"]
    argv += ["--owner"]
    return argv


def _maybe_caffeinate(
    owner_pid: int,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
) -> None:
    """Keep a macOS machine awake while the owner lives (separate process)."""
    if sys.platform != "darwin":
        return
    if shutil.which("caffeinate") is None:
        return
    try:
        popen(
            ["caffeinate", "-i", "-w", str(owner_pid)],
            start_new_session=True,
        )
    except Exception:
        pass


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > limit:
        text = text[-limit:]
    return text


def _find_ready_state(repo: Path, owner_pid: Any, *, any_status: bool = False) -> Path | None:
    runs = repo / ".tero" / "runs"
    if not runs.is_dir():
        return None
    try:
        entries = sorted(runs.iterdir())
    except OSError:
        return None
    for run_dir in entries:
        if not run_dir.is_dir():
            continue
        data = read_json_dict(run_dir / "state.json")
        if not data:
            continue
        if data.get("pid") != owner_pid:
            continue
        if any_status or data.get("status") in _READY_STATUSES:
            return run_dir / "state.json"
    return None


def detach_go(
    args: Any,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 0.2,
    ready_s: float = DETACH_READY_S,
    command: str = "go",
) -> int:
    """Parent side of `tero go|resume --detach`. Returns the process exit code."""
    import os

    repo = Path(getattr(args, "working_dir", ".")).expanduser().resolve()
    tero_dir = repo / ".tero"
    try:
        tero_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"не могу писать {tero_dir}: {exc}")
        return 1
    log_path = tero_dir / "detach.log"

    resuming = command == "resume"
    argv = resume_owner_argv(args) if resuming else owner_argv(args)
    try:
        log_fh = open(log_path, "ab")
    except OSError as exc:
        print(f"не могу писать {log_path}: {exc}")
        return 1
    try:
        try:
            with open(os.devnull, "rb") as devnull:
                owner = popen(
                    argv,
                    start_new_session=True,
                    stdin=devnull,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                )
        except Exception as exc:
            print(f"владелец не запустился: {scrub_secrets(str(exc))}")
            return 1
    finally:
        try:
            log_fh.close()
        except OSError:
            pass

    owner_pid = getattr(owner, "pid", None)
    deadline = clock() + ready_s
    while True:
        state_path = _find_ready_state(repo, owner_pid, any_status=resuming)
        if state_path is not None:
            break
        try:
            code = owner.poll()
        except Exception:
            code = None
        if code is not None:
            if resuming and code == 9:  # владелец занят: код 9 уходит агенту как есть
                print(scrub_secrets(_tail(log_path)) or "владелец занят (код 9)")
                return 9
            tail = scrub_secrets(_tail(log_path))
            if tail:
                print(tail)
            else:
                print(f"владелец вышел с кодом {code}")
            return 1
        if clock() >= deadline:
            print(f"владелец продолжает запуск (PID {owner_pid}); готовность пока не подтверждена")
            print(f"лог: {log_path}")
            print("проверьте `tero status` и `tero wait --messages` перед повторным запуском")
            try:
                _maybe_caffeinate(int(owner_pid), popen=popen)
            except (TypeError, ValueError):
                pass
            return 0
        sleep(poll_s)

    try:
        owner_pid_int = int(owner_pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        owner_pid_int = 0
    if owner_pid_int:
        _maybe_caffeinate(owner_pid_int, popen=popen)
    run_id = state_path.parent.name
    print(f"прогон: {run_id}")
    print(f"state: {state_path}")
    print(AGENT_PROTOCOL)
    return 0
