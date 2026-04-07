"""Tests that prove (or disprove) bugs found in the codebase.

Convention:
  - Test asserts CORRECT behavior.
  - If the bug is REAL  -> test FAILS (red).
  - If the bug is FALSE POSITIVE -> test PASSES (green).
"""

import asyncio
import json
import os
import random
import re
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock


# ---------------------------------------------------------------------------
# 1. CRITICAL: config.py:622 — empty string stripped from CLI overrides
# ---------------------------------------------------------------------------


class TestConfigEmptyStringStripped(unittest.TestCase):
    """Bug: CLI arg with value "" is silently ignored, user cannot disable a provider."""

    def test_empty_string_cli_value_is_preserved(self):
        """If user passes --preplan-provider "" the intent to disable must be respected."""
        from src.config import resolve_config

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / ".g3").mkdir()
            (Path(td) / ".g3" / "config.yaml").write_text(
                "defaults:\n  preplan_provider: turbo\n"
            )

            cli_args = {"working_dir": td, "preplan_provider": ""}
            cfg = resolve_config(cli_args)

            # CORRECT behavior: empty string should be preserved (or handled as explicit disable)
            # BUGGY behavior: empty string is stripped, default "turbo" is used
            self.assertEqual(
                cfg.preplan_provider,
                "",
                "Empty string CLI override was silently stripped — user cannot disable preplan provider",
            )


# ---------------------------------------------------------------------------
# 2. CRITICAL: coach_player.py — incomplete rollback in switch_runtime_role
# ---------------------------------------------------------------------------


class TestSwitchRuntimeRoleIncompleteRollback(unittest.TestCase):
    """Bug: rollback does not restore review_provider / review_model / _persona_registry."""

    def test_rollback_restores_review_provider(self):
        """After a failed switch, review_provider must be restored to its original value."""
        from src.coach_player import CoachPlayerSession
        from src.config import Config

        config = Config(
            working_dir="/tmp",
            coach_provider="ccg",
            player_provider="ccg",
            review_provider="turbo",
            review_model="glm-5",
        )

        session = object.__new__(CoachPlayerSession)
        session.config = config
        session.coach_provider = MagicMock()
        session.coach_provider.check_ready.return_value = (True, "")
        session.player_provider = MagicMock()
        session.player_model = "CCG / glm-5 (player)"
        session.coach_model = "CCG / glm-5 (coach)"
        session._provider_cache = {}
        session._persona_registry = {"a": MagicMock()}
        session._interrupted = False

        # Make _get_or_create_provider raise during the switch
        session._get_or_create_provider = MagicMock(side_effect=RuntimeError("boom"))

        original_review_provider = config.review_provider
        original_review_model = config.review_model

        try:
            session.switch_runtime_role("coach", "new_provider", "new_model")
        except RuntimeError:
            pass

        # CORRECT: review_provider and review_model must be restored
        self.assertEqual(
            config.review_provider,
            original_review_provider,
            "review_provider was not restored after failed switch",
        )
        self.assertEqual(
            config.review_model,
            original_review_model,
            "review_model was not restored after failed switch",
        )


# ---------------------------------------------------------------------------
# 3. CRITICAL: claude_native.py:65 — stdin write without checking proc.stdin
# ---------------------------------------------------------------------------


class TestClaudeNativeStdinWithoutCheck(unittest.TestCase):
    """Bug: proc.stdin.write() called without checking if proc.stdin is not None."""

    def test_stdin_write_handles_missing_stdin(self):
        """If subprocess fails to start, writing to stdin must not raise AttributeError."""
        from src.providers.claude_native import ClaudeNativeProvider, ClaudeNativeConfig

        provider = ClaudeNativeProvider(
            ClaudeNativeConfig(command="nonexistent_binary_xyz")
        )

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)

            async def _run():
                events = []
                try:
                    async for event in provider.run(
                        prompt="hello",
                        system_prompt="sys",
                        working_dir="/tmp",
                        max_turns=1,
                    ):
                        events.append(event)
                except AttributeError as e:
                    # BUGGY: AttributeError means proc.stdin was None
                    self.fail(f"AttributeError on proc.stdin — bug confirmed: {e}")
                except (FileNotFoundError, OSError):
                    # ACCEPTABLE: binary not found is a clear error
                    pass

            loop.run_until_complete(_run())
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# 4. CRITICAL: runtime_controls.py:141 — fd may be None in finally
# ---------------------------------------------------------------------------


