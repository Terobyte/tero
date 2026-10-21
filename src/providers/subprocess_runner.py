"""Shared subprocess runner for JSONL-streaming providers.

Consolidates process creation, cleanup, large-stdout reading, stderr deadlock
prevention, and JSON parsing into a single async generator used by
claude_native.py, codex.py, and opencode.py.
"""

import asyncio
import json
import os
import signal
from contextlib import aclosing
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from src.constants import STDOUT_READ_CHUNK_SIZE
from src.errors import ProviderError


@dataclass
class SubprocessExit:
    """Sentinel yielded after all stdout lines are consumed.

    Carry the subprocess returncode and raw stderr bytes so callers can
    build provider-specific error messages without coupling the runner to
    any particular message format.
    """

    returncode: int | None
    stderr: bytes


async def _iter_stdout(
    stdout, stall_timeout: float = 0, on_stall: Callable[[], None] | None = None
) -> AsyncIterator[bytes]:
    """Yield raw line bytes from a subprocess stdout stream.

    Supports two modes:
    - Real asyncio StreamReader (has .read()): chunk-based reading, no
      readline() 64 KB limit issues.
    - Async-iterable mock (test doubles that implement __aiter__): falls
      through to a plain `async for` loop.

    stall_timeout: if > 0, abort if no bytes arrive within that many seconds.
    on_stall: called when a stall is detected, so the caller can kill the
        still-running process instead of hanging on proc.wait().
    """
    if stdout is None:
        return
    if hasattr(stdout, "read"):
        buffer = b""
        while True:
            try:
                if stall_timeout > 0:
                    chunk = await asyncio.wait_for(
                        stdout.read(STDOUT_READ_CHUNK_SIZE),
                        timeout=stall_timeout,
                    )
                else:
                    chunk = await stdout.read(STDOUT_READ_CHUNK_SIZE)
            except asyncio.TimeoutError:
                if on_stall is not None:
                    on_stall()
                return  # stall detected — process killed, stop reading
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield line
        if buffer:
            yield buffer
        return
    async for line in stdout:
        yield line


async def run_subprocess_jsonl(
    cmd: list[str],
    working_dir: str,
    env: dict[str, str] | None = None,
    stdin_data: bytes | None = None,
    stall_timeout: float = 0,
) -> AsyncIterator["dict | SubprocessExit"]:
    """Launch a subprocess and yield parsed JSONL events, then a SubprocessExit.

    Args:
        cmd: Command and arguments to execute.
        working_dir: Working directory for the subprocess (cwd).
        env: Environment variables. Defaults to inheriting the current process
            environment when None.
        stdin_data: Raw bytes to write to stdin before closing it. Pass None
            to open stdin as DEVNULL (no stdin at all).

    Yields:
        Parsed JSON dicts -- one per non-empty, valid-JSON stdout line.
        SubprocessExit -- exactly once, after all stdout lines are consumed,
            carrying the returncode and raw stderr bytes.

    Notes:
        - Non-JSON lines are silently skipped.
        - Stderr is drained concurrently from process start to avoid the OS
          pipe-buffer deadlock: a child that writes >64 KB to stderr before
          closing stdout blocks until someone reads it.
        - The subprocess runs in its own process group; the whole group is
          killed when the run ends, times out or is cancelled.
        - stall_timeout: if > 0, stop reading stdout if no bytes arrive within
          that many seconds (useful for providers that hang silently).
    """
    proc = None
    stderr_task = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE
            if stdin_data is not None
            else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            env=env,
            # Own process group: background jobs the CLI started (`npm run dev &`)
            # die with it instead of holding the pipes or outliving the run.
            start_new_session=True,
        )
        # Drain stderr from the start: a child that fills the stderr pipe
        # before closing stdout would block forever, and proc.wait() would
        # never return because asyncio waits for every pipe to hit EOF.
        stderr_task = asyncio.create_task(
            proc.stderr.read() if proc.stderr is not None else _empty_bytes()
        )

        if stdin_data is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_data)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        # aclosing: an early close of this generator closes the reader too,
        # instead of leaving it to GC after the event loop is gone.
        async with aclosing(
            _iter_stdout(
                proc.stdout, stall_timeout=stall_timeout, on_stall=lambda: _kill_group(proc)
            )
        ) as lines:
            async for line_bytes in lines:
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    pass

        await proc.wait()
        _kill_group(proc)
        stderr_bytes = await stderr_task

        yield SubprocessExit(returncode=proc.returncode, stderr=stderr_bytes)

    finally:
        if proc is not None:
            _kill_group(proc)
            if proc.returncode is None:
                await proc.wait()
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()


async def _empty_bytes() -> bytes:
    return b""


def raise_for_exit(
    name: str,
    returncode: int | None,
    stderr_data: bytes | str,
    errors: list[str] | None = None,
) -> None:
    """Raise when a provider CLI subprocess exits unsuccessfully.

    Shared decode/fallback logic for all providers: stderr bytes are
    decoded with errors="replace", empty output falls back to
    "subprocess exited without stderr output". When `errors` are given
    (opencode collects in-stream error events), they are prepended to
    the stderr text, joined with "; ".
    """
    if returncode in (None, 0):
        return

    if isinstance(stderr_data, bytes):
        stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
    else:
        stderr_text = (stderr_data or "").strip()

    if errors is None:
        detail = stderr_text or "subprocess exited without stderr output"
    else:
        parts = [*(errors or []), stderr_text] if stderr_text else list(errors or [])
        detail = "; ".join(parts) or "subprocess exited without stderr output"
    raise ProviderError(f"{name} exited with code {returncode}: {detail}")


def _kill_group(proc) -> None:
    """Kill the subprocess and everything left in its process group."""
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int):
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
