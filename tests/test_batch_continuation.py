"""Tests for batch_executor using _run_with_continuation."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.config import Config
from src.feedback import Approved


@pytest.mark.asyncio
async def test_batch_player_uses_run_with_continuation(monkeypatch):
    """batch_executor._run_phase() must call session._run_with_continuation for player."""
    from src.batch_executor import BatchExecutor
    from src.plan_tracker import Phase, PlanItem
    from src.coach_player import TurnResult

    session = MagicMock()
    session.config = Config()
    session._interrupted = False

    # _run_with_continuation returns a result with PHASE_COMPLETE markers
    complete_text = (
        "PHASE_COMPLETE: Test\nWhat changed:\n- x\nEvidence:\n- y\nVerification:\n- z"
    )
    session._run_with_continuation = AsyncMock(
        return_value=TurnResult("player", 1.0, 0, [], complete_text)
    )
    # _run_coach_turn_for_phase returns Approved
    session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
    session._snapshot_pids = MagicMock(return_value=set())
    session._kill_new_processes = MagicMock()

    tracker = MagicMock()
    tracker.items = [PlanItem(text="step one")]

    executor = BatchExecutor(session, tracker)
    phase = Phase(
        name="Test", type="batch", steps=[PlanItem(text="step one")], status="pending"
    )

    await executor._run_phase(phase)

    session._run_with_continuation.assert_called_once()