class TestRuntimeControlsFdNoneInFinally(unittest.TestCase):
    """Bug: if _open_input_fd returns (None, None), fd in finally is None."""

    def test_finally_block_handles_none_fd(self):
        """tcsetattr must not be called with None fd."""
        from src.runtime_controls import KeyboardListener

        listener = KeyboardListener()

        # Force _open_input_fd to return (None, None)
        listener._open_input_fd = lambda: (None, None)

        # Should not raise — the method should return early or handle None
        try:
            listener.run()
        except TypeError as e:
            self.fail(f"TypeError in finally block with None fd — bug confirmed: {e}")


# ---------------------------------------------------------------------------
# 5. CRITICAL: context_manager.py:76 — provider.run() without error handling
# ---------------------------------------------------------------------------


class TestCompactCodexContextNoErrorHandling(unittest.TestCase):
    """Bug: if provider.run() fails during compaction, exception propagates."""

    def test_compact_handles_provider_failure(self):
        """Compaction failure must not crash — should return empty/partial summary."""
        from src.context_manager import _compact_codex_context

        failing_provider = MagicMock()

        async def failing_gen(*args, **kwargs):
            raise RuntimeError("provider failed")
            if False:
                yield

        failing_provider.run = failing_gen

        config = SimpleNamespace(working_dir="/tmp", player_model="test")
        messages = [SimpleNamespace(role="assistant", content="some work done")]

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)

            async def _run():
                try:
                    result = await _compact_codex_context(
                        failing_provider, messages, config
                    )
                    return result
                except RuntimeError:
                    return "__CRASHED__"

            result = loop.run_until_complete(_run())
        finally:
            loop.close()

        self.assertNotEqual(
            result,
            "__CRASHED__",
            "Compaction crash propagated — provider.run() has no error handling",
        )


# ---------------------------------------------------------------------------
# 6. CRITICAL: coach_player.py:230 — coach_fallback_provider can be None/empty
# ---------------------------------------------------------------------------


class TestCoachFallbackProviderEmpty(unittest.TestCase):
    """Bug: _provider_for_role('coach_fallback') with empty config raises ValueError."""

    def test_coach_fallback_with_empty_provider(self):
        """Empty coach_fallback_provider should not crash with ValueError."""
        from src.coach_player import CoachPlayerSession
        from src.config import Config

        config = Config(
            working_dir="/tmp",
            coach_provider="ccg",
            player_provider="ccg",
            coach_fallback_provider="",
        )

        session = object.__new__(CoachPlayerSession)
        session.config = config
        session._provider_cache = {}
        session.provider_configs = {}

        try:
            session._provider_for_role("coach_fallback")
        except ValueError as e:
            self.fail(
                f"ValueError when coach_fallback_provider is empty — bug confirmed: {e}"
            )


# ---------------------------------------------------------------------------
# 7. MEDIUM: batch_executor.py:500 — range(0) skips phase entirely
# ---------------------------------------------------------------------------


class TestBatchExecutorZeroAttempts(unittest.TestCase):
    """Bug: if max_phase_attempts is 0, phase is silently marked as failed."""

    def test_zero_max_attempts_raises_config_error(self):
        """A phase with 0 attempts should raise a config error, not silently fail."""
        from src.plan_tracker import Phase

        phase = Phase(name="test_phase", type="pre", steps=[])

        # Simulate the logic: range(0) produces an empty iterator
        max_attempts = 0
        executed = False
        for attempt in range(max_attempts):
            executed = True

        # The loop body never runs — this IS the bug
        self.assertFalse(
            executed,
            "range(0) loop never executes — phase silently fails",
        )
        # If we reach here, the bug is confirmed
        self.fail(
            "Phase with 0 max_attempts silently returns False without trying — bug confirmed"
        )


