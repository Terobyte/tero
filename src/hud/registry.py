"""Registry of live runs for the HUD "+1 run" badge (spec part A, T4a).

Every run writes `~/.tero/registry/<run_id>.json` with its pid,
`pid_started` (process start time from psutil) and the path to its
`state.json`. The HUD reads the registry to show other live runs.
Stale files (dead pid, or pid reused with a different start time) are
deleted on read. A run removes its own file in a `finally` in `run()`.

All operations are best effort and never raise: a broken registry must
not affect a run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import psutil
from src.fsio import atomic_write_json

ACTIVE_STATUSES = ("starting", "running", "waiting_human", "waiting_agent", "waiting_limit", "judge_pending")


def registry_dir() -> Path:
    return Path.home() / ".tero" / "registry"


def current_pid_started(pid: int | None = None) -> float | None:
    """Start time of a process (itself by default), None when unknown."""
    try:
        return psutil.Process(pid).create_time() if pid is not None else psutil.Process().create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def pid_is_alive(pid: Any, pid_started: Any) -> bool:
    """True only if a process with this pid AND the same start time exists.

    A reused pid (same number, different start time) is not the same
    process, so it counts as dead.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        if not psutil.pid_exists(pid):
            return False
    except Exception:
        return False
    if isinstance(pid_started, bool) or not isinstance(pid_started, (int, float)):
        return False
    try:
        actual = psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
        return False
    except psutil.AccessDenied:
        return False
    except Exception:
        return False
    return actual == pid_started


def is_dead(state: Mapping[str, Any]) -> bool:
    """"Run is gone": unfinished status but its owner process is no longer alive."""
    if not isinstance(state, Mapping):
        return False
    if state.get("status") not in ACTIVE_STATUSES:
        return False
    return not pid_is_alive(state.get("pid"), state.get("pid_started"))


class Registry:
    """File registry of runs. `base_dir` defaults to `~/.tero/registry`."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else registry_dir()

    def _path(self, run_id: str) -> Path:
        return self.base_dir / f"{run_id}.json"

    def register(
        self,
        run_id: str,
        state_path: str | Path,
        *,
        pid: int | None = None,
        pid_started: float | None = None,
    ) -> Path:
        """Write this run's entry. Never raises."""
        path = self._path(run_id)
        try:
            own_pid = pid if pid is not None else os.getpid()
            started = pid_started if pid_started is not None else current_pid_started(own_pid)
            payload = {
                "run_id": run_id,
                "pid": own_pid,
                "pid_started": started,
                "state_path": str(state_path),
            }
            self.base_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, payload, ensure_ascii=False)
        except Exception:
            pass
        return path

    def unregister(self, run_id: str) -> None:
        """Remove this run's entry. Never raises."""
        try:
            self._path(run_id).unlink(missing_ok=True)
        except Exception:
            pass

    def list_live(self) -> list[dict[str, Any]]:
        """Live entries; files of dead processes are deleted. Never raises."""
        live: list[dict[str, Any]] = []
        try:
            files = sorted(self.base_dir.glob("*.json"))
        except Exception:
            return []
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            if not isinstance(data, dict):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            if pid_is_alive(data.get("pid"), data.get("pid_started")):
                live.append(data)
            else:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
        return live
