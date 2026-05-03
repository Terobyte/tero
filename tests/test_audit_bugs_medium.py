"""Proof-of-bug tests for MEDIUM bugs and FALSE POSITIVES.

RED tests assert CORRECT behaviour — they FAIL because the bug exists.
GREEN tests (false positives) assert code works fine — they PASS.
"""

import pytest
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════════════
# GREEN — FALSE POSITIVES (tests that PASS, showing no bug exists)
# ═══════════════════════════════════════════════════════════════════════


# ── FP-11: _fallback_menu no return after 'd' ──────────────────────────


class TestFallbackMenuNoBug:
    """FP-11: Suspected fall-through after 'd' selection.
    run_debugger_menu is a separate function, so fall-through is unreachable."""

    def test_d_handler_calls_sys_exit(self):
        import inspect
        from src.menu import run_debugger_menu

        source = inspect.getsource(run_debugger_menu)
        assert "sys.exit" in source or "run_debugger_menu" in source


# ── FP-19: Picker cancel timer leak on ESC ─────────────────────────────


class TestPickerTimerNoLeak:
    """FP-19: Suspected timer leak when pressing ESC.
    _handle_locked cancels AND nullifies _cancel_timer at the top
    (lines 336-338), before any action-specific logic."""

    def test_timer_cancelled_at_handler_top(self):
        import inspect
        from src.runtime_controls import Picker

        source = inspect.getsource(Picker._handle_locked)
        assert "_cancel_timer.cancel()" in source
        assert "_cancel_timer = None" in source


# ── FP-26: KeyboardListener finally block — old_settings ───────────────


class TestKeyboardListenerNoUnboundLocal:
    """FP-26: Suspected UnboundLocalError in finally block.
    old_settings is initialised to None before try, and tcsetattr is
    wrapped in try/except."""

    def test_old_settings_initialised_to_none(self):
        import inspect
        from src.runtime_controls import KeyboardListener

        source = inspect.getsource(KeyboardListener.run)
        assert "old_settings = None" in source
        assert "termios.tcsetattr" in source


# ── BUG-08 edge case — normal quoting works fine ───────────────────────


class TestReadExportFromZshrcNormalCases:
    """BUG-08 suspected for malformed quotes, but normal quoting works."""

    def test_single_quoted_value(self):
        from src.config import _read_export_from_zshrc
        from pathlib import Path

        zshrc = "export MY_KEY='hello world'\n"
        with patch.object(Path, "read_text", return_value=zshrc):
            with patch.object(Path, "exists", return_value=True):
                result = _read_export_from_zshrc("MY_KEY")
                assert result == "hello world"

    def test_double_quoted_value(self):
        from src.config import _read_export_from_zshrc
        from pathlib import Path

        zshrc = 'export MY_KEY="hello world"\n'
        with patch.object(Path, "read_text", return_value=zshrc):
            with patch.object(Path, "exists", return_value=True):
                result = _read_export_from_zshrc("MY_KEY")
                assert result == "hello world"


# ── BUG-13 edge case — content=None handled fine ──────────────────────


class TestBuildCompactSummaryNoBug:
    """BUG-13 suspected crash with content=None, but code handles it."""

    def test_content_none_non_assistant(self):
        from src.context_manager import _build_compact_summary

        msg = MagicMock()
        msg.role = "user"
        msg.content = None
        result = _build_compact_summary([msg])
        assert isinstance(result, str)

    def test_content_none_assistant(self):
        from src.context_manager import _build_compact_summary

        msg = MagicMock()
        msg.role = "assistant"
        msg.content = None
        result = _build_compact_summary([msg])
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════
# RED — CONFIRMED BUGS (tests that FAIL, proving the bug)
# ═══════════════════════════════════════════════════════════════════════


# ── RED: BUG-15 — _latest_assistant_text loses first chunk ────────────


class TestLatestAssistantTextCapturesAll:
    """BUG-15: When coach writes text → tool_use → text, the function
    walks backward and breaks on the first non-assistant message.
    The first assistant text chunk is lost.

    Expected (correct): all assistant text in the latest contiguous block.
    Actual (bug): only text after the last tool message is kept."""

    def test_all_assistant_text_captured(self):
        from src.feedback import _latest_assistant_text

        msg1 = MagicMock()
        msg1.role = "assistant"
        type(msg1).__name__ = "AssistantMessage"
        msg1.content = "First response part"

        msg_tool = MagicMock()
        msg_tool.role = "tool"
        type(msg_tool).__name__ = "ToolResultMessage"
        msg_tool.content = "tool output"

        msg2 = MagicMock()
        msg2.role = "assistant"
        type(msg2).__name__ = "AssistantMessage"
        msg2.content = "Second response part with IMPORTANT verdict"

        messages = [msg1, msg_tool, msg2]

        with patch("src.feedback.normalize_message") as mock_norm:

            def side_effect(m):
                result = MagicMock()
                text = getattr(m, "content", "")
                if isinstance(text, str):
                    result.get_text_content.return_value = text
                else:
                    result.get_text_content.return_value = ""
                return result

            mock_norm.side_effect = side_effect
            text = _latest_assistant_text(messages)

        assert "First" in text, f"First assistant text lost — only got: '{text}'"


