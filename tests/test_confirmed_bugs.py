"""Tests confirming REAL bugs in the codebase.

PHILOSOPHY:
- Test FAILS (red)  → bug EXISTS (correct behavior is broken)
- Test PASSES (green) → bug is FIXED

Each test asserts CORRECT behavior. If the bug exists, the test fails.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import replace

# ===========================================================================
# BUG #1 (CRITICAL): orchestrator.py imports non-existent modules
# src/learning/analyzer.py, classifier.py, recommender.py do not exist
# File: src/orchestrator.py:15-17
# ===========================================================================


class TestOrchestratorImportBug:
    """orchestrator.py imports from modules that don't exist on disk.
    CORRECT: orchestrator should be importable without errors."""

    def test_orchestrator_imports_successfully(self):
        """Importing orchestrator should NOT fail — all learning modules should exist."""
        from src import orchestrator  # noqa: F401


# ===========================================================================
# BUG #2 (MEDIUM): parse_completed_steps triggers on PHASE_COMPLETE
# anywhere in text, even in passing discussion
# File: src/batch_executor.py:119
# ===========================================================================


class TestPhaseCompleteAnywhereBug:
    """parse_completed_steps should NOT mark all steps done when PHASE_COMPLETE
    appears only in discussion text, not in the actual completion report."""

    def test_phase_complete_in_discussion_does_not_mark_all_done(self):
        """PHASE_COMPLETE mentioned in passing should NOT mark all steps done."""
        from src.batch_executor import parse_completed_steps
        from src.plan_tracker import PlanItem, Phase

        phase = Phase(
            name="Test",
            type="create",
            steps=[
                PlanItem(text="Implement auth"),
                PlanItem(text="Add tests"),
                PlanItem(text="Deploy"),
            ],
        )

        result = MagicMock()
        result.text = (
            "Let me think about this. The requirements say I should end with\n"
            "PHASE_COMPLETE: Test when I'm done. But first let me explore...\n"
            "\n"
            "What changed:\n- explored the codebase\n"
            "Evidence:\n- read files\n"
            "Verification:\n- none needed\n"
        )

        completed = parse_completed_steps(result, phase)

        # CORRECT: should return empty — PHASE_COMPLETE is only in discussion
        # BUG: returns ALL 3 steps because regex matches anywhere in text
        assert completed == [], (
            f"BUG: PHASE_COMPLETE in discussion text marked ALL {len(completed)} steps done"
        )

    def test_phase_complete_in_report_marks_all_done(self):
        """PHASE_COMPLETE in the actual completion report SHOULD mark all done."""
        from src.batch_executor import parse_completed_steps
        from src.plan_tracker import PlanItem, Phase

        phase = Phase(
            name="Test",
            type="create",
            steps=[
                PlanItem(text="Implement auth"),
                PlanItem(text="Add tests"),
                PlanItem(text="Deploy"),
            ],
        )

        result = MagicMock()
        result.text = (
            "Step 1 done: Implemented auth middleware\n"
            "Step 2 done: Added unit tests\n"
            "Step 3 done: Deployed to staging\n"
            "\n"
            "PHASE_COMPLETE: Test\n"
            "What changed:\n- implemented all steps\n"
            "Evidence:\n- all files created\n"
            "Verification:\n- tests pass\n"
        )

        completed = parse_completed_steps(result, phase)
        assert completed == ["Implement auth", "Add tests", "Deploy"]


# ===========================================================================
# BUG #3 (MEDIUM): worktree.py _create_git_worktree doesn't handle
# existing branch, crashes with "branch already exists"
# File: src/worktree.py:124-127
# ===========================================================================


class TestWorktreeBranchAlreadyExistsBug:
    """_create_git_worktree should handle existing branches gracefully."""

    def test_create_git_worktree_handles_existing_branch(self):
        """When branch already exists, should not crash (uses -f to overwrite)."""
        from src.worktree import WorktreeManager
        import subprocess

        manager = WorktreeManager(
            session_dir="/tmp/test-session",
            source_dir="/tmp/test-source",
            mode="git",
        )

        def mock_run(*args, **kwargs):
            cmd = args[0]
            # git branch without -f would fail for existing branch
            # git branch -f should succeed (force overwrite)
            if cmd[:2] == ["git", "branch"] and "-f" not in cmd and "worktree" not in cmd:
                raise subprocess.CalledProcessError(
                    128, cmd, "fatal: A branch named 'g3/...' already exists"
                )
            return MagicMock(returncode=0)

        with patch("src.worktree.subprocess.run", side_effect=mock_run):
            # With -f flag, existing branch is overwritten — no crash
            manager._create_git_worktree("agent-a", "/tmp/ws-a")


# ===========================================================================
# BUG #4 (LOW): feedback.py parse_review_output returns ReviewIssues
# for ambiguous output instead of a neutral verdict
# File: src/feedback.py:248-249
# ===========================================================================


class TestReviewAmbiguousOutputBug:
    """parse_review_output should return a neutral verdict for ambiguous output,
    not ReviewIssues with truncated text."""

    def test_ambiguous_review_not_treated_as_issues(self):
        """When reviewer gives vague response with no numbered issues,
        it should NOT be treated as ReviewIssues."""
        from src.feedback import parse_review_output, ReviewIssues

        messages = [
            MagicMock(
                role="assistant",
                content="I've reviewed the code. It looks like a standard implementation.",
            ),
        ]

        verdict = parse_review_output(messages)

        # CORRECT: should be ReviewPassed or a neutral verdict
        # BUG: returns ReviewIssues with "did not return a clear verdict"
        assert not isinstance(verdict, ReviewIssues), (
            "BUG: ambiguous review output incorrectly treated as ReviewIssues"
        )


# ===========================================================================
# BUG #5: REJECTED — false positive
# The test omitted the guard `if restart_requested: continue` (line 1178-1179)
# that exists in the real code. The warning check at line 1181 is NEVER reached
# when restart_requested is True. Not a real bug.
# ===========================================================================
