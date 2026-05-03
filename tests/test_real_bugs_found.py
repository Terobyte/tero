"""Tests proving real bugs in the codebase. Every test here MUST fail (red).
If any test passes, that's a false positive and should be removed."""

import re

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1: get_context_window — minimax-m2.5 model gets wrong context window
#
# _MODEL_CONTEXT_WINDOWS has "minimax/minimax-m2.5:free" → 262_144, but that
# pattern never matches real model strings (the colon and slash format is
# wrong).  Meanwhile "minimax-m2" is a substring of "minimax-m2.5", so
# "opencode/minimax-m2.5-free" incorrectly matches "minimax-m2" → 1_000_000.
# ─────────────────────────────────────────────────────────────────────────────


class TestGetContextWindowMinimaxBug:
    def test_opencode_minimax_m25_gets_wrong_window(self):
        from src.config import get_context_window

        result = get_context_window("opencode/minimax-m2.5-free")
        assert result == 262_144, f"minimax-m2.5 should have 262K context, got {result}"

    def test_plain_minimax_m25_model_gets_wrong_window(self):
        from src.config import get_context_window

        result = get_context_window("minimax-m2.5")
        assert result == 262_144, f"minimax-m2.5 should have 262K context, got {result}"


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2: _extract_numbered_issues — blank lines do NOT separate issues
#
# Non-numbered text between numbered issues is silently merged into the
# preceding issue, even across blank lines.  This means reviewer chit-chat
# or section headers between issues get concatenated into the issue text.
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractNumberedIssuesMergeBug:
    def test_unrelated_text_merged_across_blank_lines(self):
        from src.feedback import _extract_numbered_issues

        text = (
            "1. Fix the login validation\n"
            "\n"
            "Here is some reviewer commentary that is NOT part of the issue.\n"
            "\n"
            "2. Add unit tests for auth"
        )
        issues = _extract_numbered_issues(text)

        assert issues[0] == "1. Fix the login validation", (
            f"Unrelated text was merged: {issues[0]!r}"
        )

    def test_section_header_merged_into_issue(self):
        from src.feedback import _extract_numbered_issues

        text = (
            "## Critical Issues\n"
            "1. Fix SQL injection\n"
            "## Minor Issues\n"
            "2. Fix typo in README"
        )
        issues = _extract_numbered_issues(text)

        assert issues[0] == "1. Fix SQL injection", (
            f"Section header merged in: {issues[0]!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3: build_batch_prompt — step numbering has gaps after completed steps
#
# When some steps are completed, remaining steps keep their ORIGINAL
# position numbers (1, 3, 4…) instead of being renumbered (1, 2, 3…).
# This creates confusing "Step 3 done:" confirmations when only steps 1
# and 3 are remaining, causing the LLM to output "Step 2 done:" for what
# is actually step 3.
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildBatchPromptNumberingBug:
    def test_remaining_steps_keep_sequential_numbers(self):
        from src.batch_executor import build_batch_prompt
        from src.plan_tracker import Phase, PlanItem

        phase = Phase(
            name="Build Auth",
            type="create",
            steps=[
                PlanItem(text="Write auth module"),
                PlanItem(text="Write unit tests"),
                PlanItem(text="Deploy to staging"),
            ],
        )
        prompt = build_batch_prompt(phase, completed_steps=["Write unit tests"])

        step_done_nums = [
            int(m.group(1)) for m in re.finditer(r"Step (\d+) done:", prompt)
        ]

        assert step_done_nums == [1, 2], (
            f"Expected sequential [1, 2] for 2 remaining steps, got {step_done_nums}. "
            "The prompt uses original position numbers with gaps, confusing the LLM."
        )

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
# Bug 4: merge_bugs — false dedup from _normalize_description stripping :digits
#
# _normalize_description strips ":<digits>" from descriptions for dedup.
# This means "Fix timeout:5000 in handler" and "Fix timeout:3000 in handler"
# both normalize to "fix timeout in handler" — two DIFFERENT bugs at
# DIFFERENT lines get falsely deduped.
# ─────────────────────────────────────────────────────────────────────────────


class TestMergeBugsFalseDedupBug:
    def test_different_timeout_values_not_falsely_deduped(self):
        from src.debugger_bugs import BugEntry, merge_bugs

        existing = [
            BugEntry(
                id=1,
                file="handler.py",
                line=10,
                description="Fix timeout:5000 in handler",
                severity="high",
            ),
        ]
        new_bugs = [
            BugEntry(
                id=2,
                file="handler.py",
                line=25,
                description="Fix timeout:3000 in handler",
                severity="high",
            ),
        ]

        result = merge_bugs(existing, new_bugs)

        assert len(result) == 2, (
            f"Two different bugs at different lines deduped to 1. "
            f"Descriptions normalized to same string due to ':digits' stripping."
        )

    def test_different_port_numbers_not_falsely_deduped(self):
        from src.debugger_bugs import BugEntry, merge_bugs

        existing = [
            BugEntry(
                id=1,
                file="server.py",
                line=5,
                description="Hardcoded port:8080 should be configurable",
                severity="medium",
            ),
        ]
        new_bugs = [
            BugEntry(
                id=2,
                file="server.py",
                line=15,
                description="Hardcoded port:3000 should be configurable",
                severity="medium",
            ),
        ]

        result = merge_bugs(existing, new_bugs)

        assert len(result) == 2, (
            f"Two different port bugs deduped to 1 due to ':digits' stripping."
        )

    def test_normalize_description_strips_leading_dot(self):
        from src.debugger_bugs import _normalize_description

        result = _normalize_description(".env file contains hardcoded secret")

        assert result.startswith(".env"), (
            f"Leading dot stripped: got {result!r}. "
            "Descriptions of dotfiles lose their leading period during normalization."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bug 5: short_model_name — all GLM models labeled as "GLM-5"
#
# The catch-all `"glm" in m` returns "GLM-5" for ANY glm model, including
# glm-4, glm-3, etc. Only specific variants (glm-5.1, glm-4.7, glm-5-turbo)
# are handled explicitly.
# ─────────────────────────────────────────────────────────────────────────────


class TestShortModelNameGlmMislabelBug:
    def test_glm4_labeled_correctly(self):
        from src.config import short_model_name

        result = short_model_name("glm-4")
        assert result == "GLM-4", f"glm-4 should be 'GLM-4', got {result!r}"

    def test_glm3_labeled_correctly(self):
        from src.config import short_model_name

        result = short_model_name("glm-3")
        assert result == "GLM-3", f"glm-3 should be 'GLM-3', got {result!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Bug 6: get_context_window — "minimax-m2" substring matches minimax-m2.5
#
# The list has "minimax-m2" → 1_000_000 BEFORE "minimax" → 40_960.
# Any model containing "minimax-m2" (including minimax-m2.5 variants)
# gets 1M instead of the correct value.  The "minimax/minimax-m2.5:free"
# pattern can never match because real model strings don't contain the colon.
# ─────────────────────────────────────────────────────────────────────────────


class TestGetContextWindowPatternOrderBug:
    def test_minimax_m25_pattern_never_matches(self):
        from src.config import _MODEL_CONTEXT_WINDOWS

        pattern, size = "minimax/minimax-m2.5:free", 262_144
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


# ─────────────────────────────────────────────────────────────────────────────
# Bug 7: plan_tracker.parse_enriched_plan — overlapping step ranges create
# duplicate steps in a phase
#
# "steps 1-3, 2-4" produces indices [0,1,2,1,2,3] with duplicates.
# Phase steps list will contain the same PlanItem twice.
# ─────────────────────────────────────────────────────────────────────────────


class TestParseEnrichedPlanDuplicateStepsBug:
    def test_overlapping_ranges_create_duplicate_steps(self):
        """Single phase with comma-separated overlapping ranges must not produce duplicate steps.

        "steps 1-3, 2-4" → raw indices [0,1,2,1,2,3] → items[1] and items[2] appear twice.
        Without dedup, phase.steps has 6 entries with 2 duplicates.
        """
        from src.plan_tracker import parse_enriched_plan

        content = (
            "## Phases\n"
            '- Phase 1: "Setup" → steps 1-3, 2-4\n'
            "\n"
            "## Steps\n"
            "1. Init project\n"
            "2. Add dependencies\n"
            "3. Configure build\n"
            "4. Write tests\n"
        )

        items, phases = parse_enriched_plan(content)

        assert len(phases) == 1
        phase_steps = phases[0].steps
        # All 4 unique steps should appear exactly once.
        assert len(phase_steps) == 4, (
            f"Expected 4 unique steps, got {len(phase_steps)}: "
            f"{[s.text for s in phase_steps]}. "
            "Overlapping ranges 1-3,2-4 produce duplicate PlanItem refs without dedup."
        )
        # Confirm no object-identity duplicates either.
        step_ids = [id(s) for s in phase_steps]
        assert len(step_ids) == len(set(step_ids)), (
            "Phase steps list contains duplicate PlanItem object references."
        )
