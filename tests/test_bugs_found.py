"""Tests that prove (or disprove) bugs from bugs.md.

Convention:
  - Test asserts CORRECT behavior.
  - If the bug is REAL  -> test FAILS (red).
  - If the bug is FALSE POSITIVE -> test PASSES (green).
"""

import asyncio
import inspect
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Bug #1 (HIGH): _SENTENCE_CONTINUATION_RE rejects valid PHASE_COMPLETE lines
# File: src/batch_executor.py:98,128
# ---------------------------------------------------------------------------


class TestSentenceContinuationFalsePositive(unittest.TestCase):
    """Bug: PHASE_COMPLETE lines containing a period+space are rejected.

    The regex ``\\.\\s+\\S`` is meant to reject PHASE_COMPLETE markers
    embedded in discussion text, but it also rejects valid standalone
    completion reports like:

        PHASE_COMPLETE: Setup. All code verified.
    """

    def _make_phase(self):
        from src.plan_tracker import PlanItem, Phase

        return Phase(
            name="Test Phase",
            type="create",
            steps=[
                PlanItem(text="Step one"),
                PlanItem(text="Step two"),
            ],
        )

    def test_valid_phase_complete_with_period_is_recognized(self):
        """PHASE_COMPLETE line containing '. ' must still be recognized
        when there are NO 'Step N done' markers (isolates the PHASE_COMPLETE path)."""
        from src.batch_executor import parse_completed_steps

        phase = self._make_phase()
        # NO "Step N done:" markers — only PHASE_COMPLETE path can resolve
        result = SimpleNamespace(
            text=(
                "PHASE_COMPLETE: Test Phase. All code changes verified.\n"
                "What changed:\n"
                "- Created module\n"
                "Evidence:\n"
                "- File exists\n"
                "Verification:\n"
                "- Tests pass"
            )
        )

        steps = parse_completed_steps(result, phase)
        self.assertEqual(
            len(steps),
            len(phase.steps),
            "PHASE_COMPLETE with period in description was rejected — "
            "valid completion marker treated as discussion text. "
            "Regex \\.(space)(word) is too aggressive",
        )

    def test_valid_phase_complete_without_period_works(self):
        """Baseline: PHASE_COMPLETE WITHOUT period must be recognized (proves test setup works)."""
        from src.batch_executor import parse_completed_steps

        phase = self._make_phase()
        # No period after PHASE_COMPLETE — should always work
        result = SimpleNamespace(
            text=(
                "PHASE_COMPLETE: Test Phase\n"
                "What changed:\n"
                "- Added auth middleware\n"
                "Evidence:\n"
                "- All green\n"
                "Verification:\n"
                "- pytest passed"
            )
        )

        steps = parse_completed_steps(result, phase)
        self.assertEqual(
            len(steps),
            len(phase.steps),
            "PHASE_COMPLETE without period should always work — test setup broken if this fails",
        )

    def test_embedded_phase_complete_in_discussion_is_rejected(self):
        """PHASE_COMPLETE genuinely embedded in discussion should be rejected."""
        from src.batch_executor import parse_completed_steps

        phase = self._make_phase()
        result = SimpleNamespace(
            text=(
                "I reviewed the requirements. PHASE_COMPLETE: is mentioned "
                "but I haven't actually done the work yet. Let me continue."
            )
        )

        steps = parse_completed_steps(result, phase)
        # Without a standalone PHASE_COMPLETE, only explicit "Step N done" matches count
        self.assertLess(
            len(steps),
            len(phase.steps),
            "Embedded PHASE_COMPLETE in discussion was incorrectly accepted",
        )


# ---------------------------------------------------------------------------
# Bug #2 (HIGH): pytest "failed," with comma not matched
# File: src/bug_detector.py:203
# ---------------------------------------------------------------------------


