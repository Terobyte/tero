"""Tests for coach_player session behavior."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from src.coach_player import CoachPlayerSession
from src.config import Config
from src.feedback import ReviewIssues
from src.menu import _sync_batch_roles_with_coach
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


def _player_report(summary: str) -> str:
    """Build a valid player completion report for step-mode tests."""
    return (
        f"{summary}\n"
        "What changed:\n"
        "- Updated the implementation.\n"
        "Evidence:\n"
        "- Checked the relevant files for the required behavior.\n"
        "Verification:\n"
        "- pytest\n"
    )


def test_session_passes_role_specific_system_prompts(tmp_path, monkeypatch):
    """Player and coach should receive different system prompts."""
    captured_prompts = []

    async def fake_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        captured_prompts.append(system_prompt)
        if system_prompt == COACH_STRICT_SYSTEM_PROMPT:
            yield MockAssistantMessage([MockTextBlock("IMPLEMENTATION_APPROVED")])
        else:
            yield MockAssistantMessage([MockTextBlock(_player_report("Implemented"))])
        yield MockResultMessage()

    mock_player = _make_mock_provider()
    mock_player.run = fake_run
    mock_coach = _make_mock_provider()
    mock_coach.run = fake_run

    monkeypatch.setattr("src.coach_player.create_provider", lambda name, env=None, cfg=None: mock_player if name == "player_provider" or name == "zai" else mock_coach)
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

        yield MockAssistantMessage([MockTextBlock(_player_report("Implemented on retry"))])
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
        yield MockAssistantMessage([MockTextBlock(_player_report("Implemented"))])
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


def test_run_turn_passes_context_config_to_context_aware_provider(tmp_path, monkeypatch):
    """Providers that support context settings should receive resolved config values."""
    captured = {}

    class FakeProvider:
        def check_ready(self):
            return True, ""

        async def run(
            self,
            prompt,
            system_prompt,
            working_dir,
            max_turns=30,
            model="",
            context_limit=0,
            compact_threshold=0.0,
        ):
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            captured["working_dir"] = working_dir
            captured["max_turns"] = max_turns
            captured["model"] = model
            captured["context_limit"] = context_limit
            captured["compact_threshold"] = compact_threshold
            yield MockAssistantMessage([MockTextBlock("Implemented")])
            yield MockResultMessage()

    provider = FakeProvider()
    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)
    monkeypatch.setattr("src.streaming.print_turn_timing", lambda *args, **kwargs: None)

    cfg = Config(
        working_dir=str(tmp_path),
        context_limit=123_000,
        compact_threshold=0.72,
    )
    session = object.__new__(CoachPlayerSession)
    session.config = cfg
    session._interrupted = False
    session._provider_for_role = lambda role: provider
    session._provider_model = lambda prov: ""

    result = asyncio.run(
        session._run_turn(
            role="player",
            prompt="do the thing",
            system_prompt="system",
            max_turns=7,
            timeout_s=30,
        )
    )

    assert result.text == "Implemented"
    assert captured["working_dir"] == str(tmp_path)
    assert captured["context_limit"] == 123_000
    assert captured["compact_threshold"] == 0.72


def test_init_review_provider_defaults_to_coach_when_unset(tmp_path, monkeypatch):
    """Empty review_provider should follow coach even when Codex is available."""
    codex_provider = _make_mock_provider()
    coach_provider = _make_mock_provider()

    def fake_create_provider(name, env=None, cfg=None):
        if name == "codex":
            return codex_provider
        return coach_provider

    monkeypatch.setattr("src.coach_player.create_provider", fake_create_provider)

    cfg = Config(
        working_dir=str(tmp_path),
        plan_file="requirements.md",
        code_review=True,
        review_provider="",
        coach_provider="zai",
    )
    session = CoachPlayerSession(cfg, "1. Ship feature")

    assert session.review_provider is coach_provider
    assert session.review_provider_name == "zai"
    assert "codex" not in session._provider_cache


def test_init_review_provider_does_not_probe_codex_when_unset(tmp_path, monkeypatch):
    """Default review routing should not touch Codex at startup."""
    coach_provider = _make_mock_provider()
    created = []

    def fake_create_provider(name, env=None, cfg=None):
        created.append(name)
        return coach_provider

    monkeypatch.setattr("src.coach_player.create_provider", fake_create_provider)

    cfg = Config(
        working_dir=str(tmp_path),
        plan_file="requirements.md",
        code_review=True,
        review_provider="",
        coach_provider="zai",
    )
    session = CoachPlayerSession(cfg, "1. Ship feature")

    assert session.review_provider is coach_provider
    assert session.review_provider_name == "zai"
    assert "zai" in created
    assert "codex" not in session._provider_cache


def test_auto_review_follows_live_coach_switch_and_model(tmp_path, monkeypatch):
    """When review auto-falls back to coach, runtime coach switches should carry review with them."""
    codex_provider = _make_mock_provider()
    codex_provider.check_ready.return_value = (False, "missing auth")
    zai_provider = _make_mock_provider()
    opencode_provider = _make_mock_provider()

    def fake_create_provider(name, env=None, cfg=None):
        if name == "codex":
            return codex_provider
        if name == "opencode":
            return opencode_provider
        return zai_provider

    monkeypatch.setattr("src.coach_player.create_provider", fake_create_provider)

    cfg = Config(
        working_dir=str(tmp_path),
        plan_file="requirements.md",
        code_review=True,
        review_provider="",
        review_model="",
        coach_provider="zai",
        coach_model="glm-5.1",
    )
    session = CoachPlayerSession(cfg, "1. Ship feature")

    assert session.router._resolve_review_provider_name() == "zai"
    assert session.router._resolve_review_provider() is zai_provider
    assert session.router._resolve_review_model() == "glm-5.1"

    session.switch_runtime_role(
        "coach", "opencode", "opencode/minimax-m2.5-free"
    )

    assert session.router._resolve_review_provider_name() == "opencode"
    assert session.router._resolve_review_provider() is opencode_provider
    assert session.router._resolve_review_model() == "opencode/minimax-m2.5-free"


def test_init_fails_when_review_provider_not_ready(tmp_path, monkeypatch):
    """Code review mode should validate the resolved review provider during startup."""
    ready_provider = _make_mock_provider()
    broken_review = _make_mock_provider()
    broken_review.check_ready.return_value = (False, "missing auth")

    def fake_create_provider(name, env=None, cfg=None):
        if name == "codex":
            return broken_review
        return ready_provider

    monkeypatch.setattr("src.coach_player.create_provider", fake_create_provider)

    cfg = Config(
        working_dir=str(tmp_path),
        code_review=True,
        review_provider="codex",
        coach_provider="zai",
        player_provider="zai",
    )

    from src.errors import ProviderNotReadyError

    with pytest.raises(ProviderNotReadyError, match="reviewer provider \\(codex\\) not ready: missing auth"):
        CoachPlayerSession(cfg, "1. Ship feature")


def test_run_turn_uses_native_codex_usage_for_tokens(tmp_path, monkeypatch):
    """Codex AdaptedMessages should still populate TurnResult.tokens_used."""
    from src.providers.message_adapter import AdaptedMessage, TextBlock

    class FakeCodexProvider:
        def __init__(self):
            self._last_input_tokens = 0
            self._last_output_tokens = 0

        async def run(self, prompt, system_prompt, working_dir, max_turns=30, model=""):
            yield AdaptedMessage(
                role="assistant",
                content=[TextBlock(text="Implemented")],
                type="text",
            )
            self._last_input_tokens = 321
            self._last_output_tokens = 45
            yield AdaptedMessage(
                role="assistant",
                content=[],
                stop_reason="end_turn",
                type="result",
            )

    provider = FakeCodexProvider()
    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)
    monkeypatch.setattr("src.streaming.print_turn_timing", lambda *args, **kwargs: None)

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

    assert result.text == "Implemented"
    assert result.tokens_used == 366


def test_step_continuation_does_not_require_phase_complete():
    """Single-step continuation prompts should accept the normal player report."""
    from src.context_manager import _build_continuation_prompt

    prompt = _build_continuation_prompt("Implemented the requested step.", role="player")
    text = _player_report("Implemented on retry")

    assert CoachPlayerSession._player_output_complete(text, prompt) is True


def test_code_review_rejects_step_when_issues_remain(tmp_path, monkeypatch):
    """A step must not be approved when code review still reports bugs."""

    async def player_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        yield MockAssistantMessage([MockTextBlock(_player_report("Implemented"))])
        yield MockResultMessage()

    async def coach_run(prompt, system_prompt, working_dir, max_turns=30, model=""):
        yield MockAssistantMessage([MockTextBlock("IMPLEMENTATION_APPROVED")])
        yield MockResultMessage()

    mock_player = _make_mock_provider()
    mock_player.run = player_run
    mock_coach = _make_mock_provider()
    mock_coach.run = coach_run
    mock_review = _make_mock_provider()
    mock_review.run = coach_run

    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)
    monkeypatch.setattr("src.coach_player.parse_review_output", lambda messages: ReviewIssues("1. Found a bug in the implementation."))

    cfg = Config(
        working_dir=str(tmp_path),
        plan_file="requirements.md",
        max_turns=1,
        code_review=True,
        max_review_iterations=1,
    )
    session = CoachPlayerSession(cfg, "1. Ship feature")
    session.player_provider = mock_player
    session.coach_provider = mock_coach
    session.review_provider = mock_review
    session.review_provider_name = "codex"
    session.review_model = ""

    result = asyncio.run(session.run())

    assert result.approved is False
    assert result.steps_completed == 0
    assert result.status == "max_turns_reached"


def test_code_review_fix_uses_raw_player_model_id(tmp_path, monkeypatch):
    """Fix-up turns after review should pass the configured model id, not the display label."""
    from src.feedback import Approved, ReviewPassed

    captured_model_overrides = []

    async def fake_run_turn(
        role,
        prompt,
        system_prompt,
        max_turns,
        timeout_s,
        model_override="",
        provider_override=None,
    ):
        captured_model_overrides.append((role, model_override))
        if role == "player":
            return MagicMock(
                messages=[],
                text=_player_report("Implemented"),
                duration_s=0.1,
                tools_used=0,
            )
        if role == "coach":
            return MagicMock(
                messages=[],
                text="IMPLEMENTATION_APPROVED",
                duration_s=0.1,
                tools_used=0,
            )
        return MagicMock(messages=[], text="review", duration_s=0.1, tools_used=0)

    review_verdicts = iter([ReviewIssues("1. Fix this bug."), ReviewPassed()])

    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)
    monkeypatch.setattr("src.coach_player.parse_coach_output", lambda messages: Approved())
    monkeypatch.setattr("src.coach_player.parse_review_output", lambda messages: next(review_verdicts))

    cfg = Config(
        working_dir=str(tmp_path),
        plan_file="requirements.md",
        max_turns=1,
        code_review=True,
        max_review_iterations=2,
        player_model="opencode/mimo-v2-pro-free",
    )
    session = CoachPlayerSession(cfg, "1. Ship feature")
    session.player_model = "opencode | model=opencode/mimo-v2-pro-free"
    session.review_provider = _make_mock_provider()
    session.review_provider_name = "codex"
    session.review_model = ""
    session._run_turn = AsyncMock(side_effect=fake_run_turn)

    result = asyncio.run(session.run())

    assert result.approved is True
    assert ("player", "opencode/mimo-v2-pro-free") in captured_model_overrides
    assert ("player", "opencode | model=opencode/mimo-v2-pro-free") not in captured_model_overrides


def test_sync_batch_roles_with_coach_preserves_custom_overrides():
    """Changing coach should not overwrite explicitly customized batch roles."""
    config = Config(
        coach_provider="opencode",
        coach_model="opencode/mimo-v2-pro-free",
        batch_pre_provider="claude",
        batch_pre_model="sonnet",
        batch_post_provider="codex",
        batch_post_model="o3",
    )

    synced = _sync_batch_roles_with_coach(
        config, previous_provider="zai", previous_model=""
    )

    assert synced.batch_pre_provider == "claude"
    assert synced.batch_pre_model == "sonnet"
    assert synced.batch_post_provider == "codex"
    assert synced.batch_post_model == "o3"


def test_run_turn_with_provider_override_skips_router(tmp_path):
    """When provider_override is set, router.provider_for must not be called."""
    from unittest.mock import AsyncMock, MagicMock

    override_provider = MagicMock()
    fake_result = MagicMock()

    turn_runner_mock = MagicMock()
    turn_runner_mock.run_turn = AsyncMock(return_value=fake_result)
    turn_runner_mock._last_effective_context_limit = 200_000

    router_mock = MagicMock()
    router_mock.provider_for.side_effect = ValueError("Must not be called")

    cfg = Config(working_dir=str(tmp_path))
    session = object.__new__(CoachPlayerSession)
    session.config = cfg
    session._turn_runner = turn_runner_mock
    session.router = router_mock
    session._runtime = None

    result = asyncio.run(
        session._run_turn(
            role="judge",
            prompt="judge this",
            system_prompt="sys",
            max_turns=1,
            timeout_s=30,
            provider_override=override_provider,
        )
    )

    assert result == fake_result
    router_mock.provider_for.assert_not_called()
    _, call_kwargs = turn_runner_mock.run_turn.call_args
    assert call_kwargs["provider"] is override_provider


def test_run_with_continuation_with_provider_override_skips_router(tmp_path):
    """When provider_override is set, run_with_continuation must not call router.provider_for."""
    from unittest.mock import AsyncMock, MagicMock

    override_provider = MagicMock()
    fake_result = MagicMock()

    turn_runner_mock = MagicMock()
    turn_runner_mock.run_with_continuation = AsyncMock(return_value=fake_result)

    router_mock = MagicMock()
    router_mock.provider_for.side_effect = ValueError("Must not be called")

    cfg = Config(working_dir=str(tmp_path))
    session = object.__new__(CoachPlayerSession)
    session.config = cfg
    session._turn_runner = turn_runner_mock
    session.router = router_mock

    result = asyncio.run(
        session._run_with_continuation(
            role="judge",
            prompt="continue",
            system_prompt="sys",
            max_turns=1,
            timeout_s=30,
            provider_override=override_provider,
        )
    )

    assert result == fake_result
    router_mock.provider_for.assert_not_called()
    _, call_kwargs = turn_runner_mock.run_with_continuation.call_args
    assert call_kwargs["provider"] is override_provider
