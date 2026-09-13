"""Tests for the Cursor Headless (`agent -p`) provider."""

from unittest.mock import patch

import pytest

from src.errors import ProviderError
from src.providers import create_provider
from src.providers.cursor import CursorConfig, CursorProvider
from src.providers.registry import ProviderRegistry


def test_create_provider_returns_cursor():
    provider = create_provider("cursor")
    assert isinstance(provider, CursorProvider)
    assert provider.config.default_model == "composer-2.5"
    assert provider.config.force is True
    assert provider.config.trust is True
    assert provider.config.command == "agent"


def test_registry_creates_cursor():
    provider = ProviderRegistry().get("cursor")
    assert isinstance(provider, CursorProvider)
    assert provider.config.default_model == "composer-2.5"


def test_build_command_uses_print_force_trust_and_workspace():
    provider = CursorProvider(CursorConfig())
    cmd = provider._build_command(
        model="composer-2.5",
        working_dir="/tmp/work",
        prompt="do the thing",
    )
    assert cmd[:2] == ["agent", "-p"]
    assert "--force" in cmd
    assert "--trust" in cmd
    assert "--worktree" not in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--workspace") + 1] == "/tmp/work"
    assert cmd[cmd.index("--model") + 1] == "composer-2.5"
    assert cmd[-1] == "do the thing"
    assert "-p" in cmd and cmd[cmd.index("-p") + 1] != "do the thing"


def test_adapt_assistant_and_result_events():
    provider = CursorProvider()
    assistant = provider._adapt_cursor_event(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}],
            },
        }
    )
    assert assistant is not None
    assert assistant.role == "assistant"
    assert assistant.content[0].text == "hello"

    result = provider._adapt_cursor_event(
        {"type": "result", "subtype": "success", "is_error": False, "result": "hello"}
    )
    assert result is not None
    assert result.type == "result"


def test_adapt_tool_call_started_and_completed():
    provider = CursorProvider()
    started = provider._adapt_cursor_event(
        {
            "type": "tool_call",
            "subtype": "started",
            "call_id": "c1",
            "tool_call": {"readToolCall": {"args": {"path": "README.md"}}},
        }
    )
    assert started is not None
    assert started.type == "tool_use"
    assert started.content[0].name == "read"
    assert started.content[0].input == {"path": "README.md"}

    completed = provider._adapt_cursor_event(
        {
            "type": "tool_call",
            "subtype": "completed",
            "call_id": "c1",
            "tool_call": {
                "readToolCall": {
                    "args": {"path": "README.md"},
                    "result": {"success": {"content": "# hi", "totalLines": 1}},
                }
            },
        }
    )
    assert completed is not None
    assert completed.type == "tool_result"
    assert completed.content[0].is_error is False
    assert "# hi" in completed.content[0].content


def test_skips_partial_output_duplicates():
    provider = CursorProvider()
    skipped = provider._adapt_cursor_event(
        {
            "type": "assistant",
            "timestamp_ms": 1,
            "model_call_id": "x",
            "message": {"content": [{"type": "text", "text": "dup"}]},
        }
    )
    assert skipped is None


def test_error_event_raises():
    provider = CursorProvider()
    with pytest.raises(ProviderError, match="cursor CLI error"):
        provider._adapt_cursor_event({"type": "error", "message": "boom"})


def test_check_ready_requires_binary():
    provider = CursorProvider()
    with patch("src.providers.cursor.shutil.which", return_value=None):
        ok, reason = provider.check_ready()
    assert ok is False
    assert "agent" in reason
