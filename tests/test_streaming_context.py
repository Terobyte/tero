"""Tests for streaming context-management helpers."""

from io import StringIO
import sys

from src import streaming as s
from src.providers.message_adapter import AdaptedMessage, ToolResultBlock, ToolUseBlock


def capture(fn, *args, **kwargs):
    """Capture stdout from a streaming helper."""
    buf = StringIO()
    old = sys.stdout
    try:
        sys.stdout = buf
        result = fn(*args, **kwargs)
    finally:
        sys.stdout = old
    return result, buf.getvalue()


def test_print_compact_triggered():
    """Compact notification should mention token usage."""
    _, out = capture(s.print_compact_triggered, 91_000, 110_000)
    assert "91" in out or "compact" in out.lower()


def test_print_continuation_started():
    """Continuation notification should include role and attempt counters."""
    _, out = capture(s.print_continuation_started, "player", 1, 2)
    assert "1" in out and "2" in out
    assert "player" in out.lower() or "continuation" in out.lower()


def test_stream_messages_prints_codex_tool_use():
    """Codex tool-use blocks should render as tool lines and count tool usage."""
    msg = AdaptedMessage(
        role="assistant",
        content=[ToolUseBlock(name="shell", input={"command": "pwd"})],
        type="tool_use",
    )

    tools_used, out = capture(s.stream_messages, msg)

    assert tools_used == 1
    assert "[tool]" in out
    assert "shell" in out
    assert "pwd" in out


def test_stream_messages_prints_codex_tool_result_in_verbose():
    """Codex tool-result blocks should render in verbose mode."""
    msg = AdaptedMessage(
        role="tool",
        content=[ToolResultBlock(
            tool_use_id="cmd-123",
            content="[exit code: 0]\n/home/user/project",
        )],
        type="tool_result",
    )

    tools_used, out = capture(s.stream_messages, msg, verbose=True)

    assert tools_used == 0
    assert "[result]" in out
    assert "[exit code: 0]" in out


def test_stream_messages_prints_codex_tool_error_without_verbose():
    """Errored Codex tool results should still be surfaced outside verbose mode."""
    msg = AdaptedMessage(
        role="tool",
        content=[ToolResultBlock(
            tool_use_id="cmd-456",
            content="[exit code: 1]\npermission denied",
            is_error=True,
        )],
        type="tool_result",
    )

    tools_used, out = capture(s.stream_messages, msg, verbose=False)

    assert tools_used == 0
    assert "[result]" in out
    assert "permission denied" in out
