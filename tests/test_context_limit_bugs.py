"""Bug-proving tests for the context-limit feature.

Each test targets a specific bug found during code review of the
model-aware context limit implementation.  Tests are intentionally
RED — they fail because the bugs exist.
"""

import pytest
from src.config import (
    Config,
    get_context_window,
    get_effective_context_limit,
    _DEFAULT_CONTEXT_LIMIT,
)
from src.menu import _format_context_limit


# ─── Bug 1: Claude model aliases not matched ────────────────────────────
#
# ClaudeNativeProvider stores model as "sonnet", "opus", or "haiku".
# _run_turn resolves the model to provider.config.default_model, which is
# the short alias.  None of the _MODEL_CONTEXT_WINDOWS patterns contain
# these short names as substrings, so get_context_window() returns 0 and
# auto-detection falls back to 110K instead of 1M.
#
# Expected: 1_000_000 for all three aliases (Claude 4 family supports 1M).
# Actual:   0 (no match).

class TestClaudeAliasBug:
    """Claude native provider uses short aliases that bypass model matching."""

    @pytest.mark.parametrize("alias", ["sonnet", "opus", "haiku"])
    def test_context_window_for_claude_aliases(self, alias):
        """Short Claude aliases must resolve to 1M context window."""
        window = get_context_window(alias)
        # BUG: returns 0 because "sonnet" doesn't contain "claude-sonnet-4"
        assert window == 1_000_000, (
            f"Model alias '{alias}' should resolve to 1M context window, "
            f"got {window}"
        )

    @pytest.mark.parametrize("alias", ["sonnet", "opus", "haiku"])
    def test_effective_limit_auto_detects_for_claude_aliases(self, alias):
        """With default sentinel, effective limit must auto-detect 1M for Claude."""
        effective = get_effective_context_limit(alias, _DEFAULT_CONTEXT_LIMIT)
        # BUG: falls back to 110K because get_context_window(alias) == 0
        assert effective > 110_000, (
            f"Effective limit for Claude alias '{alias}' should be well above "
            f"110K (got {effective})"
        )

    def test_player_turn_would_use_110k_for_sonnet(self):
        """Simulates what _run_turn does for Claude native with model='sonnet'.

        The flow in _run_turn is:
            resolved_model_for_ctx = model or self._provider_model(provider)
            # For ClaudeNative, _provider_model returns config.default_model = 'sonnet'
            effective = get_effective_context_limit('sonnet', 110_000)
        """
        # This is exactly what _run_turn computes
        resolved_model_for_ctx = "sonnet"  # from ClaudeNativeConfig.default_model
        effective = get_effective_context_limit(
            resolved_model_for_ctx, _DEFAULT_CONTEXT_LIMIT
        )
        # BUG: effective is 110_000 instead of 800_000+ (80% of 1M)
        assert effective != 110_000, (
            "Claude native (sonnet) effective limit should not be the "
            f"110K sentinel — got {effective}"
        )


# ─── Bug 2: Empty model string not matched ──────────────────────────────
#
# Codex with default model has player_model="" (empty).  _run_turn resolves
# to _provider_model(provider), which for Codex may also return "".  Then
# get_context_window("") returns 0, and auto-detection fails, keeping 110K
# instead of the 1M that Codex supports.
#
# Expected: 1_000_000 (or at minimum a sensible default for known providers).
# Actual:   0 (no match).

class TestEmptyModelBug:
    """Codex uses empty default_model — context limit must still resolve via provider fallback."""

    def test_context_window_for_empty_string_returns_zero(self):
        """get_context_window('') correctly returns 0 — there is nothing to match.

        The fix for empty-model providers lives in get_effective_context_limit(provider=...)
        which falls back to the provider class name as a hint.
        """
        window = get_context_window("")
        assert window == 0, (
            f"get_context_window('') should return 0 (no model to match), got {window}"
        )

    def test_effective_limit_for_codex_provider_with_unknown_model(self):
        """When Codex has empty default_model, _run_turn resolves to 'unknown'.

        The provider-class fallback must kick in:
            type(CodexProvider).__name__.lower() == 'codexprovider'
            'codex' in 'codexprovider' → True → 1_000_000
        """
        from src.providers.codex import CodexProvider
        provider = CodexProvider()
        # _run_turn sets resolved_model_for_ctx = "" or "" or "unknown" → "unknown"
        effective = get_effective_context_limit("unknown", _DEFAULT_CONTEXT_LIMIT, provider=provider)
        assert effective == 1_000_000, (
            f"Codex with unknown model should fall back to 1M via class name, got {effective}"
        )


