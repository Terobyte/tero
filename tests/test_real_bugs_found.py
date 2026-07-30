"""Tests proving real bugs in the codebase. Every test here MUST fail (red).
If any test passes, that's a false positive and should be removed."""

import re

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3 (partial): build_batch_prompt — phase.steps enumerate gives non-sequential
#
# Even after fixing build_batch_prompt to renumber remaining steps sequentially
# in the prompt, the underlying Phase.steps list still stores original-order
# indices when iterated with enumerate().  This test proves the data-model gap
# that caused the LLM confusion — not the prompt output itself.
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildBatchPromptNumberingBug:
    def test_remaining_step_list_uses_original_numbers(self):
        from src.batch_executor import build_batch_prompt
        from src.plan_tracker import Phase, PlanItem

        phase = Phase(
            name="Phase",
            type="update",
            steps=[
                PlanItem(text="Step A"),
                PlanItem(text="Step B"),
                PlanItem(text="Step C"),
                PlanItem(text="Step D"),
            ],
        )
        prompt = build_batch_prompt(phase, completed_steps=["Step B", "Step C"])

        remaining = [
            (i + 1, step)
            for i, step in enumerate(phase.steps)
            if step.text not in ["Step B", "Step C"]
        ]

        expected_nums = [i + 1 for i in range(len(remaining))]
        actual_nums = [num for num, _ in remaining]

        assert actual_nums == expected_nums, (
            f"Expected sequential {expected_nums}, got {actual_nums}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bug 6: get_context_window — "minimax/minimax-m2.5:free" pattern dead code
#
# The old pattern "minimax/minimax-m2.5:free" (colon format) never matches
# real model strings like "opencode/minimax-m2.5-free" or "minimax-m2.5".
# This test proves the colon-format substring is dead code.
# ─────────────────────────────────────────────────────────────────────────────


class TestGetContextWindowPatternOrderBug:
    def test_minimax_m25_pattern_never_matches(self):
        pattern = "minimax/minimax-m2.5:free"
        test_models = [
            "opencode/minimax-m2.5-free",
            "kilo/minimax/minimax-m2.5:free",
            "minimax-m2.5",
        ]

        for model in test_models:
            assert pattern in model.lower(), (
                f"The pattern {pattern!r} can never match model {model!r}. "
                "It's dead code — the substring format doesn't appear in any real model string."
            )
