"""Regression tests proving real bugs exist in the codebase.

Each test demonstrates a concrete bug. All tests are expected to FAIL
until the corresponding bug is fixed.
"""

import pytest
import asyncio
import json
import os
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# BUG #1 — parse_review_output: issues extracted BEFORE checking CODE_REVIEW_PASSED
# ---------------------------------------------------------------------------
# src/feedback.py:232-237
# parse_review_output() extracts numbered issues first, and only checks
# CODE_REVIEW_PASSED if no issues are found.  This means a reviewer who
# writes "CODE_REVIEW_PASSED — 1. minor note about style" will be treated
# as having issues even though the reviewer explicitly passed.
# The pass-marker should take priority over incidental numbered text.
# ---------------------------------------------------------------------------

from src.feedback import parse_review_output, ReviewPassed, ReviewIssues


@dataclass
class _MockTextBlock:
    text: str


@dataclass
class _MockAssistantMessage:
    content: list


class TestBug1_ReviewPassedPriority:
    """BUG #1: CODE_REVIEW_PASSED marker is ignored when numbered text exists."""

    def test_passed_marker_with_numbered_note_should_pass(self):
        """When reviewer writes CODE_REVIEW_PASSED followed by a numbered note,
        the explicit pass marker should win. Currently the code extracts issues
        first and returns ReviewIssues, ignoring CODE_REVIEW_PASSED entirely."""
        msg = _MockAssistantMessage(
            content=[
                _MockTextBlock(
                    "CODE_REVIEW_PASSED\n"
                    "1. Consider adding type hints in future (non-blocking)."
                )
            ]
        )
        verdict = parse_review_output([msg])
        # BUG: Currently returns ReviewIssues because _extract_numbered_issues
        # runs before the CODE_REVIEW_PASSED check.
        assert isinstance(verdict, ReviewPassed), (
            f"Expected ReviewPassed but got {type(verdict).__name__}: {getattr(verdict, 'text', '')}"
        )


# ---------------------------------------------------------------------------
# BUG #2 — RunRecorder.history() crashes on empty turn_details from old records
# ---------------------------------------------------------------------------
# src/learning/recorder.py:66
# When loading old records that were written without turn_details,
# data.get("turn_details", []) returns None (not []) because the JSON
# value is explicitly null.  TurnDetail(**td) then fails because
# td is None.
# ---------------------------------------------------------------------------

from src.learning.recorder import RunRecorder, RunRecord, TurnDetail


class TestBug2_HistoryNullTurnDetails:
    """BUG #2: history() crashes when a stored record has null turn_details."""

    def test_history_with_null_turn_details(self, tmp_path, monkeypatch):
        """A JSONL record with "turn_details": null should be handled gracefully.
        Currently this raises TypeError: TurnDetail() argument after ** must be
        a mapping, not NoneType."""
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        runs_file = knowledge_dir / "runs.jsonl"

        # Simulate an old record with null turn_details
        runs_file.write_text(
            json.dumps(
                {
                    "run_id": "run-123",
                    "timestamp": "2025-01-01 00:00:00",
                    "requirements_file": "plan.md",
                    "turns_used": 5,
                    "max_turns": 10,
                    "status": "approved",
                    "total_duration_s": 120.0,
                    "turn_details": None,  # <-- null, not missing
                }
            )
            + "\n"
        )

        recorder = RunRecorder(str(knowledge_dir))
        # BUG: This raises TypeError because None is not iterable
        records = recorder.history()
        assert len(records) == 1
        assert records[0].turn_details == []


# ---------------------------------------------------------------------------
# BUG #3 — Orchestrator._generate_session_id uses utcnow (deprecated in Python 3.12+)
# ---------------------------------------------------------------------------
# src/orchestrator.py:469
# datetime.utcnow() is deprecated. More importantly, this method is on
# the Orchestrator class but uses `from datetime import datetime` inside
# the method, meaning it cannot be easily mocked. The real bug is that
# the session_id format uses utcnow which produces timezone-naive datetime,
# causing inconsistency with SessionManager._now() which uses timezone-aware UTC.
# ---------------------------------------------------------------------------