# ─── Bug 3: Double compaction threshold ─────────────────────────────────
#
# The plan's impact table promises compact at 85% of model window.
# Example: Claude 1M → compact at 850K.
#
# But the implementation applies TWO reductions:
#   1. get_effective_context_limit returns window * 0.80
#   2. compact_threshold applies another * 0.85
#
# So actual compact trigger = window * 0.80 * 0.85 = window * 0.68
# For Claude 1M: 680K, not the promised 850K.
#
# This means the plan's impact table is wrong:
#   Plan says:  Claude @85% = 850,000
#   Actual:     Claude @85% of 80% = 680,000
#   Plan says:  ZAI @85% = 173,400
#   Actual:     ZAI @85% of 80% = 139,264

class TestDoubleThresholdBug:
    """Effective limit + compact_threshold double-dips the safety margin."""

    def test_claude_compact_trigger_matches_plan_impact_table(self):
        """The plan says Claude (1M) compacts at 850K (85% of 1M)."""
        model = "claude-sonnet-4-20250514"  # full name that DOES match
        effective = get_effective_context_limit(model, _DEFAULT_CONTEXT_LIMIT)
        compact_threshold = 0.85
        compact_trigger = int(effective * compact_threshold)

        # Plan's impact table says compact at 850,000 for Claude
        expected_from_plan = 850_000
        # BUG: compact_trigger is 680,000 (680% more conservative than plan)
        assert compact_trigger == expected_from_plan, (
            f"Compact trigger for Claude should be {expected_from_plan:,} per plan, "
            f"but is {compact_trigger:,} due to double 80%*85% reduction"
        )

    def test_zai_compact_trigger_matches_plan_impact_table(self):
        """ZAI (GLM-5.1, 204,800 tokens) compacts at 85% = 174,080 tokens."""
        model = "glm-5.1"
        effective = get_effective_context_limit(model, _DEFAULT_CONTEXT_LIMIT)
        compact_threshold = 0.85
        compact_trigger = int(effective * compact_threshold)

        # GLM-5.1 window is 204,800 (200 * 1024); 85% = 174,080
        # (The original plan approximated 204K, actual window is 204,800)
        expected = 174_080
        assert compact_trigger == expected, (
            f"Compact trigger for ZAI should be {expected:,}, got {compact_trigger:,}"
        )

    def test_no_double_safety_margin(self):
        """get_effective_context_limit should NOT apply an extra 80% factor.

        The 80% inside get_effective_context_limit plus the 0.85
        compact_threshold creates a double reduction.  The effective limit
        should return the raw model window, letting compact_threshold be
        the only safety factor.
        """
        model = "claude-sonnet-4-20250514"
        effective = get_effective_context_limit(model, _DEFAULT_CONTEXT_LIMIT)
        raw_window = get_context_window(model)

        # If effective_limit == raw_window, then compact at 85% would be correct.
        # BUG: effective is 80% of raw_window due to extra * 0.80 factor
        assert effective == raw_window, (
            f"Effective limit ({effective:,}) should equal raw window "
            f"({raw_window:,}) — the 0.85 compact_threshold is the safety margin"
        )


# ─── Bug 4: _format_context_limit displays "0K" for small values ────────
#
# _format_context_limit(500) → "0K" because 500 // 1000 == 0.
# This is confusing for users who enter a custom limit < 1000.

class TestFormatContextLimitBug:
    """Menu display for small context limits is wrong."""

    def test_format_small_limit(self):
        """Values < 1000 should display meaningfully, not as '0K'."""
        result = _format_context_limit(500)
        # BUG: displays "0K"
        assert result != "0K", (
            f"Context limit 500 should display as something meaningful, "
            f"not '0K' — got '{result}'"
        )

    def test_format_zero_limit(self):
        """Zero should not display as '0K' either."""
        result = _format_context_limit(0)
        assert result != "0K", (
            f"Context limit 0 should display as something meaningful, "
            f"not '0K' — got '{result}'"
        )
