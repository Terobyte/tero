"""Tests for Codex token tracking from JSONL usage field."""

import asyncio
import json
from unittest.mock import patch

import pytest
from src.providers.codex import CodexProvider, CodexConfig


def _make_jsonl_lines(content: str, input_tokens: int) -> list[bytes]:
    """Create JSONL lines for testing the new Codex CLI format."""
    return [
        (json.dumps({"type": "thread.started"}) + "\n").encode(),
        (json.dumps({"type": "turn.started"}) + "\n").encode(),
        (json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": content}
        }) + "\n").encode(),
        (json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": input_tokens, "output_tokens": 50}
        }) + "\n").encode(),
    ]


class MockStdout:
    """Mock stdout with JSONL lines."""

    def __init__(self, lines: list[bytes]):
        self.lines = lines
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.lines):
            raise StopAsyncIteration
        line = self.lines[self.index]
        self.index += 1
        return line


class MockStderr:
    """Mock stderr."""

    def __init__(self, text: str = ""):
        self.text = text

    async def read(self):
        return self.text.encode()


class MockStdin:
    """Mock stdin writer compatible with asyncio subprocess streams."""

    def __init__(self):
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes):
        self.writes.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class MockProcess:
    """Mock subprocess."""

    def __init__(self, lines: list[bytes], stderr_text: str = "", wait_returncode: int = 0):
        self.stdout = MockStdout(lines)
        self.stderr = MockStderr(stderr_text)
        self.returncode = None
        self.stdin = MockStdin()
        self.killed = False
        self._wait_returncode = wait_returncode

    async def wait(self):
        if self.returncode is None:
            self.returncode = self._wait_returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class BlockingStdout:
    """Yield one line, then hang forever until the generator is closed."""

    def __init__(self, first_line: bytes):
        self.first_line = first_line
        self.emitted = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.emitted:
            self.emitted = True
            return self.first_line
        await asyncio.Future()


class ChunkedStdout:
    """Mock stdout that only supports chunked reads."""

    def __init__(self, payload: bytes, chunk_size: int = 32768):
        self.payload = payload
        self.chunk_size = chunk_size
        self.offset = 0

    async def read(self, n: int = -1):
        if self.offset >= len(self.payload):
            return b""
        if n is None or n < 0:
            n = len(self.payload) - self.offset
        size = min(n, self.chunk_size, len(self.payload) - self.offset)
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += size
        return chunk


@pytest.mark.asyncio
async def test_last_input_tokens_stored():
    """Codex provider stores input_tokens from JSONL turn.completed.usage field."""
    provider = CodexProvider(CodexConfig())
    lines = _make_jsonl_lines("hello", input_tokens=85000)
    mock_process = MockProcess(lines)

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        chunks = []
        async for c in provider.run("hi", "", ".", 10):
            chunks.append(c)

    assert provider._last_input_tokens == 85000
    assert provider._last_output_tokens == 50


@pytest.mark.asyncio
async def test_usage_counters_reset_when_next_run_has_no_usage_event():
    """A later turn without usage data must not inherit the previous turn's counters."""
    provider = CodexProvider(CodexConfig())
    first_process = MockProcess(_make_jsonl_lines("hello", input_tokens=85_000))
    second_process = MockProcess([
        (json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "partial"},
        }) + "\n").encode(),
    ])
    processes = [first_process, second_process]

    async def mock_create_subprocess(*args, **kwargs):
        return processes.pop(0)

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        async for _chunk in provider.run("first", "", ".", 10):
            pass
        async for _chunk in provider.run("second", "", ".", 10):
            pass

    assert provider._last_input_tokens == 0
    assert provider._last_output_tokens == 0


@pytest.mark.asyncio
async def test_tool_execution_becomes_tool_use_and_result():
    """Codex provider should surface command_execution as tool-use and tool-result messages."""
    provider = CodexProvider(CodexConfig())
    lines = [
        (json.dumps({"type": "thread.started"}) + "\n").encode(),
        (json.dumps({"type": "turn.started"}) + "\n").encode(),
        (json.dumps({
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "id": "cmd-123",
                "command": "pwd"
            }
        }) + "\n").encode(),
        (json.dumps({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": "cmd-123",
                "command": "pwd",
                "exit_code": 0,
                "aggregated_output": "/home/user/project"
            }
        }) + "\n").encode(),
        (json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 123, "output_tokens": 7}
        }) + "\n").encode(),
    ]
    mock_process = MockProcess(lines)

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        chunks = []
        async for chunk in provider.run("inspect", "", ".", 10):
            chunks.append(chunk)

    # Find tool_use and tool_result blocks
    tool_use_blocks = []
    tool_result_blocks = []
    for chunk in chunks:
        for block in getattr(chunk, "content", []) or []:
            if getattr(block, "type", "") == "tool_use":
                tool_use_blocks.append(block)
            elif getattr(block, "type", "") == "tool_result":
                tool_result_blocks.append(block)

    assert len(tool_use_blocks) == 1
    assert tool_use_blocks[0].name == "shell"
    assert tool_use_blocks[0].input == {"command": "pwd"}

    assert len(tool_result_blocks) == 1
    assert "/home/user/project" in tool_result_blocks[0].content

    assert provider._last_input_tokens == 123


