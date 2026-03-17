"""Tests for context_manager.py utilities."""

from src.context_manager import _build_compact_summary, _build_continuation_prompt
from src.providers.message_adapter import AdaptedMessage, TextBlock, ToolUseBlock, ToolResultBlock


def _make_assistant_msg(text: str) -> AdaptedMessage:
    return AdaptedMessage(role="assistant", content=[TextBlock(text=text)])


def _make_tool_result_msg() -> AdaptedMessage:
    return AdaptedMessage(role="tool", content=[ToolResultBlock(
        tool_use_id="x", content="big file content " * 500
    )])


def test_compact_summary_keeps_assistant_text():
    """Compact summary preserves assistant text, drops tool results."""
    msgs = [
        _make_assistant_msg("I read config.py and found CcgEnv."),
        _make_tool_result_msg(),
        _make_assistant_msg("Step 1 done: added from_env_b()"),
    ]
    summary = _build_compact_summary(msgs)
    assert "CcgEnv" in summary
    assert "Step 1 done" in summary
    assert "big file content" not in summary  # tool results dropped


def test_compact_summary_empty_messages():
    """Compact summary returns empty string for empty list."""
    assert _build_compact_summary([]) == ""


def test_continuation_prompt_player():
    """Continuation prompt for player includes PHASE_COMPLETE marker."""
    prompt = _build_continuation_prompt("Did step 1 and 2.", role="player")
    assert "PHASE_COMPLETE" in prompt
    assert "Did step 1 and 2." in prompt


def test_continuation_prompt_coach():
    """Continuation prompt for coach includes IMPLEMENTATION_APPROVED marker."""
    prompt = _build_continuation_prompt("Found null issue.", role="coach")
    assert "IMPLEMENTATION_APPROVED" in prompt or "verdict" in prompt.lower()
    assert "Found null issue." in prompt
