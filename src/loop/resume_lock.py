"""Run-lock acquisition for crash resume: hung-owner kill, then wait."""

from __future__ import annotations

import time
from pathlib import Path


_CRASH_STATUSES = frozenset({
    "starting", "running",
    "waiting_human", "waiting_agent", "waiting_limit",
    "judge_pending",
})


def _refuse_outcome(run_path: Path, reason: str):
    from src.loop.session import RunOutcome as _Outcome

    print(f"стоп: {reason} (код 1)", flush=True)
    return _Outcome(status="error", exit_code=1, reason=reason,
                    run_dir=str(run_path))


def _git_running() -> bool:
    """True when any git process is alive; unknown counts as alive."""
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name") or ""
            if name == "git" or name.startswith("git-"):
                return True
        return False
    except Exception:
        return True


def check_index_lock(repo_path: Path) -> None:
    """Drop a lock no git process can own (left by a crashed run); refuse a live one."""
    from src.loop.git import GitError, _git

    path = Path(_git(repo_path, "rev-parse", "--git-path", "index.lock").strip())
    if not path.is_absolute():
        path = Path(repo_path) / path
    if not (path.exists() or path.is_symlink()):
        return
    if not _git_running():
        path.unlink(missing_ok=True)
        return
    raise GitError(f"Git index locked: {path}; завершите операцию Git; "
                   "удаляйте lock вручную только после проверки, что он устарел")


def acquire_for_crash(repo_path: Path, run_path: Path, state: dict,
                      timeout_s: float = 10.0):
    """Lock with hung-owner handling. Returns (lock|None, outcome|None)."""
    from src.loop.lock import acquire

    status = (state or {}).get("status") or ""
    if status not in _CRASH_STATUSES:
        lock = acquire(repo_path)
        if lock is None:
            return None, _refuse_outcome(run_path, "another run holds this repo")
        return lock, None
    lock = acquire(repo_path)
    if lock is not None:
        return lock, None
    from src.hud.registry import pid_is_alive

    pid, started = (state or {}).get("pid"), (state or {}).get("pid_started")
    if not pid_is_alive(pid, started):
        lock = acquire(repo_path)
        if lock is not None:
            return lock, None
        return None, _refuse_outcome(run_path, "another run holds this repo")
    from src.fsio import read_json_dict
    from src.loop.wait import is_hung

    try:
        pulse = read_json_dict(run_path / "pulse")
    except Exception:
        pulse = None
    if not is_hung(state, pulse if isinstance(pulse, dict) else None, time.time()):
        return None, _refuse_outcome(run_path, "прогон жив")
    from src.loop.resume_kill import kill_hung_owner

    try:
        kill_hung_owner(pid)
    except Exception:
        pass
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        lock = acquire(repo_path)
        if lock is not None:
            return lock, None
        time.sleep(0.05)
    return None, _refuse_outcome(run_path, "another run holds this repo")


def acquire_for_resume(repo_path: Path, run_path: Path, state: dict):
    """Acquire tero's lock and refuse Git's lock without leaking our own."""
    lock, denied = acquire_for_crash(repo_path, run_path, state)
    if denied is not None:
        return lock, denied
    try:
        check_index_lock(repo_path)
    except Exception as exc:
        lock.release()
        return None, _refuse_outcome(run_path, str(exc))
    return lock, None