@pytest.mark.asyncio
async def test_stderr_is_emitted_without_crashing_session():
    """Codex stderr should surface as a non-fatal assistant message."""
    provider = CodexProvider(CodexConfig())
    lines = _make_jsonl_lines("hello", input_tokens=12)
    mock_process = MockProcess(lines, stderr_text="warning: config fallback")

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        chunks = []
        async for chunk in provider.run("hi", "", ".", 10):
            chunks.append(chunk)

    assert any(
        "[codex stderr] warning: config fallback" in chunk.get_text_content()
        for chunk in chunks
    )


@pytest.mark.asyncio
async def test_closing_stream_early_kills_subprocess():
    """Generator cleanup should kill a still-running codex subprocess."""
    provider = CodexProvider(CodexConfig())
    first_line = (json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "hello"},
    }) + "\n").encode()
    mock_process = MockProcess([], wait_returncode=0)
    mock_process.stdout = BlockingStdout(first_line)

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        stream = provider.run("hi", "", ".", 10)
        first_chunk = await stream.__anext__()
        assert first_chunk.get_text_content() == "hello"
        await stream.aclose()

    assert mock_process.killed is True


@pytest.mark.asyncio
async def test_run_handles_long_jsonl_lines_from_chunked_stdout():
    """Codex provider should not rely on readline() for oversized JSONL events."""
    provider = CodexProvider(CodexConfig())
    huge_text = "X" * 70_000
    payload = b"".join([
        (json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": huge_text},
        }) + "\n").encode(),
        (json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 11, "output_tokens": 22},
        }) + "\n").encode(),
    ])
    mock_process = MockProcess([])
    mock_process.stdout = ChunkedStdout(payload, chunk_size=4096)

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        chunks = []
        async for chunk in provider.run("hi", "", ".", 10):
            chunks.append(chunk)

    assert any(chunk.get_text_content() == huge_text for chunk in chunks)
    assert provider._last_input_tokens == 11
    assert provider._last_output_tokens == 22


def test_build_command_includes_approval_policy_when_not_bypassing():
    """Native Codex exec should honor configured approval policy."""
    provider = CodexProvider(CodexConfig(
        bypass_approvals=False,
        full_auto=False,
        sandbox_mode="read-only",
        approval_policy="on-request",
    ))

    cmd = provider._build_command(model="", working_dir="/tmp/work")

    assert "-s" in cmd
    assert "read-only" in cmd
    assert "-a" in cmd
    assert "on-request" in cmd


def test_build_command_disables_tools_for_text_only_runs():
    """Text-only Codex runs should not expose shell or multi-agent tools."""
    provider = CodexProvider(CodexConfig(
        bypass_approvals=True,
        disabled_features=["undo"],
    ))

    cmd = provider._build_command(
        model="",
        working_dir="/tmp/work",
        disable_tools=True,
    )

    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert "-s" in cmd
    assert "read-only" in cmd
    assert "-a" in cmd
    assert "never" in cmd
    assert cmd.count("--disable") >= 4
    assert "undo" in cmd
    assert "shell_tool" in cmd
    assert "multi_agent" in cmd
    assert "apps" in cmd


@pytest.mark.asyncio
async def test_review_usage_counters_reset_between_runs():
    """Native review should also clear stale usage when the next run has no usage event."""
    provider = CodexProvider(CodexConfig())
    first_process = MockProcess(_make_jsonl_lines("review ok", input_tokens=321))
    second_process = MockProcess([
        (json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "partial review"},
        }) + "\n").encode(),
    ])
    processes = [first_process, second_process]

    async def mock_create_subprocess(*args, **kwargs):
        return processes.pop(0)

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        async for _chunk in provider.run_review(".", "first review"):
            pass
        async for _chunk in provider.run_review(".", "second review"):
            pass

    assert provider._last_input_tokens == 0
    assert provider._last_output_tokens == 0
