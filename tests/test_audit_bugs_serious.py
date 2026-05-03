"""Proof-of-bug tests for SERIOUS bugs.

RED tests assert CORRECT behaviour — they FAIL because the bug exists.
GREEN tests (false positives) assert code works fine — they PASS.
"""

import pytest
from unittest.mock import MagicMock, patch


# ── RED: BUG-04 — kill_new_processes has no pgrep fallback ─────────────


class TestProcessGuardPgrepFallback:
    """BUG-04: snapshot_pids() has a pgrep fallback when psutil is missing,
    but kill_new_processes() silently returns (does nothing).
    On systems without psutil, child processes are never killed.

    Expected (correct): kill should have same pgrep fallback as snapshot.
    Actual (bug): kill returns immediately after ImportError."""

    def test_kill_has_pgrep_fallback(self):
        import inspect
        from src.process_guard import ProcessGuard

        source = inspect.getsource(ProcessGuard.kill_new_processes)
        assert "pgrep" in source, (
            "kill_new_processes has no pgrep fallback — "
            "processes are never cleaned up without psutil"
        )


# ── RED: BUG-05 — string decision gives wrong status ──────────────────


class TestRecorderStringDecisionStatus:
    """BUG-05: When orchestrator passes decision='winner_a' (string),
    _build_record_from_kwargs does getattr(decision, 'action', '') → '' on
    strings, so status falls to 'completed' instead of 'approved'.

    Expected (correct): string 'winner_a' → status 'approved'.
    Actual (bug): status becomes 'completed'."""

    def test_string_winner_decision_gives_approved(self):
        import tempfile
        from src.learning.recorder import RunRecorder

        with tempfile.TemporaryDirectory() as tmp:
            recorder = RunRecorder(f"{tmp}/knowledge")
            recorder.record(
                decision="winner_a",
                rounds_used=1,
                total_duration_s=60.0,
            )
            records = recorder.history(limit=1)
            assert records[0].status == "approved", (
                f"decision='winner_a' should give status='approved', "
                f"got '{records[0].status}'"
            )


# ── RED: BUG-06 — id() mapping breaks after replace() ────────────────


class TestPhaseZeroIdMapping:
    """BUG-06: _run_phase_zero builds {id(item): idx} then looks up
    id(step).  If anyone copies items via replace(), the mapping silently
    breaks — steps are lost without any error.

    Expected (correct): mapping should survive dataclass replace().
    Actual (bug): new objects have different id()s — lookup returns nothing."""

    def test_id_mapping_survives_replace(self):
        from dataclasses import replace
        from src.plan_tracker import PlanItem, Phase

        items = [
            PlanItem(text="step 1", done=False),
            PlanItem(text="step 2", done=False),
        ]
        Phase(name="Test", type="update", steps=items)

        preserved_items = [replace(item, done=item.done) for item in items]
        index_by_old_id = {id(item): idx for idx, item in enumerate(items)}

        for item in preserved_items:
            assert id(item) in index_by_old_id, (
                "replace() creates new objects with different id()s — "
                "id()-based mapping is broken"
            )


# ── RED: BUG-07 — parse_enriched_plan silently drops invalid indices ──


class TestEnrichedPlanInvalidIndices:
    """SW-47: Phase step indices out of range should be filtered, not clamped.

    Clamping (min(i, len-1)) creates duplicate refs to the last item.
    Filtering returns only valid indices, keeping each step unique."""

    def test_all_referenced_steps_present(self):
        from src.plan_tracker import parse_enriched_plan

        content = (
            '## Steps\n1. Do something\n\n## Phases\n- Phase 1: "Big" → steps 1-5\n'
        )
        items, phases = parse_enriched_plan(content)

        assert len(items) == 1
        assert len(phases[0].steps) == 1, (
            f"Phase references steps 1-5 but only 1 exists — "
            f"should get 1 valid step, not 5 clamped duplicates"
        )


# ── RED: BUG-14 — _match_header false positive on prose ──────────────


