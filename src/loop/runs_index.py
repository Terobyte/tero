"""`~/.tero/runs.json`: run_id → {repo, started_at}, written by the owner at start."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from src.fsio import atomic_write_text

KEEP_S = 30 * 86400

# Test-only index path: tests run with an isolated HOME via conftest, but a
# scrubbed env (no HOME) would fall back to the real home — this override
# keeps such runs away from ~/.tero. Never set in production.
TEST_INDEX_ENV = "TERO_TEST_RUNS_INDEX"


def index_path() -> Path:
    raw = os.environ.get(TEST_INDEX_ENV)
    if raw:
        return Path(raw)
    return Path.home() / ".tero" / "runs.json"


def _fresh(entry: Any, stamp: float) -> bool:
    """Under-30-days entry; a corrupt started_at drops just that entry."""
    if not isinstance(entry, dict):
        return False
    try:
        started = float(entry.get("started_at") or 0)
    except (TypeError, ValueError):
        return False
    return stamp - started <= KEEP_S


def register_run(run_id: str, repo: str, *, now: float | None = None) -> None:
    """Add the run under flock and drop entries older than 30 days. Never raises."""
    stamp = time.time() if now is None else now
    path = index_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_name("runs.lock"), "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data = {k: v for k, v in data.items() if _fresh(v, stamp)}
            data[run_id] = {"repo": str(repo), "started_at": stamp}
            atomic_write_text(path, json.dumps(data, ensure_ascii=False))
    except Exception:
        pass
