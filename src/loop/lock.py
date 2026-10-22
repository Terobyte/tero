"""Single-owner run lock: non-blocking flock on <repo>/.tero/run.lock."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class Lock:
    """Held exclusive flock on run.lock. Keeps the fd open until released."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd = fd
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass

    def __enter__(self) -> "Lock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire(repo: str | Path) -> Lock | None:
    """Take the run lock for repo, or return None if another owner holds it."""
    lock_path = Path(repo) / ".tero" / "run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None
    return Lock(lock_path, fd)
