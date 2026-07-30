"""Regression tests proving the 7 confirmed bugs from the deep code review.

Each test is designed to:
  - FAIL before the corresponding fix (proving the bug is real)
  - PASS after the fix (proving it is resolved)
"""

from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
from pathlib import Path
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




# ── Bug 6: zai.py — temp dir leaked when shutil.copy2 raises ─────────────────

@pytest.mark.asyncio
async def test_zai_temp_dir_cleaned_up_on_copy_error():
    """Temp dir must be removed even when shutil.copy2 raises before the try block."""
    import shutil as _shutil
    from src.providers.zai import ZaiProvider

    created_dirs: list[str] = []
    orig_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(prefix="", **kwargs):
        d = orig_mkdtemp(prefix=prefix, **kwargs)
        created_dirs.append(d)
        return d

    with tempfile.TemporaryDirectory() as fake_home:
        # Create a settings.json so the `if base_settings.exists()` branch is taken
        # and shutil.copy2 is actually called.
        (Path(fake_home) / "settings.json").write_text('{"env": {"ZAI_API_KEY": "tok"}}')

        provider = ZaiProvider()
        provider.config.claude_home = fake_home

        with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
            with patch("shutil.copy2", side_effect=PermissionError("denied")):
                with pytest.raises(PermissionError):
                    async for _ in provider.run("prompt", "system", "/tmp"):
                        pass  # pragma: no cover

    assert created_dirs, "mkdtemp should have been called"
    for d in created_dirs:
        assert not os.path.exists(d), (
            f"Temp dir leaked: '{d}' still exists after PermissionError in copy2. "
            "Fix: move tempfile.mkdtemp() and shutil.copy2() inside the try block."
        )


