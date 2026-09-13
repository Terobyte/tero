"""Regression tests proving the 7 confirmed bugs from the deep code review.

Each test is designed to:
  - FAIL before the corresponding fix (proving the bug is real)
  - PASS after the fix (proving it is resolved)
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch

import pytest


# ── Bug 1: claude_native.py — stderr pipe deadlock ────────────────────────────

def test_stderr_concurrent_drain_before_wait():
    """Stderr must be drained concurrently (before proc.wait) to prevent deadlock.

    If the subprocess writes >64 KB to stderr, the OS pipe buffer fills up and
    the process blocks.  Without a concurrent reader, proc.wait() never returns.
    Fix: asyncio.create_task(proc.stderr.read()) BEFORE await proc.wait().

    After Phase 6B refactor, this guarantee lives in subprocess_runner.py which
    all subprocess-based providers (claude_native, codex, opencode) delegate to.
    """
    from src.providers import subprocess_runner

    source = inspect.getsource(subprocess_runner.run_subprocess_jsonl)

    assert "create_task" in source, (
        "run_subprocess_jsonl() must use asyncio.create_task(proc.stderr.read()) "
        "before proc.wait() to prevent deadlock when stderr exceeds the OS pipe buffer."
    )

    task_pos = source.find("create_task")
    wait_pos = source.find("await proc.wait()")
    assert task_pos < wait_pos, (
        "asyncio.create_task for stderr must appear BEFORE await proc.wait(). "
        f"create_task at pos={task_pos}, await proc.wait() at pos={wait_pos}."
    )


@pytest.mark.asyncio
async def test_stderr_deadlock_does_not_hang():
    """run() must complete within 5 s even when subprocess writes >64 KB to stderr."""
    import sys
    from src.providers.claude_native import ClaudeNativeProvider

    provider = ClaudeNativeProvider()
    # Script: write 65 KB to stderr first, then emit one JSON line on stdout.
    # Without concurrent stderr draining, the subprocess blocks on the stderr
    # write, stdout is never written, and run() hangs indefinitely.
    script = (
        "import sys, json; "
        "sys.stderr.write('e' * 66560); "
        "sys.stderr.flush(); "
        "print(json.dumps({'type': 'text', 'text': 'done'})); "
        "sys.stdout.flush()"
    )
    with patch.object(provider, "_build_command", return_value=[sys.executable, "-c", script]):
        events = []
        try:
            async def _collect():
                async for event in provider.run("p", "s", "/tmp", max_turns=1):
                    events.append(event)

            await asyncio.wait_for(_collect(), timeout=5.0)
        except asyncio.TimeoutError:
            pytest.fail(
                "Deadlock detected: run() timed out waiting for subprocess "
                "that wrote >64 KB to stderr."
            )

    assert any(e.get("text") == "done" for e in events), (
        f"Expected a 'done' text event; got: {events}"
    )