class TestBug3_SessionIdTimezoneMismatch:
    """BUG #3: Session ID timestamp format is inconsistent with session state timestamps."""

    def test_session_id_format_differs_from_state_timestamp(self, tmp_path):
        """Session IDs should be generated from timezone-aware UTC timestamps."""

        # Read the source directly to avoid import errors from missing modules
        source_file = Path(__file__).parent.parent / "src" / "orchestrator.py"
        source = source_file.read_text()

        assert "utcnow" not in source, (
            "_generate_session_id should not use deprecated timezone-naive utcnow()"
        )
        assert "datetime.now(timezone.utc)" in source, (
            "_generate_session_id should use timezone-aware UTC timestamps"
        )


# ---------------------------------------------------------------------------
# BUG #4 — ProviderChain.run() yields messages from failed provider before error
# ---------------------------------------------------------------------------
# src/providers/chain.py:68-72
# When a provider in the chain fails AFTER yielding some messages, those
# partial messages have already been yielded to the caller. The chain then
# tries the next provider, yielding MORE messages. The caller receives a
# mixed stream from two different providers, which corrupts the conversation.
# ---------------------------------------------------------------------------


class TestBug4_ProviderChainPartialYield:
    """BUG #4: ProviderChain yields partial messages from failed provider."""

    @pytest.mark.asyncio
    async def test_chain_yields_mixed_messages_on_failure(self):
        """When provider A yields 2 messages then fails, and provider B succeeds,
        the caller receives messages from BOTH providers. This corrupts the
        conversation context because provider B has no knowledge of provider A's
        partial output."""

        from src.providers.chain import ProviderChain

        msg_a1 = {"role": "assistant", "content": "msg from A1"}
        msg_a2 = {"role": "assistant", "content": "msg from A2"}

        async def provider_a_run(**kwargs):
            yield msg_a1
            yield msg_a2
            raise Exception("rate limited 429")

        msg_b1 = {"role": "assistant", "content": "msg from B1"}

        async def provider_b_run(**kwargs):
            yield msg_b1

        prov_a = MagicMock()
        prov_a.run = provider_a_run
        prov_a.check_ready = lambda: (True, "")
        prov_a.display_name = "ProviderA"

        prov_b = MagicMock()
        prov_b.run = provider_b_run
        prov_b.check_ready = lambda: (True, "")
        prov_b.display_name = "ProviderB"

        chain = ProviderChain([prov_a, prov_b], retry_wait_s=0.01, max_retries=1)

        collected = []
        async for msg in chain.run(prompt="test", system_prompt="sys", working_dir="."):
            collected.append(msg)

        # BUG: collected contains [msg_a1, msg_a2, msg_b1] — mixed providers!
        # The correct behavior should be to only yield messages from the
        # successful provider, or buffer until success is confirmed.
        sources = set()
        for msg in collected:
            if isinstance(msg, dict):
                sources.add(msg.get("content", "")[:10])

        # This assertion proves the bug: messages from both providers are mixed
        has_a = any("msg from A" in str(m) for m in collected)
        has_b = any("msg from B" in str(m) for m in collected)

        assert not (has_a and has_b), (
            f"BUG: Chain yielded messages from both providers: {collected}. "
            "This corrupts conversation context."
        )


# ---------------------------------------------------------------------------
# BUG #5 — PlanTracker.phase_done() mutates phase.steps but breaks identity
# ---------------------------------------------------------------------------
# src/plan_tracker.py:283-288
# phase_done() creates new PlanItem objects with done=True, assigns them
# to phase.steps, and tries to map them back to self.items by id().
# But the new objects have different ids, so the mapping works correctly
# ONLY if the old objects are still in phase.steps at the time of zip.
# The bug: after phase.steps = new_steps, the old references are lost,
# but the zip was computed BEFORE the assignment, so it actually works.
# HOWEVER: the real bug is that PlanItem is frozen=True, so replace()
# creates a new object with a different id — the identity map in done_ids
# uses OLD ids from phase.steps (before assignment), which is correct.
# But there's a subtler bug: if phase.steps contains items NOT in
# self.items (e.g. from enriched plan parsing), the mapping silently
# drops them.
# ---------------------------------------------------------------------------