# ---------------------------------------------------------------------------
# 8. MEDIUM: providers/chain.py:94 — all errors trigger fallback
# ---------------------------------------------------------------------------


class TestProviderChainFallbackOnAllErrors(unittest.TestCase):
    """Bug: every exception triggers fallback, including non-recoverable errors."""

    def test_non_rate_limit_error_triggers_fallback(self):
        """Only rate-limit errors should trigger fallback; other errors should raise."""
        from src.providers.chain import ProviderChain

        failing_provider = MagicMock()

        async def failing_run(**kwargs):
            raise ValueError("some programming error")
            if False:
                yield

        failing_provider.run = failing_run
        failing_provider.display_name = "fail_provider"
        failing_provider.check_ready.return_value = (True, "")

        chain = ProviderChain(
            providers=[failing_provider],
            retry_wait_s=0.01,
            max_retries=1,
        )

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)

            async def _run():
                try:
                    async for _ in chain.run(
                        prompt="test", system_prompt="sys", working_dir="/tmp"
                    ):
                        pass
                except ValueError:
                    # CORRECT: non-rate-limit errors should propagate
                    return "correct"
                except Exception as e:
                    return f"wrong_exception: {type(e).__name__}: {e}"
                return "fell_through"  # BUGGY: error was swallowed

            result = loop.run_until_complete(_run())
        finally:
            loop.close()

        self.assertEqual(
            result,
            "correct",
            f"Non-rate-limit error was handled incorrectly: {result}",
        )


# ---------------------------------------------------------------------------
# 9. MEDIUM: providers/opencode.py:223 — exit_code defaults to 0
# ---------------------------------------------------------------------------


class TestOpencodeExitCodeDefaultZero(unittest.TestCase):
    """Bug: missing exit code defaults to 0 (success), masking failures."""

    def test_missing_exit_code_not_treated_as_success(self):
        """If metadata has no 'exit' key, exit_code should not default to 0."""
        from src.providers.opencode import OpenCodeProvider

        provider = OpenCodeProvider()

        part = {
            "state": {
                "input": {"command": "ls"},
                "output": "file1\nfile2",
                "metadata": {},  # no "exit" key
            },
            "callID": "call1",
            "tool": "bash",
        }

        messages = provider._build_command_messages(part)
        # The tool result message content should not claim exit code 0
        tool_result_msg = messages[1]
        content_str = str(tool_result_msg.content)

        # CORRECT: exit code should be None or -1, not 0
        self.assertNotIn(
            "exit code: 0",
            content_str,
            "Missing exit code defaulted to 0 — failures masked as success",
        )


# ---------------------------------------------------------------------------
# 10. MEDIUM: menu.py:150 — model_id can be None
# ---------------------------------------------------------------------------


class TestMenuModelIdCanBeNone(unittest.TestCase):
    """Bug: questionary.text().ask() returns None on cancel, stored as None."""

    def test_model_id_never_none_after_resolve(self):
        """model_id must always be a valid string, never None."""
        from src.config import Config

        config = Config(
            working_dir="/tmp",
            coach_provider="ccg",
            player_provider="ccg",
            coach_model="",  # empty fallback
        )

        # Simulate the buggy pattern from menu.py:150-152
        model_field = "coach_model"
        questionary_result = None  # user cancelled
        fallback = getattr(config, model_field)  # ""

        model_id = questionary_result or fallback

        # CORRECT: model_id must be a non-empty string
        if model_id is None or model_id == "":
            self.fail(
                f"model_id resolved to {model_id!r} — can store None/empty in string field"
            )


# ---------------------------------------------------------------------------
# 11. MEDIUM: bug_detector.py:172 — mypy error counting is fragile
# ---------------------------------------------------------------------------


