"""Process Guard — snapshot and clean up child processes spawned during agent turns.

Extracted from CoachPlayerSession so the logic can be reused by BatchExecutor
and other subsystems without needing a full session object.
"""

from __future__ import annotations

import os
import signal
import subprocess


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
            import psutil

            try:
                return {
                    c.pid for c in psutil.Process(os.getpid()).children(recursive=True)
                }
            except psutil.NoSuchProcess:
                return set()
        except ImportError:
            pass

        try:
            result = subprocess.run(
                ["pgrep", "-P", str(os.getpid())],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return {
                    int(pid)
                    for pid in result.stdout.strip().split("\n")
                    if pid.strip().isdigit()
                }
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        return set()

    def kill_new_processes(self, before: set[int]) -> None:
        """Kill processes that appeared since the snapshot, including children."""
        try:
            import psutil
        except ImportError:
            return

        after = self.snapshot_pids()
        new_pids = after - before - {os.getpid()}
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
        if new_pids and self.verbose:
            print(f"  [cleanup] убито процессов: {len(new_pids)}")
