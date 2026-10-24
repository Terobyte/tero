"""Shared subprocess runner for JSONL-streaming providers.

Consolidates process creation, cleanup, large-stdout reading, stderr deadlock
prevention, and JSON parsing into a single async generator used by
claude_native.py, codex.py, and opencode.py.
"""

import asyncio
import json
import os
import signal
import time
from contextlib import aclosing
from dataclasses import dataclass
from typing import AsyncIterator

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
    stalled: bool = False


async def _iter_stdout(stdout, on_chunk=None) -> AsyncIterator[bytes]:
    """Yield raw line bytes from a subprocess stdout stream.

    Supports two modes:
    - Real asyncio StreamReader (has .read()): chunk-based reading, no
      readline() 64 KB limit issues.
    - Async-iterable mock (test doubles that implement __aiter__): falls
      through to a plain `async for` loop.

    on_chunk: optional sync callback invoked on every stdout read — once per
        non-empty chunk in reader mode, once per line in mock mode. The stall
        watchdog uses it to reset the silence budget on any output bytes,
        while JSON parsing stays tied to completed lines.
    """
    if stdout is None:
        return
    if hasattr(stdout, "read"):
        buffer = b""
        while True:
            chunk = await stdout.read(STDOUT_READ_CHUNK_SIZE)
            if not chunk:
                break
            if on_chunk is not None:
                on_chunk()
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield line
        if buffer:
            yield buffer
        return
    async for line in stdout:
        if on_chunk is not None:
            on_chunk()
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
        Parsed JSON dicts -- one per non-empty, valid-JSON stdout line, as
        soon as stdout produces them (not buffered until process exit).
        SubprocessExit -- exactly once, after all stdout lines are consumed,
            carrying the returncode and raw stderr bytes.

    Notes:
        - Non-JSON lines are silently skipped.
        - Stderr is drained concurrently from process start to avoid the OS
          pipe-buffer deadlock: a child that writes >64 KB to stderr before
          closing stdout blocks until someone reads it.
        - The subprocess runs in its own process group; the whole group is
          killed when the run ends, times out or is cancelled.
        - stall_timeout: if > 0, the process is killed after that many seconds
          of continuous stdout silence. Every new stdout event restarts the
          budget; earlier events stay delivered even if the run later stalls.
    """
    proc = None
    stderr_task = None
    stalled = False
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

        def _on_stall() -> None:
            nonlocal stalled
            stalled = True
            _kill_group(proc)

        async def _write_stdin() -> None:
            if stdin_data is None or proc.stdin is None:
                return
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

        # Both paths produce parsed events line-by-line; the stall branch
        # restarts the silence budget on every stdout read (F8: any chunk,
        # not just a completed line) via an edge-triggered deadline. The
        # consumer waits only until that deadline (F9: the same deadline
        # governs the post-EOF wait), and a watchdog task keeps ticking
        # while events sit queued behind a slow consumer (F10).
        _FEED_DONE = object()
        stall_state: dict = {}
        stall_lock: asyncio.Lock | None = None

        if stall_timeout > 0:
            stall_state["deadline"] = time.monotonic() + stall_timeout
            stall_lock = asyncio.Lock()
            loop = asyncio.get_running_loop()

            def _poke() -> None:
                stall_state["deadline"] = time.monotonic() + stall_timeout

            # on_chunk runs inside the feed task but must not block it;
            # call_soon_threadsafe also keeps it safe if the reader ever
            # moves to a thread. Guard: loop closes only after the feed
            # task is cancelled below, so the callback never fires on a
            # closed loop.
            def _poke_from_reader() -> None:
                try:
                    loop.call_soon_threadsafe(_poke)
                except RuntimeError:
                    pass

            async def _parsed_events():
                # aclosing: an early close of this generator closes the
                # reader too, instead of leaving it to GC after the event
                # loop is gone.
                async with aclosing(
                    _iter_stdout(proc.stdout, on_chunk=_poke_from_reader)
                ) as lines:
                    async for line_bytes in lines:
                        parsed = _parse_line(line_bytes)
                        if parsed is not None:
                            yield parsed

            watchdog_fired = asyncio.Event()

            async def _watchdog() -> None:
                assert stall_lock is not None
                while True:
                    async with stall_lock:
                        remaining = stall_state["deadline"] - time.monotonic()
                    if remaining <= 0:
                        watchdog_fired.set()
                        _on_stall()
                        return
                    try:
                        await asyncio.sleep(remaining)
                    except asyncio.CancelledError:
                        return
                    # Loop re-reads the deadline: a poke during the sleep
                    # pushes it forward instead of firing.

            async def _feed_into(queue: asyncio.Queue) -> None:
                try:
                    async with aclosing(_parsed_events()) as agen:
                        async for item in agen:
                            queue.put_nowait(item)
                finally:
                    queue.put_nowait(_FEED_DONE)

            queue: asyncio.Queue = asyncio.Queue()
            feed_error: list[BaseException] = []

            async def _feed() -> None:
                try:
                    await _feed_into(queue)
                except BaseException as exc:  # noqa: BLE001 — re-raised by consumer
                    feed_error.append(exc)
                # _FEED_DONE is queued by _feed_into's finally even on error.

            # Stdin is written concurrently with stdout reading: a silent
            # child that never reads its stdin would block drain() on a full
            # pipe (>64 KB) forever, so the stall timeout must cover the
            # write too. _on_stall kills the process group, unblocking it.
            stdin_task = asyncio.create_task(_write_stdin())
            feed_task = asyncio.create_task(_feed())
            watchdog_task = asyncio.create_task(_watchdog())
            try:
                # Streaming consume (F7): each event is yielded as soon as
                # stdout produces it; only continuous silence longer than
                # stall_timeout kills the process. The watchdog fires even
                # while the consumer is busy, so queue.get() never needs a
                # timeout of its own.
                while True:
                    if watchdog_fired.is_set():
                        break
                    get_task = asyncio.create_task(queue.get())
                    fired_task = asyncio.create_task(watchdog_fired.wait())
                    try:
                        await asyncio.wait(
                            {get_task, fired_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except asyncio.CancelledError:
                        get_task.cancel()
                        fired_task.cancel()
                        for _t in (get_task, fired_task):
                            try:
                                await _t
                            except (asyncio.CancelledError, Exception):
                                pass
                        raise
                    fired_task.cancel()
                    if watchdog_fired.is_set():
                        # A line may have landed while the stall fired;
                        # drop it — the process is dead either way.
                        if get_task.done() and not get_task.cancelled():
                            try:
                                get_task.result()
                            except (asyncio.CancelledError, Exception):
                                pass
                        else:
                            get_task.cancel()
                        break
                    item = get_task.result()
                    if item is _FEED_DONE:
                        break
                    yield item
                if feed_error:
                    raise feed_error[0]
            finally:
                await _cancel_task(feed_task)
                await _cancel_task(watchdog_task)
                await _cancel_task(stdin_task)
                while True:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
        else:
            async def _parsed_events():
                async with aclosing(_iter_stdout(proc.stdout)) as lines:
                    async for line_bytes in lines:
                        parsed = _parse_line(line_bytes)
                        if parsed is not None:
                            yield parsed

            stdin_task = asyncio.create_task(_write_stdin())
            try:
                async with aclosing(_parsed_events()) as agen:
                    async for parsed in agen:
                        yield parsed
                await asyncio.wait_for(stdin_task, timeout=None)
            finally:
                await _cancel_task(stdin_task)

        if stall_timeout > 0:
            assert stall_lock is not None
            # F9: after stdout EOF the deadline still runs from the last
            # chunk, so wait only the remaining silence — not a fresh full
            # stall_timeout.
            async with stall_lock:
                remaining = stall_state["deadline"] - time.monotonic()
            if remaining <= 0:
                _on_stall()
                await proc.wait()
            else:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    _on_stall()
                    await proc.wait()
        else:
            await proc.wait()
        _kill_group(proc)
        stderr_bytes = await stderr_task

        yield SubprocessExit(
            returncode=proc.returncode, stderr=stderr_bytes, stalled=stalled
        )

    finally:
        if proc is not None:
            _kill_group(proc)
            if proc.returncode is None:
                await proc.wait()
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()


async def _empty_bytes() -> bytes:
    return b""


async def _cancel_task(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def _parse_line(line_bytes: bytes):
    line = line_bytes.decode("utf-8", errors="replace").strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


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