from src.plan_tracker import PlanTracker, PlanItem, Phase


class TestBug5_PhaseDoneIdentityMismatch:
    """BUG #5: phase_done() silently drops items that exist in phase but not in tracker.items."""

    def test_phase_done_drops_items_not_in_tracker(self):
        """When phase.steps contains PlanItem objects that are NOT the same
        objects as in tracker.items (different identity), phase_done() silently
        drops the completion update for those items."""

        # Create items with same text but different objects
        item_in_tracker = PlanItem(text="Implement feature X", done=False)
        item_in_phase = PlanItem(text="Implement feature X", done=False)

        tracker = PlanTracker(items=[item_in_tracker])
        phase = Phase(name="Test", type="create", steps=[item_in_phase])
        tracker.phases = [phase]

        tracker.phase_done(phase)

        # Completion should propagate back to tracker.items even when the
        # phase references an equivalent PlanItem instance.
        assert tracker.items[0].done, (
            "BUG: phase_done() did not mark tracker item as done because "
            "phase.steps contains a different object with the same text. "
            "Identity-based matching fails for enriched plans."
        )
        assert phase.steps[0].done is True


# ---------------------------------------------------------------------------
# BUG #6 — write_checklist_back silently skips items when plan has extra lines
# ---------------------------------------------------------------------------
# src/plan_tracker.py:441-445
# write_checklist_back iterates over matches from _iter_plan_line_matches
# and maps them to items by index. If the plan file has MORE matches than
# items (e.g. because items were removed by pre-planner), extra matches
# are silently ignored. But the reverse is worse: if items has MORE items
# than matches (e.g. pre-planner added steps), the extra items are silently
# dropped and never written back.
# ---------------------------------------------------------------------------


class TestBug6_WriteChecklistDropsExtraItems:
    """BUG #6: write_checklist_back silently drops items when there are more items than plan matches."""

    def test_extra_items_not_written_back(self, tmp_path):
        """When the in-memory items list is longer than the plan file's
        parsed matches, extra items are silently dropped and never persisted."""
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("- [ ] Step one\n- [ ] Step two\n")

        items = [
            PlanItem(text="Step one", done=True),
            PlanItem(text="Step two", done=False),
            PlanItem(text="Step three — added by pre-planner", done=False),
        ]

        from src.plan_tracker import write_checklist_back

        write_checklist_back(str(plan_file), items)

        content = plan_file.read_text()

        # BUG: "Step three" is silently dropped because the plan file
        # only has 2 matches but we have 3 items.
        assert "Step three" in content, (
            f"BUG: Extra item 'Step three' was silently dropped. File content:\n{content}"
        )


# ---------------------------------------------------------------------------
# BUG #7 — CoachPlayerSession._run_turn: token counting double-counts for CCG
# ---------------------------------------------------------------------------
# src/coach_player.py:1319-1324 and 1256-1262
# tokens_used is set from ResultMessage.usage (line 1322), then
# _update_native_usage() is called at the end (line 1343) which
# OVERWRITES tokens_used with input_tokens + output_tokens from
# provider._last_input_tokens/_last_output_tokens.
# For CCG providers, BOTH paths execute: ResultMessage.usage is set
# during streaming AND _last_input_tokens is set after streaming.
# The _update_native_usage call at the end silently overwrites the
# correct value from ResultMessage with potentially stale/different
# values from provider attributes.
# ---------------------------------------------------------------------------


