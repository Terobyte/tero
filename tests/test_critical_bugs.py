"""
Tests confirming bugs from priority bug list.

Convention: each test asserts CORRECT/EXPECTED behavior.
  FAIL = bug still present
  PASS = bug has been fixed

Severity levels match the original bug report.
"""

import asyncio
import inspect
import json
import tempfile
from pathlib import Path

import pytest


# ============================================================================
# HIGH SEVERITY
# ============================================================================


class TestBug_ClaudeNativeSubprocessLeak:
    """
    HIGH — src/providers/claude_native.py lines 56-86
    Subprocess is created but never killed if an exception is raised during
    stdout reading. No try/finally block exists to ensure cleanup.
    """

    def test_run_has_try_finally_for_process_cleanup(self):
        """
        BUG: asyncio.create_subprocess_exec creates a process; if an exception
        occurs during 'async for line in proc.stdout', the process is orphaned
        because there is no try/finally that calls proc.kill() / proc.terminate().
        EXPECTED: source contains try/finally or equivalent cleanup
        ACTUAL:   no try/finally around the subprocess lifetime

        After Phase 6B refactor: cleanup lives in subprocess_runner.run_subprocess_jsonl()
        which all subprocess-based providers delegate to.
        """
        from src.providers import subprocess_runner

        source = inspect.getsource(subprocess_runner.run_subprocess_jsonl)

        # The process must be cleaned up even on exception
        has_cleanup = (
            "try:" in source and ("proc.kill" in source or "proc.terminate" in source)
        ) or (
            "finally" in source and ("proc.kill" in source or "proc.terminate" in source)
        )

        assert has_cleanup, (
            "BUG CONFIRMED: run_subprocess_jsonl() creates a subprocess but has no "
            "try/finally to call proc.kill() or proc.terminate() on exception. "
            "If the caller raises during iteration, the child process is orphaned."
        )


class TestBug_DuelAsyncioToThread:
    """
    HIGH — src/duel.py lines 59-61
    asyncio.to_thread() is designed for SYNC callables. AgentProvider.run() is an
    async generator function. Calling it via asyncio.to_thread() creates the
    generator object in a thread but never iterates it — the agent never runs.
    """

    @pytest.mark.asyncio
    async def test_asyncio_to_thread_does_not_execute_async_generator(self):
        """
        FIX VERIFIED: _collect_agent_result (which replaced asyncio.to_thread in duel.py)
        correctly executes async generator agents.
        EXPECTED: generator body executes (executed has items)
        """
        from src.duel import _collect_agent_result

        executed = []

        class FakeAgent:
            async def run(self, prompt, system_prompt, working_dir, max_turns=30, model=""):
                executed.append("ran")
                yield "message1"
                yield "message2"

        await _collect_agent_result(FakeAgent(), "task", "/ws", 60)

        assert len(executed) > 0, (
            "FIX BROKEN: _collect_agent_result should execute the async generator body, "
            "but 'executed' is still empty."
        )

    @pytest.mark.asyncio
    async def test_duel_runner_agent_result_is_not_async_generator_object(self):
        """
        FIX VERIFIED: _collect_agent_result returns AgentResult, not an async generator object.
        EXPECTED: result is AgentResult
        """
        from src.duel import _collect_agent_result
        from src.providers.base import AgentResult

        class FakeAgent:
            async def run(self, prompt, system_prompt, working_dir, max_turns=30, model=""):
                yield "message"

        result = await _collect_agent_result(FakeAgent(), "task", "/ws", 60)

        assert isinstance(result, AgentResult), (
            "FIX BROKEN: _collect_agent_result returned "
            f"'{type(result).__name__}', not AgentResult."
        )


# ============================================================================
# MEDIUM SEVERITY
# ============================================================================