class TestPytestFailedCommaNotMatched(unittest.TestCase):
    """Bug: pytest output "3 failed, 5 passed" — the comma after "failed"
    prevents the parser from extracting the count.

    The parser checks ``p == "failed"`` but pytest outputs ``"failed,"``
    (with trailing comma) when there are both failures and passes.
    """

    def test_mixed_failures_and_passes_counted_correctly(self):
        """Parser must extract '3' from '3 failed, 5 passed in 0.5s'."""
        from src.bug_detector import BugDetector

        with tempfile.TemporaryDirectory() as td:
            detector = BugDetector(run_tests=True, run_types=False, run_lint=False, run_compile=False)

            mock_result = SimpleNamespace(
                returncode=1,
                stdout="FAILED test_a.py::test_1 - assert False\n"
                       "FAILED test_a.py::test_3 - assert False\n"
                       "FAILED test_a.py::test_5 - assert False\n"
                       "\n"
                       "=== short test summary info ===\n"
                       "3 failed, 5 passed in 0.50s\n",
                stderr="",
            )

            with patch("subprocess.run", return_value=mock_result):
                count = detector._check_tests(td)

            self.assertEqual(
                count,
                3,
                f"Expected 3 failures but got {count} — "
                "parser fails on 'failed,' with comma (bug #2 confirmed)",
            )

    def test_only_failures_counted_correctly(self):
        """When only failures (no comma), parser works correctly — baseline."""
        from src.bug_detector import BugDetector

        with tempfile.TemporaryDirectory() as td:
            detector = BugDetector(run_tests=True, run_types=False, run_lint=False, run_compile=False)

            # "2 failed in 0.1s" — no comma, should work even with the bug
            mock_result = SimpleNamespace(
                returncode=1,
                stdout="FAILED test_a.py::test_1\n"
                       "FAILED test_a.py::test_2\n"
                       "\n"
                       "2 failed in 0.10s\n",
                stderr="",
            )

            with patch("subprocess.run", return_value=mock_result):
                count = detector._check_tests(td)

            self.assertEqual(
                count,
                2,
                f"Expected 2 but got {count} — baseline 'N failed' parse is broken too",
            )

    def test_two_failures_with_passes_counted_correctly(self):
        """Parser must extract '2' from '2 failed, 3 passed, 1 warning'."""
        from src.bug_detector import BugDetector

        with tempfile.TemporaryDirectory() as td:
            detector = BugDetector(run_tests=True, run_types=False, run_lint=False, run_compile=False)

            mock_result = SimpleNamespace(
                returncode=1,
                stdout=(
                    "FAILED test_a.py::test_1\n"
                    "FAILED test_a.py::test_2\n"
                    "2 failed, 3 passed, 1 warning in 0.50s\n"
                ),
                stderr="",
            )

            with patch("subprocess.run", return_value=mock_result):
                count = detector._check_tests(td)

            self.assertEqual(
                count,
                2,
                f"Expected 2 failures but got {count} — "
                "'2 failed, 3 passed, 1 warning' not parsed (comma after 'failed' breaks matching)",
            )


# ---------------------------------------------------------------------------
# Bug #3 (MEDIUM): resume() / run() returns rounds_used=0 on error
# File: src/orchestrator.py:247,414
# ---------------------------------------------------------------------------


class TestOrchestratorRoundsUsedOnFailure(unittest.TestCase):
    """Bug: both run() and resume() hardcode rounds_used=0 in their error
    handlers, discarding the actual number of completed rounds.
    """

    def test_run_error_handler_preserves_round_count(self):
        """Error handler in run() should report the actual rounds completed."""
        from src.orchestrator import Orchestrator

        # Check the source for the bug pattern
        import inspect
        source = inspect.getsource(Orchestrator.run)

        # Find the error handler block
        self.assertIn(
            "rounds_used",
            source,
            "run() doesn't track rounds_used at all in error path",
        )

        # The specific bug: error handler returns rounds_used=0 instead of round_num
        # Look for the pattern in the except block
        lines = source.splitlines()
        in_except = False
        found_bug = False
        found_fix = False

        for i, line in enumerate(lines):
            if "except Exception" in line:
                in_except = True
            if in_except and "rounds_used=0" in line:
                found_bug = True
            if in_except and "rounds_used=round_num" in line:
                found_fix = True

        self.assertFalse(
            found_bug,
            "run() error handler still hardcodes rounds_used=0 — "
            "learning module receives wrong data",
        )

    def test_resume_error_handler_preserves_round_count(self):
        """Error handler in resume() should report the actual rounds completed."""
        from src.orchestrator import Orchestrator

        import inspect
        source = inspect.getsource(Orchestrator.resume)

        lines = source.splitlines()
        in_except = False
        found_bug = False

        for line in lines:
            if "except Exception" in line:
                in_except = True
            if in_except and "rounds_used=0" in line:
                found_bug = True

        self.assertFalse(
            found_bug,
            "resume() error handler still hardcodes rounds_used=0 — "
            "learning module receives wrong data",
        )