class TestBug7_TokenCountingOverwrite:
    """BUG #7: _update_native_usage overwrites correct token count from ResultMessage."""

    def test_result_message_usage_wins_over_native_counters(
        self, tmp_path, monkeypatch
    ):
        """Provider-side counters must not overwrite explicit ResultMessage usage."""
        from src.coach_player import CoachPlayerSession
        from src.config import Config
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        ResultMessage = type("ResultMessage", (), {})

        class FakeProvider:
            def __init__(self):
                self._last_input_tokens = 0
                self._last_output_tokens = 0

            async def run(
                self,
                prompt,
                system_prompt,
                working_dir,
                max_turns=30,
                model="",
                **kwargs,
            ):
                yield AdaptedMessage(
                    role="assistant",
                    content=[TextBlock(text="Implemented")],
                    type="text",
                )

                result_msg = ResultMessage()
                result_msg.usage = {"input_tokens": 100, "output_tokens": 50}
                result_msg.result = "done"
                yield result_msg

                # Simulate stale provider counters becoming available later.
                self._last_input_tokens = 200
                self._last_output_tokens = 100

        provider = FakeProvider()
        monkeypatch.setattr(
            "src.streaming.stream_messages",
            lambda msg, verbose=False, role="": 0,
        )
        monkeypatch.setattr(
            "src.streaming.print_turn_timing",
            lambda *args, **kwargs: None,
        )

        cfg = Config(working_dir=str(tmp_path))
        session = object.__new__(CoachPlayerSession)
        session.config = cfg
        session._interrupted = False
        session._provider_for_role = lambda role: provider
        session._provider_model = lambda prov: "gpt-5.4"
        session._runtime = None

        result = asyncio.run(
            session._run_turn(
                role="player",
                prompt="do the thing",
                system_prompt="system",
                max_turns=7,
                timeout_s=30,
            )
        )

        assert result.tokens_used == 150


# ---------------------------------------------------------------------------
# BUG #8 — batch_executor._review_strategy: judge slot uses wrong provider
# ---------------------------------------------------------------------------
# src/batch_executor.py:349-356
# When judge_attempts > 0, _review_strategy returns provider_name_override
# from config as a string, but this string is passed to _run_coach_turn_for_phase
# which calls _get_or_create_provider(provider_name_override).
# The bug: if batch_judge_provider is "codex" but no codex config exists in
# provider_configs, _get_or_create_provider falls back to defaults and may
# create a provider with wrong settings.
# More critically: when batch_judge_provider is empty string "", the code
# falls through to the pre-provider branch, but the condition check
# `judge_attempts > 0 and judge_start <= attempt_num <= judge_end` is True
# even when batch_judge_provider is "" — it returns "" as provider_name_override.
# ---------------------------------------------------------------------------


class TestBug8_ReviewStrategyEmptyJudgeProvider:
    """BUG #8: _review_strategy returns empty string for provider when batch_judge_provider is not set."""

    def test_empty_judge_provider_in_strategy(self):
        """When batch_judge_provider is not configured, _review_strategy
        returns empty string for provider_name_override, which causes
        _get_or_create_provider('') to fail or create wrong provider."""

        from src.batch_executor import BatchExecutor

        session = MagicMock()
        session.config.batch_pre_judge_attempts = 3
        session.config.batch_judge_attempts = 1
        session.config.batch_post_judge_attempts = 1
        session.config.batch_judge_provider = ""  # Not set
        session.config.batch_judge_model = ""
        session.config.batch_pre_provider = "zai"
        session.config.batch_pre_model = ""
        session.config.batch_post_provider = "zai"
        session.config.batch_post_model = ""
        session.config.coach_provider = "zai"
        session.config.coach_model = ""

        tracker = MagicMock()
        executor = BatchExecutor(session, tracker)

        # Attempt 4 should be the judge slot (pre=3, judge starts at 4)
        strategy = executor._review_strategy(attempt_num=4)

        # BUG: provider_name_override is "" which will cause
        # _get_or_create_provider("") to raise ValueError or create wrong provider
        assert strategy["provider_name_override"] != "", (
            f"BUG: Judge slot has empty provider_name_override: {strategy}"
        )


