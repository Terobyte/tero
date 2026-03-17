"""Tests for coach_player session behavior."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

from src.coach_player import CoachPlayerSession
from src.config import Config
from src.prompts import COACH_STRICT_SYSTEM_PROMPT, PLAYER_SYSTEM_PROMPT


@dataclass
class MockTextBlock:
    text: str


@dataclass
class MockAssistantMessage:
    content: list
    role: str = "assistant"


@dataclass
class MockResultMessage:
    result: str = "done"


def _make_mock_provider():
    """Create a mock provider for testing."""
    provider = MagicMock()
    provider.check_ready = MagicMock(return_value=(True, ""))
    provider.display_name = "Mock Provider"
    return provider


def test_session_passes_role_specific_system_prompts(tmp_path, monkeypatch):
    """Player and coach should receive different system prompts."""
    captured_prompts = []

    async def fake_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        captured_prompts.append(system_prompt)
        if system_prompt == COACH_STRICT_SYSTEM_PROMPT:
            yield MockAssistantMessage([MockTextBlock("IMPLEMENTATION_APPROVED")])
        else:
            yield MockAssistantMessage([MockTextBlock("Implemented")])
        yield MockResultMessage()

    mock_player = _make_mock_provider()
    mock_player.run = fake_run
    mock_coach = _make_mock_provider()
    mock_coach.run = fake_run

    monkeypatch.setattr("src.coach_player.create_provider", lambda name, env=None, cfg=None: mock_player if name == "player_provider" or name == "ccg" else mock_coach)
    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)

    cfg = Config(working_dir=str(tmp_path), plan_file="requirements.md", max_turns=1)
    session = CoachPlayerSession(cfg, "1. Ship feature")
    session.player_provider = mock_player
    session.coach_provider = mock_coach

    result = asyncio.run(session.run())

    assert result.approved is True
    assert PLAYER_SYSTEM_PROMPT in captured_prompts
    assert COACH_STRICT_SYSTEM_PROMPT in captured_prompts


def test_session_marks_provider_errors_as_failed(tmp_path, monkeypatch):
    """Provider/runtime errors should fail the session, not look like review feedback."""

    async def failing_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover

    mock_provider = _make_mock_provider()
    mock_provider.run = failing_run

    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)

    cfg = Config(working_dir=str(tmp_path), plan_file="requirements.md", max_turns=2)
    session = CoachPlayerSession(cfg, "1. Ship feature")
    session.player_provider = mock_provider
    session.coach_provider = mock_provider

    result = asyncio.run(session.run())

    assert result.approved is False
    assert result.status == "failed"
    assert "provider exploded" in (result.error or "")


def test_player_timeout_is_enforced(tmp_path, monkeypatch):
    """Player timeout should fall back to the next turn instead of failing the session."""
    player_calls = 0
    coach_calls = 0
    player_prompts = []

    async def slow_player_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        nonlocal player_calls
        player_calls += 1
        player_prompts.append(prompt)
        if player_calls == 1:
            await asyncio.sleep(0.05)
            yield MockAssistantMessage([MockTextBlock("Too late")])
            return

        yield MockAssistantMessage([MockTextBlock("Implemented on retry")])
        yield MockResultMessage()

    async def coach_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        nonlocal coach_calls
        coach_calls += 1
        yield MockAssistantMessage([MockTextBlock("IMPLEMENTATION_APPROVED")])
        yield MockResultMessage()

    mock_player = _make_mock_provider()
    mock_player.run = slow_player_run
    mock_coach = _make_mock_provider()
    mock_coach.run = coach_run

    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)

    cfg = Config(
        working_dir=str(tmp_path),
        plan_file="requirements.md",
        max_turns=2,
        player_timeout_s=0.01,
    )
    session = CoachPlayerSession(cfg, "1. Ship feature")
    session.player_provider = mock_player
    session.coach_provider = mock_coach

    result = asyncio.run(session.run())

    assert result.approved is True
    assert result.status == "approved"
    assert result.error is None
    assert player_calls == 2
    assert coach_calls == 1
    assert result.turns_used == 2


def test_coach_timeout_continues_to_next_turn(tmp_path, monkeypatch):
    """Coach timeout should feed actionable fallback text into the next player turn."""
    player_prompts = []
    coach_calls = 0

    async def player_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        player_prompts.append(prompt)
        yield MockAssistantMessage([MockTextBlock("Implemented")])
        yield MockResultMessage()

    async def slow_coach_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        nonlocal coach_calls
        coach_calls += 1
        if coach_calls == 1:
            await asyncio.sleep(0.05)
            yield MockAssistantMessage([MockTextBlock("Too late coach")])
            return

        yield MockAssistantMessage([MockTextBlock("IMPLEMENTATION_APPROVED")])
        yield MockResultMessage()

    mock_player = _make_mock_provider()
    mock_player.run = player_run
    mock_coach = _make_mock_provider()
    mock_coach.run = slow_coach_run

    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)

    cfg = Config(
        working_dir=str(tmp_path),
        plan_file="requirements.md",
        max_turns=2,
        coach_timeout_s=0.01,
    )
    session = CoachPlayerSession(cfg, "1. Ship feature")
    session.player_provider = mock_player
    session.coach_provider = mock_coach

    result = asyncio.run(session.run())

    assert result.approved is True
    assert result.status == "approved"
    assert result.error is None
    assert coach_calls == 2
    assert result.turns_used == 2


def test_interrupt_stops_collecting_mid_turn(tmp_path, monkeypatch):
    """Interrupt flag should stop consuming additional agent messages right away."""
    streamed_messages = 0

    async def verbose_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        yield MockAssistantMessage([MockTextBlock("first")])
        yield MockAssistantMessage([MockTextBlock("second")])
        yield MockResultMessage()

    mock_provider = _make_mock_provider()
    mock_provider.run = verbose_run

    cfg = Config(working_dir=str(tmp_path), plan_file="requirements.md", max_turns=1)
    session = CoachPlayerSession(cfg, "1. Ship feature")
    session.player_provider = mock_provider
    session.coach_provider = mock_provider

    def fake_stream_messages(msg, verbose=False, role=""):
        nonlocal streamed_messages
        streamed_messages += 1
        session._interrupted = True
        return 0

    monkeypatch.setattr("src.streaming.stream_messages", fake_stream_messages)

    result = asyncio.run(session.run())

    assert result.status == "interrupted"
    assert streamed_messages == 1
