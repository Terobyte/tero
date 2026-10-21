"""Red tests for provider bugs B44/B45/B46/B52 (src/providers/).

Follows the approach of tests/test_hunt_providers.py (patched
run_subprocess_jsonl) and the unit-test style of per-provider test files.
"""

from __future__ import annotations

import pytest

import src.providers.claude_native as claude_native_mod
from src.errors import ProviderError
from src.providers.claude_native import ClaudeNativeProvider
from src.providers.codex import CodexProvider
from src.providers.gemini import GeminiConfig, GeminiProvider
from src.providers.opencode import OpenCodeConfig, OpenCodeProvider
from src.providers.subprocess_runner import SubprocessExit


def _patch_runner(monkeypatch, module, events, exit_code=0, stderr=b""):
    async def _fake(cmd, working_dir, env=None, stdin_data=None, stall_timeout=0):
        for ev in events:
            yield ev
        yield SubprocessExit(returncode=exit_code, stderr=stderr)

    monkeypatch.setattr(module, "run_subprocess_jsonl", _fake)


async def _drain(provider, *args, **kwargs):
    out = []
    async for m in provider.run(*args, **kwargs):
        out.append(m)
    return out


def _make_gemini(**overrides):
    return GeminiProvider(GeminiConfig(**overrides))


def _make_opencode(**overrides):
    return OpenCodeProvider(OpenCodeConfig(**overrides))


# ---------------------------------------------------------------- B44


@pytest.mark.asyncio
async def test_b44_claude_native_non_utf8_stderr_raises_provider_error(
    monkeypatch, tmp_path
):
    """SubprocessExit with non-UTF-8 stderr → ProviderError with replaced bytes,
    not UnicodeDecodeError."""
    _patch_runner(
        monkeypatch,
        claude_native_mod,
        events=[],
        exit_code=1,
        stderr=b"\xff\xfe boom",
    )
    provider = ClaudeNativeProvider()

    with pytest.raises(Exception) as exc_info:
        await _drain(provider, "p", "sys", str(tmp_path))

    assert isinstance(exc_info.value, ProviderError), (
        f"B44: non-UTF-8 stderr produced {exc_info.value!r}, "
        "expected ProviderError like with valid stderr"
    )
    assert "boom" in str(exc_info.value), (
        f"B44: stderr text lost in ProviderError message: {exc_info.value!r}"
    )
    assert "\ufffd" in str(exc_info.value), (
        f"B44: expected replaced bytes in message: {exc_info.value!r}"
    )


@pytest.mark.asyncio
async def test_b44b_claude_native_valid_stderr_still_provider_error(
    monkeypatch, tmp_path
):
    """Sanity control: valid-UTF-8 stderr exit already raises ProviderError with
    the text, so B44 is only about the decode crash, not the error type."""
    _patch_runner(
        monkeypatch,
        claude_native_mod,
        events=[],
        exit_code=1,
        stderr=b"some fatal error",
    )
    provider = ClaudeNativeProvider()

    with pytest.raises(ProviderError) as exc_info:
        await _drain(provider, "p", "sys", str(tmp_path))

    assert "some fatal error" in str(exc_info.value)


# ---------------------------------------------------------------- B45


def test_b45_gemini_result_event_stats_null():
    """gemini result event with "stats": null → zero tokens, no AttributeError."""
    provider = _make_gemini()
    event = {"type": "result", "status": "success", "stats": None}

    try:
        msg = provider._adapt_gemini_event(event)
    except (AttributeError, TypeError) as exc:
        assert False, (
            f"B45: result event with stats=null crashed: {exc!r} — "
            "expected it treated as an empty dict (zero tokens)"
        )

    assert msg is not None, "B45: result event with stats=null should still yield the result message"
    assert msg.stop_reason == "end_turn"
    assert provider._last_input_tokens == 0, (
        f"B45: expected zero input tokens for stats=null, got {provider._last_input_tokens}"
    )
    assert provider._last_output_tokens == 0, (
        f"B45: expected zero output tokens for stats=null, got {provider._last_output_tokens}"
    )


def test_b45_codex_turn_completed_usage_null():
    """codex turn.completed event with "usage": null → result message, no crash."""
    provider = CodexProvider()
    event = {"type": "turn.completed", "usage": None}

    try:
        msg = provider._adapt_codex_event(event)
    except (AttributeError, TypeError) as exc:
        assert False, (
            f"B45: turn.completed with usage=null crashed: {exc!r} — "
            "expected it treated as an empty dict (zero tokens)"
        )

    assert msg is not None, (
        f"B45: usage=null dropped the result message: {msg!r}"
    )
    assert msg.type == "result", f"B45: expected result message, got {msg.type}"
    assert provider._last_input_tokens == 0
    assert provider._last_output_tokens == 0


def test_b45_opencode_step_finish_tokens_null():
    """opencode step_finish event with "tokens": null → zero tokens, no AttributeError."""
    provider = _make_opencode()
    event = {"type": "step_finish", "part": {"tokens": None}}

    try:
        result = provider._adapt_opencode_event(event)
    except (AttributeError, TypeError) as exc:
        assert False, (
            f"B45: step_finish with tokens=null crashed: {exc!r} — "
            "expected it treated as an empty dict (zero tokens)"
        )

    assert result is None, "B45: step_finish is a side-effect-only event, should yield no message"
    assert provider._last_input_tokens == 0
    assert provider._last_output_tokens == 0


# ---------------------------------------------------------------- B46


def test_b46_opencode_tool_state_output_null():
    """opencode tool_use part with state.output = null → empty tool result, no TypeError."""
    provider = _make_opencode()
    event = {
        "type": "tool_use",
        "part": {
            "type": "tool",
            "tool": "read",
            "callID": "call-null-out",
            "state": {
                "status": "completed",
                "input": {"filePath": "src/foo.py"},
                "output": None,
                "metadata": {"exit": 0},
            },
        },
    }

    try:
        messages = provider._adapt_opencode_event(event)
    except (TypeError, AttributeError) as exc:
        assert False, (
            f"B46: tool_use with state.output=null crashed: {exc!r} — "
            "expected empty output with no exception"
        )

    assert messages is not None, "B46: expected tool_use/tool_result pair"
    assert isinstance(messages, list) and len(messages) == 2, (
        f"B46: expected 2 messages, got {messages!r}"
    )
    tool_result = messages[1].content[0]
    assert tool_result.type == "tool_result"
    assert tool_result.content == "", (
        f"B46: expected empty output for state.output=null, got {tool_result.content!r}"
    )
    assert tool_result.is_error is False


# ---------------------------------------------------------------- B52


def test_b52_gemini_result_error_status_treated_as_error():
    """gemini result event with status="error" must follow the same error path
    as a type:"error" event and raise ProviderError."""
    provider = _make_gemini()
    event = {
        "type": "result",
        "status": "error",
        "message": "model exploded mid-run",
    }

    with pytest.raises(ProviderError) as exc_info:
        provider._adapt_gemini_event(event)

    assert exc_info.value is not None
