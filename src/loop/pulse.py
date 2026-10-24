"""Owner heartbeat: <run_dir>/pulse that never breaks a run."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path


class Pulse:
    """Background alive ticker + completed-step counter.

    A separate daemon thread (not an asyncio task: the gate and git
    block the event loop) refreshes ``alive`` every ``interval_s``.
    The main loop calls :meth:`progress` after each completed step
    (player call, gate, judge, tester, phase commit). Every write is
    atomic (temp file + rename) and never raises.
    """

    def __init__(self, run_dir: str | Path, interval_s: float = 30) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "pulse"
        self.interval_s = interval_s
        # RLock: progress() snapshots the counter under the lock and _write
        # serializes the whole snapshot + temp-file write + replace under the
        # same lock, so a ticker tick can never overwrite a newer progress
        # write with a stale snapshot (F3).
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._progress = 0
        self._progress_ts = time.time()
        self._thread = threading.Thread(
            target=self._tick, daemon=True, name="tero-pulse"
        )
        try:
            self._write(time.time())
        except Exception:
            pass
        # F4: without the ticker alive never refreshes during long blocking
        # steps — surface a startup failure instead of silently proceeding
        # without the required thread.
        self._thread.start()

    def progress(self) -> None:
        """Record one completed step: counter +1 with a fresh progress_ts."""
        try:
            with self._lock:
                self._progress += 1
                self._progress_ts = time.time()
                self._write_locked(time.time())
        except Exception:
            pass

    def stop(self) -> None:
        """Stop the ticker thread. Never raises, safe to call twice."""
        try:
            self._stop_event.set()
        except Exception:
            pass
        try:
            thread = self._thread
            if (
                thread is not None
                and thread.is_alive()
                and threading.current_thread() is not thread
            ):
                thread.join(timeout=5)
        except Exception:
            pass
        try:
            # No resurrection: the nothing_to_do path removes the run dir
            # before this finally runs; don't recreate it with one file.
            if self.path.parent.is_dir():
                self._write(time.time())
        except Exception:
            pass

    def _tick(self) -> None:
        try:
            while not self._stop_event.wait(self.interval_s):
                try:
                    self._write(time.time())
                except Exception:
                    pass
        except Exception:
            pass

    def _write(
        self,
        alive: float,
        *,
        progress: int | None = None,
        progress_ts: float | None = None,
    ) -> None:
        try:
            with self._lock:
                self._write_locked(
                    alive, progress=progress, progress_ts=progress_ts
                )
        except Exception:
            pass

    def _write_locked(
        self,
        alive: float,
        *,
        progress: int | None = None,
        progress_ts: float | None = None,
    ) -> None:
        """Snapshot + temp-file write + replace while holding the lock."""
        if self._stop_event.is_set() and not self.path.is_file():
            return
        if progress is None:
            progress = self._progress
            progress_ts = self._progress_ts
        payload = {
            "alive": alive,
            "progress": progress,
            "progress_ts": progress_ts,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=self.path.name + ".",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
