"""Focused tests for manual plan-progress reset controls."""

from unittest.mock import MagicMock

from src.batch_executor import BatchExecutor
from src.coach_player import CoachPlayerSession
from src.plan_tracker import Phase, PlanItem
from src.runtime_controls import RuntimeControls, _char_to_action


def test_char_to_action_maps_r_to_reset_progress():
    assert _char_to_action("r") == "reset_progress"
    assert _char_to_action("R") == "reset_progress"


def test_runtime_controls_apply_pending_sets_reset_requested():
    controls = RuntimeControls.__new__(RuntimeControls)
    controls._listener = MagicMock()
    controls._listener.pop_action.side_effect = ["reset_progress", None]
    controls._status_bar = MagicMock()
    controls._picker = MagicMock()
    controls._picker.handle_action.return_value = "reset_progress"
    controls._picker.pop_pending_change.return_value = None
    controls._compact_requested = False
    controls._reset_requested = False
    controls._current_coach_idx = 0
    controls._current_player_idx = 0
    controls._player_name = ""
    controls._coach_name = ""
    controls._ctx_pct = 0

    controls.apply_pending(MagicMock())

    assert controls.reset_requested is True
    controls.clear_reset()
    assert controls.reset_requested is False


def test_coach_player_reset_plan_progress_clears_items_and_plan_file(tmp_path):
    plan_path = tmp_path / "requirements.md"
    plan_path.write_text("- [x] first\n- [ ] second\n")
    session = CoachPlayerSession.__new__(CoachPlayerSession)
    session.plan_file_path = str(plan_path)
    session._last_turn_result = object()

    reset_items = session._reset_plan_progress(
        [PlanItem(text="first", done=True), PlanItem(text="second", done=False)]
    )

    assert [item.done for item in reset_items] == [False, False]
    assert session._last_turn_result is None
    assert plan_path.read_text() == "- [ ] first\n- [ ] second\n"


def test_batch_executor_reset_plan_progress_clears_items_phases_and_plan_file(tmp_path):
    plan_path = tmp_path / "requirements.md"
    plan_path.write_text("- [x] first\n- [x] second\n")
    items = [PlanItem(text="first", done=True), PlanItem(text="second", done=True)]
    phase = Phase(name="Create", type="create", steps=items, status="done", attempts=3)
    tracker = MagicMock()
    tracker.items = items
    session = MagicMock()
    session.plan_file_path = str(plan_path)

    executor = BatchExecutor(session=session, tracker=tracker)
    executor._reset_plan_progress([phase])

    assert [item.done for item in items] == [False, False]
    assert phase.status == "pending"
    assert phase.attempts == 0
    assert plan_path.read_text() == "- [ ] first\n- [ ] second\n"
