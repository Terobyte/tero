"""Structured exception hierarchy for tero.

All application-specific exceptions inherit from TeroError so callers
can catch the entire family with a single except clause.

Existing exceptions (PhaseFailedError, PlanResetRequested, RateLimitError)
are re-exported from their original modules via aliases so that import
paths stay backward-compatible.
"""

from __future__ import annotations

from dataclasses import dataclass


class TeroError(Exception):
    """Base exception for all tero application errors."""


class ProviderError(TeroError):
    """Base for provider / LLM related errors."""


class ProviderNotReadyError(ProviderError):
    """Raised when a provider fails its readiness check."""


class ProviderTimeoutError(ProviderError):
    """Raised when a provider call exceeds its time budget."""

    def __init__(self, provider: str, timeout_s: float) -> None:
        self.provider = provider
        self.timeout_s = timeout_s
        super().__init__(f"{provider} timed out after {timeout_s}s")


class RateLimitError(ProviderError):
    """Raised when all providers in a chain are exhausted."""


class SessionError(TeroError):
    """Base for session / state machine errors."""


class StateTransitionError(SessionError):
    """Raised on an invalid state transition."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid state transition: {current} -> {target}")


class ConfigError(TeroError):
    """Raised for invalid or inconsistent configuration."""


class ContextError(TeroError):
    """Raised for context management issues (overflow, budget exceeded)."""


class BudgetExceededError(TeroError):
    """Raised when a cost budget (daily/monthly) is exceeded."""

    def __init__(self, budget_type: str, spent: float, limit: float) -> None:
        self.budget_type = budget_type
        self.spent = spent
        self.limit = limit
        super().__init__(f"{budget_type} budget exceeded: ${spent:.2f} / ${limit:.2f}")


@dataclass
class PhaseFailedError(TeroError):
    """Raised when a phase exhausts all retry attempts."""

    phase: object | None
    attempts: int

    def __post_init__(self) -> None:
        super().__init__(str(self))

    def __str__(self) -> str:
        if self.phase is None:
            return f"Phase failed after {self.attempts} attempts"
        completed = [s.text for s in self.phase.steps if s.done]
        return (
            f"Phase '{self.phase.name}' failed after {self.attempts} attempts. "
            f"Completed steps: {completed}"
        )


class PlanResetRequested(TeroError):
    """Raised when the operator requests a full plan-progress reset."""
