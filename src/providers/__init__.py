"""Provider implementations for Claude Agent SDK."""

from .base import AgentProvider
from .ccg import CcgProvider, run_agent
from .claude_native import ClaudeNativeConfig, ClaudeNativeProvider
from .message_adapter import (
    AdaptedMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    adapt_claude_event,
    adapt_sdk_message,
)

from src.config import CcgEnv


def create_provider(
    provider_name: str,
    ccg_env: CcgEnv | None = None,
    provider_config: dict | None = None,
):
    """Create provider by name from config.

    Args:
        provider_name: "ccg", "ccg2", or "claude"
        ccg_env: CcgEnv instance (required for CCG providers)
        provider_config: Optional config dict for provider settings

    Returns:
        Provider instance (CcgProvider or ClaudeNativeProvider)

    Raises:
        ValueError: If provider_name is unknown or ccg_env is missing for CCG
    """
    provider_config = provider_config or {}

    if provider_name in ("ccg", "ccg2"):
        if ccg_env is None:
            raise ValueError("CcgEnv required for CCG provider")
        return CcgProvider(ccg_env)

    if provider_name == "claude":
        native_cfg = ClaudeNativeConfig(
            claude_home=provider_config.get("claude_home", "~/.claude"),
            command=provider_config.get("command", "claude"),
            default_model=provider_config.get("default_model", "sonnet"),
        )
        return ClaudeNativeProvider(native_cfg)

    raise ValueError(f"Unknown provider: {provider_name}")


__all__ = [
    # Base
    "AgentProvider",
    # CCG
    "CcgEnv",
    "CcgProvider",
    "run_agent",
    # Claude Native
    "ClaudeNativeConfig",
    "ClaudeNativeProvider",
    # Message adapter
    "AdaptedMessage",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "adapt_claude_event",
    "adapt_sdk_message",
    # Factory
    "create_provider",
]