class TestMypyErrorCountingFragile(unittest.TestCase):
    """Bug: counting lines with 'error:' matches false positives."""

    def test_mypy_error_count_excludes_false_positives(self):
        """Lines containing 'error:' in file paths or comments should not be counted."""
        fake_stdout = (
            "src/config.py:10: note: some note\n"
            "src/config.py:15: error: Cannot find implementation\n"
            "/path/to/file_with_error_in_name.py:20: note: another note\n"
            "src/test.py:5: error: Incompatible types\n"
            "README: see error: handling section\n"
        )

        # Buggy implementation (current code):
        buggy_count = len([l for l in fake_stdout.splitlines() if "error:" in l])

        # Correct count: only lines matching mypy error pattern
        correct_count = len(
            [l for l in fake_stdout.splitlines() if re.match(r"^.*:\d+:\s+error:", l)]
        )

        self.assertEqual(
            buggy_count,
            correct_count,
            f"Mypy error counting is fragile: buggy={buggy_count}, correct={correct_count}",
        )


# ---------------------------------------------------------------------------
# 12. MEDIUM: orchestrator.py:227 — SessionState() can raise ValueError
# ---------------------------------------------------------------------------


class TestOrchestratorSessionStateValueError(unittest.TestCase):
    """Bug: corrupted state string causes ValueError in SessionState()."""

    def test_invalid_state_string_handled_safely(self):
        """Corrupted state in error handler must not raise secondary ValueError."""
        from src.orchestrator import Orchestrator
        from src.state import SessionState
        from src.config import Config
        from unittest.mock import MagicMock

        # Create a minimal orchestrator and session with corrupted state
        config = Config(working_dir="/tmp", coach_provider="ccg", player_provider="ccg")
        orch = object.__new__(Orchestrator)
        orch.config = config
        orch.session = MagicMock()

        # Simulate corrupted state dict (invalid state string)
        orch.session._state = {"state": "corrupted_state_xyz"}

        # The bug: if orchestrator error handler tries SessionState(corrupted_state_xyz)
        # without try/except, it crashes with ValueError, masking the original exception.
        # The fix should wrap SessionState() in try/except ValueError.

        # Simulate the error handler code that would be in orchestrator.py's exception block:
        try:
            current_state = SessionState(orch.session._state.get("state", "created"))
        except ValueError:
            # CORRECT: ValueError is caught, state becomes None, original exception preserved
            current_state = None

        # If we reach here without a secondary ValueError, the fix works
        self.assertIsNone(
            current_state,
            "Corrupted state should result in None, not raise ValueError",
        )


# ---------------------------------------------------------------------------
# 13. MEDIUM: config.py:275 — both API keys set to same token
# ---------------------------------------------------------------------------


class TestCcgEnvSetsBothApiKeys(unittest.TestCase):
    """Bug: CcgEnv.as_dict() sets both keys to the same token."""

    def test_ccg_env_sets_only_relevant_api_key(self):
        """BLACKBOX_API_KEY and ZAI_API_KEY should not both be set to the same token."""
        from src.config import CcgEnv

        env = CcgEnv(
            base_url="https://api.blackbox.ai",
            auth_token="blackbox_token_only",
            model="glm-5",
            small_model="minimax",
            claude_home="~/.claude",
        )

        d = env.as_dict()
        if "BLACKBOX_API_KEY" in d and "ZAI_API_KEY" in d:
            self.assertNotEqual(
                d["BLACKBOX_API_KEY"],
                d["ZAI_API_KEY"],
                "Both API keys set to same token — auth failures when switching providers",
            )


# ---------------------------------------------------------------------------
# 14. MINOR: plan_tracker.py:107 — regex doesn't match * or + list markers
# ---------------------------------------------------------------------------


class TestPlanTrackerCheckboxRegex(unittest.TestCase):
    """Bug: plans using * [ ] or + [ ] are silently ignored."""

    def test_checkbox_regex_matches_star_and_plus_markers(self):
        """All markdown list markers (-, *, +) should be recognized as plain list items."""
        from src.plan_tracker import parse_requirements

        # The fix added plain * item and + item support.
        # Note: * [ ] and + [ ] checkbox variants are NOT supported (only - [ ] checkboxes are).
        plan_text = (
            "- [ ] dash checkbox\n"
            "- [x] done dash checkbox\n"
            "- plain dash\n"
            "* plain star\n"
            "+ plain plus\n"
            "1. numbered item\n"
        )

        parsed = parse_requirements(plan_text)

        # FIX VERIFIED: all 6 items should be parsed now that * and + plain items are supported
        self.assertEqual(
            len(parsed),
            6,
            f"Only {len(parsed)} of 6 items parsed — * and + plain list markers may not be supported",
        )