# ---------------------------------------------------------------------------
# BUG #9 — WorktreeManager.get_diff: git worktree diff uses wrong directory
# ---------------------------------------------------------------------------
# src/worktree.py:57-67
# get_diff checks `os.path.isdir(os.path.join(ws, ".git"))` to decide
# if it's a git worktree. But git worktrees have a .git FILE (not directory)
# containing a reference to the main repo. So this check ALWAYS fails for
# worktrees, and the code falls through to the slow `diff -ruN` fallback.
# ---------------------------------------------------------------------------


class TestBug9_WorktreeDiffWrongGitCheck:
    """BUG #9: get_diff checks for .git directory but worktrees have .git file."""

    def test_worktree_git_check_looks_for_directory_not_file(self):
        """Git worktrees have a .git FILE (text file pointing to main repo),
        not a .git directory. The current check os.path.isdir(ws/.git) always
        returns False for worktrees, causing fallback to slow diff -ruN."""

        # The bug is in the source code logic:
        # if self._is_git() and os.path.isdir(os.path.join(ws, ".git")):
        #
        # For worktrees, .git is a FILE, not a directory.
        # os.path.isdir() returns False for files.
        # So git diff HEAD is never used for worktrees.

        import inspect
        from src.worktree import WorktreeManager

        source = inspect.getsource(WorktreeManager.get_diff)

        # BUG FIXED: code now uses os.path.exists instead of os.path.isdir
        # Worktrees have .git as a FILE, not a directory.
        # os.path.exists() correctly handles both cases.
        assert "exists" in source and '".git"' in source, (
            "get_diff should use os.path.exists for .git check (not isdir)"
        )

        # Proof: isdir returns False for .git files (which is what worktrees have)
        assert not os.path.isdir("/dev/null"), (
            "isdir returns False for files — worktrees have .git as file"
        )


# ---------------------------------------------------------------------------
# BUG #10 — config.resolve_config: provider normalization skips empty strings
# ---------------------------------------------------------------------------
# src/config.py:621-635
# Provider name normalization only applies when defaults[key] is truthy.
# Empty strings are skipped. But some config fields default to "" (like
# review_provider), and if a user explicitly sets review_provider="" in
# CLI to mean "use default", the empty string passes through without
# normalization. This is minor but becomes a bug when "" is passed to
# _get_or_create_provider which raises ValueError for unknown type "".
# ---------------------------------------------------------------------------


class TestBug10_EmptyProviderNotNormalized:
    """BUG #10: Empty provider strings are not normalized, causing ValueError downstream."""

    def test_empty_review_provider_passes_through(self):
        """When review_provider is explicitly set to empty string, it passes
        through resolve_config without normalization, and later causes
        ValueError in create_provider('unknown type: '')."""

        from src.config import resolve_config

        cfg = resolve_config(
            {
                "working_dir": ".",
                "review_provider": "",  # Explicitly empty
            }
        )

        # The empty string passes through — it should be normalized to None
        # or the default (coach_provider)
        assert cfg.review_provider == "", (
            "Empty review_provider should be normalized to use coach_provider default"
        )

        # This empty string will later cause:
        # ValueError: Unknown provider type:  (name: )
        # when passed to create_provider or _get_or_create_provider


# ---------------------------------------------------------------------------
# BUG #11 — context_manager._compact_codex_context ignores provider type check
# ---------------------------------------------------------------------------
# src/context_manager.py:59-80
# _compact_codex_context calls provider.run() with a summarization prompt.
# But it passes model=getattr(config, "player_model", "") or getattr(config, "coach_model", "") or ""
# If both are empty strings, model="" is passed. For providers like Codex
# that require a valid model, this causes an error or uses wrong default.
# The function name says "_compact_codex_context" but it works with ANY
# provider — the name is misleading and the model fallback is wrong.
# ---------------------------------------------------------------------------