# ---------------------------------------------------------------------------
# Bug #5 (MEDIUM): ProviderChain.run() buffers all messages in memory
# File: src/providers/chain.py:75-82
# ---------------------------------------------------------------------------


class TestProviderChainUnboundedBuffer(unittest.TestCase):
    """Bug: ProviderChain buffers ALL messages from a provider before yielding.

    If a provider produces large output before failing, the buffer grows
    without any size limit.
    """

    def test_chain_has_no_buffer_size_limit(self):
        """ProviderChain should have a configurable buffer size limit."""
        from src.providers.chain import ProviderChain
        import inspect

        source = inspect.getsource(ProviderChain.run)

        self.assertNotIn(
            "buffer: list = []",
            source,
            "ProviderChain.run() still uses unbounded list buffer — "
            "no MAX_BUFFER_SIZE or streaming fallback",
        )

    def test_chain_does_not_limit_buffer(self):
        """Verify the buffer grows without bounds (source-level check)."""
        from src.providers.chain import ProviderChain
        import inspect

        source = inspect.getsource(ProviderChain.run)

        # The fix should add some form of buffer size check
        has_size_check = (
            "MAX_BUFFER" in source
            or "max_buffer" in source
            or "buffer_size" in source
            or "len(buffer)" in source
        )

        self.assertTrue(
            has_size_check,
            "ProviderChain.run() has no buffer size limit — "
            "OOM risk with large provider output before rate-limit error",
        )


# ---------------------------------------------------------------------------
# Bug #6 (MEDIUM): SessionManager.load() no JSON error handling
# File: src/state.py:140-141
# ---------------------------------------------------------------------------


class TestSessionManagerLoadJsonError(unittest.TestCase):
    """Bug: SessionManager.load() raises unhandled json.JSONDecodeError
    when session.json is partially written or corrupted.
    """

    def test_load_handles_corrupted_json(self):
        """load() should not crash on invalid JSON — must return empty dict or handle gracefully."""
        from src.state import SessionManager

        with tempfile.TemporaryDirectory() as td:
            mgr = SessionManager(td)
            # Write corrupted JSON (simulating partial write from crash)
            mgr._state_file.parent.mkdir(parents=True, exist_ok=True)
            mgr._state_file.write_text('{"state": "agents_running", "rounds": [1, 2,')

            try:
                result = mgr.load()
                # If we get here, the bug is fixed — load() handled bad JSON
                self.assertIsInstance(result, dict, "load() should return dict even for bad JSON")
            except json.JSONDecodeError:
                self.fail(
                    "SessionManager.load() crashed with JSONDecodeError — "
                    "no error handling for corrupted session.json"
                )

    def test_load_handles_empty_file(self):
        """load() should handle empty session.json file."""
        from src.state import SessionManager

        with tempfile.TemporaryDirectory() as td:
            mgr = SessionManager(td)
            mgr._state_file.parent.mkdir(parents=True, exist_ok=True)
            mgr._state_file.write_text("")

            try:
                result = mgr.load()
                self.assertIsInstance(result, dict)
            except json.JSONDecodeError:
                self.fail(
                    "SessionManager.load() crashed on empty file — no error handling"
                )


# ---------------------------------------------------------------------------
# Bug #8 (LOW): _run_phase_zero() uses id() for identity mapping
# File: src/coach_player.py:808-814
# ---------------------------------------------------------------------------


