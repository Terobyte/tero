"""Host-wide gate lock: one test suite at a time across tero runs."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


def _gate_lock_path() -> Path | None:
    raw = os.environ.get("TERO_GATE_LOCK", "")
    if raw == "off":
        return None
    return Path(raw) if raw else Path.home() / ".tero" / "gate.lock"


class host_gate_lock:
    """One test suite at a time on this machine, across tero runs.

    Two runs gating together starve each other's timing-sensitive tests
    (false reds); the wait sits outside the suite's timeout_s.
    """

    def __enter__(self):
        self._fd = None
        path = _gate_lock_path()
        if path is None:
            return self
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return self
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("  тесты: жду, гейт другого прогона ещё идёт", flush=True)
            fcntl.flock(fd, fcntl.LOCK_EX)
        self._fd = fd
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            os.close(self._fd)
        return False
