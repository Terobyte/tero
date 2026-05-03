"""Tests for src/providers/subprocess_runner.py."""

import asyncio
import json
import pytest

from src.providers.subprocess_runner import SubprocessExit, _iter_stdout, run_subprocess_jsonl


# ── Helpers ───────────────────────────────────────────────────────────────────


class MockStdout:
    """Async-iterable mock that yields pre-set line bytes."""

    def __init__(self, lines: list[bytes]):
        self.lines = list(lines)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self.lines):
            raise StopAsyncIteration
        line = self.lines[self._index]
        self._index += 1
        return line


class ChunkedStdout:
    """Mock stdout that exposes .read() with pre-set chunks."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self._index = 0

    async def read(self, _size: int = -1) -> bytes:
        if self._index >= len(self._chunks):
            return b""
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


class MockStderr:
    def __init__(self, text: str = ""):
        self._text = text

    async def read(self) -> bytes:
        return self._text.encode()


class MockStdin:
    def __init__(self):
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes):
        self.written.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class MockProcess:
    def __init__(
        self,
        stdout,
        stderr_text: str = "",
        wait_returncode: int = 0,
        stdin=None,
    ):
        self.stdout = stdout
        self.stderr = MockStderr(stderr_text)
        self.stdin = stdin or MockStdin()
        self.returncode = None
        self._wait_returncode = wait_returncode
        self.killed = False

    async def wait(self):
        if self.returncode is None:
            self.returncode = self._wait_returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


# ── _iter_stdout tests ────────────────────────────────────────────────────────


class TestIterStdout:
    @pytest.mark.asyncio
    async def test_async_iterable_yields_lines(self):
        stdout = MockStdout([b'{"a":1}\n', b'{"b":2}\n'])
        lines = [line async for line in _iter_stdout(stdout)]
        assert lines == [b'{"a":1}\n', b'{"b":2}\n']

    @pytest.mark.asyncio
    async def test_chunked_read_splits_on_newlines(self):
        # Two JSON lines packed into one chunk
        chunk = b'{"a":1}\n{"b":2}\n'
        stdout = ChunkedStdout([chunk])
        lines = [line async for line in _iter_stdout(stdout)]
        assert lines == [b'{"a":1}', b'{"b":2}']

    @pytest.mark.asyncio
    async def test_none_stdout_yields_nothing(self):
        lines = [line async for line in _iter_stdout(None)]
        assert lines == []

    @pytest.mark.asyncio
    async def test_partial_line_flushed_at_end(self):
        # Chunk with no trailing newline
        stdout = ChunkedStdout([b'{"a":1}'])
        lines = [line async for line in _iter_stdout(stdout)]
        assert lines == [b'{"a":1}']


# ── run_subprocess_jsonl tests ────────────────────────────────────────────────


class TestRunSubprocessJsonl:
    def _make_proc(self, lines: list[bytes], stderr_text: str = "", rc: int = 0):
        stdout = MockStdout(lines)
        return MockProcess(stdout, stderr_text=stderr_text, wait_returncode=rc)

    @pytest.mark.asyncio
    async def test_yields_parsed_json_events(self, monkeypatch):
        proc = self._make_proc([b'{"type":"text"}\n', b'{"type":"result"}\n'])

        async def mock_create(*args, **kwargs):
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_create)

        events = []
        async for ev in run_subprocess_jsonl(["cmd"], "/tmp"):
            events.append(ev)

        dicts = [e for e in events if isinstance(e, dict)]
        exits = [e for e in events if isinstance(e, SubprocessExit)]

        assert dicts == [{"type": "text"}, {"type": "result"}]
        assert len(exits) == 1
        assert exits[0].returncode == 0

    @pytest.mark.asyncio
    async def test_non_json_lines_are_skipped(self, monkeypatch):
        proc = self._make_proc([b"not json\n", b'{"ok":true}\n'])

        async def mock_create(*args, **kwargs):
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_create)

        events = [e async for e in run_subprocess_jsonl(["cmd"], "/tmp")]
        dicts = [e for e in events if isinstance(e, dict)]
        assert dicts == [{"ok": True}]

    @pytest.mark.asyncio
    async def test_subprocess_exit_carries_returncode_and_stderr(self, monkeypatch):
        proc = self._make_proc([], stderr_text="something went wrong", rc=1)

        async def mock_create(*args, **kwargs):
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_create)

        events = [e async for e in run_subprocess_jsonl(["cmd"], "/tmp")]
        exits = [e for e in events if isinstance(e, SubprocessExit)]
        assert len(exits) == 1
        assert exits[0].returncode == 1
        assert exits[0].stderr == b"something went wrong"

    @pytest.mark.asyncio
    async def test_stdin_data_written_and_closed(self, monkeypatch):
        stdin = MockStdin()
        proc = self._make_proc([])
        proc.stdin = stdin

        async def mock_create(*args, **kwargs):
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_create)

        [e async for e in run_subprocess_jsonl(["cmd"], "/tmp", stdin_data=b"hello")]
        assert stdin.written == [b"hello"]
        assert stdin.closed

    @pytest.mark.asyncio
    async def test_none_stdin_data_does_not_write(self, monkeypatch):
        stdin = MockStdin()
        proc = self._make_proc([])
        proc.stdin = stdin

        async def mock_create(*args, **kwargs):
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_create)

        [e async for e in run_subprocess_jsonl(["cmd"], "/tmp", stdin_data=None)]
        assert stdin.written == []

    @pytest.mark.asyncio
    async def test_process_killed_on_early_exit(self, monkeypatch):
        """If generator is closed before exhausting stdout, process is killed."""
        proc = self._make_proc([b'{"a":1}\n', b'{"b":2}\n'])

        async def mock_create(*args, **kwargs):
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_create)

        gen = run_subprocess_jsonl(["cmd"], "/tmp")
        await gen.__anext__()  # consume first event
        await gen.aclose()  # close early

        assert proc.killed

    @pytest.mark.asyncio
    async def test_empty_stdout_yields_only_exit(self, monkeypatch):
        proc = self._make_proc([])

        async def mock_create(*args, **kwargs):
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_create)

        events = [e async for e in run_subprocess_jsonl(["cmd"], "/tmp")]
        assert len(events) == 1
        assert isinstance(events[0], SubprocessExit)
        assert events[0].returncode == 0