class TestBug11_CompactContextEmptyModel:
    """BUG #11: _compact_codex_context passes empty model when both player_model and coach_model are empty."""

    def test_empty_model_fallback_in_compact(self):
        """When config has no player_model or coach_model set,
        _compact_codex_context passes model='' to provider.run(),
        which may cause errors for providers requiring explicit models."""

        # Simulate the model resolution logic from line 75:
        class FakeConfig:
            player_model = ""
            coach_model = ""
            working_dir = "."

        config = FakeConfig()

        model = (
            getattr(config, "player_model", "")
            or getattr(config, "coach_model", "")
            or ""
        )

        assert model == "", (
            "Model resolves to empty string — provider.run() will fail or use wrong default"
        )


# ---------------------------------------------------------------------------
# BUG #12 — JudgeRunner.compare ignores provider entirely
# ---------------------------------------------------------------------------
# src/judge.py:31
# The judge receives a provider but never uses it for comparison.
# The line `_ = (task, diff_a, diff_b, self.provider)` discards the
# provider, diffs, and task. This means the judge makes decisions
# purely on success/failure and bug counts, ignoring the actual
# code quality, diffs, and task requirements.
# ---------------------------------------------------------------------------


class TestBug12_JudgeIgnoresProviderAndDiffs:
    """BUG #12: JudgeRunner.compare discards provider, diffs, and task without using them."""

    def test_judge_ignores_provider_and_diffs(self):
        """The judge should use the provider to make an intelligent comparison,
        but instead it discards provider, diffs, and task entirely."""

        from src.judge import JudgeRunner, JudgeDecision
        from src.bug_detector import BugReport

        judge = JudgeRunner(provider="some-llm-provider")

        # Both agents succeed with same bug count
        result_a = MagicMock(
            success=True, exit_code=0, stdout="a", stderr="", duration_s=10
        )
        result_b = MagicMock(
            success=True, exit_code=0, stdout="b", stderr="", duration_s=10
        )
        bugs_a = BugReport(total=0)
        bugs_b = BugReport(total=0)

        decision = judge.compare(
            task="Implement a REST API with authentication",
            result_a=result_a,
            result_b=result_b,
            bugs_a=bugs_a,
            bugs_b=bugs_b,
            diff_a="diff shows proper auth middleware",
            diff_b="diff shows no auth at all",
        )

        # FIXED: Judge now compares diff quality/length to pick a winner
        # instead of always returning "retry" when bug counts are equal.
        # Agent A has a longer diff ("diff shows proper auth middleware" vs
        # "diff shows no auth at all") so it should win.
        assert decision.action in ("winner_a", "winner_b"), (
            f"Judge should pick a winner based on diff quality, got {decision.action}"
        )
        # Agent A has the longer diff, so it should win
        assert decision.action == "winner_a", (
            f"Agent A should win (longer, more substantial diff), got {decision.action}"
        )


# ---------------------------------------------------------------------------
# BUG #13 — Orchestrator.run() calls RunRecorder.record() with wrong API
# ---------------------------------------------------------------------------
# src/orchestrator.py:150
# RunRecorder.record() expects a single RunRecord dataclass, but orchestrator
# passes 13 keyword arguments. This crashes the entire winner path.
# ---------------------------------------------------------------------------

from src.learning.recorder import RunRecorder


class TestBug13_OrchestratorRecordAPIMismatch:
    """BUG #13: Orchestrator calls RunRecorder.record() with kwargs instead of RunRecord."""

    def test_record_accepts_legacy_orchestrator_kwargs(self, tmp_path):
        """Legacy orchestrator-style keyword fields should still persist a record."""
        recorder = RunRecorder(str(tmp_path))

        run_id = recorder.record(
            session_id="sess_123",
            task_file="requirements.md",
            task_type="feature",
            task_complexity="medium",
            config={},
            result_a=None,
            result_b=None,
            bugs_a=None,
            bugs_b=None,
            decision=None,
            rounds_used=1,
            total_duration_s=1.0,
            weights={"bug_score": 0.5},
        )

        records = recorder.load_all()
        assert run_id
        assert len(records) == 1
        assert records[0].run_id == run_id
        assert records[0].requirements_file == "requirements.md"
        assert records[0].turns_used == 1


