"""Snapshot child processes before an agent turn and kill new ones after."""

from __future__ import annotations

import os
import signal

import psutil


class ProcessGuard:
    """Manages subprocess lifecycle around agent turns."""

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose

    def snapshot_pids(self) -> set[int]:
        """Snapshot child PIDs of this process.

        Scoped to descendants of the current PID so parallel tero instances
        don't see each other's subprocesses and kill them.
        """
        try:
            return {
                c.pid for c in psutil.Process(os.getpid()).children(recursive=True)
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return set()

    def kill_new_processes(self, before: set[int]) -> None:
        """Kill processes that appeared since the snapshot, including children."""
        after = self.snapshot_pids()
        new_pids = after - before - {os.getpid()}

        if not new_pids:
            return

        for pid in new_pids:
            try:
                proc = psutil.Process(pid)
                children = proc.children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                pass

        if self.verbose:
            print(f"  [cleanup] убито процессов: {len(new_pids)}")
