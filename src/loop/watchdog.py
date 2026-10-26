"""Stop watchdog: watches inbox/stop.json during a run step."""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

STOP_POLL_S = 5
KILL_GRACE_S = 5

_lock = threading.Lock()
_STEP_BEFORE: set[int] | None = None


def stop_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "inbox" / "stop.json"


def stop_requested(run_dir: str | Path) -> bool:
    """Flag: owner must stop after the current step. Never raises."""
    try:
        p = stop_path(run_dir)
        return p.is_file() and not p.is_symlink()
    except Exception:
        return False


def note_step_before(pids: set[int] | None) -> None:
    """Mark which pids predate the current step (None = step over).

    set_step_scope() in _call writes this so the watchdog only kills
    processes the current step spawned, never the HUD or older children.
    """
    global _STEP_BEFORE
    with _lock:
        _STEP_BEFORE = None if pids is None else set(pids)


def step_before() -> set[int] | None:
    """Current-step baseline, or None when no step runs. Never raises."""
    try:
        with _lock:
            return None if _STEP_BEFORE is None else set(_STEP_BEFORE)
    except Exception:
        return None


def _kill_new_processes(before: set[int] | None) -> None:
    if before is None:
        before = step_before()
    if before is None:
        return
    try:
        from src.process_guard import ProcessGuard

        guard = ProcessGuard()
        after = guard.snapshot_pids()
        new_pids = set(after) - set(before) - {os.getpid()}
        for pid in new_pids:
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        if not new_pids:
            return
        deadline = time.time() + KILL_GRACE_S
        while time.time() < deadline:
            try:
                rest = guard.snapshot_pids() & set(new_pids)
            except Exception:
                return
            if not rest:
                return
            time.sleep(0.2)
        for pid in new_pids:
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                pass
    except Exception:
        pass


def arm(run_dir: str | Path, poll_s: float = STOP_POLL_S) -> Any | None:
    """Start a watchdog without a Pulse (run_phase tests). Stop it after."""
    try:
        wd = Watchdog(run_dir, poll_s=poll_s)
        wd.start()
        return wd
    except Exception:
        return None


class Watchdog:
    """Thread watching stop.json next to the pulse. Sets cancel flag + kills step group."""

    def __init__(
        self,
        run_dir: str | Path,
        poll_s: float | None = None,
        guard: Any = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.poll_s = STOP_POLL_S if poll_s is None else poll_s
        self.cancel = threading.Event()
        self._stop_event = threading.Event()
        self._guard = guard
        self._before: set[int] | None = None
        self._thread = threading.Thread(
            target=self._tick, daemon=True, name="tero-stop-watchdog"
        )

    def start(self) -> None:
        try:
            if not self._thread.is_alive():
                self._thread.start()
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self._stop_event.set()
        except Exception:
            pass
        try:
            t = self._thread
            if t.is_alive() and threading.current_thread() is not t:
                t.join(timeout=5)
        except Exception:
            pass

    def _tick(self) -> None:
        """Keep polling after the first kill: a retried step spawns a new
        process under a fresh baseline (note_step_before), which still
        needs killing even though the flag was already set once."""
        try:
            while not self._stop_event.wait(self.poll_s):
                try:
                    if stop_requested(self.run_dir):
                        self.cancel.set()
                        _kill_new_processes(step_before())
                except Exception:
                    pass
        except Exception:
            pass