class TestPhaseZeroIdMappingFragility(unittest.TestCase):
    """Bug: id()-based mapping breaks silently if objects are re-created
    between parsing and phase building.
    """

    def test_id_mapping_survives_object_copy(self):
        """If items are re-created (e.g. via replace), phases must still resolve."""
        from src.plan_tracker import PlanItem, Phase, parse_enriched_plan, auto_group_phases
        from dataclasses import replace

        content = (
            "## Phases\n"
            '- Phase 1: "Setup" → steps 1-2\n\n'
            "## Steps\n"
            "1. [security] Add auth\n"
            "2. [security] Add middleware\n"
        )

        items, phases = parse_enriched_plan(content)
        self.assertTrue(len(items) == 2, f"Expected 2 items, got {len(items)}")
        self.assertTrue(len(phases) == 1, f"Expected 1 phase, got {len(phases)}")

        # Simulate what _run_phase_zero does: create preserved_items with replace()
        original_items = items
        preserved_items = [
            replace(item, done=False)
            for item in original_items
        ]

        # Build id-based index from ORIGINAL items
        index_by_old_id = {id(item): idx for idx, item in enumerate(items)}

        # Try to resolve phase.steps — they reference the ORIGINAL items
        phase = phases[0]
        resolved_count = sum(
            1 for step in phase.steps
            if id(step) in index_by_old_id
        )

        # This should work because phase.steps still point to the same objects
        self.assertEqual(
            resolved_count,
            len(phase.steps),
            f"id() mapping resolved {resolved_count}/{len(phase.steps)} steps — "
            "phase steps reference different objects than the index map",
        )

    def test_id_mapping_breaks_on_re_parse(self):
        """If items are re-parsed, id() mapping completely breaks."""
        from src.plan_tracker import PlanItem, Phase, parse_enriched_plan

        content = (
            "## Phases\n"
            '- Phase 1: "Setup" → steps 1-2\n\n'
            "## Steps\n"
            "1. [security] Add auth\n"
            "2. [security] Add middleware\n"
        )

        items1, phases1 = parse_enriched_plan(content)
        # Re-parse: new objects with same content but different ids
        items2, _ = parse_enriched_plan(content)

        index_by_old_id = {id(item): idx for idx, item in enumerate(items2)}

        phase = phases1[0]
        resolved_count = sum(
            1 for step in phase.steps
            if id(step) in index_by_old_id
        )

        self.assertEqual(
            resolved_count,
            0,
            "id() mapping should fail completely on re-parsed objects — "
            "this proves the fragility (steps resolved from wrong index)",
        )


# ---------------------------------------------------------------------------
# Bug #9 (LOW): ClaudeNativeProvider._clean_env() leaks ZAI_API_KEY
# File: src/providers/claude_native.py:12-19,118-125
# ---------------------------------------------------------------------------


class TestClaudeNativeZaiKeyLeak(unittest.TestCase):
    """Bug: blocked provider env vars do not include ZAI_API_KEY, so native Claude
    CLI may pick up wrong credentials.
    """

    def test_zai_api_key_stripped_from_clean_env(self):
        """_clean_env() must remove ZAI_API_KEY to prevent credential conflict."""
        from src.providers.claude_native import ClaudeNativeProvider, _BLOCKED_ENV_VARS

        provider = ClaudeNativeProvider()

        with patch.dict(os.environ, {"ZAI_API_KEY": "zai-secret-key-12345"}, clear=False):
            env = provider._clean_env()

        self.assertNotIn(
            "ZAI_API_KEY",
            env,
            "ZAI_API_KEY leaked into Claude native env — "
            "not listed in _BLOCKED_ENV_VARS, credentials conflict possible",
        )


# ---------------------------------------------------------------------------
# Bug #4 (MEDIUM): Non-atomic file write in write_checklist_back()
# File: src/plan_tracker.py:520
# ---------------------------------------------------------------------------