class TestBug_ReviewPassedSuppressesSecurity:
    """
    MEDIUM — src/feedback.py lines 249-262
    CODE_REVIEW_PASSED marker triggers an early return that bypasses all
    subsequent security concern detection. A reviewer writing
    "CODE_REVIEW_PASSED. However, XSS vulnerability at line 42." gets
    treated as a clean pass because the sentinel is checked first.
    """

    def _msg(self, text: str):
        class _M:
            role = "assistant"
            content = text
        return _M()

    def test_pass_marker_does_not_suppress_xss_issue(self):
        """
        BUG: 'CODE_REVIEW_PASSED. However, XSS vulnerability...' returns ReviewPassed.
        EXPECTED: ReviewIssues (explicit security concern mentioned by reviewer)
        ACTUAL:   ReviewPassed (sentinel short-circuits before security check)
        """
        from src.feedback import parse_review_output, ReviewIssues

        text = (
            "CODE_REVIEW_PASSED. However, there is an XSS vulnerability "
            "in the render() function that could allow script injection."
        )
        result = parse_review_output([self._msg(text)])
        assert isinstance(result, ReviewIssues), (
            f"BUG CONFIRMED: expected ReviewIssues for text with explicit XSS mention, "
            f"got {type(result).__name__}. CODE_REVIEW_PASSED sentinel at line 249 "
            f"returns early before _FREE_TEXT_CONCERN_RE is evaluated at line 261."
        )

    def test_pass_marker_does_not_suppress_sql_injection(self):
        """
        BUG: CODE_REVIEW_PASSED with SQL injection mention should NOT pass cleanly.
        EXPECTED: ReviewIssues
        ACTUAL:   ReviewPassed
        """
        from src.feedback import parse_review_output, ReviewIssues

        text = (
            "CODE_REVIEW_PASSED — but note: SQL injection risk in the query builder "
            "at db.py:88 where user input is concatenated directly."
        )
        result = parse_review_output([self._msg(text)])
        assert isinstance(result, ReviewIssues), (
            f"BUG CONFIRMED: SQL injection risk ignored because CODE_REVIEW_PASSED "
            f"triggers early return before security regex is checked. Got {type(result).__name__}."
        )

    def test_pass_marker_does_not_suppress_numbered_security_issue(self):
        """
        BUG: CODE_REVIEW_PASSED followed by a numbered security issue should be ReviewIssues.
        The numbered issues check comes AFTER the pass marker check.
        EXPECTED: ReviewIssues (numbered issue present)
        ACTUAL:   ReviewPassed (pass marker wins)
        """
        from src.feedback import parse_review_output, ReviewIssues

        text = (
            "CODE_REVIEW_PASSED\n"
            "1. Hardcoded API key found in config.py line 12 — must be removed."
        )
        result = parse_review_output([self._msg(text)])
        assert isinstance(result, ReviewIssues), (
            f"BUG CONFIRMED: CODE_REVIEW_PASSED short-circuits before the numbered "
            f"issue check. Reviewer's '1. Hardcoded API key' was ignored. "
            f"Got {type(result).__name__}."
        )


