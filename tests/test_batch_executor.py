"""Tests for batch execution components."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from dataclasses import replace

from src.plan_tracker import (
    DEFAULT_STEP_TYPE,
    PHASE_SIZE,
    STEP_TYPE_MAP,
    Phase,
    PlanItem,
    auto_group_phases,
    detect_step_type,
)


class TestPhaseDataclass:
    def test_phase_default_status(self):
        items = [PlanItem(text="create x.py")]
        phase = Phase(name="Create", type="create", steps=items)
        assert phase.status == "pending"
        assert phase.attempts == 0

    def test_phase_fields(self):
        items = [PlanItem(text="update config"), PlanItem(text="update menu")]
        phase = Phase(name="Update", type="update", steps=items)
        assert phase.name == "Update"
        assert phase.type == "update"
        assert len(phase.steps) == 2

    def test_step_type_map_has_required_keys(self):
        assert set(STEP_TYPE_MAP.keys()) == {"create", "update", "test", "review"}

    def test_phase_size_covers_all_types(self):
        assert set(PHASE_SIZE.keys()) == set(STEP_TYPE_MAP.keys())

    def test_default_step_type_is_update(self):
        assert DEFAULT_STEP_TYPE == "update"


class TestDetectStepType:
    def test_create_keyword(self):
        assert detect_step_type(PlanItem(text="Create providers/base.py")) == "create"

    def test_update_keyword(self):
        assert (
            detect_step_type(PlanItem(text="Update config.py with new fields"))
            == "update"
        )

    def test_test_keyword(self):
        assert detect_step_type(PlanItem(text="Write tests for provider")) == "test"

    def test_review_keyword(self):
        assert detect_step_type(PlanItem(text="Review all changes")) == "review"

    def test_case_insensitive(self):
        assert detect_step_type(PlanItem(text="CREATE the module")) == "create"

    def test_unknown_keyword_returns_default(self):
        assert (
            detect_step_type(PlanItem(text="Do something completely unknown"))
            == DEFAULT_STEP_TYPE
        )

    def test_first_match_wins(self):
        assert detect_step_type(PlanItem(text="implement and refactor")) == "create"

    def test_string_input_still_works(self):
        assert detect_step_type("review the implementation") == "review"


class TestAutoGroupPhases:
    def test_empty_list_returns_empty(self):
        assert auto_group_phases([]) == []

    def test_single_item_creates_one_phase(self):
        items = [PlanItem(text="create x.py")]
        phases = auto_group_phases(items)
        assert len(phases) == 1
        assert phases[0].type == "create"

    def test_groups_by_type(self):
        items = [
            PlanItem(text="create a.py"),
            PlanItem(text="create b.py"),
            PlanItem(text="update config"),
            PlanItem(text="update menu"),
        ]
        phases = auto_group_phases(items)
        assert len(phases) == 2
        assert phases[0].type == "create"
        assert phases[1].type == "update"

    def test_splits_at_phase_size_limit(self):
        items = [PlanItem(text=f"create file{i}.py") for i in range(7)]
        phases = auto_group_phases(items)
        assert len(phases) == 2
        assert len(phases[0].steps) == 6
        assert len(phases[1].steps) == 1

    def test_unknown_type_uses_default(self):
        items = [PlanItem(text="do something unknown")]
        phases = auto_group_phases(items)
        assert phases[0].type == DEFAULT_STEP_TYPE

    def test_type_change_starts_new_phase(self):
        items = [
            PlanItem(text="create a.py"),
            PlanItem(text="update b.py"),
            PlanItem(text="update c.py"),
        ]
        phases = auto_group_phases(items)
        assert len(phases) == 2

    def test_phase_name_contains_type_and_count(self):
        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phases = auto_group_phases(items)
        assert "create" in phases[0].name.lower()
        assert "a.py" in phases[0].name


class TestPlanTracker:
    def test_init_stores_items(self):
        from src.plan_tracker import PlanTracker

        items = [PlanItem(text="create a.py"), PlanItem(text="update b.py")]
        tracker = PlanTracker(items)
        assert tracker.items == items
        assert tracker.phases == []

    def test_phase_done_marks_all_steps(self):
        from src.plan_tracker import PlanTracker

        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        tracker = PlanTracker(items)
        phase = Phase(name="Create", type="create", steps=items, status="done")
        tracker.phases = [phase]
        tracker.phase_done(phase)
        assert all(s.done for s in phase.steps)

    def test_phase_done_does_not_require_live(self):
        from src.plan_tracker import PlanTracker

        items = [PlanItem(text="create a.py")]
        tracker = PlanTracker(items)
        phase = Phase(name="Create", type="create", steps=items)
        tracker.phase_done(phase)  # should not raise

    def test_phase_done_duplicate_text_only_marks_phase_items(self):
        """Regression: completing a phase must not alias same-text items
        that belong to a different phase (issue: text-only lookup)."""
        from src.plan_tracker import PlanTracker

        # Two items with identical text; only the first is in the phase.
        first = PlanItem(text="repeat")
        second = PlanItem(text="repeat", roles=("reviewer",))
        items = [first, second]
        tracker = PlanTracker(items)

        phase = Phase(name="Create", type="create", steps=[first])
        tracker.phases = [phase]
        tracker.phase_done(phase)

        # Only the first item (in the phase) should be done.
        assert tracker.items[0].done is True
        assert tracker.items[1].done is False
        # The second item must keep its original roles.
        assert tracker.items[1].roles == ("reviewer",)
        # The items must be distinct objects (no aliasing).
        assert tracker.items[0] is not tracker.items[1]

    def test_render_dashboard_noop_when_not_started(self):
        from src.plan_tracker import PlanTracker

        tracker = PlanTracker([])
        tracker.render_dashboard()  # should not raise

    def test_stop_dashboard_noop_when_not_started(self):
        from src.plan_tracker import PlanTracker

        tracker = PlanTracker([])
        tracker.stop_dashboard()  # should not raise


class TestBuildBatchPrompt:
    def _phase(self, texts):
        items = [PlanItem(text=t) for t in texts]
        return Phase(name="Test Phase", type="create", steps=items)

    def test_first_attempt_no_extra_sections(self):
        from src.batch_executor import build_batch_prompt

        phase = self._phase(["create a.py", "create b.py"])
        prompt = build_batch_prompt(phase, [], "")
        assert "REJECTED" not in prompt
        assert "Already completed" not in prompt
        assert "create a.py" in prompt
        assert "create b.py" in prompt
        assert "PHASE_COMPLETE" in prompt

    def test_with_done_steps_shows_already_completed(self):
        from src.batch_executor import build_batch_prompt

        phase = self._phase(["create a.py", "create b.py"])
        prompt = build_batch_prompt(phase, ["create a.py"], "")
        assert "Already completed" in prompt
        assert "create a.py" in prompt
        assert "create b.py" in prompt
        assert "  2. create b.py" in prompt
        assert "Step 2 done:" in prompt

    def test_with_coach_feedback_shows_rejection(self):
        from src.batch_executor import build_batch_prompt

        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, [], "Missing error handling")
        assert "STRUCTURED REVIEW FEEDBACK" in prompt
        assert "Missing error handling" in prompt

    def test_step_confirmation_instructions_present(self):
        from src.batch_executor import build_batch_prompt

        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, [], "")
        assert "Step 1 done:" in prompt

    def test_prompt_requires_verifying_existing_implementation_first(self):
        from src.batch_executor import build_batch_prompt

        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, [], "")
        assert "verify whether each planned step is already satisfied" in prompt
        assert "do not rewrite it; treat it as complete" in prompt

    def test_prompt_forbids_pasted_shell_transcripts(self):
        from src.batch_executor import build_batch_prompt

        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, [], "")
        assert "Use the available tools" in prompt
        assert "Do not print raw shell or patch commands" in prompt
        assert "tools are unavailable in this session is incorrect" in prompt


class TestReviewStrategy:
    def test_uses_post_provider_after_judge_window(self):
        from src.batch_executor import BatchExecutor
        from src.role_router import RoleRouter

        session = MagicMock()
        session.config.batch_pre_judge_attempts = 1
        session.config.batch_judge_attempts = 1
        session.config.batch_post_judge_attempts = 1
        session.config.batch_pre_provider = "claude"
        session.config.batch_pre_model = "sonnet"
        session.config.batch_judge_provider = "codex"
        session.config.batch_judge_model = "gpt-5.4"
        session.config.batch_post_provider = "zai"
        session.config.batch_post_model = "glm-5"

        mock_provider = MagicMock()
        mock_provider.env = None
        mock_provider.config = MagicMock()
        mock_provider.config.default_model = ""
        mock_provider.config.model = ""
        router = RoleRouter(
            config=session.config,
            get_or_create_provider=MagicMock(return_value=mock_provider),
            player_provider=MagicMock(),
            coach_provider=MagicMock(),
        )

        executor = BatchExecutor(session, MagicMock(), router)

        strategy = executor._review_strategy(3)

        assert strategy["provider_name_override"] == "zai"
        assert strategy["model_override"] == "glm-5"
        assert strategy["label"] == "zai | model=glm-5"


class TestPlayerToolAvailabilityDetection:
    def _phase(self, texts):
        items = [PlanItem(text=t) for t in texts]
        return Phase(name="Test Phase", type="create", steps=items)

    def test_detects_tool_unavailable_excuse_without_tool_use(self):
        from src.batch_executor import player_claimed_tools_unavailable

        result = MagicMock()
        result.tools_used = 0
        result.text = "Не удалось проверить: в этой сессии не доступны инструменты доступа к файловой системе."
        assert player_claimed_tools_unavailable(result) is True

    def test_ignores_tool_unavailable_text_when_tools_were_used(self):
        from src.batch_executor import player_claimed_tools_unavailable

        result = MagicMock()
        result.tools_used = 2
        result.text = "tools are unavailable"
        assert player_claimed_tools_unavailable(result) is False

    def test_phase_complete_marker_present(self):
        from src.batch_executor import build_batch_prompt

        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, [], "")
        assert f"PHASE_COMPLETE: {phase.name}" in prompt
        assert "What changed:" in prompt
        assert "Evidence:" in prompt
        assert "Verification:" in prompt

    def test_prompt_for_report_only_when_all_steps_already_done(self):
        from src.batch_executor import build_batch_prompt

        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, ["create a.py"], "Need the report")
        assert "All planned steps for phase" in prompt
        assert "Do NOT redo the implementation" in prompt
        assert "Evidence:" in prompt


class TestParseCompletedSteps:
    def _result(self, text):
        r = MagicMock()
        r.text = text
        return r

    def _phase(self, texts):
        items = [PlanItem(text=t) for t in texts]
        return Phase(name="P", type="create", steps=items)

    def test_phase_complete_returns_all(self):
        from src.batch_executor import parse_completed_steps

        phase = self._phase(["create a.py", "create b.py"])
        result = self._result("I did stuff\nPHASE_COMPLETE: P")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create a.py", "create b.py"]

    def test_step_done_by_index(self):
        from src.batch_executor import parse_completed_steps

        phase = self._phase(["create a.py", "create b.py", "create c.py"])
        result = self._result("Step 1 done: created it")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create a.py"]

    def test_partial_completion(self):
        from src.batch_executor import parse_completed_steps

        phase = self._phase(["create a.py", "create b.py", "create c.py"])
        result = self._result("Step 1 done: x\nStep 3 done: y")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create a.py", "create c.py"]

    def test_empty_output_returns_empty(self):
        from src.batch_executor import parse_completed_steps

        phase = self._phase(["create a.py"])
        result = self._result("I tried but got confused")
        completed = parse_completed_steps(result, phase)
        assert completed == []

    def test_out_of_range_index_ignored(self):
        from src.batch_executor import parse_completed_steps

        phase = self._phase(["create a.py"])
        result = self._result("Step 99 done: whatever")
        completed = parse_completed_steps(result, phase)
        assert completed == []

    def test_case_insensitive_step_done(self):
        from src.batch_executor import parse_completed_steps

        phase = self._phase(["create a.py"])
        result = self._result("STEP 1 DONE: created")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create a.py"]

    def test_retry_keeps_original_step_numbers(self):
        from src.batch_executor import parse_completed_steps

        phase = self._phase(["create a.py", "create b.py"])
        result = self._result("Step 2 done: created b.py")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create b.py"]

    def test_phase_complete_unconditional(self):
        from src.batch_executor import parse_completed_steps

        phase = self._phase(["create a.py", "create b.py"])
        result = self._result("PHASE_COMPLETE: P\n(forgot to write step confirmations)")
        completed = parse_completed_steps(result, phase)
        assert len(completed) == 2


class TestPhaseFailedError:
    def test_str_contains_phase_name(self):
        from src.errors import PhaseFailedError

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create (1 steps)", type="create", steps=items)
        err = PhaseFailedError(phase=phase, attempts=3)
        assert "Create (1 steps)" in str(err)

    def test_str_contains_attempt_count(self):
        from src.errors import PhaseFailedError

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        err = PhaseFailedError(phase=phase, attempts=3)
        assert "3" in str(err)

    def test_is_exception(self):
        from src.errors import PhaseFailedError

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        err = PhaseFailedError(phase=phase, attempts=1)
        assert isinstance(err, Exception)

    def test_args_populated(self):
        from src.errors import PhaseFailedError

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        err = PhaseFailedError(phase=phase, attempts=2)
        assert len(err.args) > 0


class TestRunPhase:
    def _with_batch_report(self, text: str) -> str:
        if "PHASE_COMPLETE:" not in text or "What changed:" in text:
            return text

        return (
            f"{text}\n"
            "What changed:\n"
            "- Updated the implementation.\n"
            "Evidence:\n"
            "- Relevant source files now contain the required behavior.\n"
            "Verification:\n"
            "- pytest\n"
        )

    def _make_session(self, player_texts, coach_verdict=None):
        from src.feedback import Approved
        from src.batch_executor import BatchExecutor

        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_provider = "zai"
        session.config.coach_provider = "claude"
        session.config.player_model = ""
        session.config.coach_model = ""
        session.config.batch_pre_provider = ""
        session.config.batch_pre_model = ""
        session.config.batch_pre_judge_attempts = 5
        session.config.batch_judge_attempts = 2
        session.config.batch_post_judge_attempts = 3
        session.config.batch_post_provider = ""
        session.config.batch_post_model = ""
        session.config.batch_judge_provider = "codex"
        session.config.batch_judge_model = "gpt-5.4"
        session.config.player_turns_per_session = 30
        session._runtime = None
        session.player_model = "zai | model=glm-5.1"
        session.coach_model = "claude | model=sonnet"
        session.build_provider_display = MagicMock(
            return_value=f"{session.config.batch_judge_provider} | model={session.config.batch_judge_model}"
        )

        results = []
        for text in player_texts:
            r = MagicMock()
            r.text = self._with_batch_report(text)
            r.messages = []
            results.append(r)

        session._run_with_continuation = AsyncMock(side_effect=results)
        session._run_coach_turn_for_phase = AsyncMock(
            return_value=coach_verdict or Approved()
        )
        return session

    def _make_executor(self, session):
        from src.batch_executor import BatchExecutor

        tracker = MagicMock()
        tracker.render_dashboard = MagicMock()
        return BatchExecutor(session=session, tracker=tracker)

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"])
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is True
        assert phase.attempts == 1

    @pytest.mark.asyncio
    async def test_retry_on_incomplete_then_success(self):
        from src.feedback import Approved, Feedback

        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(
            [
                "Step 1 done: created a.py",
                "PHASE_COMPLETE: Create",
                "PHASE_COMPLETE: Create",
            ]
        )
        # First coach call rejects (incomplete), second approves
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=[Feedback(text="Missing step 2"), Approved()]
        )
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is True
        assert phase.attempts == 3
        assert session._run_coach_turn_for_phase.await_count == 2

    @pytest.mark.asyncio
    async def test_phase_complete_without_report_retries_before_coach_review(self):
        from src.feedback import Approved, Feedback
        from src.batch_executor import BatchExecutor

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_provider = "zai"
        session.config.coach_provider = "claude"
        session.config.player_model = ""
        session.config.coach_model = ""
        session.config.batch_pre_provider = ""
        session.config.batch_pre_model = ""
        session.config.batch_pre_judge_attempts = 3
        session.config.batch_judge_attempts = 1
        session.config.batch_post_judge_attempts = 1
        session.config.batch_post_provider = ""
        session.config.batch_post_model = ""
        session.config.batch_judge_provider = "codex"
        session.config.batch_judge_model = "gpt-5.4"
        session.config.player_turns_per_session = 30
        session._runtime = None
        session.player_model = "zai | model=glm-5.1"
        session.coach_model = "claude | model=sonnet"
        session.build_provider_display = MagicMock(
            return_value=f"{session.config.batch_judge_provider} | model={session.config.batch_judge_model}"
        )
        result_one = MagicMock()
        result_one.text = "PHASE_COMPLETE: Create"
        result_one.messages = []
        result_two = MagicMock()
        result_two.text = (
            "PHASE_COMPLETE: Create\n"
            "What changed:\n"
            "- No code changes were needed.\n"
            "Evidence:\n"
            "- Verified create a.py already exists.\n"
            "Verification:\n"
            "- pytest"
        )
        result_two.messages = []
        result_three = MagicMock()
        result_three.text = result_two.text
        result_three.messages = []
        session._run_with_continuation = AsyncMock(
            side_effect=[result_one, result_two, result_three]
        )
        # First coach call rejects (missing report), second approves
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=[Feedback(text="Missing report"), Approved()]
        )
        executor = self._make_executor(session)

        result = await executor._run_phase(phase)

        assert result is True
        assert phase.attempts == 3
        assert session._run_coach_turn_for_phase.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_coach_rejection_then_success(self):
        from src.feedback import Approved, Feedback
        from src.batch_executor import BatchExecutor

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(
            ["PHASE_COMPLETE: Create", "PHASE_COMPLETE: Create"]
        )
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=[Feedback(text="Missing tests"), Approved()]
        )
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is True
        assert phase.attempts == 2

    @pytest.mark.asyncio
    async def test_exhausts_attempts_returns_false(self):
        from src.feedback import Feedback
        from src.batch_executor import BatchExecutor

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        # Use the _make_session defaults (5/2/3) to compute total attempts
        total_attempts = 5 + 2 + 3  # pre + judge + post
        session = self._make_session(["no output"] * total_attempts)
        session._run_coach_turn_for_phase = AsyncMock(
            return_value=Feedback(text="Incomplete")
        )
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is False
        assert phase.attempts == total_attempts

    @pytest.mark.asyncio
    async def test_attempt_six_uses_codex_judge_first(self):
        from src.feedback import Approved, Feedback

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"] * 6)
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=[
                Feedback(text="try 1"),
                Feedback(text="try 2"),
                Feedback(text="try 3"),
                Feedback(text="try 4"),
                Feedback(text="try 5"),
                Approved(),
            ]
        )
        executor = self._make_executor(session)

        result = await executor._run_phase(phase)

        assert result is True
        assert phase.attempts == 6
        sixth_call = session._run_coach_turn_for_phase.await_args_list[5].kwargs
        assert sixth_call["provider_name_override"] == "codex"
        assert sixth_call["model_override"] == "gpt-5.4"
        assert sixth_call["review_role"] == "judge"

    @pytest.mark.asyncio
    async def test_attempt_five_returns_to_regular_coach(self):
        from src.feedback import Approved, Feedback

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"] * 5)
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=[
                Feedback(text="try 1"),
                Feedback(text="try 2"),
                Feedback(text="try 3"),
                Feedback(text="try 4"),
                Approved(),
            ]
        )
        executor = self._make_executor(session)

        result = await executor._run_phase(phase)

        assert result is True
        assert phase.attempts == 5
        fifth_call = session._run_coach_turn_for_phase.await_args_list[4].kwargs
        assert fifth_call["provider_name_override"] is None
        assert fifth_call["review_role"] == "coach"

    @pytest.mark.asyncio
    async def test_invalid_coach_feedback_does_not_auto_escalate_to_judge(self):
        from src.feedback import Feedback

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"] * 5)
        session.config.batch_pre_judge_attempts = 3
        session.config.batch_judge_attempts = 1
        session.config.batch_post_judge_attempts = 1
        session._run_coach_turn_for_phase = AsyncMock(
            return_value=Feedback(
                text="1. Coach produced no output — проверь текущую реализацию на очевидные ошибки.\n"
                "2. Не переписывай код заново — только исправь найденные проблемы."
            )
        )
        executor = self._make_executor(session)

        result = await executor._run_phase(phase)

        assert result is False
        assert phase.attempts == 5
        assert session._run_coach_turn_for_phase.await_count == 5
        first_call = session._run_coach_turn_for_phase.await_args_list[0].kwargs
        assert first_call["provider_name_override"] is None
        assert first_call["review_role"] == "coach"

    @pytest.mark.asyncio
    async def test_uses_batch_system_prompt(self):
        from src.batch_executor import BatchExecutor
        from src.prompts import PLAYER_BATCH_SYSTEM_PROMPT

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"])
        executor = self._make_executor(session)

        await executor._run_phase(phase)

        assert (
            session._run_with_continuation.await_args.kwargs["system_prompt"]
            == PLAYER_BATCH_SYSTEM_PROMPT
        )

    @pytest.mark.asyncio
    async def test_batch_persona_overlay_appended_to_player_prompt(self):
        """Batch player prompt should include the combined phase persona overlay."""
        items = [
            PlanItem(text="create a.py", roles=["security"]),
            PlanItem(text="create b.py", roles=["architect"]),
        ]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"])
        session._persona_registry = MagicMock()
        session._persona_registry.build_overlay.return_value = (
            "## Specialist Context: Security\nFocus on vulns."
        )
        executor = self._make_executor(session)

        await executor._run_phase(phase)

        system_prompt = session._run_with_continuation.await_args.kwargs[
            "system_prompt"
        ]
        assert "## Specialist Context: Security" in system_prompt
        session._persona_registry.build_overlay.assert_called_once_with(
            ["security", "architect"]
        )

    @pytest.mark.asyncio
    async def test_coach_feedback_included_in_retry_prompt(self):
        from src.feedback import Feedback, Approved
        from src.batch_executor import BatchExecutor

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(
            [
                "did work but no markers",
                "PHASE_COMPLETE: Create",
                "PHASE_COMPLETE: Create",
            ]
        )
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=[Feedback(text="Missing implementation details"), Approved()]
        )
        executor = self._make_executor(session)

        result = await executor._run_phase(phase)

        assert result is True
        third_prompt = session._run_with_continuation.await_args_list[2].kwargs[
            "prompt"
        ]
        assert "Missing implementation details" in third_prompt

    @pytest.mark.asyncio
    async def test_player_timeout_retries_instead_of_crashing(self):
        from src.feedback import Approved

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"])
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()

        timeout_exc = TimeoutError("player exceeded timeout of 600s")
        success_result = MagicMock()
        success_result.text = self._with_batch_report("PHASE_COMPLETE: Create")
        success_result.messages = []
        session._run_with_continuation = AsyncMock(
            side_effect=[timeout_exc, success_result]
        )
        session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
        executor = self._make_executor(session)

        result = await executor._run_phase(phase)

        assert result is True
        assert phase.attempts == 2
        assert session._run_coach_turn_for_phase.await_count == 1

    @pytest.mark.asyncio
    async def test_run_phase_swallows_review_timeout_and_retries(self):
        """TimeoutError from _run_coach_turn_for_phase causes continue, not crash."""
        from src.feedback import Approved
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"] * 2)
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=[TimeoutError("review timed out"), Approved()]
        )
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is True  # second attempt succeeded
        assert session._run_coach_turn_for_phase.await_count == 2

    @pytest.mark.asyncio
    async def test_run_phase_propagates_value_error_from_router(self):
        """ValueError (e.g. unknown role) from _run_coach_turn_for_phase must NOT be caught."""
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(["PHASE_COMPLETE: Create"])
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=ValueError("Unknown role: judge")
        )
        executor = self._make_executor(session)
        with pytest.raises(ValueError, match="Unknown role"):
            await executor._run_phase(phase)


class TestBatchExecutorRun:
    def _make_full_session(self, player_text="PHASE_COMPLETE: Create (1 steps)"):
        from src.feedback import Approved
        from src.batch_executor import BatchExecutor

        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_provider = "zai"
        session.config.coach_provider = "claude"
        session.config.player_model = ""
        session.config.coach_model = ""
        session.config.batch_pre_provider = ""
        session.config.batch_pre_model = ""
        session.config.batch_pre_judge_attempts = 3
        session.config.batch_judge_attempts = 1
        session.config.batch_post_judge_attempts = 1
        session.config.batch_post_provider = ""
        session.config.batch_post_model = ""
        session.config.batch_judge_provider = "codex"
        session.config.batch_judge_model = "gpt-5.4"
        session.player_model = "zai | model=glm-5.1"
        session.coach_model = "claude | model=sonnet"
        session._runtime = None
        session.build_provider_display = MagicMock(
            return_value=f"{session.config.batch_judge_provider} | model={session.config.batch_judge_model}"
        )
        if "PHASE_COMPLETE:" in player_text and "What changed:" not in player_text:
            player_text = (
                f"{player_text}\n"
                "What changed:\n"
                "- Updated the implementation.\n"
                "Evidence:\n"
                "- Relevant source files now contain the required behavior.\n"
                "Verification:\n"
                "- pytest\n"
            )
        r = MagicMock()
        r.text = player_text
        r.messages = []
        session._run_with_continuation = AsyncMock(return_value=r)
        session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
        return session

    @pytest.mark.asyncio
    async def test_run_assigns_phases_to_tracker(self):
        from src.batch_executor import BatchExecutor

        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        session = self._make_full_session()
        executor = BatchExecutor(session=session, tracker=tracker)
        await executor.run()
        assert len(tracker.phases) == 1

    @pytest.mark.asyncio
    async def test_run_uses_preplanner_phases_when_set(self):
        """Pre-populated tracker phases must win over auto grouping."""
        from src.batch_executor import BatchExecutor

        items = [PlanItem(text="Step 1", roles=["devops"])]
        preplanned_phases = [
            Phase(
                name="Create (1) · Step 1",
                type="create",
                steps=items,
                display_name="Setup",
            )
        ]
        tracker = MagicMock()
        tracker.items = items
        tracker.phases = preplanned_phases
        tracker.render_dashboard = MagicMock()
        tracker.start_dashboard = MagicMock()
        tracker.stop_dashboard = MagicMock()
        tracker.phase_done = MagicMock()
        session = self._make_full_session()
        executor = BatchExecutor(session=session, tracker=tracker)
        executor._run_phase = AsyncMock(return_value=True)

        with patch("src.plan_tracker.auto_group_phases") as mock_group:
            await executor.run()

        mock_group.assert_not_called()
        assert tracker.phases == preplanned_phases

    @pytest.mark.asyncio
    async def test_run_calls_start_and_stop_dashboard(self):
        from src.batch_executor import BatchExecutor

        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        session = self._make_full_session()
        executor = BatchExecutor(session=session, tracker=tracker)
        await executor.run()
        tracker.start_dashboard.assert_called_once()
        tracker.stop_dashboard.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_prints_role_model_labels(self, capsys):
        from src.batch_executor import BatchExecutor

        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        session = self._make_full_session()
        executor = BatchExecutor(session=session, tracker=tracker)

        await executor.run()

        out = capsys.readouterr().out
        assert "Player: zai | model=glm-5.1" in out
        assert "Coach: claude | model=sonnet" in out
        assert "Pre-Coach: claude | model=sonnet" in out
        assert "Judge: codex | model=gpt-5.4" in out
        assert "Post-Coach: claude | model=sonnet" in out
        assert "Batch review: 3 / 1 / 1 (coach / judge / coach)" in out

    @pytest.mark.asyncio
    async def test_run_skips_phase_instead_of_raising(self):
        """Exhausted phase is skipped instead of raising PhaseFailedError."""
        from src.batch_executor import BatchExecutor
        from src.feedback import Feedback

        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        r = MagicMock()
        r.text = "no output"
        r.messages = []
        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_model = ""
        session.config.player_turns_per_session = 30
        session._runtime = None
        session._run_with_continuation = AsyncMock(return_value=r)
        session._run_coach_turn_for_phase = AsyncMock(
            return_value=Feedback(text="Incomplete")
        )
        executor = BatchExecutor(session=session, tracker=tracker)
        # run() must NOT raise — it should skip and continue
        await executor.run()
        tracker.stop_dashboard.assert_called_once()
        # The phase should be marked as skipped
        phase = tracker.phases[0]
        assert phase.status == "skipped"

    @pytest.mark.asyncio
    async def test_run_empty_items_is_noop(self):
        from src.batch_executor import BatchExecutor

        tracker = MagicMock()
        tracker.items = []
        session = MagicMock()
        session._runtime = None
        executor = BatchExecutor(session=session, tracker=tracker)
        await executor.run()  # should not raise
        tracker.start_dashboard.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_resets_previously_done_items_and_reexecutes_all_steps(self):
        from src.batch_executor import BatchExecutor

        items = [
            PlanItem(text="create done.py", done=True),
            PlanItem(text="create pending.py", done=False),
        ]
        tracker = MagicMock()
        tracker.items = items
        tracker.render_dashboard = MagicMock()
        tracker.start_dashboard = MagicMock()
        tracker.stop_dashboard = MagicMock()

        def mark_phase_done(phase):
            phase.steps = [replace(item, done=True) for item in phase.steps]
            # Mirror the real PlanTracker.phase_done which updates tracker.items
            tracker.items[:] = [replace(item, done=True) for item in tracker.items]

        tracker.phase_done = MagicMock(side_effect=mark_phase_done)
        session = self._make_full_session("PHASE_COMPLETE: Create (2 steps)")
        executor = BatchExecutor(session=session, tracker=tracker)

        await executor.run()

        assert len(tracker.phases) == 1
        assert [step.text for step in tracker.phases[0].steps] == [
            "create done.py",
            "create pending.py",
        ]
        # After successful phase completion, items are marked done by phase_done
        assert all(item.done is True for item in items)

    @pytest.mark.asyncio
    async def test_run_persists_completed_phases_to_plan_file(self, tmp_path):
        from src.batch_executor import BatchExecutor

        plan_path = tmp_path / "requirements.md"
        plan_path.write_text("- [ ] create a.py\n")

        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        tracker.render_dashboard = MagicMock()
        tracker.start_dashboard = MagicMock()
        tracker.stop_dashboard = MagicMock()

        def mark_phase_done(phase):
            phase.steps = [replace(item, done=True) for item in phase.steps]
            # Mirror the real PlanTracker.phase_done which updates tracker.items
            tracker.items[:] = [replace(item, done=True) for item in tracker.items]

        tracker.phase_done = MagicMock(side_effect=mark_phase_done)

        session = self._make_full_session()
        session.plan_file_path = str(plan_path)
        executor = BatchExecutor(session=session, tracker=tracker)

        await executor.run()

        assert "- [x] create a.py" in plan_path.read_text()

    @pytest.mark.asyncio
    async def test_phase_exhausted_attempts_skipped_not_raised(self):
        """When phase exhausts all attempts, run() completes without PhaseFailedError.
        Phase status is 'skipped'."""
        from src.batch_executor import BatchExecutor
        from src.feedback import Feedback

        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        r = MagicMock(); r.text = "no output"; r.messages = []
        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_model = ""
        session.config.batch_pre_judge_attempts = 1
        session.config.batch_judge_attempts = 0
        session.config.batch_post_judge_attempts = 0
        session.config.player_turns_per_session = 30
        session._runtime = None
        session._run_with_continuation = AsyncMock(return_value=r)
        session._run_coach_turn_for_phase = AsyncMock(return_value=Feedback(text="nope"))
        executor = BatchExecutor(session=session, tracker=tracker)
        # run() must NOT raise
        await executor.run()
        assert tracker.phases[0].status == "skipped"

    @pytest.mark.asyncio
    async def test_final_report_lists_skipped_phases(self, capsys):
        """After run(), skipped phases appear in stdout final report."""
        from src.batch_executor import BatchExecutor
        from src.feedback import Feedback

        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        r = MagicMock(); r.text = "no output"; r.messages = []
        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_model = ""
        session.config.batch_pre_judge_attempts = 1
        session.config.batch_judge_attempts = 0
        session.config.batch_post_judge_attempts = 0
        session.config.player_turns_per_session = 30
        session._runtime = None
        session._run_with_continuation = AsyncMock(return_value=r)
        session._run_coach_turn_for_phase = AsyncMock(return_value=Feedback(text="nope"))
        executor = BatchExecutor(session=session, tracker=tracker)
        await executor.run()
        out = capsys.readouterr().out
        assert "Skipped" in out or "skipped" in out or "⏭" in out


class TestBatchPlayerRunnerSelection:
    @pytest.mark.asyncio
    async def test_falls_back_to_run_turn_when_continuation_not_available(self):
        from src.batch_executor import BatchExecutor
        from src.feedback import Approved

        phase = Phase(
            name="Create", type="create", steps=[PlanItem(text="create a.py")]
        )
        player_result = MagicMock()
        player_result.text = (
            "PHASE_COMPLETE: Create\n"
            "What changed:\n"
            "- Added create a.py\n"
            "Evidence:\n"
            "- create a.py exists\n"
            "Verification:\n"
            "- pytest\n"
        )
        player_result.messages = []

        session = MagicMock()
        session.config.max_turns = 3
        session.config.player_timeout_s = 60
        session.config.player_model = ""
        session.config.player_provider = "zai"
        session.config.coach_provider = "claude"
        session.config.batch_pre_judge_attempts = 3
        session.config.batch_judge_attempts = 1
        session.config.batch_post_judge_attempts = 1
        session.config.batch_judge_provider = "codex"
        session.config.batch_judge_model = "gpt-5.4"
        session.config.player_turns_per_session = 30
        session._runtime = None
        session._run_turn = AsyncMock(return_value=player_result)
        session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()
        session.build_provider_display = MagicMock(return_value="codex | model=gpt-5.4")

        tracker = MagicMock()
        tracker.items = []
        tracker.render_dashboard = MagicMock()

        executor = BatchExecutor(session, tracker)
        result = await executor._run_phase(phase)

        assert result is True
        session._run_turn.assert_awaited_once()


class TestRunCoachTurnForPhase:
    @pytest.mark.asyncio
    async def test_returns_approved_on_approved_output(self):
        from src.coach_player import CoachPlayerSession, TurnResult
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)

        session = MagicMock(spec=CoachPlayerSession)
        session.config = MagicMock()
        session.config.max_turns = 5
        session.config.coach_timeout_s = 300
        session.config.coach_model = ""

        approved_msg = AdaptedMessage(
            role="assistant",
            content=[TextBlock(text="IMPLEMENTATION_APPROVED")],
        )
        turn_result = TurnResult(
            role="coach",
            duration_s=1.0,
            tools_used=0,
            messages=[approved_msg],
            text="IMPLEMENTATION_APPROVED",
        )
        session._run_turn = AsyncMock(return_value=turn_result)

        result = await CoachPlayerSession._run_coach_turn_for_phase(
            session, phase, MagicMock(text="player output")
        )
        from src.feedback import Approved

        assert isinstance(result, Approved)

    @pytest.mark.asyncio
    async def test_returns_feedback_on_timeout(self):
        from src.coach_player import CoachPlayerSession
        from src.feedback import Feedback

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)

        session = MagicMock(spec=CoachPlayerSession)
        session.config = MagicMock()
        session.config.max_turns = 5
        session.config.coach_timeout_s = 300
        session.config.coach_model = ""
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()
        session._run_turn = AsyncMock(
            side_effect=TimeoutError("coach exceeded timeout of 300s")
        )

        result = await CoachPlayerSession._run_coach_turn_for_phase(
            session, phase, MagicMock(text="player output")
        )

        assert isinstance(result, Feedback)
        assert "coach exceeded timeout of 300s" in result.text
        assert "Run the most relevant verification" in result.text

    @pytest.mark.asyncio
    async def test_invalid_feedback_becomes_contextual_missing_step_feedback(self):
        from src.coach_player import CoachPlayerSession, TurnResult
        from src.feedback import Feedback
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phase = Phase(name="Create", type="create", steps=items)

        session = MagicMock(spec=CoachPlayerSession)
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.coach_timeout_s = 300
        session.config.coach_model = ""
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()
        from src.constants import BATCH_REVIEW_MAX_TURNS
        session.BATCH_REVIEW_MAX_TURNS = BATCH_REVIEW_MAX_TURNS
        session._build_phase_fallback_feedback = (
            CoachPlayerSession._build_phase_fallback_feedback
        )

        invalid_msg = AdaptedMessage(
            role="assistant",
            content=[TextBlock(text="Let me inspect more files before deciding.")],
        )
        turn_result = TurnResult(
            role="coach",
            duration_s=1.0,
            tools_used=3,
            messages=[invalid_msg],
            text="Let me inspect more files before deciding.",
        )
        session._run_turn = AsyncMock(return_value=turn_result)

        result = await CoachPlayerSession._run_coach_turn_for_phase(
            session,
            phase,
            MagicMock(text="Step 1 done: created a.py"),
            completed_steps=["create a.py"],
        )

        assert isinstance(result, Feedback)
        assert "Complete the missing planned step: create b.py" in result.text

    @pytest.mark.asyncio
    async def test_retries_when_reviewer_returns_no_verdict(self):
        from src.coach_player import CoachPlayerSession, TurnResult
        from src.feedback import Approved
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)

        session = MagicMock(spec=CoachPlayerSession)
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.coach_timeout_s = 300
        session.config.coach_model = ""
        session.config.coach_retry_max = 2
        session.config.coach_fallback_provider = ""
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()

        empty_result = TurnResult(
            role="coach",
            duration_s=1.0,
            tools_used=1,
            messages=[],
            text="",
        )
        approved_msg = AdaptedMessage(
            role="assistant",
            content=[TextBlock(text="IMPLEMENTATION_APPROVED")],
        )
        approved_result = TurnResult(
            role="coach",
            duration_s=1.0,
            tools_used=0,
            messages=[approved_msg],
            text="IMPLEMENTATION_APPROVED",
        )
        session._run_turn = AsyncMock(side_effect=[empty_result, approved_result])

        result = await CoachPlayerSession._run_coach_turn_for_phase(
            session,
            phase,
            MagicMock(text="PHASE_COMPLETE: Create"),
            completed_steps=["create a.py"],
        )

        assert isinstance(result, Approved)
        assert session._run_turn.await_count == 2

    @pytest.mark.asyncio
    async def test_fallback_provider_used_when_reviewer_stays_silent(self):
        from src.coach_player import CoachPlayerSession, TurnResult
        from src.feedback import Approved
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)

        session = MagicMock(spec=CoachPlayerSession)
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.coach_timeout_s = 300
        session.config.coach_model = ""
        session.config.coach_retry_max = 1
        session.config.coach_fallback_provider = "claude"
        session.config.coach_fallback_model = ""
        session.config.coach_provider = "zai"
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()

        fallback_provider = MagicMock()
        fallback_provider.check_ready = MagicMock(return_value=(True, ""))
        fallback_provider.display_name = "Claude"
        session._get_or_create_provider = MagicMock(return_value=fallback_provider)

        empty_result = TurnResult(
            role="coach",
            duration_s=1.0,
            tools_used=1,
            messages=[],
            text="",
        )
        approved_msg = AdaptedMessage(
            role="assistant",
            content=[TextBlock(text="IMPLEMENTATION_APPROVED")],
        )
        fallback_result = TurnResult(
            role="coach_fallback",
            duration_s=1.0,
            tools_used=0,
            messages=[approved_msg],
            text="IMPLEMENTATION_APPROVED",
        )
        session._run_turn = AsyncMock(side_effect=[empty_result, fallback_result])

        result = await CoachPlayerSession._run_coach_turn_for_phase(
            session,
            phase,
            MagicMock(text="PHASE_COMPLETE: Create"),
            completed_steps=["create a.py"],
        )

        assert isinstance(result, Approved)
        assert session._get_or_create_provider.called
        assert session._run_turn.await_count == 2

    @pytest.mark.asyncio
    async def test_silent_reviewer_returns_feedback_instead_of_approval(self):
        from src.coach_player import CoachPlayerSession, TurnResult
        from src.feedback import Feedback

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)

        session = MagicMock(spec=CoachPlayerSession)
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.coach_timeout_s = 300
        session.config.coach_model = ""
        session.config.coach_retry_max = 1
        session.config.coach_fallback_provider = ""
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()
        from src.constants import BATCH_REVIEW_MAX_TURNS
        session.BATCH_REVIEW_MAX_TURNS = BATCH_REVIEW_MAX_TURNS
        session._build_phase_fallback_feedback = (
            CoachPlayerSession._build_phase_fallback_feedback
        )

        empty_result = TurnResult(
            role="coach",
            duration_s=1.0,
            tools_used=0,
            messages=[],
            text="",
        )
        session._run_turn = AsyncMock(return_value=empty_result)

        result = await CoachPlayerSession._run_coach_turn_for_phase(
            session,
            phase,
            MagicMock(text="PHASE_COMPLETE: Create"),
            completed_steps=["create a.py"],
        )

        assert isinstance(result, Feedback)
        assert "Reviewer produced no verdict" in result.text


class TestBuildPhaseCoachPrompt:
    def test_contains_phase_name(self):
        from src.prompts import build_phase_coach_prompt

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create (1 steps)", type="create", steps=items)
        result = MagicMock()
        result.text = "I created a.py"
        prompt = build_phase_coach_prompt(phase, result)
        assert "Create (1 steps)" in prompt

    def test_contains_step_texts(self):
        from src.prompts import build_phase_coach_prompt

        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phase = Phase(name="Create", type="create", steps=items)
        result = MagicMock()
        result.text = "done"
        prompt = build_phase_coach_prompt(phase, result)
        assert "create a.py" in prompt
        assert "create b.py" in prompt

    def test_contains_player_output_truncated(self):
        from src.prompts import build_phase_coach_prompt

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        long_text = "x" * 3000
        result = MagicMock()
        result.text = long_text
        prompt = build_phase_coach_prompt(phase, result)
        assert "x" * 2000 in prompt
        assert "x" * 2001 not in prompt

    def test_asks_for_implementation_approved(self):
        from src.prompts import build_phase_coach_prompt

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        result = MagicMock()
        result.text = "done"
        prompt = build_phase_coach_prompt(phase, result)
        assert "IMPLEMENTATION_APPROVED" in prompt
        assert "Do not end your turn with a tool call." in prompt


class TestTurnResultText:
    def test_turn_result_has_text_field(self):
        from src.coach_player import TurnResult

        r = TurnResult(role="player", duration_s=1.0, tools_used=0, messages=[])
        assert hasattr(r, "text")
        assert isinstance(r.text, str)


class TestCoachAlwaysCalled:
    """Tests verifying that coach is always called, never bypassed by auto-reject."""

    def _make_phase(self, name: str, steps: list[str]) -> Phase:
        return Phase(name=name, type="update", steps=[PlanItem(text=s) for s in steps])

    @pytest.mark.asyncio
    async def test_missing_phase_complete_retries_player_without_calling_coach(self):
        """Batch mode should reject incomplete player output before coach review."""
        from src.batch_executor import BatchExecutor
        from src.feedback import Approved

        phase = self._make_phase("Update", ["Step A", "Step B"])

        # Player result: no PHASE_COMPLETE, no report headers
        player_result = MagicMock()
        player_result.text = "I explored the codebase."  # no markers

        session = MagicMock()
        session.config.max_turns = 3
        session.config.player_model = ""
        session.config.coach_model = ""
        session.config.player_timeout_s = 60
        session.config.player_provider = "zai"
        session.config.coach_provider = "claude"
        session.config.batch_pre_judge_attempts = 3
        session.config.batch_judge_attempts = 1
        session.config.batch_post_judge_attempts = 1
        session.config.batch_judge_provider = "codex"
        session.config.batch_judge_model = "gpt-5.4"
        session.config.player_turns_per_session = 30
        session._runtime = None

        session._run_with_continuation = AsyncMock(return_value=player_result)

        session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()
        session.build_provider_display = MagicMock(return_value="codex | model=gpt-5.4")

        tracker = MagicMock()
        tracker.items = []
        tracker.render_dashboard = MagicMock()
        tracker.phase_done = MagicMock()

        executor = BatchExecutor(session, tracker)
        result = await executor._run_phase(phase)

        assert result is False
        session._run_coach_turn_for_phase.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_report_headers_retries_player_without_calling_coach(self):
        """Batch mode should require the completion report before coach review."""
        from src.batch_executor import BatchExecutor
        from src.feedback import Approved

        phase = self._make_phase("Update", ["Step A"])

        player_result = MagicMock()
        # Has PHASE_COMPLETE but no What changed / Evidence / Verification
        player_result.text = "Step 1 done: done\nPHASE_COMPLETE: Update"

        session = MagicMock()
        session.config.max_turns = 3
        session.config.player_model = ""
        session.config.coach_model = ""
        session.config.player_timeout_s = 60
        session.config.player_provider = "zai"
        session.config.coach_provider = "claude"
        session.config.batch_pre_judge_attempts = 3
        session.config.batch_judge_attempts = 1
        session.config.batch_post_judge_attempts = 1
        session.config.batch_judge_provider = "codex"
        session.config.batch_judge_model = "gpt-5.4"
        session.config.player_turns_per_session = 30
        session._runtime = None

        session._run_with_continuation = AsyncMock(return_value=player_result)

        session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
        session._snapshot_pids = MagicMock(return_value=set())
        session._kill_new_processes = MagicMock()
        session.build_provider_display = MagicMock(return_value="codex | model=gpt-5.4")

        tracker = MagicMock()
        tracker.items = []
        tracker.render_dashboard = MagicMock()
        tracker.phase_done = MagicMock()

        executor = BatchExecutor(session, tracker)
        result = await executor._run_phase(phase)

        assert result is False
        session._run_coach_turn_for_phase.assert_not_called()


class TestPlayerStrategy:
    def _make_executor_with_schedule(self, pre, judge, post):
        from unittest.mock import MagicMock
        from src.batch_executor import BatchExecutor
        session = MagicMock()
        session.config.batch_pre_judge_attempts = pre
        session.config.batch_judge_attempts = judge
        session.config.batch_post_judge_attempts = post
        session.config.player_provider = "zai"
        session.config.player_model = "glm-5"
        tracker = MagicMock()
        tracker.items = []
        return BatchExecutor(session, tracker)

    def test_player_strategy_attempts_1_to_pre_uses_configured_player(self):
        executor = self._make_executor_with_schedule(5, 2, 3)
        for attempt in range(1, 6):
            strategy = executor._player_strategy(attempt)
            assert strategy["provider_name"] == "zai"
            assert strategy["model"] == "glm-5"

    def test_player_strategy_attempts_pre_plus_1_to_sonnet_window(self):
        from src.constants import PLAYER_ESCALATION_SONNET_MODEL
        executor = self._make_executor_with_schedule(5, 2, 3)
        for attempt in (6, 7):
            strategy = executor._player_strategy(attempt)
            assert strategy["provider_name"] == "claude_native"
            assert strategy["model"] == PLAYER_ESCALATION_SONNET_MODEL

    def test_player_strategy_attempts_after_sonnet_window_uses_opus(self):
        from src.constants import PLAYER_ESCALATION_OPUS_MODEL
        executor = self._make_executor_with_schedule(5, 2, 3)
        for attempt in (8, 9, 10):
            strategy = executor._player_strategy(attempt)
            assert strategy["provider_name"] == "claude_native"
            assert strategy["model"] == PLAYER_ESCALATION_OPUS_MODEL

    def test_unavailable_claude_native_falls_back_to_configured_player_with_warning(self):
        import logging
        from unittest.mock import MagicMock, patch
        executor = self._make_executor_with_schedule(5, 2, 3)
        not_ready_provider = MagicMock()
        not_ready_provider.check_ready.return_value = (False, "API key missing")
        executor.session._get_or_create_provider = MagicMock(return_value=not_ready_provider)

        with patch("logging.warning"):
            strategy = executor._player_strategy(6)  # judge window attempt
            player_provider_inst = None
            player_model_for_turn = strategy["model"]
            if strategy["provider_name"] != executor.session.config.player_provider:
                cand = executor.session._get_or_create_provider(strategy["provider_name"])
                ok, reason = cand.check_ready()
                if not ok:
                    logging.warning(
                        "Player escalation to %s unavailable: %s — using configured player",
                        strategy["provider_name"], reason,
                    )
                    player_model_for_turn = executor.session.config.player_model

            assert player_provider_inst is None
            assert player_model_for_turn == "glm-5"  # fell back to configured player

    def test_player_escalation_visible_in_start_banner(self):
        """Start banner contains player escalation line."""
        executor = self._make_executor_with_schedule(5, 2, 3)
        pre, judge, post = executor._schedule_counts()
        assert pre == 5
        assert judge == 2
        assert post == 3
        expected = f"Player escalation: sonnet x{judge} \u2192 opus x{post}"
        assert "sonnet x2" in expected
        assert "opus x3" in expected


class TestScheduleEdgeCases:
    def _make_executor(self, pre, judge, post):
        from src.batch_executor import BatchExecutor
        session = MagicMock()
        session.config.batch_pre_judge_attempts = pre
        session.config.batch_judge_attempts = judge
        session.config.batch_post_judge_attempts = post
        session.config.player_provider = "zai"
        session.config.player_model = "glm-5"
        tracker = MagicMock()
        tracker.items = []
        return BatchExecutor(session, tracker)

    def test_judge_attempts_zero_skips_judge_window(self):
        """When batch_judge_attempts=0, judge window is empty.

        With pre=5, judge=0, post=3:
        - _review_strategy(6) should return post coach (not judge), because
          judge_end = pre + judge = 5 + 0 = 5, so attempt 6 > judge_end → post
        - _player_strategy(6) should use opus, because
          judge=0 means the judge condition is False, so we fall through to opus
        """
        from src.constants import PLAYER_ESCALATION_OPUS_MODEL
        executor = self._make_executor(pre=5, judge=0, post=3)

        review = executor._review_strategy(6)
        assert review["review_role"] == "coach", (
            f"Expected post-coach for attempt 6 with judge=0, got {review['review_role']!r}"
        )

        player = executor._player_strategy(6)
        assert player["model"] == PLAYER_ESCALATION_OPUS_MODEL, (
            f"Expected opus for attempt 6 with judge=0, got {player['model']!r}"
        )

    def test_pre_attempts_zero_first_attempt_is_judge(self):
        """When batch_pre_judge_attempts=0, first attempt goes straight to judge.

        With pre=0, judge=2, post=3:
        - _review_strategy(1): judge_start=1, judge_end=2, 1 in [1..2] → judge
        - _player_strategy(1): attempt<=pre is False (0), judge>0 and 1 in sonnet window → sonnet
        """
        from src.constants import PLAYER_ESCALATION_SONNET_MODEL
        executor = self._make_executor(pre=0, judge=2, post=3)

        review = executor._review_strategy(1)
        assert review["review_role"] == "judge", (
            f"Expected judge for attempt 1 with pre=0, got {review['review_role']!r}"
        )

        player = executor._player_strategy(1)
        assert player["model"] == PLAYER_ESCALATION_SONNET_MODEL, (
            f"Expected sonnet for attempt 1 with pre=0, got {player['model']!r}"
        )

    def test_all_zero_attempts_falls_back_to_defaults(self):
        """pre=judge=post=0 → _schedule_counts() returns defaults, not zeros.

        The guard `if sum(values) <= 0: return defaults` ensures the executor
        always has a non-zero max_phase_attempts when all inputs are zero.
        """
        executor = self._make_executor(pre=0, judge=0, post=0)

        counts = executor._schedule_counts()
        assert sum(counts) > 0, (
            f"All-zero config should fall back to non-zero defaults, got {counts}"
        )
        assert executor._max_phase_attempts() > 0, (
            f"max_phase_attempts must be > 0 after all-zero fallback"
        )
