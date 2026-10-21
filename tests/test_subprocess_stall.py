"""A stalled subprocess must be killed, not hung on until the outer timeout.

Before the fix, _iter_stdout's stall_timeout just stopped reading and
returned; run_subprocess_jsonl then called `await proc.wait()` on a still-
running process and hung until the caller's own (much larger) timeout.
"""

import sys
import time

import pytest

from src.providers.subprocess_runner import SubprocessExit, run_subprocess_jsonl


@pytest.mark.asyncio
async def test_stall_kills_process_and_returns_quickly():
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    started = time.monotonic()

    events = []
    async for event in run_subprocess_jsonl(cmd, ".", stall_timeout=0.5):
        events.append(event)

    elapsed = time.monotonic() - started
    assert elapsed < 5, f"stall kill took too long: {elapsed:.1f}s"
    assert len(events) == 1
    exit_event = events[0]
    assert isinstance(exit_event, SubprocessExit)
    assert exit_event.returncode != 0
