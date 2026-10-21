"""Canonical effort helpers and provider CLI mapping."""

from src.providers.claude_native import ClaudeNativeProvider
from src.providers.codex import CodexConfig, CodexProvider
from src.providers.cursor import CursorProvider
from src.providers.effort import (
    EFFORT_LABELS,
    cursor_model_arg,
    effort_field_for,
    normalize_effort,
    provider_supports_effort,
)
from src.providers.gemini import GeminiProvider
from src.providers.opencode import OpenCodeProvider


def test_normalize_effort_accepts_canon_and_aliases():
    assert normalize_effort("High") == "high"
    assert normalize_effort("extra high") == "xhigh"
    assert normalize_effort("nope") == ""
    assert normalize_effort("") == ""


def test_gemini_has_no_effort_flag():
    assert provider_supports_effort("gemini") is False
    assert provider_supports_effort("claude") is True
    assert provider_supports_effort("muse") is True
    assert provider_supports_effort("codex") is True
    assert provider_supports_effort("cursor") is True
    assert provider_supports_effort("opencode") is True


def test_effort_field_follows_model_field_name():
    assert effort_field_for("player_model") == "player_effort"
    assert effort_field_for("batch_judge_model") == "batch_judge_effort"


def test_cursor_model_arg_wraps_effort():
    assert cursor_model_arg("cursor-grok-4.6", "high") == "cursor-grok-4.6[effort=high]"
    assert cursor_model_arg("composer-2.5", "") == "composer-2.5"
    assert cursor_model_arg("composer-2.5[effort=low]", "high") == "composer-2.5[effort=low]"


def test_claude_command_passes_effort_flag():
    cmd = ClaudeNativeProvider()._build_command(model="claude-opus-5", effort="xhigh")
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_claude_command_omits_effort_when_empty():
    cmd = ClaudeNativeProvider()._build_command(model="claude-opus-5")
    assert "--effort" not in cmd


def test_codex_run_effort_overrides_factory_default():
    provider = CodexProvider(CodexConfig(model_reasoning_effort="max"))
    cmd = provider._build_command(model="gpt-5.6-terra", effort="medium")
    assert "-c" in cmd
    assert "model_reasoning_effort=medium" in cmd
    assert "model_reasoning_effort=max" not in cmd


def test_codex_empty_run_effort_keeps_factory_default():
    provider = CodexProvider(CodexConfig(model_reasoning_effort="max"))
    cmd = provider._build_command(model="gpt-5.6-terra")
    assert "model_reasoning_effort=max" in cmd


def test_cursor_command_encodes_effort_in_model():
    cmd = CursorProvider()._build_command(
        model="cursor-grok-4.6",
        effort="high",
        prompt="hi",
    )
    assert cmd[cmd.index("--model") + 1] == "cursor-grok-4.6[effort=high]"


def test_opencode_command_passes_variant():
    cmd = OpenCodeProvider()._build_command(model="opencode/mimo-v2-pro-free", effort="max")
    assert cmd[cmd.index("--variant") + 1] == "max"


def test_gemini_command_ignores_effort():
    cmd = GeminiProvider()._build_command(model="gemini-3.8-flash", prompt="hi", effort="high")
    assert "--effort" not in cmd
    assert "--variant" not in cmd
    joined = " ".join(cmd)
    assert "effort=" not in joined


def test_effort_labels_cover_canon():
    assert set(EFFORT_LABELS) == {"low", "medium", "high", "xhigh", "max"}