class TestBug_PromoteDeletesDependencies:
    """
    MEDIUM — src/orchestrator.py lines 444-456
    _promote() deletes files from target that aren't in winner, but the winner
    workspace was created via _create_copy() which EXCLUDES node_modules and .venv.
    So _promote() will delete these directories from the real project.
    """

    def test_promote_skip_set_contains_node_modules(self):
        """
        BUG: _skip in _promote() does not include 'node_modules' or '.venv'.
        The worktree copy excludes these dirs, so they're absent from winner.
        _promote then deletes them from target because they're not in winner_files
        and not protected by _skip or _protected.
        EXPECTED: 'node_modules' in _skip
        ACTUAL:   _skip = {'.git', '__pycache__', '.g3', agent_a, agent_b}
        """
        from src.orchestrator import Orchestrator

        source = inspect.getsource(Orchestrator._promote)

        # Extract the _skip assignment
        # The _skip set must include node_modules and .venv to protect them
        skip_block = source[source.find("_skip"):source.find("_skip") + 300]

        assert "node_modules" in skip_block, (
            "BUG CONFIRMED: 'node_modules' is not in _skip. "
            "Since the winner workspace copy excludes node_modules (see _create_copy), "
            "_promote() will delete node_modules from the real project when promoting."
        )

    def test_promote_skip_set_contains_venv(self):
        """
        BUG: _skip in _promote() does not include '.venv'.
        Same issue as node_modules — copy excludes it, promote deletes it.
        EXPECTED: '.venv' in _skip
        ACTUAL:   not present
        """
        from src.orchestrator import Orchestrator

        source = inspect.getsource(Orchestrator._promote)
        skip_block = source[source.find("_skip"):source.find("_skip") + 300]

        assert ".venv" in skip_block, (
            "BUG CONFIRMED: '.venv' is not in _promote's _skip set. "
            "Virtual environment will be deleted from the real project on promotion."
        )

    def test_promote_actually_deletes_node_modules_from_target(self):
        """
        Integration test: verify that _promote() silently deletes node_modules
        from the target when winner workspace doesn't have it.
        EXPECTED: node_modules survives _promote()
        ACTUAL:   node_modules is deleted
        """
        import shutil
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as base:
            winner_ws = Path(base) / "winner"
            target = Path(base) / "project"

            # Winner has one real file (as if from _create_copy, which excludes node_modules)
            (winner_ws / "src").mkdir(parents=True)
            (winner_ws / "src" / "app.py").write_text("# updated")

            # Target has the same file PLUS node_modules
            (target / "src").mkdir(parents=True)
            (target / "src" / "app.py").write_text("# original")
            (target / "node_modules" / "lodash").mkdir(parents=True)
            (target / "node_modules" / "lodash" / "index.js").write_text("// lodash")
            (target / ".venv" / "lib").mkdir(parents=True)
            (target / ".venv" / "lib" / "python3.py").write_text("# venv")

            # Create a minimal config mock
            cfg = MagicMock()
            cfg.working_dir = str(target)
            cfg.agent_a_workspace = "agent_a"
            cfg.agent_b_workspace = "agent_b"

            orch = object.__new__(__import__("src.orchestrator", fromlist=["Orchestrator"]).Orchestrator)
            orch.config = cfg

            # Call _promote
            orch._promote(str(winner_ws))

            node_modules_exists = (target / "node_modules" / "lodash" / "index.js").exists()
            venv_exists = (target / ".venv" / "lib" / "python3.py").exists()

            assert node_modules_exists, (
                "BUG CONFIRMED: _promote() deleted node_modules from the target project. "
                "Winner workspace (created via copy) didn't have node_modules, "
                "so _promote deleted them as 'stale' files."
            )
            assert venv_exists, (
                "BUG CONFIRMED: _promote() deleted .venv from the target project."
            )


# ============================================================================
# LOW SEVERITY
# ============================================================================


class TestBug_RecorderNoFileLocking:
    """
    LOW — src/learning/recorder.py line 136
    RunRecorder.record() writes to a JSONL file with plain open("a") and no
    file locking. Concurrent sessions writing simultaneously can corrupt the file
    (interleaved JSON lines, partial writes).
    """

    def test_recorder_uses_file_locking(self):
        """
        BUG: open(self.runs_file, "a") with no fcntl.flock / filelock.
        Parallel sessions can corrupt the JSONL log.
        EXPECTED: source uses file locking (fcntl, portalocker, filelock, etc.)
        ACTUAL:   plain open(), no locking
        """
        from src.learning.recorder import RunRecorder

        source = inspect.getsource(RunRecorder.record)

        has_locking = any(
            kw in source
            for kw in ("flock", "fcntl", "lockf", "portalocker", "filelock", "FileLock")
        )

        assert has_locking, (
            "BUG CONFIRMED: RunRecorder.record() uses plain open() with no file locking. "
            "Concurrent g3 sessions writing to the same runs.jsonl file will produce "
            "interleaved/corrupted lines. "
            "Fix: use fcntl.flock(f, LOCK_EX) or a cross-platform locking library."
        )