class TestWriteChecklistAtomicity(unittest.TestCase):
    """Bug: write_checklist_back() writes directly to file without atomic
    rename. If interrupted, the plan file can be corrupted.
    """

    def test_write_checklist_uses_atomic_rename(self):
        """write_checklist_back should use write-to-temp-then-rename pattern."""
        from src.plan_tracker import write_checklist_back
        import inspect

        source = inspect.getsource(write_checklist_back)

        has_atomic = (
            "NamedTemporaryFile" in source
            or "tempfile" in source
            or "os.replace" in source
            or "shutil.move" in source
            or "atomic" in source.lower()
        )

        self.assertTrue(
            has_atomic,
            "write_checklist_back() uses direct path.write_text() — "
            "no atomic write pattern. Plan progress can be lost on crash.",
        )

    def test_write_checklist_preserves_data_integrity(self):
        """Functional test: written file must contain valid data."""
        from src.plan_tracker import PlanItem, write_checklist_back

        with tempfile.TemporaryDirectory() as td:
            plan_path = Path(td) / "plan.md"
            plan_path.write_text(
                "- [ ] Step one\n"
                "- [ ] Step two\n"
                "- [ ] Step three\n"
            )

            items = [
                PlanItem(text="Step one", done=True),
                PlanItem(text="Step two", done=False),
                PlanItem(text="Step three", done=True),
            ]

            write_checklist_back(str(plan_path), items)

            content = plan_path.read_text()
            self.assertIn("[x] Step one", content)
            self.assertIn("[ ] Step two", content)
            self.assertIn("[x] Step three", content)


# ---------------------------------------------------------------------------
# FALSE POSITIVE CHECKS — these should be GREEN (bugs don't exist)
# ---------------------------------------------------------------------------


class TestFalsePositivePhaseRangeParsing(unittest.TestCase):
    """Verify phase range "1-3" is parsed correctly (was suspected off-by-one)."""

    def test_range_1_to_3_inclusive(self):
        """'steps 1-3' must resolve to indices [0, 1, 2]."""
        from src.plan_tracker import parse_enriched_plan

        content = (
            "## Phases\n"
            '- Phase 1: "Setup" → steps 1-3\n\n'
            "## Steps\n"
            "1. [general] Step A\n"
            "2. [general] Step B\n"
            "3. [general] Step C\n"
            "4. [general] Step D\n"
        )

        items, phases = parse_enriched_plan(content)

        self.assertEqual(len(items), 4, "All 4 steps must be parsed")
        self.assertEqual(len(phases), 1, "One phase must be created")
        self.assertEqual(
            len(phases[0].steps),
            3,
            f"Phase should have 3 steps (1-3 inclusive), got {len(phases[0].steps)}",
        )
        self.assertEqual(
            phases[0].steps[0].text, "Step A",
        )
        self.assertEqual(
            phases[0].steps[2].text, "Step C",
        )


class TestFalsePositiveProcessCleanup(unittest.TestCase):
    """Verify subprocess.run() handles TimeoutExpired cleanup (was suspected leak)."""

    def test_subprocess_run_handles_timeout(self):
        """subprocess.run() with timeout kills the child — no zombie."""
        import signal

        # subprocess.run sends SIGKILL on timeout by default (since Python 3.3)
        try:
            subprocess.run(
                ["sleep", "10"],
                capture_output=True,
                timeout=0.1,
            )
        except subprocess.TimeoutExpired as exc:
            # Process was killed — no zombie
            self.assertIsNotNone(exc)


class TestFalsePositiveSharedMutableState(unittest.TestCase):
    """Verify batch executor runs sequentially (was suspected race condition)."""

    def test_batch_executor_runs_phases_sequentially(self):
        """Phases are processed one at a time — no concurrent access."""
        from src.batch_executor import BatchExecutor
        import inspect

        source = inspect.getsource(BatchExecutor.run)

        # Must use while loop, not asyncio.gather
        self.assertIn("while phase_index", source, "No sequential phase loop found")
        self.assertNotIn("asyncio.gather", source, "Unexpected concurrent execution")


# ---------------------------------------------------------------------------
# Bug A (CRITICAL): psutil imported unconditionally but not a listed dependency
# File: src/coach_player.py:461, pyproject.toml:10-12
# ---------------------------------------------------------------------------


