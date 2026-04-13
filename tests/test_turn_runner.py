"""Tests for src/turn_runner.py — AgentTurnRunner unit tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.turn_runner import AgentTurnRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    working_dir: str = "/tmp",
    max_continuation_attempts: int = 0,
    compact_threshold: float = 0.8,
    context_limit: int = 100_000,
) -> MagicMock:
    cfg = MagicMock()
    cfg.working_dir = working_dir
    cfg.max_continuation_attempts = max_continuation_attempts
    cfg.compact_threshold = compact_threshold
    cfg.context_limit = context_limit
    return cfg


def _make_provider(*, messages=None, input_tokens: int = 0, output_tokens: int = 0):
    """Return an async-iterable mock provider."""
    from src.providers.message_adapter import AdaptedMessage, TextBlock

    if messages is None:
        messages = [AdaptedMessage(role="assistant", content=[TextBlock(text="done")])]

    async def _run(**kwargs):
        for msg in messages:
            yield msg

    provider = MagicMock()
    provider.run = _run
    provider._last_input_tokens = input_tokens
    provider._last_output_tokens = output_tokens
    provider.display_name = "mock"
    return provider


def _make_router(provider=None):
    router = MagicMock()
    router.provider_for.return_value = provider or _make_provider()
    return router


# ---------------------------------------------------------------------------
# run_turn — happy path
# ---------------------------------------------------------------------------


class TestRunTurn:
    @pytest.mark.asyncio
    async def test_returns_turn_result_with_text(self):
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        msg = AdaptedMessage(role="assistant", content=[TextBlock(text="hello world")])
        provider = _make_provider(messages=[msg])
        runner = AgentTurnRunner(verbose=False)

        with (
            patch("src.turn_runner.streaming_ui") as mock_ui,
            patch("src.config.get_effective_context_limit", return_value=50_000),
            patch("src.config.get_context_window", return_value=50_000),
        ):
            mock_ui.stream_messages.return_value = 0
            mock_ui.print_turn_timing.return_value = None

            result = await runner.run_turn(
                role="player",
                prompt="do something",
                system_prompt="be helpful",
                max_turns=5,
                timeout_s=60,
                provider=provider,
                router=_make_router(provider),
                config=_make_config(),
            )

        assert result.role == "player"
        assert result.text == "hello world"
        assert result.duration_s >= 0

    @pytest.mark.asyncio
    async def test_stores_effective_context_limit(self):
        runner = AgentTurnRunner()

        with (
            patch("src.turn_runner.streaming_ui") as mock_ui,
            patch("src.config.get_effective_context_limit", return_value=42_000),
            patch("src.config.get_context_window", return_value=42_000),
        ):
            mock_ui.stream_messages.return_value = 0
            mock_ui.print_turn_timing.return_value = None

            await runner.run_turn(
                role="coach",
                prompt="review",
                system_prompt="",
                max_turns=1,
                timeout_s=30,
                provider=_make_provider(),
                router=_make_router(),
                config=_make_config(),
            )

        assert runner._last_effective_context_limit == 42_000

    @pytest.mark.asyncio
    async def test_timeout_raises_timeout_error(self):
        async def slow_run(**kwargs):
            await asyncio.sleep(10)
            return
            yield  # make it an async generator

        provider = MagicMock()
        provider.run = slow_run
        provider._last_input_tokens = 0
        provider._last_output_tokens = 0
        runner = AgentTurnRunner()

        with (
            patch("src.turn_runner.streaming_ui"),
            patch("src.config.get_effective_context_limit", return_value=50_000),
            patch("src.config.get_context_window", return_value=50_000),
        ):
            with pytest.raises(TimeoutError, match="player"):
                await runner.run_turn(
                    role="player",
                    prompt="slow",
                    system_prompt="",
                    max_turns=1,
                    timeout_s=0,  # immediate timeout
                    provider=provider,
                    router=_make_router(provider),
                    config=_make_config(),
                )

    @pytest.mark.asyncio
    async def test_provider_override_used_instead_of_router(self):
        """When provider_override is supplied, the router is not consulted."""
        override = _make_provider(messages=[])
        router = _make_router()

        runner = AgentTurnRunner()
        with (
            patch("src.turn_runner.streaming_ui") as mock_ui,
            patch("src.config.get_effective_context_limit", return_value=50_000),
            patch("src.config.get_context_window", return_value=50_000),
        ):
            mock_ui.stream_messages.return_value = 0
            mock_ui.print_turn_timing.return_value = None

            await runner.run_turn(
                role="player",
                prompt="test",
                system_prompt="",
                max_turns=1,
                timeout_s=10,
                provider=override,
                router=router,
                config=_make_config(),
                provider_override=override,
            )

        router.provider_for.assert_not_called()


# ---------------------------------------------------------------------------
# _player_output_complete
# ---------------------------------------------------------------------------


class TestPlayerOutputComplete:
    def test_complete_with_all_headers(self):
        text = "what changed: x\nevidence: y\nverification: z"
        assert AgentTurnRunner._player_output_complete(text, "some prompt")

    def test_missing_header_not_complete(self):
        text = "what changed: x\nevidence: y"  # missing verification
        assert not AgentTurnRunner._player_output_complete(text, "some prompt")

    def test_phase_complete_required_when_in_prompt(self):
        prompt = "finish and add PHASE_COMPLETE marker"
        text = "what changed: x\nevidence: y\nverification: z"
        # missing PHASE_COMPLETE in text
        assert not AgentTurnRunner._player_output_complete(text, prompt)

    def test_phase_complete_satisfied(self):
        prompt = "finish with PHASE_COMPLETE"
        text = "what changed: x\nevidence: y\nverification: z\nPHASE_COMPLETE: done"
        assert AgentTurnRunner._player_output_complete(text, prompt)

    def test_empty_text_not_complete(self):
        assert not AgentTurnRunner._player_output_complete("", "prompt")


# ---------------------------------------------------------------------------
# run_with_continuation — no continuation when max_attempts=0
# ---------------------------------------------------------------------------


class TestRunWithContinuation:
    @pytest.mark.asyncio
    async def test_no_continuation_when_zero_attempts(self):
        """With max_continuation_attempts=0, returns the first result unchanged."""
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        msg = AdaptedMessage(role="assistant", content=[TextBlock(text="what changed: x\nevidence: y\nverification: z")])
        provider = _make_provider(messages=[msg])
        runner = AgentTurnRunner()

        with (
            patch("src.turn_runner.streaming_ui") as mock_ui,
            patch("src.config.get_effective_context_limit", return_value=50_000),
            patch("src.config.get_context_window", return_value=50_000),
        ):
            mock_ui.stream_messages.return_value = 0
            mock_ui.print_turn_timing.return_value = None

            result = await runner.run_with_continuation(
                role="player",
                prompt="build x",
                system_prompt="",
                max_turns=1,
                timeout_s=30,
                provider=provider,
                router=_make_router(provider),
                config=_make_config(max_continuation_attempts=0),
            )

        assert "what changed" in result.text.lower()

    @pytest.mark.asyncio
    async def test_non_player_role_skips_continuation(self):
        """Only 'player' role gets continuation logic."""
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        msg = AdaptedMessage(role="assistant", content=[TextBlock(text="approved")])
        provider = _make_provider(messages=[msg])
        runner = AgentTurnRunner()

        with (
            patch("src.turn_runner.streaming_ui") as mock_ui,
            patch("src.config.get_effective_context_limit", return_value=50_000),
            patch("src.config.get_context_window", return_value=50_000),
        ):
            mock_ui.stream_messages.return_value = 0
            mock_ui.print_turn_timing.return_value = None

            result = await runner.run_with_continuation(
                role="coach",
                prompt="review",
                system_prompt="",
                max_turns=1,
                timeout_s=30,
                provider=provider,
                router=_make_router(provider),
                config=_make_config(max_continuation_attempts=3),
            )

        assert result.role == "coach"