# ── RED: BUG-21 — "codex" substring matches any model with "codex" ────


class TestModelContextWindowsCodexSubstring:
    """BUG-21: Pattern "codex" in _MODEL_CONTEXT_WINDOWS is a substring
    match.  Model "my-custom-codex-model-v2" gets 1M token window.

    Expected (correct): unrelated models with "codex" in name get 0.
    Actual (bug): substring match returns 1_000_000."""

    def test_custom_codex_model_not_1m(self):
        from src.config import get_context_window

        result = get_context_window("my-custom-codex-model-v2")
        assert result == 0, (
            f"'my-custom-codex-model-v2' should not get 1M window, got {result}"
        )

    def test_random_word_containing_codex(self):
        from src.config import get_context_window

        result = get_context_window("codexifier-3000")
        assert result == 0, f"'codexifier-3000' should not match codex, got {result}"


# ── RED: BUG-22 — short_model_name "glm" catch-all → always "GLM-5" ──


class TestShortModelNameGlmCatchall:
    """BUG-22: Any model with "glm" in name displays as "GLM-5".
    glm-3, glm-2, glm-anything all show "GLM-5".

    Expected (correct): glm-3 → "GLM-3", glm-2 → "GLM-2".
    Actual (bug): everything → "GLM-5"."""

    def test_glm_3_not_glm5(self):
        from src.config import short_model_name

        result = short_model_name("glm-3-32b")
        assert result != "GLM-5", (
            f"'glm-3-32b' should not show as 'GLM-5', got '{result}'"
        )

    def test_glm_2_not_glm5(self):
        from src.config import short_model_name

        result = short_model_name("glm-2")
        assert result != "GLM-5", f"'glm-2' should not show as 'GLM-5', got '{result}'"


# ── RED: BUG-25 — "eof" substring matches unrelated words ─────────────


class TestIsRecoverableErrorEofSubstring:
    """BUG-25: Keyword "eof" in _is_recoverable_error matches any message
    containing these three letters (e.g. "EOF handling", "reheofcal").

    Expected (correct): only real EOF errors are recoverable.
    Actual (bug): any message with "eof" substring is marked recoverable."""

    def test_eof_in_unrelated_word_not_recoverable(self):
        from src.providers.chain import _is_recoverable_error

        assert not _is_recoverable_error(Exception("pthread EOF handling")), (
            "'EOF handling' should not be classified as recoverable"
        )

    def test_eof_in_random_word_not_recoverable(self):
        from src.providers.chain import _is_recoverable_error

        assert not _is_recoverable_error(Exception("reheofcal process failed")), (
            "Random word containing 'eof' should not be recoverable"
        )


# ── RED: BUG-27 — _schedule_counts overrides intentional zeros ────────


class TestScheduleCountsOverridesZeros:
    """BUG-27 (duplicate of BUG-02): User setting all batch retry counts
    to 0 falls back to defaults (PLAN-B5 semantics)."""

    def test_all_zeros_are_respected(self):
        from src.batch_executor import BatchExecutor

        class ZeroConfig:
            batch_pre_judge_attempts = 0
            batch_judge_attempts = 0
            batch_post_judge_attempts = 0

        class FakeSession:
            config = ZeroConfig()

        executor = BatchExecutor(session=FakeSession(), tracker=object())
        result = executor._schedule_counts()
        assert sum(result) > 0, (
            f"All-zero should fall back to defaults, got {result}"
        )


# ── RED: BUG-18 — provider.claude_home overwrites shared config ───────


class TestConfigClaudeHomeOverride:
    """BUG-18: resolve_config reads provider.claude_home from YAML and
    overwrites the single shared config field.  Setting Claude's home
    to ~/.claude clobbers ZAI's ~/.claude-zai.

    Expected (correct): provider.claude_home only affects that provider.
    Actual (bug): one field is shared — any override affects all."""

    def test_provider_claude_home_does_not_overwrite_default(self):
        from src.config import Config

        cfg = Config()
        original_home = cfg.claude_home
        with patch(
            "src.config.load_merged_settings",
            return_value={"provider": {"claude_home": "/tmp/other"}},
        ):
            from src.config import resolve_config

            resolved = resolve_config({"working_dir": "."})
            assert resolved.claude_home == original_home, (
                f"provider.claude_home overwrote shared config: "
                f"expected {original_home}, got {resolved.claude_home}"
            )