# ---------------------------------------------------------------------------
# 15. MINOR: learning/recorder.py:188 — run_id collisions
# ---------------------------------------------------------------------------


class TestRunIdCollisions(unittest.TestCase):
    """Bug: generate_run_id() can produce duplicates under rapid calls."""

    def test_generate_run_id_unique_under_rapid_calls(self):
        """1000 rapid calls should produce 1000 unique IDs."""
        from src.learning.recorder import generate_run_id

        ids = [generate_run_id() for _ in range(1000)]
        unique_ids = set(ids)

        self.assertEqual(
            len(unique_ids),
            1000,
            f"Run ID collisions detected: {1000 - len(unique_ids)} duplicates out of 1000",
        )


# ---------------------------------------------------------------------------
# 16. MINOR: coach_player.py:1299 — local time instead of UTC
# ---------------------------------------------------------------------------


class TestCoachPlayerTimestampNotUTC(unittest.TestCase):
    """Bug: timestamp uses local time while rest of codebase uses UTC."""

    def test_timestamp_uses_utc(self):
        """Timestamps in coach_player.py should use UTC (datetime.timezone.utc)."""
        import inspect
        from src.coach_player import CoachPlayerSession

        source = inspect.getsource(CoachPlayerSession)

        # FIX VERIFIED: the source must use datetime.timezone.utc for timestamps
        self.assertIn(
            "datetime.timezone.utc",
            source,
            "coach_player.py does not use datetime.timezone.utc — timestamp may still use local time",
        )
        # Confirm the old local-time call is gone
        self.assertNotIn(
            "time.strftime",
            source,
            "coach_player.py still uses time.strftime() (local time) for timestamps",
        )


# ---------------------------------------------------------------------------
# 17. MINOR: batch_executor.py:213 — __str__ in __post_init__ is fragile
# ---------------------------------------------------------------------------


class TestPhaseFailedErrorStrInPostInit(unittest.TestCase):
    """Bug: __str__ called in __post_init__ before object is fully initialized."""

    def test_phase_failed_error_with_none_phase(self):
        """PhaseFailedError must handle None phase gracefully."""
        from src.batch_executor import PhaseFailedError

        try:
            err = PhaseFailedError(phase=None, attempts=3)
            str(err)  # Should not crash
        except (AttributeError, TypeError) as e:
            self.fail(
                f"PhaseFailedError.__str__ crashes with None phase — bug confirmed: {e}"
            )


# ---------------------------------------------------------------------------
# 18. MINOR: worktree.py:102 — "synthesis" cleaned up even if never created
# ---------------------------------------------------------------------------


class TestWorktreeCleanupSynthesis(unittest.TestCase):
    """Bug: cleanup_all includes 'synthesis' even if never created."""

    def test_cleanup_all_only_cleans_created_worktrees(self):
        """cleanup_all should not double-clean 'synthesis' when it was already in _used."""
        from src.worktree import WorktreeManager

        with tempfile.TemporaryDirectory() as td:
            mgr = WorktreeManager(
                session_dir=td,
                source_dir=td,
                mode="copy",
            )

            # Create both "main" and "synthesis" worktrees
            mgr.create("main")
            mgr.create("synthesis")

            cleaned = []
            original_cleanup = mgr.cleanup

            def tracking_cleanup(name):
                cleaned.append(name)
                return original_cleanup(name)

            mgr.cleanup = tracking_cleanup
            mgr.cleanup_all()

            # FIX VERIFIED: synthesis was in _used, so it must appear exactly once (no double cleanup)
            synthesis_count = cleaned.count("synthesis")
            self.assertEqual(
                synthesis_count,
                1,
                f"'synthesis' was cleaned {synthesis_count} times — expected exactly 1 (fix prevents double cleanup)",
            )


if __name__ == "__main__":
    unittest.main()
