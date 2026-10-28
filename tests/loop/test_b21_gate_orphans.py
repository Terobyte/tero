"""B21: the test gate must not leak grandchildren that leave its process group.

The gate starts the suite with start_new_session=True and on timeout kills the
group plus psutil descendants. A grandchild that starts its own session, or
that is reparented to PID 1 after its parent exits, escapes both. Each test
here must fail on the current code: the marker file appears because the
grandchild survived the gate.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from src.loop.gates import TestCommand, run_all

# Writes its own pid right away, then leaves a marker after 3 s if it survives.
GRANDCHILD = (
    "import os, sys, time\n"
    "open(sys.argv[2], 'w').write(str(os.getpid()))\n"
    "time.sleep(3)\n"
    "open(sys.argv[1], 'w').write('done')\n"
)


def _spawn_setsid_grandchild() -> str:
    return (
        "import subprocess, sys\n"
        f"grandchild = {GRANDCHILD!r}\n"
        "subprocess.Popen([sys.executable, '-c', grandchild, sys.argv[1], sys.argv[2]],\n"
        "    start_new_session=True, stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    )


def _assert_no_marker(marker: Path, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if marker.exists():
            pytest.fail("grandchild survived the gate (marker appeared)")
        time.sleep(0.1)


def _kill_leftover(pidfile: Path) -> None:
    try:
        pid = int(pidfile.read_text())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def test_gate_timeout_kills_setsid_grandchild(tmp_path: Path):
    marker = tmp_path / "grandchild.done"
    pidfile = tmp_path / "grandchild.pid"
    parent = _spawn_setsid_grandchild() + "import time\ntime.sleep(30)\n"
    command = TestCommand(
        name="override",
        run_all=[sys.executable, "-c", parent, str(marker), str(pidfile)],
        run_files=None,
    )
    try:
        result = run_all(command, repo=tmp_path, timeout_s=1)
        assert result.timed_out
        _assert_no_marker(marker)
    finally:
        _kill_leftover(pidfile)


def test_gate_normal_exit_kills_orphaned_grandchild(tmp_path: Path):
    marker = tmp_path / "grandchild.done"
    pidfile = tmp_path / "grandchild.pid"
    parent = _spawn_setsid_grandchild()  # parent exits 0 immediately
    command = TestCommand(
        name="override",
        run_all=[sys.executable, "-c", parent, str(marker), str(pidfile)],
        run_files=None,
    )
    try:
        result = run_all(command, repo=tmp_path, timeout_s=30)
        assert result.ok
        _assert_no_marker(marker)
    finally:
        _kill_leftover(pidfile)