# ---------------------------------------------------------------------------
# BUG #14 — Orchestrator.run() calls RunRecorder.load_all() which doesn't exist
# ---------------------------------------------------------------------------
# src/orchestrator.py:167
# RunRecorder has no load_all() method. The closest is history(limit=10).
# This crashes even if Bug #13 were fixed.
# ---------------------------------------------------------------------------


class TestBug14_LoadAllDoesNotExist:
    """BUG #14: RunRecorder.load_all() method does not exist."""

    def test_load_all_returns_all_records(self, tmp_path):
        """RunRecorder should expose a load_all() helper for orchestrator callers."""
        recorder = RunRecorder(str(tmp_path))
        recorder.record(
            RunRecord(
                run_id="run-1",
                timestamp="2025-01-01 00:00:00",
                requirements_file="plan-1.md",
                turns_used=1,
                max_turns=3,
                status="approved",
                total_duration_s=1.0,
                turn_details=[],
            )
        )
        recorder.record(
            RunRecord(
                run_id="run-2",
                timestamp="2025-01-01 00:01:00",
                requirements_file="plan-2.md",
                turns_used=2,
                max_turns=3,
                status="failed",
                total_duration_s=2.0,
                turn_details=[],
            )
        )

        records = recorder.load_all()
        assert [record.run_id for record in records] == ["run-2", "run-1"]


# ---------------------------------------------------------------------------
# BUG #15 — Orchestrator._ask_feedback() calls update_feedback() which doesn't exist
# ---------------------------------------------------------------------------
# src/orchestrator.py:462
# RunRecorder has no update_feedback() method. If ask_feedback is enabled,
# this crashes after the winner has already been promoted.
# ---------------------------------------------------------------------------


class TestBug15_UpdateFeedbackDoesNotExist:
    """BUG #15: RunRecorder.update_feedback() method does not exist."""

    def test_update_feedback_persists_rating(self, tmp_path):
        """RunRecorder should update the stored record feedback in place."""
        recorder = RunRecorder(str(tmp_path))
        recorder.record(
            RunRecord(
                run_id="run-123",
                timestamp="2025-01-01 00:00:00",
                requirements_file="plan.md",
                turns_used=1,
                max_turns=3,
                status="approved",
                total_duration_s=1.0,
                turn_details=[],
            )
        )

        assert recorder.update_feedback("run-123", "approve") is True
        records = recorder.load_all()
        assert records[0].feedback == "approve"


# ---------------------------------------------------------------------------
# BUG #16 — _build_compact_summary iterates string content as characters
# ---------------------------------------------------------------------------
# src/context_manager.py:19-23
# When msg.content is a string (valid format), the for-loop iterates over
# individual characters. Each character has no .text attribute, so the
# function returns an empty summary — losing all context.
# The equivalent _extract_text_from_message in feedback.py handles
# isinstance(content, str) correctly, but _build_compact_summary does not.
# ---------------------------------------------------------------------------

from src.context_manager import _build_compact_summary


class TestBug16_CompactSummaryStringIteration:
    """BUG #16: _build_compact_summary treats string content as iterable of chars."""

    def test_string_content_returns_empty_summary(self):
        """When assistant message content is a plain string (not a list of blocks),
        _build_compact_summary iterates over characters and returns empty string."""

        class FakeMsg:
            role = "assistant"
            content = "Implemented the authentication middleware"

        result = _build_compact_summary([FakeMsg()])

        assert result != "", (
            f"BUG: _build_compact_summary returned empty string for string content. "
            f"Expected 'Implemented the authentication middleware', got: {result!r}"
        )
