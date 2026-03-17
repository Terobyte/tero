"""Tests for PreCompact hook in CCG provider."""

import pytest
from src.providers.ccg import _make_compact_hooks


def test_hook_structure():
    """PreCompact hook has correct structure for SDK."""
    hooks = _make_compact_hooks(110_000, 0.85)
    assert "PreCompact" in hooks
    matchers = hooks["PreCompact"]
    assert len(matchers) == 1
    matcher = matchers[0]
    assert "hooks" in matcher
    assert len(matcher["hooks"]) == 1
    assert callable(matcher["hooks"][0])


@pytest.mark.asyncio
async def test_hook_returns_correct_keys():
    """PreCompact hook returns required keys for SDK."""
    hooks = _make_compact_hooks(110_000, 0.85)
    fn = hooks["PreCompact"][0]["hooks"][0]

    # hook takes 3 args: hook_input, tool_name, context
    result = await fn({}, None, {})
    assert "continue_" in result
    assert result["continue_"] is True
    assert "systemMessage" in result
    assert "93" in result["systemMessage"]  # 93k threshold mentioned
