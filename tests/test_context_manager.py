"""Tests for context_manager.py utilities."""

from types import SimpleNamespace

import pytest

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


def test_continuation_prompt_player_step_mode():
    """Step-mode continuation should keep the regular completion report shape."""
    prompt = _build_continuation_prompt("Did step 1 and 2.", role="player")
    assert "PHASE_COMPLETE" not in prompt
    assert "Did step 1 and 2." in prompt
    assert "You still have access to filesystem inspection" in prompt


def test_continuation_prompt_player_batch_mode():
    """Batch-mode continuation should preserve PHASE_COMPLETE requirements."""
    prompt = _build_continuation_prompt(
        "Did step 1 and 2.",
        role="player",
        require_phase_complete=True,
    )
    assert "PHASE_COMPLETE" in prompt
    assert "Did step 1 and 2." in prompt


def test_continuation_prompt_coach():
    """Continuation prompt for coach includes IMPLEMENTATION_APPROVED marker."""
    prompt = _build_continuation_prompt("Found null issue.", role="coach")
    assert "IMPLEMENTATION_APPROVED" in prompt or "verdict" in prompt.lower()
    assert "Found null issue." in prompt


@pytest.mark.asyncio
async def test_compact_codex_context_uses_config_working_dir_and_player_model():
    """Codex compaction should run inside the configured workspace."""
    from src.context_manager import _compact_codex_context

    captured = {}

    class FakeChunk:
        def __init__(self, text: str):
            self.text = text

    class FakeProvider:
        async def run(self, prompt, system_prompt, working_dir, max_turns=30, model=""):
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            captured["working_dir"] = working_dir
            captured["max_turns"] = max_turns
            captured["model"] = model
            yield FakeChunk("condensed summary")

    messages = [_make_assistant_msg("Step 1 done: updated config")]
    config = SimpleNamespace(
        working_dir="/tmp/project",
        player_model="gpt-5.4-high",
        coach_model="",
    )

    summary = await _compact_codex_context(FakeProvider(), messages, config)

    assert summary == "condensed summary"
    assert captured["working_dir"] == "/tmp/project"
    assert captured["model"] == "gpt-5.4-high"