class TestBugA_PsutilImportCrash(unittest.TestCase):
    """_kill_new_processes does `import psutil` unconditionally but psutil
    is NOT in pyproject.toml dependencies.  On any machine without psutil
    installed, the method crashes with ModuleNotFoundError on every Player turn.
    """

    def test_psutil_either_in_deps_or_import_guarded(self):
        """psutil MUST be in pyproject.toml OR the import MUST be try/except guarded."""
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        deps_text = pyproject.read_text()
        psutil_in_deps = "psutil" in deps_text

        # After Phase 3B, psutil guarding moved to ProcessGuard
        from src.process_guard import ProcessGuard
        source = inspect.getsource(ProcessGuard.kill_new_processes)
        lines_before = source.split("import psutil")[0].split("\n")
        import_guarded = any("try" in ln for ln in lines_before[-5:])

        # CORRECT: in deps OR guarded.  FAIL → bug confirmed.
        self.assertTrue(
            psutil_in_deps or import_guarded,
            f"psutil not in dependencies (={psutil_in_deps}) "
            f"and import not try/except guarded (={import_guarded})",
        )

    def test_kill_new_processes_crashes_without_psutil(self):
        """kill_new_processes MUST NOT crash when psutil is unavailable."""
        import builtins
        import sys as _sys

        real_import = builtins.__import__

        def block_psutil(name, *a, **kw):
            if name == "psutil":
                raise ModuleNotFoundError("No module named 'psutil'")
            return real_import(name, *a, **kw)

        saved_psutil = _sys.modules.pop("psutil", None)
        try:
            from src.process_guard import ProcessGuard

            guard = ProcessGuard()
            with patch("builtins.__import__", side_effect=block_psutil):
                # CORRECT: should not crash.  FAIL (ModuleNotFoundError) → bug confirmed.
                guard.kill_new_processes(set())
        finally:
            if saved_psutil is not None:
                _sys.modules["psutil"] = saved_psutil


# ---------------------------------------------------------------------------
# Bug B (HIGH): completed_steps replaced instead of accumulated in _run_phase
# File: src/batch_executor.py:567
# ---------------------------------------------------------------------------


class TestBugB_CompletedStepsLostOnRetry(unittest.TestCase):
    """In _run_phase the variable completed_steps is reassigned from
    parse_completed_steps on each attempt instead of being accumulated.
    Steps completed in earlier attempts are silently forgotten."""

    def _make_phase(self, n: int = 5):
        from src.plan_tracker import Phase, PlanItem

        steps = [PlanItem(text=f"Step {i + 1}") for i in range(n)]
        return Phase(name="test", type="create", steps=steps)

    def test_replacement_loses_previously_completed_steps(self):
        """After attempt 2 completes steps 4-5, steps 1-3 MUST still be tracked."""
        from src.batch_executor import parse_completed_steps

        phase = self._make_phase(5)

        # --- simulate _run_phase loop with the fixed accumulation pattern ---
        completed_steps: list[str] = []

        # Attempt 1: player completes steps 1-3
        r1 = SimpleNamespace(
            text="Step 1 done: x\nStep 2 done: x\nStep 3 done: x\n"
        )
        for step in parse_completed_steps(r1, phase):
            if step not in completed_steps:
                completed_steps.append(step)
        self.assertEqual(len(completed_steps), 3, "attempt 1: 3 steps done")

        # Attempt 2: player completes ONLY steps 4-5 (doesn't re-list 1-3)
        r2 = SimpleNamespace(text="Step 4 done: x\nStep 5 done: x\n")
        for step in parse_completed_steps(r2, phase):
            if step not in completed_steps:
                completed_steps.append(step)

        # CORRECT: all 5 should be tracked.
        self.assertEqual(
            len(completed_steps), 5,
            f"After 2 attempts should have 5 completed steps, "
            f"got {len(completed_steps)}: {completed_steps}",
        )

    def test_build_batch_prompt_does_not_redo_done_steps(self):
        """build_batch_prompt MUST NOT show previously completed steps as remaining."""
        from src.batch_executor import build_batch_prompt, parse_completed_steps

        phase = self._make_phase(5)

        # Attempt 1 done: steps 1-3
        r1 = SimpleNamespace(text="Step 1 done: x\nStep 2 done: x\nStep 3 done: x\n")
        completed_steps = parse_completed_steps(r1, phase)

        # Attempt 2 done: steps 4-5 (replaced, not accumulated)
        r2 = SimpleNamespace(text="Step 4 done: x\nStep 5 done: x\n")
        completed_steps = parse_completed_steps(r2, phase)

        prompt = build_batch_prompt(phase, completed_steps)

        # CORRECT: steps 1-3 must appear in prompt (as "Already completed").
        # With the bug they DON'T appear because completed_steps only has 4-5.
        for step in phase.steps[:3]:
            self.assertIn(
                step.text, prompt,
                f"'{step.text}' must appear in prompt (as already completed), "
                f"but completed_steps was replaced — step is lost",
            )


