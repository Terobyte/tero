"""One HUD window per machine (phase 9).

The window holds `~/.tero/hud.lock` (`fcntl.flock`, non-blocking) for its
whole life. A second `tero hud --run <dir>` finds the lock taken, writes the
run dir into `~/.tero/hud-target` atomically and exits; the window picks the
target up on its next poll. The lock dies with the process, so a closed or
crashed window frees it for the next `tero hud`.
"""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path

from src.fsio import atomic_write_text


def lock_path() -> Path:
    return Path.home() / ".tero" / "hud.lock"


def target_path() -> Path:
    return Path.home() / ".tero" / "hud-target"


def acquire_lock() -> int | None:
    """Open and flock the lock file; fd on success, None when another window holds it.

    Any other lock error raises.
    """
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return None
        raise  # not "another window": run() logs it to hud.log
    return fd


def write_target(run_dir: str | Path) -> None:
    path = target_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, str(Path(run_dir)))


def take_target() -> Path | None:
    """Read and consume `hud-target`, so a later `+N` switch is not undone. Never raises."""
    path = target_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
        path.unlink(missing_ok=True)
    except Exception:
        return None
    return Path(text) if text else None
