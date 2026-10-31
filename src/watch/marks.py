"""Marks for the relay: is the owner / host of each indexed run alive.

Reads `state.json` and the signed lease only. Never takes `run.lock`
(a running `go` must not lose it to the watcher).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from src.hud.registry import ACTIVE_STATUSES, pid_is_alive
from src.loop import lease
from src.loop.runs_index import index_path

STOPPED_WINDOW_S = 7 * 86400
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def resume_mark(state: dict, *, owner_alive: bool = False, other_run: bool = False) -> dict:
    """The phone button's mode: phase 3 `resume_mode` (`ok` / `needs_text` / `refused`)."""
    from src.loop.resume.mode import resume_mode

    if owner_alive and (state.get("status") in ACTIVE_STATUSES):
        return {"mode": "refused", "why": "владелец жив"}
    return resume_mode(state, other_run=other_run)


def _index() -> dict[str, Any]:
    try:
        data = json.loads(index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def run_dir_of(entry: Any, run_id: str) -> Path | None:
    if not isinstance(entry, dict) or not RUN_ID_RE.match(run_id):
        return None
    return Path(str(entry.get("repo") or "")) / ".tero" / "runs" / run_id


def collect(now: float, alive: Callable[[Any, Any], bool] = pid_is_alive) -> list[dict]:
    """One mark per unfinished or recently stopped run of the index."""
    marks = []
    index = _index()
    actives: dict[str, str] = {}
    for run_id, entry in index.items():
        run_dir = run_dir_of(entry, run_id)
        if run_dir is None:
            continue
        try:
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # Другой прогон идёт, только если его pid/pid_started подтверждают живого
        # владельца: запись `running` с мёртвым pid — не повод для `refused`.
        if isinstance(state, dict) and state.get("status") in ACTIVE_STATUSES:
            try:
                if alive(state.get("pid"), state.get("pid_started")):
                    actives[run_id] = str(entry.get("repo") or "")
            except Exception:
                continue
    for run_id, entry in index.items():
        run_dir = run_dir_of(entry, run_id)
        if run_dir is None:
            continue
        try:
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            mtime = (run_dir / "state.json").stat().st_mtime
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        status = state.get("status")
        active = status in ACTIVE_STATUSES
        if not active and not (status == "stopped" and now - mtime <= STOPPED_WINDOW_S):
            continue
        try:
            host = bool(lease.alive(lease.read(run_dir)))
        except Exception:
            host = False
        owner = bool(alive(state.get("pid"), state.get("pid_started")))
        repo = str(entry.get("repo") or "") if isinstance(entry, dict) else ""
        other = any(rid != run_id and r == repo for rid, r in actives.items())
        marks.append({"run_id": run_id, "owner_alive": owner,
                      "host_alive": host, "checked_at": now, "resume": resume_mark(state, owner_alive=owner,
                                                                                  other_run=other)})
    return marks
