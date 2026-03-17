"""Base protocol for agent providers."""

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass
class AgentResult:
    """Compatibility result object used by duel/orchestrator flows."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float


@runtime_checkable
class AgentProvider(Protocol):
    """Minimal contract for any provider.

    Uses Protocol instead of ABC because structural subtyping is sufficient.
    No registration or inheritance needed - just match the interface.
    """

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
    ) -> AsyncIterator:
        """Run an agent and return an async stream of messages.

        Args:
            prompt: User prompt to send
            system_prompt: System instructions for the agent
            working_dir: Working directory for the agent
            max_turns: Maximum number of turns (default: 30)
            model: Optional model override

        Yields:
            Messages from the agent (SDK objects or dicts)
        """
        ...

    def check_ready(self) -> tuple[bool, str]:
        """Check if provider is ready to use.

        Verifies auth, CLI availability, etc.

        Returns:
            Tuple of (is_ready: bool, reason: str)
            If ready, reason is empty string.
            If not ready, reason explains what's missing.
        """
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for UI display.

        Returns:
            Display name like "Claude Pro (sonnet)" or "CCG (glm-5)"
        """
        ...
