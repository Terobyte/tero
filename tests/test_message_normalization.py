"""Tests for normalize_message() in src/providers/message_adapter.py."""

import pytest

from src.providers.message_adapter import (
    AdaptedMessage,
    TextBlock,
    ToolUseBlock,
    normalize_message,
)


class TestNormalizeNoneAndEmpty:
    def test_none_returns_none(self):
        assert normalize_message(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_message("") is None


class TestNormalizeString:
    def test_bare_string_wraps_in_text_block(self):
        result = normalize_message("hello world")
        assert isinstance(result, AdaptedMessage)
        assert result.role == "assistant"
        assert result.get_text_content() == "hello world"

    def test_multiline_string_preserved(self):
        text = "line one\nline two"
        result = normalize_message(text)
        assert result.get_text_content() == text


class TestNormalizeAdaptedMessage:
    def test_adapted_message_passthrough(self):
        msg = AdaptedMessage(role="assistant", content=[TextBlock(text="original")])
        result = normalize_message(msg)
        assert result is msg

    def test_adapted_message_text_preserved(self):
        msg = AdaptedMessage(role="assistant", content=[TextBlock(text="test")])
        assert normalize_message(msg).get_text_content() == "test"


class TestNormalizeDict:
    def test_text_event_dict(self):
        event = {"type": "text", "text": "from cli"}
        result = normalize_message(event)
        assert result is not None
        assert "from cli" in result.get_text_content()

    def test_result_event_dict(self):
        event = {"type": "result", "result": "final answer"}
        result = normalize_message(event)
        assert result is not None
        assert "final answer" in result.get_text_content()

    def test_assistant_message_with_content_blocks(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "nested content"}],
                "stop_reason": "end_turn",
                "model": "claude-3",
            },
        }
        result = normalize_message(event)
        assert result is not None
        assert "nested content" in result.get_text_content()

    def test_ignored_event_type_returns_none(self):
        # "init" events have no text content
        event = {"type": "init", "session_id": "abc123"}
        result = normalize_message(event)
        assert result is None

    def test_tool_use_dict(self):
        event = {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}, "id": "t1"}
        result = normalize_message(event)
        assert result is not None
        assert result.role == "assistant"


class TestNormalizeSdkLike:
    """Test SDK-duck-typed objects."""

    def test_assistant_message_type(self):
        AssistantMessage = type("AssistantMessage", (), {"content": "sdk text", "model": "claude-sonnet"})
        m = AssistantMessage()
        result = normalize_message(m)
        assert result is not None
        assert result.get_text_content() == "sdk text"

    def test_role_bearing_object(self):
        import types
        m = types.SimpleNamespace(
            role="assistant", content="plain text",
            stop_reason="end_turn", model="x", type="assistant",
        )
        result = normalize_message(m)
        assert result is not None
        assert result.get_text_content() == "plain text"

    def test_result_message_type_returns_none(self):
        ResultMessage = type("ResultMessage", (), {"result": "final"})
        m = ResultMessage()
        result = normalize_message(m)
        assert result is None


class TestNormalizeIntegration:
    """Verify debugger._extract_text and feedback._extract_text_from_message use normalize_message."""

    def test_debugger_extract_text_bare_string(self):
        from src.debugger import Debugger

        parts: list[str] = []
        Debugger._extract_text("bare text message", parts)
        assert parts == ["bare text message"]

    def test_debugger_extract_text_adapted_message(self):
        from src.debugger import Debugger

        msg = AdaptedMessage(role="assistant", content=[TextBlock(text="found bug")])
        parts: list[str] = []
        Debugger._extract_text(msg, parts)
        assert parts == ["found bug"]

    def test_debugger_extract_text_dict_event(self):
        from src.debugger import Debugger

        event = {"type": "text", "text": "analysis result"}
        parts: list[str] = []
        Debugger._extract_text(event, parts)
        assert parts == ["analysis result"]

    def test_feedback_extract_text_from_message(self):
        from src.feedback import _extract_text_from_message

        msg = AdaptedMessage(role="assistant", content=[TextBlock(text="APPROVED")])
        assert _extract_text_from_message(msg) == "APPROVED"

    def test_feedback_extract_text_bare_string(self):
        from src.feedback import _extract_text_from_message

        assert _extract_text_from_message("raw text") == "raw text"
