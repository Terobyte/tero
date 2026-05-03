"""Shared subprocess runner for JSONL-streaming providers.

Consolidates process creation, cleanup, large-stdout reading, stderr deadlock
prevention, and JSON parsing into a single async generator used by
claude_native.py, codex.py, and opencode.py.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator

from src.constants import STDOUT_READ_CHUNK_SIZE, STREAM_READER_LIMIT


@dataclass
class SubprocessExit:
    """Sentinel yielded after all stdout lines are consumed.

    Carry the subprocess returncode and raw stderr bytes so callers can
    build provider-specific error messages without coupling the runner to
    any particular message format.
    """

    returncode: int | None
    stderr: bytes


async def _iter_stdout(stdout, stall_timeout: float = 0) -> AsyncIterator[bytes]:
    """Yield raw line bytes from a subprocess stdout stream.

    Supports two modes:
    - Real asyncio StreamReader (has .read()): chunk-based reading, no
      readline() 64 KB limit issues.
    - Async-iterable mock (test doubles that implement __aiter__): falls
      through to a plain `async for` loop.

    stall_timeout: if > 0, abort if no bytes arrive within that many seconds.
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
                return  # stall detected — stop reading, let proc.wait() handle cleanup
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
        - Stderr is drained concurrently (before proc.wait()) to avoid the OS
          pipe-buffer deadlock: if the child writes >64 KB to stderr and the
          parent calls proc.wait() first, both sides block.
        - The subprocess is killed in the finally block if it has not exited.
        - stall_timeout: if > 0, stop reading stdout if no bytes arrive within
          that many seconds (useful for providers that hang silently).
    """
    proc = None
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

        async for line_bytes in _iter_stdout(proc.stdout, stall_timeout=stall_timeout):
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                pass

        # Drain stderr concurrently with proc.wait() to prevent OS pipe deadlock.
        if proc.stderr is not None:
            stderr_task = asyncio.create_task(proc.stderr.read())
        else:
            stderr_task = asyncio.create_task(_empty_bytes())
        await proc.wait()
        stderr_bytes = await stderr_task

        yield SubprocessExit(returncode=proc.returncode, stderr=stderr_bytes)

    finally:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()


async def _empty_bytes() -> bytes:
    return b""