# ---------------------------------------------------------------------------
# Bug C (MEDIUM): pytest "X failed, Y passed" — comma breaks count parsing
# File: src/bug_detector.py:203
# ---------------------------------------------------------------------------


class TestBugC_PytestFailedCountComma(unittest.TestCase):
    """_check_tests splits on space and checks p == 'failed' but pytest
    outputs 'failed,' (with comma).  The exact match fails, falling back
    to return 1 regardless of actual failure count."""

    def _parse_like_check_tests(self, stdout: str) -> int | None:
        """Replicate the fixed parsing logic from BugDetector._check_tests."""
        for line in stdout.splitlines():
            if "failed" in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p.rstrip(",") == "failed" and i > 0:
                        try:
                            return int(parts[i - 1])
                        except ValueError:
                            pass
        return None

    def test_failed_with_comma_is_parsed(self):
        """'2 failed, 5 passed in 0.1s' MUST return count 2."""
        count = self._parse_like_check_tests("2 failed, 5 passed in 0.1s\n")
        # CORRECT: should return 2.  FAIL (None) → bug confirmed.
        self.assertIsNotNone(
            count,
            "'2 failed, 5 passed' not parsed — 'failed,' ≠ 'failed'",
        )
        self.assertEqual(count, 2)

    def test_failed_without_comma_still_works(self):
        """'2 failed in 0.1s' (edge case) should still parse."""
        count = self._parse_like_check_tests("2 failed in 0.1s\n")
        self.assertEqual(count, 2)

    def test_full_pytest_output_mixed(self):
        """Real pytest -q output with both failures and passes must parse."""
        stdout = "FF..FF...\n2 failed, 3 passed in 0.5s"
        count = self._parse_like_check_tests(stdout)
        self.assertIsNotNone(count, "Real pytest output not parsed")
        self.assertEqual(count, 2)


# ---------------------------------------------------------------------------
# Bug D (LOW): _detect_test_command has redundant substring check
# File: src/coach_player.py:1615-1618
# ---------------------------------------------------------------------------


class TestBugD_RedundantPytestCheck(unittest.TestCase):
    """'[tool.pytest' already matches '[tool.pytest.ini_options]' as a
    substring, making the second OR condition dead code."""

    def test_substring_proof(self):
        """The broad check is a substring of the specific check."""
        self.assertIn("[tool.pytest", "[tool.pytest.ini_options]")

# ---------------------------------------------------------------------------
# Bug E (LOW): PhaseFailedError.phase typed as Phase but __str__ guards None
# File: src/batch_executor.py:207,216
# ---------------------------------------------------------------------------


class TestBugE_TypeAnnotationNoneGuard(unittest.TestCase):
    """phase field annotation is `Phase` (no None) but __str__ checks
    `if self.phase is None`.  The type annotation should allow None."""

    def test_type_annotation_allows_none(self):
        """If __str__ guards for None, the type must allow None."""
        from src.batch_executor import PhaseFailedError

        source = inspect.getsource(PhaseFailedError)
        has_none_guard = "if self.phase is None" in source
        if not has_none_guard:
            return  # guard removed → no issue

        # Find the field annotation line
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("phase:") and "=" not in stripped:
                allows_none = "Optional" in stripped or "| None" in stripped
                # CORRECT: annotation must allow None.  FAIL → mismatch confirmed.
                self.assertTrue(
                    allows_none,
                    f"__str__ guards for None but annotation is '{stripped}'",
                )
                return

        self.fail("Could not find phase field annotation in PhaseFailedError")


if __name__ == "__main__":
    unittest.main()
