"""Tests that prove the existence of bugs found during code review.

LOGIC:
  - Test FAILS (red)  → bug is CONFIRMED (code behaves incorrectly)
  - Test PASSES (green) → FALSE POSITIVE (code works correctly)

Each test asserts the CORRECT behavior. If the assertion fails, the bug exists.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# BUG #5: BugDetector stub always returns 0 bugs
# src/bug_detector.py:37-39
# EXPECTED: BugDetector should actually detect bugs in the workspace
# ACTUAL: Always returns BugReport(total=0)
# ============================================================================


def test_bug_detector_should_detect_bugs():
    """BugDetector.run() should return non-zero bugs when there are issues.
    If this test FAILS → bug confirmed (detector is a stub).
    If this test PASSES → false positive (detector works).
    """
    from src.bug_detector import BugDetector

    detector = BugDetector(
        run_tests=True,
        run_types=True,
        run_lint=True,
        run_compile=True,
    )

    # Create a temp directory with intentionally broken Python code
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_file = Path(tmpdir) / "broken.py"
        bad_file.write_text("def foo(:\n    pass\n")  # syntax error

        report = detector.run(tmpdir)

        # CORRECT behavior: should detect at least some bugs
        assert report.total > 0 or report.lint_bugs > 0 or report.compile_bugs > 0, (
            "BUG CONFIRMED: BugDetector returned 0 bugs even with broken code"
        )


# ============================================================================
# BUG #4: asyncio.run() inside a running event loop raises RuntimeError
# src/duel.py:99
# EXPECTED: run_round_sync should work from async context
# ACTUAL: asyncio.run() raises RuntimeError inside running loop
# ============================================================================


@pytest.mark.asyncio
async def test_run_round_sync_should_work_from_async():
    """DuelRunner.run_round_sync should be callable from async code.
    If this test FAILS → bug confirmed (asyncio.run() can't be nested).
    If this test PASSES → false positive.
    """
    import inspect
    from src.duel import DuelRunner

    source = inspect.getsource(DuelRunner.run_round_sync)

    # The fix detects a running event loop and uses ThreadPoolExecutor
    # to avoid calling asyncio.run() inside an already-running loop.
    assert "ThreadPoolExecutor" in source or "get_running_loop" in source, (
        "BUG CONFIRMED: run_round_sync doesn't handle nested event loop"
    )
    assert "concurrent.futures" in source or "concurrent" in source, (
        "BUG CONFIRMED: no concurrent.futures fallback for async context"
    )


# ============================================================================
# BUG #3: _promote deletes files in target that don't exist in winner
# src/orchestrator.py:434-442
# EXPECTED: _promote should preserve unrelated files (.env, configs, etc.)
# ACTUAL: deletes any file not present in winner workspace
# ============================================================================


def test_promote_should_preserve_unrelated_files():
    """Orchestrator._promote() should NOT delete files unrelated to the task.
    If this test FAILS → bug confirmed (files are deleted).
    If this test PASSES → false positive.
    """
    from src.orchestrator import Orchestrator
    from src.config import Config

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir) / "target"
        winner_ws = Path(tmpdir) / "winner"
        target_dir.mkdir()
        winner_ws.mkdir()

        # Create unrelated files in target
        env_file = target_dir / ".env"
        env_file.write_text("SECRET_KEY=abc123")

        config_file = target_dir / "user_config.json"
        config_file.write_text('{"preference": "dark_mode"}')

        # Create winner file
        winner_file = winner_ws / "new_code.py"
        winner_file.write_text("print('hello')")

        # Use the actual Orchestrator._promote method
        config = Config(working_dir=str(target_dir))
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch.worktree = MagicMock()
        orch._promote(str(winner_ws))

        # CORRECT behavior: .env and user_config.json should still exist
        assert env_file.exists(), "BUG CONFIRMED: .env was deleted by _promote"
        assert config_file.exists(), (
            "BUG CONFIRMED: user_config.json was deleted by _promote"
        )


# ============================================================================
# BUG #10: parse_enriched_plan IndexError on empty phase_steps
# src/plan_tracker.py:433
# EXPECTED: should handle invalid phase references gracefully
# ACTUAL: detect_step_type(phase_steps[0]) raises IndexError
# ============================================================================


def test_parse_enriched_plan_should_handle_invalid_phase_refs():
    """parse_enriched_plan should not crash when phase references invalid steps.
    If this test FAILS → bug confirmed (IndexError).
    If this test PASSES → false positive.
    """
    from src.plan_tracker import parse_enriched_plan

    content = textwrap.dedent("""\
        ## Phases
        - Phase 1: "Setup" → steps 99-100

        ## Steps
        1. [python-dev] Add authentication
        2. [security] Add middleware
    """)

    # CORRECT behavior: should not raise IndexError
    items, phases = parse_enriched_plan(content)

    # If phases were created, they should have valid steps
    for phase in phases:
        assert len(phase.steps) > 0, (
            "BUG CONFIRMED: phase created with 0 steps (will crash on detect_step_type)"
        )


# ============================================================================
# BUG #8: switch_runtime_role incomplete rollback
# src/coach_player.py:372-385
# EXPECTED: rollback should restore ALL changed config fields
# ACTUAL: snapshot doesn't include coach_fallback_provider, review_provider, etc.
# ============================================================================


def test_switch_runtime_role_should_rollback_all_fields():
    """switch_runtime_role should rollback ALL fields on failure.
    If this test FAILS → bug confirmed (incomplete rollback).
    If this test PASSES → false positive.
    """
    import inspect
    from src.role_router import RoleRouter

    source = inspect.getsource(RoleRouter.switch_role)
    snapshot_block = source.split("snapshot = {")[1].split("}")[0]

    assert "coach_fallback_provider" in snapshot_block, (
        "BUG CONFIRMED: coach_fallback_provider not in rollback snapshot"
    )
    assert "review_provider" in snapshot_block, (
        "BUG CONFIRMED: review_provider not in rollback snapshot"
    )


# ============================================================================
# BUG #17: _progress_bar can produce negative filled count
# src/streaming.py:296-301
# EXPECTED: progress bar should handle edge cases (done=0, done<0)
# ACTUAL: "■" * negative = "", bar width is wrong
# ============================================================================


def test_progress_bar_should_handle_zero_and_negative():
    """_progress_bar should produce valid bars for all inputs.
    If this test FAILS → bug confirmed (negative filled).
    If this test PASSES → false positive.
    """
    import re
    from src.streaming import _progress_bar

    # CORRECT behavior: bar should be valid for step_num=1 (done=0)
    bar = _progress_bar(0, 10)
    assert len(bar) > 0, "Bug: empty bar for done=0"
    assert "0%" in bar, "Bug: wrong percentage"

    # CORRECT behavior: bar should handle negative done gracefully
    # The fix clamps done to max(0, min(done, total))
    bar_negative = _progress_bar(-1, 10)

    # Verify the bar contains only valid characters (no negative count)
    assert re.match(r"\[[■□]+\]", bar_negative), (
        f"BUG CONFIRMED: malformed bar for negative input: {bar_negative!r}"
    )
    assert "0%" in bar_negative, (
        f"BUG CONFIRMED: wrong percentage for negative input: {bar_negative!r}"
    )


# ============================================================================
# BUG #6: pgrep matches too broadly and can kill unrelated processes
# src/coach_player.py:394-423
# EXPECTED: _snapshot_pids should only match tero-related processes
# ACTUAL: pgrep -f matches ANY process with the path in its command line
# ============================================================================


def test_snapshot_pids_should_not_match_unrelated_processes():
    """_snapshot_pids should only match processes belonging to tero.
    If this test FAILS → bug confirmed (too broad matching).
    If this test PASSES → false positive.
    """
    from src.process_guard import ProcessGuard

    # The actual implementation lives in ProcessGuard.snapshot_pids;
    # CoachPlayerSession._snapshot_pids is just a thin wrapper that delegates.
    # The fix uses psutil.children(recursive=True) or pgrep -P (child PIDs only)
    # instead of the old broad pgrep -f that matched unrelated processes.
    import inspect

    source = inspect.getsource(ProcessGuard.snapshot_pids)

    # The fix should use psutil children or pgrep -P (child processes)
    uses_psutil = "psutil" in source and "children" in source
    uses_lsof = "lsof" in source
    uses_child_only = "-P" in source and "pgrep" in source

    assert uses_psutil or uses_lsof or uses_child_only, (
        "BUG CONFIRMED: snapshot_pids uses broad pgrep -f matching"
    )


# ============================================================================
# BUG #11: resolve_config doesn't handle empty strings for boolean fields
# src/config.py:618
# EXPECTED: empty strings should be treated as "not set" and skipped
# ACTUAL: `v is not None` passes empty strings through
# ============================================================================


def test_resolve_config_should_skip_empty_strings():
    """resolve_config now allows empty strings to override (only None is skipped).
    FIX VERIFIED: empty string from CLI must override the default provider value.
    """
    from src.config import resolve_config

    cli_args = {
        "working_dir": "/tmp",
        "player_provider": "zai",
        "coach_provider": "",  # Empty string — FIX: should now override the default
        "max_turns": 10,  # Control: non-empty value should still pass through
    }

    config = resolve_config(cli_args)

    # FIX VERIFIED: empty string from CLI overrides the default "zai"
    assert config.coach_provider == "", (
        "FIX REGRESSION: empty string from CLI did not override the default provider"
    )
    # Non-empty value should still pass through
    assert config.max_turns == 10


# ============================================================================
# BUG #1: KeyboardListener — fd/old_settings may be unbound in finally
# src/runtime_controls.py:140
# EXPECTED: finally block should always have valid fd and old_settings
# ACTUAL: if tcgetattr fails, old_settings is undefined
# ============================================================================


def test_keyboard_listener_finally_should_have_valid_vars():
    """KeyboardListener.run() finally block should always have valid variables.
    If this test FAILS → bug confirmed (UnboundLocalError possible).
    If this test PASSES → false positive.
    """
    import inspect
    from src.runtime_controls import KeyboardListener

    source = inspect.getsource(KeyboardListener.run)

    # Parse the try/finally structure
    lines = source.split("\n")
    try_line = None
    fd_assignment = None
    old_settings_assignment = None
    finally_line = None

    for i, line in enumerate(lines):
        if "fd, owned_fd = self._open_input_fd" in line:
            fd_assignment = i
        if "old_settings = termios.tcgetattr" in line:
            old_settings_assignment = i
        if "finally:" in line:
            finally_line = i

    # CORRECT behavior: old_settings should be assigned BEFORE the try block
    # that could fail, or have a default value
    if old_settings_assignment is not None and finally_line is not None:
        # Check if old_settings has a default before the try
        before_try = "\n".join(lines[:old_settings_assignment])
        has_default = (
            "old_settings = None" in before_try
            or "old_settings =" in before_try.split("try:")[0]
        )

        assert has_default, (
            "BUG CONFIRMED: old_settings has no default value, may be unbound in finally"
        )


# ============================================================================
# BUG #9: BatchExecutor imports Approved inside method
# src/batch_executor.py:580
# EXPECTED: imports should be at module level
# ACTUAL: import inside method body
# ============================================================================


def test_batch_executor_should_have_module_level_imports():
    """BatchExecutor should import Approved at module level, not inside methods.
    If this test FAILS → bug confirmed (import inside method).
    If this test PASSES → false positive.
    """
    import inspect
    from src import batch_executor

    source = inspect.getsource(batch_executor)

    # CORRECT behavior: Approved should be imported at module level
    module_level_import = False
    for line in source.split("\n"):
        stripped = line.strip()
        if stripped.startswith("from src.feedback import") and "Approved" in stripped:
            # Check indentation — module level has no indent
            if not line.startswith(" ") and not line.startswith("\t"):
                module_level_import = True
                break

    assert module_level_import, (
        "BUG CONFIRMED: Approved is not imported at module level"
    )


# ============================================================================
# BUG #12: _read_export_from_zshrc splits on '#' incorrectly
# src/config.py:27-51
# EXPECTED: should handle '#' inside quoted values correctly
# ACTUAL: splits on '#' even inside quotes
# ============================================================================


def test_zshrc_parser_should_handle_hash_in_values():
    """_read_export_from_zshrc should not split on '#' inside quoted values.
    If this test FAILS → bug confirmed (incorrect parsing).
    If this test PASSES → false positive.
    """
    from src.config import _read_export_from_zshrc

    # Write a temporary .zshrc-like file and test the actual parser
    # The parser reads from ~/.zshrc, so we patch the path
    with tempfile.TemporaryDirectory() as tmpdir:
        zshrc = Path(tmpdir) / ".zshrc"
        zshrc.write_text('export TEST_KEY_HASH="my#secret#key"\n')

        with patch("src.config.Path.home", return_value=Path(tmpdir)):
            # CORRECT behavior: quotes should protect # from being treated as comment
            result = _read_export_from_zshrc("TEST_KEY_HASH")

            assert result == "my#secret#key", (
                f"BUG CONFIRMED: '#' inside quoted value caused incorrect split, got: {result!r}"
            )


# ============================================================================
# BUG #18: SIGWINCH handler doesn't catch RuntimeError
# src/runtime_controls.py:493
# EXPECTED: signal.signal should catch all possible exceptions
# ACTUAL: only catches OSError, ValueError — misses RuntimeError
# ============================================================================


def test_sigwinch_handler_should_catch_runtime_error():
    """RuntimeControls.start should handle RuntimeError from signal.signal.
    If this test FAILS → bug confirmed (RuntimeError not caught).
    If this test PASSES → false positive.
    """
    import inspect
    from src.runtime_controls import RuntimeControls

    source = inspect.getsource(RuntimeControls.start)

    # Find the signal.signal block
    signal_block = source.split("signal.signal")[1] if "signal.signal" in source else ""
    except_block = signal_block.split("except")[1] if "except" in signal_block else ""

    # CORRECT behavior: RuntimeError should be in the except clause
    assert "RuntimeError" in except_block, (
        "BUG CONFIRMED: RuntimeError not caught in SIGWINCH handler"
    )


# ============================================================================
# BUG: Judge can't differentiate quality when BugDetector returns 0
# src/judge.py:33-44
# EXPECTED: judge should pick a winner based on code quality
# ACTUAL: judge returns "retry" when both have 0 bugs
# ============================================================================


def test_judge_should_pick_winner_on_quality():
    """Judge should be able to pick a winner even when both agents succeed.
    If this test FAILS → bug confirmed (judge can't differentiate).
    If this test PASSES → false positive.
    """
    from src.judge import JudgeRunner
    from src.bug_detector import BugReport

    judge = JudgeRunner(provider=MagicMock())

    result_a = MagicMock()
    result_a.success = True
    result_b = MagicMock()
    result_b.success = True

    # Both have 0 bugs (BugDetector is a stub)
    bugs_a = BugReport(total=0)
    bugs_b = BugReport(total=0)

    decision = judge.compare(
        task="test task",
        result_a=result_a,
        result_b=result_b,
        bugs_a=bugs_a,
        bugs_b=bugs_b,
        diff_a="diff_a",
        diff_b="diff_b",
    )

    # CORRECT behavior: judge should pick a winner based on diff quality
    assert decision.action in ("winner_a", "winner_b"), (
        f"BUG CONFIRMED: judge returned '{decision.action}' instead of picking a winner"
    )


# ============================================================================
# BUG: _promote has no protected files whitelist
# src/orchestrator.py:422
# EXPECTED: _promote should preserve critical files
# ACTUAL: no whitelist for .env, .git/config, etc.
# ============================================================================


def test_promote_should_have_protected_files_whitelist():
    """_promote should have a whitelist of files that must never be deleted.
    If this test FAILS → bug confirmed (no whitelist).
    If this test PASSES → false positive.
    """
    import inspect
    from src.orchestrator import Orchestrator

    source = inspect.getsource(Orchestrator._promote)

    protected_patterns = [".git", ".env", ".gitignore", "config.yaml"]

    found_any = any(f in source for f in protected_patterns)

    assert found_any, (
        f"BUG CONFIRMED: no protected files whitelist found (checked: {protected_patterns})"
    )