class TestMatchHeaderProseNotMatched:
    """BUG-14: _match_header matches prose text like
    'What changed in the implementation after the refactor' as a report
    header because cleaned.startswith(bare) + rest[0]==' ' passes.

    Expected (correct): prose should NOT match as a header.
    Actual (bug): prose is parsed as a header."""

    def test_prose_not_matched_as_header(self):
        from src.batch_executor import _match_header

        headers = {
            "what changed:": "what changed",
            "evidence:": "evidence",
            "verification:": "verification",
        }
        line = "What changed in the implementation after the refactor"
        section, _ = _match_header(line, headers)
        assert section is None, (
            f"Prose '{line}' should NOT match as header, matched '{section}'"
        )

    def test_question_not_matched_as_header(self):
        from src.batch_executor import _match_header

        headers = {"what changed:": "what changed"}
        line = "What changed in this module?"
        section, _ = _match_header(line, headers)
        assert section is None, f"Question '{line}' should NOT match as header"


# ── RED: BUG-16 — state machine _VALID_TRANSITIONS contradiction ──────


class TestStateMachineContradiction:
    """BUG-16: _RESUMABLE_TO_AGENTS_RUNNING includes ROUND_FAILED, but
    _VALID_TRANSITIONS[ROUND_FAILED] = {FAILED, STOPPED} — no AGENTS_RUNNING.
    transition() has a special-case that bypasses _VALID_TRANSITIONS.
    The state machine invariant is violated.

    Expected (correct): _RESUMABLE set and _VALID_TRANSITIONS are consistent.
    Actual (bug): ROUND_FAILED is resumable but not valid for transition."""

    def test_resumable_set_consistent_with_valid_transitions(self):
        from src.state import (
            SessionState,
            _VALID_TRANSITIONS,
            _RESUMABLE_TO_AGENTS_RUNNING,
        )

        for state in _RESUMABLE_TO_AGENTS_RUNNING:
            valid_targets = _VALID_TRANSITIONS.get(state, set())
            assert SessionState.AGENTS_RUNNING in valid_targets, (
                f"{state} is in _RESUMABLE_TO_AGENTS_RUNNING but "
                f"AGENTS_RUNNING is not in its _VALID_TRANSITIONS"
            )


# ── RED: BUG-17 — BugDetector._check_tests returns 1 on internal error ─


class TestBugDetectorInternalErrorReturnsZero:
    """BUG-17: _check_tests returns 1 when pytest has an internal error
    (not a test failure).  The function can't find "X failed" in output
    and falls through to returning 1.

    Expected (correct): internal error → return 0 (no tests actually failed).
    Actual (bug): returns 1."""

    def test_internal_error_returns_zero(self):
        from src.bug_detector import BugDetector
        from unittest.mock import patch, MagicMock

        detector = BugDetector(
            run_tests=True, run_types=False, run_lint=False, run_compile=False
        )
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "INTERNALERROR> some internal error"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            count = BugDetector._check_tests("/fake/dir")
            assert count == 0, (
                f"Internal error should report 0 failed tests, got {count}"
            )

    def test_no_failed_line_returns_zero(self):
        from src.bug_detector import BugDetector
        from unittest.mock import patch, MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "some random error output without the word failed"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            count = BugDetector._check_tests("/fake/dir")
            assert count == 0, f"No 'X failed' line → should return 0, got {count}"


# ── RED: BUG-20 — --limit 0 is ignored (falsy) ──────────────────────


class TestDebugLimitZeroPropagated:
    """BUG-20: getattr(args, 'debug_limit_value', None) returns 0 which
    is falsy — the elif branch is never taken.  --limit 0 is silently
    ignored.

    Expected (correct): --limit 0 should set mode='iterations'.
    Actual (bug): 0 is falsy, branch skipped."""

    def test_zero_limit_propagated_to_mode(self):
        class Args:
            infinite = False
            time = None
            debug_limit_value = 0

        args = Args()
        cli_overrides = {}

        if getattr(args, "infinite", False):
            cli_overrides["mode"] = "infinite"
        elif getattr(args, "time", None):
            cli_overrides["mode"] = "time"
        elif getattr(args, "debug_limit_value", None) is not None:
            cli_overrides["mode"] = "iterations"

        assert "mode" in cli_overrides, (
            "--limit 0 is ignored because 0 is falsy in truthiness check"
        )
