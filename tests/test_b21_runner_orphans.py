"""B21: a grandchild in its own session must die with the agent run.

Real agent CLIs run shell commands (e.g. `pytest -n auto`) in their own new
session. run_subprocess_jsonl only kills the agent's process group, so such a
grandchild escapes the kill, is reparented to PID 1 and keeps running.

These tests are RED on the current code: the grandchild writes its marker
after the run is over. They go green once the runner also kills the
grandchild's session tree.
"""

import asyncio
import os
import signal
import sys
import time

import pytest

from src.providers.subprocess_runner import SubprocessExit, run_subprocess_jsonl

# The grandchild records its own pid first (so cleanup can always find it),
# sleeps ~3 s, then writes the marker. stdio goes to DEVNULL in the agent so
# the grandchild does not hold the agent's stdout pipe open.
GRANDCHILD_SRC = """
import os, sys, time
pid_file, marker = sys.argv[1], sys.argv[2]
with open(pid_file, "w") as f:
    f.write(str(os.getpid()))
time.sleep(3)
with open(marker, "w") as f:
    f.write("alive")
"""


def _write_scripts(tmp_path, pid_file, marker):
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(GRANDCHILD_SRC)
    return grandchild


def _agent_cmd(tmp_path, pid_file, marker, agent_body: str) -> list[str]:
    grandchild = _write_scripts(tmp_path, pid_file, marker)
    agent_src = (
        "import json, subprocess, sys, time\n"
        f"GRANDCHILD = {str(grandchild)!r}\n"
        f"PID_FILE = {str(pid_file)!r}\n"
        f"MARKER = {str(marker)!r}\n"
        "subprocess.Popen(\n"
        "    [sys.executable, GRANDCHILD, PID_FILE, MARKER],\n"
        "    start_new_session=True,\n"
        "    stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        # the grandchild must be really up before the agent ends, or a sweep that
        # kills it early would look like 'never started'
        "import os\n"
        "for _ in range(200):\n"
        "    if os.path.exists(PID_FILE):\n"
        "        break\n"
        "    time.sleep(0.01)\n"
    ) + agent_body
    agent = tmp_path / "agent.py"
    agent.write_text(agent_src)
    return [sys.executable, str(agent)]


async def _wait_for_file(path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        await asyncio.sleep(0.05)
    return path.exists()


def _kill_grandchild(pid_file) -> None:
    """Best-effort kill so a red test never leaks the grandchild."""
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGKILL)
    except (OSError, ValueError):
        pass


@pytest.mark.asyncio
async def test_agent_exit_kills_setsid_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    marker = tmp_path / "grandchild.marker"
    cmd = _agent_cmd(
        tmp_path,
        pid_file,
        marker,
        'print(json.dumps({"type": "done"}), flush=True)\n',
    )
    try:
        events = []
        async for event in run_subprocess_jsonl(cmd, str(tmp_path)):
            events.append(event)

        assert events[0] == {"type": "done"}
        assert isinstance(events[-1], SubprocessExit)
        assert events[-1].returncode == 0
        assert await _wait_for_file(pid_file, 3), (
            "grandchild never started; the test proves nothing"
        )

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if marker.exists():
                pytest.fail(
                    "setsid grandchild outlived the agent run and wrote its marker"
                )
            await asyncio.sleep(0.1)
    finally:
        _kill_grandchild(pid_file)


@pytest.mark.asyncio
async def test_agent_stall_kills_setsid_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    marker = tmp_path / "grandchild.marker"
    cmd = _agent_cmd(tmp_path, pid_file, marker, "time.sleep(30)\n")
    try:
        events = []
        async for event in run_subprocess_jsonl(
            cmd, str(tmp_path), stall_timeout=0.5
        ):
            events.append(event)

        assert isinstance(events[-1], SubprocessExit)
        assert events[-1].stalled, "stall watchdog did not fire"
        assert await _wait_for_file(pid_file, 3), (
            "grandchild never started; the test proves nothing"
        )

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if marker.exists():
                pytest.fail(
                    "setsid grandchild survived the stall kill and wrote its marker"
                )
            await asyncio.sleep(0.1)
    finally:
        _kill_grandchild(pid_file)
