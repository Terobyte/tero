"""Tests for _run_with_continuation in CoachPlayerSession."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.config import Config
from src.coach_player import TurnResult


def make_session(max_continuation: int = 2):
    """Create a minimal CoachPlayerSession for testing."""
    from src.coach_player import CoachPlayerSession
    cfg = Config(max_continuation_attempts=max_continuation)
    session = object.__new__(CoachPlayerSession)
    session.config = cfg
    session._interrupted = False
    return session


@pytest.mark.asyncio
async def test_continuation_returns_immediately_when_markers_present():
    """When completion markers are present, no continuation needed."""
    session = make_session()
    good_result = TurnResult(
        role="player",
        duration_s=1.0,
        tools_used=0,
        messages=[],
        text="PHASE_COMPLETE: Update\nWhat changed:\n- x\nEvidence:\n- y\nVerification:\n- z",
    )

    async def fake_run_turn(*args, **kwargs):
        return good_result

    session._run_turn = fake_run_turn

    result = await session._run_with_continuation(
        role="player",
        prompt="do stuff",
        system_prompt="",
        max_turns=10,
        timeout_s=60,
    )
    assert result.text == good_result.text


@pytest.mark.asyncio
async def test_continuation_retries_when_no_markers():
    """When no completion markers, continuation agent retries."""
    session = make_session(max_continuation=2)
    call_count = 0

    async def fake_run_turn(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return TurnResult(
                role="player",
                duration_s=1.0,
                tools_used=0,
                messages=[],
                text="I explored the code but forgot to write PHASE_COMPLETE",
            )
        return TurnResult(
            role="player",
            duration_s=1.0,
            tools_used=0,
            messages=[],
            text="PHASE_COMPLETE: Update\nWhat changed:\n- added\nEvidence:\n- file\nVerification:\n- ok",
        )

    session._run_turn = fake_run_turn

    result = await session._run_with_continuation(
        role="player",
        prompt="do stuff",
        system_prompt="",
        max_turns=10,
        timeout_s=60,
    )
    assert call_count == 2
    assert "PHASE_COMPLETE" in result.text


@pytest.mark.asyncio
async def test_continuation_exhausted_returns_last_result():
    """When continuation attempts exhausted, return last result."""
    session = make_session(max_continuation=2)

    async def fake_run_turn(*args, **kwargs):
        return TurnResult(
            role="player",
            duration_s=1.0,
            tools_used=0,
            messages=[],
            text="no markers here ever",
        )

    session._run_turn = fake_run_turn

    result = await session._run_with_continuation(
        role="player",
        prompt="do stuff",
        system_prompt="",
        max_turns=10,
        timeout_s=60,
    )
    # 1 original + 2 continuation = 3 total calls
    assert result.text == "no markers here ever"


@pytest.mark.asyncio
async def test_continuation_uses_codex_compaction_when_token_threshold_hit(monkeypatch):
    """High Codex prompt token usage should trigger compact-summary retry."""
    session = make_session(max_continuation=1)
    session.config.context_limit = 100_000
    session.config.compact_threshold = 0.85
    session.config.working_dir = "/tmp/project"
    session.config.player_model = "gpt-5.4-high"

    provider = SimpleNamespace(_last_input_tokens=90_000)
    session._provider_for_role = MagicMock(return_value=provider)

    calls = []

    async def fake_run_turn(*args, **kwargs):
        calls.append(kwargs["prompt"])
        if len(calls) == 1:
            return TurnResult(
                role="player",
                duration_s=1.0,
                tools_used=0,
                messages=[],
                text="explored only, no completion markers",
            )
        return TurnResult(
            role="player",
            duration_s=1.0,
            tools_used=0,
            messages=[],
            text="PHASE_COMPLETE: Update\nWhat changed:\n- added\nEvidence:\n- file\nVerification:\n- ok",
        )

    session._run_turn = fake_run_turn

    compact_mock = AsyncMock(return_value="very compact summary")
    print_compact_mock = MagicMock()
    monkeypatch.setattr("src.context_manager._compact_codex_context", compact_mock)
    monkeypatch.setattr("src.streaming.print_compact_triggered", print_compact_mock)

    result = await session._run_with_continuation(
        role="player",
        prompt="do stuff",
        system_prompt="",
        max_turns=10,
        timeout_s=60,
    )

    assert "PHASE_COMPLETE" in result.text
    compact_mock.assert_awaited_once()
    print_compact_mock.assert_called_once_with(90_000, 100_000)
    assert "very compact summary" in calls[1]
