"""Provider implementations for Claude Agent SDK."""

from .base import AgentProvider
from .ccg import CcgProvider, run_agent
from .claude_native import ClaudeNativeConfig, ClaudeNativeProvider
from .codex import CodexConfig, CodexProvider
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
        provider_name: "ccg", "ccg2", "claude", or "codex"
        ccg_env: CcgEnv instance (optional; if not provided, created for account)
        provider_config: Optional config dict from .g3/config.yaml providers section
                        Supports "type" key to override provider type

    Returns:
        Provider instance (CcgProvider, ClaudeNativeProvider, or CodexProvider)

    Raises:
        ValueError: If provider type is unknown or no token for CCG
    """
    provider_config = provider_config or {}

    # Use type from config if specified, otherwise fall back to provider_name
    provider_type = provider_config.get("type", provider_name)

    if provider_type in ("claude_glm", "ccg", "ccg2"):
        # CCG providers support multi-account via CcgEnv
        # If ccg_env provided, use it; otherwise create for this account
        if ccg_env is None:
            ccg_env = CcgEnv.for_account(provider_name, provider_config)
        return CcgProvider(ccg_env)

    if provider_type in ("claude_native", "claude"):
        native_cfg = ClaudeNativeConfig(
            claude_home=provider_config.get("claude_home", "~/.claude"),
            command=provider_config.get("command", "claude"),
            default_model=provider_config.get("default_model", "sonnet"),
        )
        return ClaudeNativeProvider(native_cfg)

    if provider_type == "codex":
        codex_cfg = CodexConfig(
            command=provider_config.get("command", "codex"),
            default_model=provider_config.get("default_model", provider_config.get("model", "")),
            default_timeout=provider_config.get("default_timeout", 900),
            sandbox_mode=provider_config.get("sandbox_mode", "workspace-write"),
            approval_policy=provider_config.get("approval_policy", "never"),
            ephemeral=provider_config.get("ephemeral", True),
            full_auto=provider_config.get("full_auto", False),
            bypass_approvals=provider_config.get("bypass_approvals", True),
            config_overrides=provider_config.get("config_overrides", {}),
            extra_args=provider_config.get("extra_args", []),
            enabled_features=provider_config.get("enabled_features", []),
            disabled_features=provider_config.get("disabled_features", []),
        )
        return CodexProvider(codex_cfg)

    raise ValueError(f"Unknown provider type: {provider_type} (name: {provider_name})")


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
    # Codex
    "CodexConfig",
    "CodexProvider",
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
