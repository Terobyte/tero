"""Tests for src/errors.py — structured exception hierarchy."""

import pytest

from src.errors import (
    BudgetExceededError,
    ConfigError,
    ContextError,
    PhaseFailedError,
    PlanResetRequested,
    ProviderError,
    ProviderNotReadyError,
    ProviderTimeoutError,
    RateLimitError,
    SessionError,
    StateTransitionError,
    TeroError,
)


class TestHierarchy:
    def test_all_errors_are_tero_error(self):
        classes = [
            ProviderError,
            ProviderNotReadyError,
            ProviderTimeoutError,
            RateLimitError,
            SessionError,
            StateTransitionError,
            ConfigError,
            ContextError,
            BudgetExceededError,
            PhaseFailedError,
            PlanResetRequested,
        ]
        for cls in classes:
            assert issubclass(cls, TeroError), f"{cls.__name__} should be a TeroError"

    def test_provider_family(self):
        assert issubclass(ProviderNotReadyError, ProviderError)
        assert issubclass(ProviderTimeoutError, ProviderError)
        assert issubclass(RateLimitError, ProviderError)

    def test_session_family(self):
        assert issubclass(StateTransitionError, SessionError)

    def test_catch_all_with_tero_error(self):
        with pytest.raises(TeroError):
            raise ProviderError("boom")

        with pytest.raises(TeroError):
            raise StateTransitionError("a", "b")

        with pytest.raises(TeroError):
            raise BudgetExceededError("daily", 15.0, 10.0)


class TestProviderErrors:
    def test_provider_not_ready_message(self):
        err = ProviderNotReadyError("zai not ready: no API key")
        assert "zai not ready" in str(err)
        assert isinstance(err, ProviderError)

    def test_provider_timeout_stores_fields(self):
        err = ProviderTimeoutError("codex", 120.0)
        assert err.provider == "codex"
        assert err.timeout_s == 120.0
        assert "codex" in str(err)
        assert "120" in str(err)

    def test_rate_limit_is_provider_error(self):
        err = RateLimitError("all providers exhausted")
        assert isinstance(err, ProviderError)


class TestSessionErrors:
    def test_state_transition_stores_fields(self):
        err = StateTransitionError("idle", "done")
        assert err.current == "idle"
        assert err.target == "done"
        assert "idle" in str(err)
        assert "done" in str(err)


class TestBudgetError:
    def test_budget_exceeded_format(self):
        err = BudgetExceededError("daily", 15.50, 10.00)
        assert err.budget_type == "daily"
        assert err.spent == 15.50
        assert err.limit == 10.00
        assert "$15.50" in str(err)
        assert "$10.00" in str(err)


class TestPhaseFailedError:
    def test_phase_failed_no_phase(self):
        err = PhaseFailedError(phase=None, attempts=3)
        assert "3 attempts" in str(err)

    def test_phase_failed_with_phase(self):
        step = type("Step", (), {"text": "write tests", "done": False})()
        phase = type("Phase", (), {"name": "alpha", "steps": [step]})()
        err = PhaseFailedError(phase=phase, attempts=5)
        assert "alpha" in str(err)
        assert "5 attempts" in str(err)

    def test_phase_failed_completed_steps_listed(self):
        done_step = type("Step", (), {"text": "setup", "done": True})()
        pending_step = type("Step", (), {"text": "impl", "done": False})()
        phase = type("Phase", (), {"name": "p1", "steps": [done_step, pending_step]})()
        err = PhaseFailedError(phase=phase, attempts=2)
        assert "setup" in str(err)


class TestPlanResetRequested:
    def test_plan_reset_is_tero_error(self):
        err = PlanResetRequested()
        assert isinstance(err, TeroError)


class TestConfigAndContextErrors:
    def test_config_error(self):
        err = ConfigError("missing field")
        assert "missing field" in str(err)
        assert isinstance(err, TeroError)

    def test_context_error(self):
        err = ContextError("overflow")
        assert "overflow" in str(err)
        assert isinstance(err, TeroError)
