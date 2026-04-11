"""Tests that prove bugs found during code review.

LOGIC:
  - Test FAILS (red)  → bug EXISTS (code behaves incorrectly)
  - Test PASSES (green) → bug is FIXED or false positive

Each test asserts CORRECT behavior. If the assertion fails, the bug exists.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass, field, replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# BUG #1 (HIGH): _run_phase_zero uses id() for matching PlanItem objects
# across independently created lists — identity never matches
# File: src/coach_player.py:796-802
# EXPECTED: phase.steps should be populated with matching PlanItem objects
# ACTUAL: all phase.steps become empty because id() never matches
# ============================================================================


def test_run_phase_zero_id_matching_should_preserve_phase_steps():
    """Phase steps should be preserved after _run_phase_zero enrichment.

    The bug: index_by_old_id uses id(item) from parse_enriched_plan output,
    but phase.steps come from the same parse_enriched_plan call — so they
    SHOULD match. Let's verify the actual behavior with real objects.
    """
    from src.plan_tracker import PlanItem, Phase, parse_enriched_plan

    plan_content = textwrap.dedent("""\
        ## Steps
        1. Setup the project
        2. Add authentication
        3. Write tests
        4. Deploy

        ## Phases
        - Phase 1: "Setup" → steps 1-2
        - Phase 2: "Ship" → steps 3-4
    """)

    items, phases = parse_enriched_plan(plan_content)

    # The bug: after _run_phase_zero, items are replaced with new objects
    # via replace(), but phase.steps still reference old objects.
    # Simulate what _run_phase_zero does:
    preserved_items = [replace(item, done=False) for item in items]
    index_by_old_id = {id(item): idx for idx, item in enumerate(items)}

    for phase in phases:
        phase.steps = [
            preserved_items[index_by_old_id[id(step)]]
            for step in phase.steps
            if id(step) in index_by_old_id
        ]

    # CORRECT behavior: phases should have their steps populated
    for phase in phases:
        assert len(phase.steps) > 0, (
            f"BUG CONFIRMED: phase '{phase.name}' has 0 steps after id() matching — "
            "id() identity check fails across object copies"
        )


# ============================================================================
# BUG #2 (HIGH): claude_native.py reads all stdout before stderr — deadlock
# File: src/providers/claude_native.py:69-82
# EXPECTED: should read stdout and stderr concurrently to avoid deadlock
# ACTUAL: reads all stdout first, then stderr — classic pipe deadlock
# ============================================================================


def test_claude_native_should_not_deadlock_on_large_stderr():
    """ClaudeNativeProvider.run() should not deadlock with large stderr output.

    The bug: code reads all stdout (async for line in proc.stdout) before
    reading stderr (await proc.stderr.read()). If subprocess fills stderr
    pipe buffer, it blocks, causing deadlock.
    """
    import inspect
    from src.providers.claude_native import ClaudeNativeProvider

    source = inspect.getsource(ClaudeNativeProvider.run)

    # CORRECT behavior: should use concurrent reading or merge stderr into stdout
    uses_concurrent_reading = (
        "stderr"
        not in source.split("async for line in proc.stdout")[1].split(
            "await proc.wait()"
        )[0]
        or "merge_stderr" in source
        or "STDERR" in source
    )

    # Check if stderr is read AFTER stdout loop (the bug pattern)
    stdout_loop = (
        source.split("async for line in proc.stdout")[1]
        if "async for line in proc.stdout" in source
        else ""
    )
    stderr_read_after = (
        "await proc.stderr.read()" in stdout_loop.split("await proc.wait()")[0]
        if "await proc.wait()" in stdout_loop
        else False
    )

    assert not stderr_read_after, (
        "BUG CONFIRMED: stderr is read after stdout loop completes — "
        "this causes deadlock when stderr pipe buffer fills up"
    )


# ============================================================================
# BUG #3 (MEDIUM): _snapshot_pids uses lsof which matches too broadly
# File: src/coach_player.py:421-422
# EXPECTED: should only match processes with cwd in working_dir
# ACTUAL: lsof with directory argument matches any process with files open
#         in that directory tree
# ============================================================================


def test_snapshot_pids_lsof_should_not_match_unrelated_processes():
    """_snapshot_pids should only match processes whose cwd is in working_dir.

    The bug: lsof "+c" "0" "-Fn" working_dir matches processes that have
    ANY file open in the directory tree, not just cwd.
    """
    from src.coach_player import CoachPlayerSession

    # Create a mock config
    config = MagicMock()
    config.working_dir = "/tmp/tero-test-workspace"

    session = CoachPlayerSession.__new__(CoachPlayerSession)
    session.config = config

    # Simulate lsof output that includes unrelated processes
    # (processes that happen to have a file open in the directory)
    fake_lsof_output = "\n".join(
        [
            "p12345",  # our target process
            "p67890",  # unrelated process that has a file open in the dir
            "p11111",  # another unrelated process
        ]
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_lsof_output)
        pids = session._snapshot_pids()

    # CORRECT behavior: should only return PIDs of processes with cwd in working_dir
    # BUG: returns ALL PIDs from lsof output including unrelated ones
    # We can't easily test the real lsof behavior, but we can check the implementation
    import inspect

    source = inspect.getsource(CoachPlayerSession._snapshot_pids)

    # The implementation should filter PIDs by checking /proc/PID/cwd
    # or use a more targeted approach
    checks_proc_cwd = "/proc" in source and "cwd" in source
    uses_pgrep_child_only = "pgrep" in source and "-P" in source

    # If using lsof, should filter results; if using pgrep, should use -P (child only)
    if "lsof" in source:
        # lsof alone is too broad — should have additional filtering
        has_filtering = (
            "cwd" in source or "/proc" in source or "filter" in source.lower()
        )
        assert has_filtering, (
            "BUG CONFIRMED: lsof matches too broadly without additional filtering — "
            "unrelated processes with files open in the directory will be matched"
        )


# ============================================================================
# BUG #4 (MEDIUM): _kill_new_processes doesn't kill process trees
# File: src/coach_player.py:456-466
# EXPECTED: should kill entire process tree including grandchildren
# ACTUAL: only sends SIGTERM to direct PIDs, orphaning children
# ============================================================================


def test_kill_new_processes_should_kill_process_tree():
    """_kill_new_processes should kill entire process trees.

    The bug: only kills direct PIDs, leaving orphaned grandchildren running.
    """
    import inspect
    from src.coach_player import CoachPlayerSession

    source = inspect.getsource(CoachPlayerSession._kill_new_processes)

    # CORRECT behavior: should use process group kill or recursive child killing
    uses_process_group = (
        "killpg" in source or "process_group" in source or "-GROUP" in source
    )
    uses_pstree = "pstree" in source or "psutil" in source or "children" in source
    uses_recursive = "recursive" in source.lower() or "descendant" in source.lower()

    assert uses_process_group or uses_pstree or uses_recursive, (
        "BUG CONFIRMED: _kill_new_processes only kills direct PIDs — "
        "grandchildren processes are orphaned and continue running"
    )


# ============================================================================
# BUG #5 (MEDIUM): PlanResetRequested not handled during coach turn
# File: src/batch_executor.py:573-580
# EXPECTUAL: should catch PlanResetRequested and propagate it properly
# ACTUAL: PlanResetRequested from _run_coach_turn_for_phase causes phase failure
# ============================================================================


def test_batch_executor_should_handle_plan_reset_during_coach_turn():
    """BatchExecutor._run_phase should handle PlanResetRequested from coach turn.

    The bug: if runtime controls trigger a reset during the coach turn,
    PlanResetRequested propagates up and phase is treated as failed.
    """
    import inspect
    from src.batch_executor import BatchExecutor

    source = inspect.getsource(BatchExecutor._run_phase)

    # Find the coach turn call block
    coach_turn_call = "_run_coach_turn_for_phase"
    if coach_turn_call not in source:
        return  # Method structure changed

    # Check if PlanResetRequested is caught around the coach turn
    # Split source around the coach turn call
    lines = source.split("\n")
    coach_turn_line = None
    for i, line in enumerate(lines):
        if coach_turn_call in line:
            coach_turn_line = i
            break

    assert coach_turn_line is not None, "Could not find coach turn call"

    # Check if there's a try/except for PlanResetRequested around this call
    # Look backwards from coach_turn_line for a try block
    has_reset_handler = False
    for i in range(coach_turn_line, -1, -1):
        if "try:" in lines[i]:
            # Check forward from this try for PlanResetRequested handler
            for j in range(i, min(i + 100, len(lines))):
                if "PlanResetRequested" in lines[j] and "except" in lines[j]:
                    has_reset_handler = True
                    break
            break

    assert has_reset_handler, (
        "BUG CONFIRMED: PlanResetRequested from coach turn is not caught — "
        "reset during coach review causes phase failure instead of proper reset"
    )


# ============================================================================
# BUG #6 (MEDIUM): _promote doesn't handle file deletion errors
# File: src/orchestrator.py:443-452
# EXPECTED: should handle PermissionError/OSError when deleting files
# ACTUAL: item.unlink() without error handling crashes on locked files
# ============================================================================


def test_promote_should_handle_file_deletion_errors():
    """Orchestrator._promote should handle errors when deleting files.

    The bug: item.unlink() doesn't handle PermissionError or OSError,
    crashing when files are locked or in use.
    """
    import inspect
    from src.orchestrator import Orchestrator

    source = inspect.getsource(Orchestrator._promote)

    # Find the unlink call
    if "unlink()" not in source:
        return  # Implementation changed

    # Check if unlink is wrapped in try/except
    unlink_section = source.split("unlink()")[0] if "unlink()" in source else ""

    # Look for try/except around the unlink block
    lines = source.split("\n")
    unlink_line = None
    for i, line in enumerate(lines):
        if "unlink()" in line:
            unlink_line = i
            break

    assert unlink_line is not None, "Could not find unlink call"

    # Check if there's error handling around unlink
    has_error_handling = False
    for i in range(unlink_line, -1, -1):
        if "try:" in lines[i]:
            # Check forward for except block
            for j in range(i, min(i + 20, len(lines))):
                if "except" in lines[j] and (
                    "PermissionError" in lines[j]
                    or "OSError" in lines[j]
                    or "Exception" in lines[j]
                ):
                    has_error_handling = True
                    break
            break

    assert has_error_handling, (
        "BUG CONFIRMED: _promote doesn't handle PermissionError/OSError on file deletion — "
        "crashes when files are locked or in use"
    )


# ============================================================================
# BUG #7 (MEDIUM): RunRecorder uses relative path instead of config.working_dir
# File: src/orchestrator.py:69
# EXPECTED: knowledge_dir should be relative to config.working_dir
# ACTUAL: uses ".g3/knowledge" relative to CWD
# ============================================================================


def test_orchestrator_recorder_should_use_config_working_dir():
    """Orchestrator should create RunRecorder with path relative to config.working_dir.

    The bug: RunRecorder() defaults to ".g3/knowledge" relative to CWD,
    not config.working_dir.
    """
    import inspect
    from src.orchestrator import Orchestrator

    source = inspect.getsource(Orchestrator.__init__)

    # Check if RunRecorder is created with a path based on config.working_dir
    if "RunRecorder()" in source:
        # Bug: no path argument — uses default relative to CWD
        assert False, (
            "BUG CONFIRMED: RunRecorder() created with no path argument — "
            "uses '.g3/knowledge' relative to CWD instead of config.working_dir"
        )

    # If RunRecorder is created with a path, check it's based on config.working_dir
    if "RunRecorder" in source:
        recorder_line = [l for l in source.split("\n") if "RunRecorder" in l][0]
        uses_config_path = "working_dir" in recorder_line or "config." in recorder_line
        assert uses_config_path, (
            "BUG CONFIRMED: RunRecorder path not based on config.working_dir"
        )


# ============================================================================
# BUG #8 (MEDIUM): state.py load() crashes on corrupted JSON
# File: src/state.py:141
# EXPECTED: should handle json.JSONDecodeError gracefully
# ACTUAL: crashes with unhandled JSONDecodeError
# ============================================================================


def test_session_manager_load_should_handle_corrupted_json():
    """SessionManager.load() should handle corrupted JSON files gracefully.

    The bug: json.loads() raises JSONDecodeError on corrupted files,
    which is not caught.
    """
    from src.state import SessionManager

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state_file.write_text("{corrupted json!!!")

        manager = SessionManager(Path(tmpdir))

        # CORRECT behavior: should not crash, should return empty state or handle gracefully
        try:
            result = manager.load()
            # If it doesn't crash, check it returns something reasonable
            assert isinstance(result, dict), (
                "BUG CONFIRMED: load() returned non-dict after corrupted JSON"
            )
        except json.JSONDecodeError:
            pytest.fail(
                "BUG CONFIRMED: load() crashes with JSONDecodeError on corrupted state file — "
                "should handle gracefully with fallback to empty state"
            )


# ============================================================================
# BUG #9 (MEDIUM): bug_detector lint check has no real fallback
# File: src/bug_detector.py:83
# EXPECTED: should have multiple fallback lint tools
# ACTUAL: tuple contains only flake8 — if missing, returns 0 (false negative)
# ============================================================================


def test_bug_detector_lint_should_have_real_fallback():
    """BugDetector._check_lint should have actual fallback tools.

    The bug: the for loop implies multiple fallbacks, but tuple has only
    one command. If flake8 is not installed, returns 0 (false negative).
    """
    import inspect
    from src.bug_detector import BugDetector

    source = inspect.getsource(BugDetector._check_lint)

    # Count commands in the `commands` tuple by extracting the block
    # between the assignment and the for-loop, then counting `), (` separators.
    commands_idx = source.find("commands")
    for_idx = source.find("for ", commands_idx)
    commands_block = source[commands_idx:for_idx]
    command_count = commands_block.count("), (") + 1 if "commands" in commands_block else 0

    # CORRECT behavior: should have at least 2 fallback commands
    assert command_count >= 2, (
        f"BUG CONFIRMED: _check_lint has only {command_count} command(s) — "
        "if flake8 is not installed, returns 0 bugs (false negative)"
    )


# ============================================================================
# BUG #10 (MEDIUM): batch_executor doesn't check session interruption
# during retry loop
# File: src/batch_executor.py:495-590
# EXPECTED: should check self.session._interrupted between attempts
# ACTUAL: continues retrying even after user interrupt
# ============================================================================


def test_batch_executor_should_respect_interruption_during_retry():
    """BatchExecutor._run_phase should check for session interruption between retries.

    The bug: the retry loop doesn't check self.session._interrupted,
    so user interrupts are ignored during batch phase retries.
    """
    import inspect
    from src.batch_executor import BatchExecutor

    source = inspect.getsource(BatchExecutor._run_phase)

    # Check if _interrupted is checked inside the retry loop
    checks_interrupted = "_interrupted" in source

    assert checks_interrupted, (
        "BUG CONFIRMED: _run_phase doesn't check session._interrupted — "
        "user interrupts are ignored during batch phase retry loop"
    )


# ============================================================================
# BUG #11 (MEDIUM): plan_tracker write_checklist_back corrupts file
# when file doesn't end with newline
# File: src/plan_tracker.py:484
# EXPECTED: should handle files without trailing newline correctly
# ACTUAL: insert_at calculation can insert in wrong place
# ============================================================================


def test_write_checklist_back_should_handle_no_trailing_newline():
    """write_checklist_back should handle files without trailing newline.

    The bug: if file doesn't end with newline, split("\\n") produces
    ["line1", "line2"] (no empty string), and insert_at calculation
    can append items incorrectly.
    """
    from src.plan_tracker import PlanItem, write_checklist_back

    with tempfile.TemporaryDirectory() as tmpdir:
        plan_file = Path(tmpdir) / "plan.md"

        # File WITHOUT trailing newline
        plan_file.write_text("- [ ] Step one\n- [ ] Step two")

        items = [
            PlanItem(text="Step one", done=True),
            PlanItem(text="Step two", done=False),
            PlanItem(text="Step three", done=False),  # Extra item
        ]

        write_checklist_back(str(plan_file), items)

        content = plan_file.read_text()

        # CORRECT behavior: all items should be present and correctly formatted
        assert "- [x] Step one" in content, (
            "BUG CONFIRMED: first item not correctly updated"
        )
        assert "- [ ] Step two" in content, (
            "BUG CONFIRMED: second item not correctly updated"
        )
        assert "- [ ] Step three" in content, (
            "BUG CONFIRMED: extra item not correctly appended — "
            "file corruption when no trailing newline"
        )


# ============================================================================
# BUG #12 (LOW): g3.py has dead run_history wrapper
# File: g3.py:41-42
# EXPECTED: all functions should be used
# ACTUAL: run_history() is never called — main() calls _shared_run_history directly
# ============================================================================


def test_g3_should_not_have_dead_run_history_wrapper():
    """g3.py should not have unused run_history wrapper function.

    The bug: run_history() at line 41 delegates to _shared_run_history,
    but main() at line 34 calls _shared_run_history directly.
    """
    import inspect
    import g3

    source = inspect.getsource(g3)

    # Check if run_history is defined
    has_run_history_def = "def run_history(" in source

    if not has_run_history_def:
        return  # Already fixed

    # Check if run_history is actually called anywhere (not just defined)
    # Count definitions vs calls
    def_count = source.count("def run_history(")
    call_count = source.count("run_history(") - def_count  # Exclude the definition

    assert call_count > 0, (
        "BUG CONFIRMED: run_history() is defined but never called — dead code"
    )


# ============================================================================
# BUG #13 (MEDIUM): providers/chain.py buffers all messages before yield
# File: src/providers/chain.py:71-75
# EXPECTED: should stream messages as they arrive
# ACTUAL: buffers entire provider output before yielding — no streaming, high memory
# ============================================================================


def test_provider_chain_should_stream_not_buffer():
    """ProviderChain.run() should stream messages, not buffer them all.

    The bug: collects all messages from provider.run() into buffered_messages
    list before yielding — no streaming, high memory for long sessions.
    """
    import inspect
    from src.providers.chain import ProviderChain

    source = inspect.getsource(ProviderChain.run)

    # Check if buffering is used
    uses_buffering = "buffered_messages" in source

    if not uses_buffering:
        return  # Already fixed

    # If buffering exists, check if there's a mechanism to limit memory
    has_streaming_fallback = (
        "yield" in source.split("buffered_messages")[0]
        if "buffered_messages" in source
        else True
    )

    # The bug: ALL messages are buffered before any are yielded
    buffer_block = (
        source.split("buffered_messages = []")[1]
        if "buffered_messages = []" in source
        else ""
    )
    yield_after_buffer = "for msg in buffered_messages" in buffer_block
    yield_during_collection = False

    # Check if there's any yield inside the async for loop (before buffering completes)
    async_for_block = (
        buffer_block.split("async for msg in")[1]
        if "async for msg in" in buffer_block
        else ""
    )
    yield_before_buffer_complete = (
        "yield" in async_for_block.split("for msg in buffered_messages")[0]
        if "for msg in buffered_messages" in async_for_block
        else False
    )

    assert yield_before_buffer_complete or not uses_buffering, (
        "BUG CONFIRMED: ProviderChain buffers ALL messages before yielding any — "
        "no streaming output, high memory usage for long sessions"
    )


# ============================================================================
# BUG #14 (MEDIUM): providers/opencode.py combines tool_use and tool_result
# in single message
# File: src/providers/opencode.py:228-249
# EXPECTED: tool_use and tool_result should be separate messages
# ACTUAL: combined in single AdaptedMessage content list
# ============================================================================


def test_opencode_should_separate_tool_use_and_result():
    """OpenCode provider should emit tool_use and tool_result as separate messages.

    The bug: creates single AdaptedMessage with both ToolUseBlock and
    ToolResultBlock in same content list — protocol issue.
    """
    import inspect
    from src.providers.opencode import OpenCodeProvider

    source = inspect.getsource(OpenCodeProvider._adapt_opencode_event)

    # Check if tool_use and tool_result are combined in same message
    if "tool_use" not in source or "tool_result" not in source:
        return  # Implementation changed

    # Look for the pattern where both are added to same content list
    combines_both = (
        "ToolUseBlock" in source and "ToolResultBlock" in source and "content" in source
    )

    if not combines_both:
        return

    # Check if they're in the same message creation
    lines = source.split("\n")
    for i, line in enumerate(lines):
        if "ToolUseBlock" in line and "ToolResultBlock" in line:
            pytest.fail(
                "BUG CONFIRMED: tool_use and tool_result combined in same message — "
                "violates expected message protocol"
            )
        # Check if both are appended to same content list within a few lines
        if "ToolUseBlock" in line or "tool_use" in line.lower():
            next_few_lines = "\n".join(lines[i : i + 10])
            if "ToolResultBlock" in next_few_lines and "content" in next_few_lines:
                pytest.fail(
                    "BUG CONFIRMED: tool_use and tool_result combined in same message content — "
                    "violates expected message protocol"
                )


# ============================================================================
# BUG #15 (MEDIUM): providers/codex.py _write_stdin no exception handling
# File: src/providers/codex.py:236-244
# EXPECTED: should handle BrokenPipeError when subprocess exits quickly
# ACTUAL: no exception handling around stdin.drain()
# ============================================================================


def test_codex_write_stdin_should_handle_broken_pipe():
    """CodexProvider._write_stdin should handle BrokenPipeError.

    The bug: stdin.drain() can raise BrokenPipeError if subprocess
    exits quickly, but there's no exception handling.
    """
    import inspect
    from src.providers.codex import CodexProvider

    source = inspect.getsource(CodexProvider._write_stdin)

    # Check if there's exception handling around drain/close
    has_try_except = "try:" in source and "except" in source
    has_broken_pipe_handler = (
        "BrokenPipeError" in source or "ConnectionResetError" in source
    )

    if has_try_except and has_broken_pipe_handler:
        return  # Already fixed

    # Check if drain() is called without protection
    drain_line = None
    for i, line in enumerate(source.split("\n")):
        if "drain()" in line:
            drain_line = i
            break

    if drain_line is None:
        return  # Implementation changed

    # Check if drain is inside a try block
    lines = source.split("\n")
    in_try_block = False
    for i in range(drain_line, -1, -1):
        if "try:" in lines[i]:
            in_try_block = True
            break
        if "def " in lines[i] and i < drain_line:
            break

    assert in_try_block, (
        "BUG CONFIRMED: _write_stdin calls drain() without exception handling — "
        "crashes with BrokenPipeError when subprocess exits quickly"
    )


# ============================================================================
# BUG #16 (MEDIUM): bug_detector runs pytest on entire working_dir
# File: src/bug_detector.py:126
# EXPECTED: should exclude venv, node_modules, etc.
# ACTUAL: runs pytest on entire directory including dependencies
# ============================================================================


def test_bug_detector_tests_should_exclude_venv_and_node_modules():
    """BugDetector._check_tests should exclude venv, node_modules, etc.

    The bug: runs pytest on entire working_dir, which can include
    .venv, node_modules, and other unrelated test files.
    """
    import inspect
    from src.bug_detector import BugDetector

    source = inspect.getsource(BugDetector._check_tests)

    # Check if there are exclusions
    has_exclusions = (
        "--ignore" in source
        or "exclude" in source.lower()
        or "venv" in source
        or "node_modules" in source
    )

    assert has_exclusions, (
        "BUG CONFIRMED: _check_tests runs pytest on entire working_dir — "
        "includes .venv, node_modules, and unrelated test files"
    )


# ============================================================================
# BUG #17 (HIGH): merge_bugs discards regressions at previously-fixed lines
# File: src/debugger_bugs.py:207-218
# EXPECTED: new bugs at previously-fixed lines should be accepted (regressions)
# ACTUAL: seen_lines includes ALL bugs (including fixed), blocking regressions
# ============================================================================


def test_merge_bugs_should_accept_regressions_at_fixed_lines():
    """merge_bugs should not discard new bugs at lines where old bugs are fixed.

    The bug: seen_lines is built from ALL bugs regardless of status.
    If a bug was fixed at line 100 and a regression appears at line 100,
    merge_bugs silently discards the regression.
    """
    from src.debugger_bugs import BugEntry, merge_bugs

    existing = [
        BugEntry(
            id=1, file="config.py", line=437,
            description="unsafe key bypass",
            severity="high", status="fixed",
        ),
    ]
    new_bugs = [
        BugEntry(
            id=2, file="config.py", line=437,
            description="regression: key bypass re-introduced",
            severity="critical",
        ),
    ]

    merged = merge_bugs(existing, new_bugs)

    # CORRECT: regression at a previously-fixed line should be accepted
    assert len(merged) == 2, (
        "BUG CONFIRMED: merge_bugs discards regressions at previously-fixed lines — "
        "seen_lines includes bugs with status='fixed', so new bugs at those lines "
        "are silently ignored"
    )


# ============================================================================
# BUG #18 (MEDIUM): _run_tester/_run_fixer don't guarantee pulse cleanup
# File: src/debugger.py:266-290, 326-351
# EXPECTED: pulse.stop() called via try/finally
# ACTUAL: no try/finally — if code between start/stop raises, task leaks
# ============================================================================


def test_run_tester_uses_try_finally_for_pulse():
    """_run_tester should use try/finally to ensure pulse.stop() is always called.

    The bug: pulse.start() creates an asyncio.Task but pulse.stop() is not
    guaranteed via try/finally. If build_context raises, the animation leaks.
    """
    import inspect
    from src.debugger import Debugger

    source = inspect.getsource(Debugger._run_tester)

    if "pulse.start()" not in source:
        return

    start_idx = source.index("pulse.start()")
    stop_idx = source.index("pulse.stop()")
    between = source[start_idx:stop_idx]

    # CORRECT: should have try and finally between start() and stop()
    has_try = "try:" in between or source[:start_idx].rstrip().endswith("try:")
    has_finally = "finally:" in between

    assert has_try and has_finally, (
        "BUG CONFIRMED: _run_tester doesn't use try/finally for pulse cleanup — "
        "animation task leaks if build_context() raises between start() and stop()"
    )


def test_run_fixer_uses_try_finally_for_pulse():
    """_run_fixer should use try/finally to ensure pulse.stop() is always called.

    Same bug as _run_tester — no try/finally for pulse cleanup.
    """
    import inspect
    from src.debugger import Debugger

    source = inspect.getsource(Debugger._run_fixer)

    if "pulse.start()" not in source:
        return

    start_idx = source.index("pulse.start()")
    stop_idx = source.index("pulse.stop()")
    between = source[start_idx:stop_idx]

    has_try = "try:" in between or source[:start_idx].rstrip().endswith("try:")
    has_finally = "finally:" in between

    assert has_try and has_finally, (
        "BUG CONFIRMED: _run_fixer doesn't use try/finally for pulse cleanup — "
        "animation task leaks if build_context() raises between start() and stop()"
    )


# ============================================================================
# BUG #19 (MEDIUM): _git_commit only catches CalledProcessError
# File: src/debugger.py:411-412
# EXPECTED: should handle FileNotFoundError, PermissionError, OSError
# ACTUAL: only CalledProcessError caught — other failures crash the debugger
# ============================================================================


def test_git_commit_handles_all_subprocess_failures():
    """_git_commit should handle all subprocess failure modes gracefully.

    The bug: except clause only catches CalledProcessError. FileNotFoundError
    (git not installed), PermissionError (.git locked), OSError (disk full)
    are not caught and crash the debugger loop.
    """
    import inspect
    from src.debugger import Debugger

    source = inspect.getsource(Debugger._git_commit)

    catches_broad = "except Exception" in source or "except:" in source
    catches_narrow = "CalledProcessError" in source

    # CORRECT: should either catch broadly or handle all specific error types
    assert not (catches_narrow and not catches_broad), (
        "BUG CONFIRMED: _git_commit only catches CalledProcessError — "
        "FileNotFoundError, PermissionError, OSError (disk full) crash the debugger "
        "and silently lose fix work"
    )


# ============================================================================
# BUG #20 (LOW): discover_py_files crashes on symlinks outside project root
# File: src/debugger_graph.py:29-34
# EXPECTED: should skip symlinks pointing outside the project tree
# ACTUAL: path.relative_to(root) raises ValueError for external symlinks
# ============================================================================


def test_discover_py_files_handles_external_symlinks(tmp_path):
    """discover_py_files should not crash when symlinks point outside the project.

    The bug: rglob follows symlinks, and path.relative_to(root) raises
    ValueError when the target is outside the project root.
    """
    from src.debugger_graph import discover_py_files

    external = tmp_path / "external"
    external.mkdir()
    (external / "other.py").write_text("x = 1\n")

    project = tmp_path / "project"
    project.mkdir()
    (project / "real.py").write_text("y = 2\n")

    link = project / "linked.py"
    try:
        link.symlink_to(external / "other.py")
    except OSError:
        pytest.skip("Symlinks not supported on this filesystem")

    # CORRECT: should not crash, should return at least real.py
    files = discover_py_files(str(project))
    assert isinstance(files, list) and len(files) >= 1, (
        "BUG CONFIRMED: discover_py_files crashes on symlinks outside project root — "
        "path.relative_to(root) raises ValueError"
    )


# ============================================================================
# BUG #21 (LOW): collect_text has unreachable final return
# File: src/debugger_llm.py:109
# EXPECTED: all code paths should be reachable
# ACTUAL: final return is dead code — for-loop always returns before it
# ============================================================================


def test_collect_text_has_no_unreachable_return():
    """collect_text should not have unreachable code after the retry loop.

    The bug: the final return is unreachable — the for-loop always returns
    on its last iteration (completed=True or completed=False).
    """
    import ast
    import inspect
    from src.debugger_llm import collect_text

    source = inspect.getsource(collect_text)
    tree = ast.parse(source)

    func_node = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "collect_text"),
        None,
    )
    assert func_node is not None

    last_stmt = func_node.body[-1]
    has_for = any(isinstance(s, ast.For) for s in func_node.body)

    # CORRECT: last statement should not be an unreachable return after a for loop
    assert not (isinstance(last_stmt, ast.Return) and has_for), (
        "BUG CONFIRMED: collect_text ends with unreachable return — "
        "the for-loop always returns, making the final return dead code"
    )


# ============================================================================
# BUG #22 (LOW): _build_structure_overview is dead code
# File: src/debugger_context.py:381-405
# EXPECTED: all defined functions should be used
# ACTUAL: _build_structure_overview is defined (~25 lines) but never called
# ============================================================================


# ============================================================================
# BUG #23 (LOW): parse_file silently corrupts non-UTF-8 source
# File: src/debugger_graph.py:339
# EXPECTED: should skip or reject non-UTF-8 files explicitly
# ACTUAL: errors="replace" silently replaces invalid bytes with �
# ============================================================================


def test_parse_file_rejects_non_utf8_source(tmp_path):
    """parse_file should not silently parse non-UTF-8 encoded files.

    The bug: read_text(encoding="utf-8", errors="replace") silently replaces
    invalid bytes, corrupting string literals that may contain bugs.
    """
    from src.debugger_graph import parse_file

    src = tmp_path / "latin.py"
    # \xe9 is valid Latin-1 (é) but invalid UTF-8
    src.write_bytes(b'def foo():\n    name = "r\xe9sum\xe9"\n    return name\n')

    # Verify file is genuinely non-UTF-8
    try:
        src.read_text(encoding="utf-8", errors="strict")
        pytest.skip("File unexpectedly valid UTF-8")
    except UnicodeDecodeError:
        pass  # Expected — file has non-UTF-8 bytes

    node = parse_file(str(src), "latin.py", str(tmp_path))

    # CORRECT: should return None for non-UTF-8 files, not silently corrupt
    assert node is None, (
        "BUG CONFIRMED: parse_file silently parses non-UTF-8 source with "
        "errors='replace', corrupting content — should return None or raise"
    )