class TestBug_OpencodeStderrTypeMismatch:
    """
    LOW — src/providers/opencode.py line 202-203
    _stderr_message() calls stderr_data.decode() without verifying that
    stderr_data is bytes. If the stream's read() returns str (e.g. from a
    mock or text-mode stream), this raises AttributeError and crashes the session.
    """

    @pytest.mark.asyncio
    async def test_stderr_message_handles_str_stderr_data(self):
        """
        BUG: await stderr.read() returns str in some contexts; .decode() fails.
        EXPECTED: _stderr_message_from_bytes() handles str gracefully (no AttributeError)
        ACTUAL:   AttributeError: 'str' object has no attribute 'decode'

        After Phase 6B refactor: _stderr_message() was replaced by
        _stderr_message_from_bytes() which takes already-read bytes/str.
        The guard is isinstance(stderr_data, bytes) in _stderr_message_from_bytes().
        """
        from src.providers.opencode import OpenCodeProvider

        provider = object.__new__(OpenCodeProvider)

        try:
            result = await provider._stderr_message_from_bytes("connection refused")
            # If we get here without error, the bug is fixed
        except AttributeError as e:
            pytest.fail(
                f"BUG CONFIRMED: _stderr_message_from_bytes() crashed with AttributeError: {e}. "
                f"str data passed but code calls .decode() unconditionally. "
                f"Fix: check isinstance(stderr_data, bytes) before calling .decode()."
            )


class TestBug_ConfigOneFlagThreeChecks:
    """
    LOW — src/orchestrator.py lines 102-107
    One boolean flag (run_bug_detection) controls three independent checks:
    run_types, run_lint, run_compile. There is no way to disable only lint
    without also disabling type and compile checks.
    """

    def test_config_has_independent_flags_for_each_check(self):
        """
        BUG: Config only has run_bug_detection for types/lint/compile as a bundle.
        EXPECTED: Config has run_types, run_lint, run_compile as separate fields
        ACTUAL:   only run_bug_detection bundles all three
        """
        from src.config import Config

        fields = Config.__dataclass_fields__

        missing = [f for f in ("run_lint", "run_types", "run_compile") if f not in fields]

        assert not missing, (
            f"BUG CONFIRMED: Config is missing independent flags: {missing}. "
            f"Currently run_bug_detection controls all three checks together. "
            f"Users cannot selectively disable lint without also disabling type checks."
        )

    def test_orchestrator_uses_separate_flags_per_check(self):
        """
        BUG: Orchestrator passes self.config.run_bug_detection for all three checks.
        EXPECTED: each BugDetector flag maps to its own Config field
        ACTUAL:   all three use the same run_bug_detection flag
        """
        from src.orchestrator import Orchestrator

        source = inspect.getsource(Orchestrator.run)

        # All three should use different config fields
        assert "run_types=self.config.run_types" in source, (
            "BUG CONFIRMED: run_types is controlled by run_bug_detection, not run_types. "
            "Types check cannot be independently toggled."
        )
        assert "run_lint=self.config.run_lint" in source, (
            "BUG CONFIRMED: run_lint is controlled by run_bug_detection, not run_lint. "
            "Lint check cannot be independently toggled."
        )
        assert "run_compile=self.config.run_compile" in source, (
            "BUG CONFIRMED: run_compile is controlled by run_bug_detection, not run_compile."
        )


class TestBug_ConfigTyposSilentlyIgnored:
    """
    LOW — src/config.py line 645
    load_config() silently discards unknown config keys with no warning.
    A typo like 'runn_tests: true' in config.yaml will be silently ignored,
    leaving the user confused why their setting has no effect.
    """

    def test_load_config_warns_on_unknown_keys(self):
        """
        BUG: {k: v for k, v in defaults.items() if k in valid_fields} silently drops
        unknown keys. No warning is emitted.
        EXPECTED: UserWarning or logged warning for unknown config keys
        ACTUAL:   silently ignored
        """
        import warnings
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            # load_config needs a working_dir
            # Patch load_merged_settings to inject an unknown key
            with patch("src.config.load_merged_settings") as mock_settings:
                mock_settings.return_value = {
                    "provider": {},
                    "runn_tests": True,       # typo for run_tests
                    "plann_file": "plan.md",  # typo for plan_file
                }

                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    from src.config import resolve_config
                    resolve_config({"working_dir": tmp})

                unknown_key_warnings = [
                    x for x in w
                    if "runn_tests" in str(x.message) or "plann_file" in str(x.message)
                    or "unknown" in str(x.message).lower()
                    or "unrecognized" in str(x.message).lower()
                ]

                assert unknown_key_warnings, (
                    "BUG CONFIRMED: load_config() silently ignored unknown keys "
                    "'runn_tests' and 'plann_file' without any warning. "
                    "A typo in config.yaml will be invisible to the user. "
                    "Fix: warn when a key is present in settings but not in Config.__dataclass_fields__."
                )
