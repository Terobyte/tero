"""Live stop flag: stop.json honoured only while its owner's run goes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.loop.watchdog import stop_requested


def live(run_dir: str | Path, *, require_pid: bool = False) -> bool:
    """stop.json present AND the owner's run still going. Never raises."""
    try:
        if not stop_requested(run_dir):
            return False
        return _owner_live(run_dir, require_pid=require_pid)
    except Exception:
        return False


def saved_state(run_dir: Any) -> dict:
    """state.json as a dict, {} when unreadable. Never raises."""
    try:
        import json as _json
        return _json.loads((Path(run_dir) / "state.json").read_text()) or {}
    except Exception:
        return {}


def _owner_live(run_dir: Any, *, require_pid: bool = False) -> bool:
    try:
        from src.hud import registry as _reg
        saved = saved_state(run_dir)
        if (saved.get("status") or "") in ("done", "stopped"):
            return False
        pid, started = saved.get("pid"), saved.get("pid_started")
        if pid is not None and started is not None:
            try:
                return bool(_reg.pid_is_alive(pid, started))
            except Exception:
                pass
        return not require_pid
    except Exception:
        return False


def stop_requested_flag(run_dir: str | None) -> bool:
    """True when a stop was requested for this run dir (else its `.tero/runs`).

    Never raises.
    """
    try:
        if not run_dir:
            return False
        direct = Path(run_dir)
        if (direct / "inbox").is_dir() or (direct / "state.json").is_file():
            return bool(live(direct, require_pid=True))
        runs = direct / ".tero" / "runs"
        if not runs.is_dir():
            return False
        return any(
            live(c, require_pid=True) for c in runs.iterdir())
    except Exception:
        return False
