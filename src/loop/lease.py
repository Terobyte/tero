"""The agent lease: `<run_dir>/lease`, written by `tero wait`, extended by the owner.

`{until, pid, pid_started, host, session, written_at, sig}`. The run dir
lives inside the worktree the player writes to, so the file is signed with
`~/.tero/human.key` (`human_key.sign_file`): a lease without `pid_started`
or without a valid signature is dead. Writers merge under `flock` on
`lease.lock` instead of overwriting the whole file. Two facts come out of
it: `alive` (the host process exists) and `on_duty` (alive and not expired).
Nothing here raises.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from src.fsio import append_text, atomic_write_json, read_json_dict
from src.hud.registry import pid_is_alive
from src.loop.human_key import file_signed, sign_file

KIND = "lease"
FIELDS = ("until", "pid", "pid_started", "host", "session", "written_at")
AliveFn = Callable[[Any, Any], bool]


def lease_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "lease"


def _run_id(run_dir: str | Path) -> str:
    return Path(run_dir).name


_HELD = threading.local()  # per thread: lock path → depth (flock is per open file)


def _depth() -> dict:
    held = getattr(_HELD, "depth", None)
    if held is None:
        held = _HELD.depth = {}
    return held


class _Locked:
    """`flock` on `<run_dir>/lease.lock`, re-entrant in one thread; a missing
    fcntl (Windows) degrades to no lock."""

    def __init__(self, run_dir: str | Path) -> None:
        self.path = Path(run_dir) / "lease.lock"
        self._fh = None

    def __enter__(self) -> "_Locked":
        key = str(self.path)
        if _depth().get(key, 0) > 0:
            _depth()[key] += 1
            return self
        try:
            import fcntl

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._fh = None
        _depth()[key] = 1
        return self

    def __exit__(self, *exc: Any) -> None:
        key = str(self.path)
        left = _depth().get(key, 1) - 1
        if left > 0:
            _depth()[key] = left
            return
        _depth().pop(key, None)
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:
            pass


_LOGGED: set[tuple[str, str]] = set()


def _log(run_dir: str | Path, text: str) -> None:
    """One line per (run, reason) per process: the HUD reads the lease every second."""
    key = (str(run_dir), text)
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    try:
        append_text(Path(run_dir).parent.parent / "hud.log", f"{time.time():.0f} lease: {text}\n")
    except Exception:
        pass


def read_raw(run_dir: str | Path) -> dict | None:
    return read_json_dict(lease_path(run_dir))


def read(run_dir: str | Path) -> dict | None:
    """The lease when it is signed and carries pid_started; None otherwise."""
    data = read_raw(run_dir)
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("pid_started"), (int, float)) or isinstance(data.get("pid_started"), bool):
        _log(run_dir, "без pid_started, игнорирую")
        return None
    if not file_signed(KIND, _run_id(run_dir), data):
        _log(run_dir, "подпись не сошлась, игнорирую")
        return None
    return data


def alive(lease: dict | None, is_alive: AliveFn = pid_is_alive) -> bool:
    if not isinstance(lease, dict):
        return False
    try:
        return bool(is_alive(lease.get("pid"), lease.get("pid_started")))
    except Exception:
        return False


def expired(lease: dict | None, now: float | None = None) -> bool:
    try:
        return float((lease or {}).get("until") or 0) <= (time.time() if now is None else now)
    except (TypeError, ValueError):
        return True


def on_duty(run_dir: str | Path, now: float | None = None,
            is_alive: AliveFn = pid_is_alive) -> bool:
    """Someone is obliged to answer: a signed lease, not expired, host alive."""
    lease = read(run_dir)
    return lease is not None and not expired(lease, now) and alive(lease, is_alive)


def write(run_dir: str | Path, *, until: float, pid: int, pid_started: float | None,
          host: str = "", session: str = "", now: float | None = None,
          if_may_take: bool = False, is_alive: AliveFn = pid_is_alive) -> bool:
    """Write the lease under flock. Only the signed fields are kept: an unsigned
    key planted next to them is not laundered into a signed file. With
    `if_may_take` the `may_take` rule is checked inside the same lock, so two
    hosts cannot both pass the check and overwrite each other. False on failure
    or refusal."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    ts = time.time() if now is None else now
    try:
        with _Locked(run_dir):
            if if_may_take and not may_take(run_dir, pid, ts, is_alive):
                return False
            data = {"until": float(until), "pid": pid, "pid_started": pid_started,
                    "host": str(host or ""), "session": str(session or ""), "written_at": ts}
            sign_file(KIND, _run_id(run_dir), data)
            Path(run_dir).mkdir(parents=True, exist_ok=True)
            atomic_write_json(lease_path(run_dir), data, mode=0o600)
        return True
    except Exception:
        return False


def extend(run_dir: str | Path, until: float, now: float | None = None,
           is_alive: AliveFn = pid_is_alive) -> bool:
    """Push `until` of a live signed lease; a dead or missing one is left alone."""
    try:
        with _Locked(run_dir):
            lease = read(run_dir)
            if lease is None or not alive(lease, is_alive):
                return False
            lease["until"] = float(until)
            lease["written_at"] = time.time() if now is None else now
            sign_file(KIND, _run_id(run_dir), lease)
            atomic_write_json(lease_path(run_dir), lease, mode=0o600)
        return True
    except Exception:
        return False


def may_take(run_dir: str | Path, pid: int, now: float | None = None,
             is_alive: AliveFn = pid_is_alive) -> bool:
    """A host may (re)write the lease when it is theirs, expired, dead or absent."""
    lease = read(run_dir)
    if lease is None:
        return True
    try:
        if int(lease.get("pid") or 0) == int(pid):
            return True
    except (TypeError, ValueError):
        return True
    return expired(lease, now) or not alive(lease, is_alive)


def agent_view(run_dir: str | Path, now: float | None = None,
               is_alive: AliveFn = pid_is_alive) -> dict:
    """`{host, alive, on_duty, pid_started_at, lease_until, lease_written_at}` for every screen."""
    ts = time.time() if now is None else now
    lease = read(run_dir)
    if lease is None:
        return {"host": "", "alive": False, "on_duty": False, "pid_started_at": None,
                "lease_until": None, "lease_written_at": None}
    live = alive(lease, is_alive)
    return {
        "host": str(lease.get("host") or ""),
        "alive": live,
        "on_duty": live and not expired(lease, ts),
        "pid_started_at": lease.get("pid_started"),
        "lease_until": lease.get("until"),
        "lease_written_at": lease.get("written_at"),
    }


def legacy_view(run_dir: str | Path) -> dict | None:
    """The old `lease` key of `tero status --json`: `{until, pid, host}` or None."""
    lease = read(run_dir)
    if lease is None:
        return None
    return {"until": lease.get("until"), "pid": lease.get("pid"), "host": str(lease.get("host") or "")}
