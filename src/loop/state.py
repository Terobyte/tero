"""Run snapshot for the HUD: atomic state.json that never breaks a run."""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from src.fsio import atomic_write_json, read_json_dict


class Status(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    WAITING_AGENT = "waiting_agent"
    WAITING_LIMIT = "waiting_limit"
    JUDGE_PENDING = "judge_pending"
    DONE = "done"
    STOPPED = "stopped"


_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


def owner_lock(path: str | Path) -> threading.RLock:
    """Один lock на прогон: общий для записей state и git-операций."""
    key = str(Path(path).expanduser().absolute())
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_LOCKS[key] = lock
        return lock


class RunState:
    """Atomic read/write of a run dir's state.json.

    Only the run owner writes. A failed write warns once on stderr and
    never raises, so the run continues without HUD files.
    """

    def __init__(self, path: str | Path, lock: threading.RLock | None = None) -> None:
        self.path = Path(path)
        self._lock = lock if lock is not None else owner_lock(self.path)
        self._warned = False

    def update(self, **fields: Any) -> None:
        from src.errors import scrub_data

        fields = scrub_data(fields)
        try:
            with self._lock:
                current = RunState.read(self.path) or {}
                current.update(fields)
                current["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self.path, current, ensure_ascii=False, mode=0o600)
        except Exception as exc:
            with self._lock:
                if self._warned:
                    return
                self._warned = True
            try:
                print(f"HUD: не могу записать {self.path}: {exc}", file=sys.stderr)
            except Exception:
                pass

    @staticmethod
    def read(path: str | Path) -> dict[str, Any] | None:
        return read_json_dict(path)
