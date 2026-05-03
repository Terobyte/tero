"""Provider implementations for Claude Agent SDK."""

from src.constants import DEFAULT_PROVIDER_TIMEOUT_S
from src.errors import ProviderError
from .base import AgentProvider
from .claude_native import ClaudeNativeConfig, ClaudeNativeProvider
from .codex import CodexConfig, CodexProvider
from .opencode import OpenCodeConfig, OpenCodeProvider
from .zai import ZaiConfig, ZaiProvider
from .message_adapter import (
    AdaptedMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    adapt_claude_event,
    adapt_sdk_message,
)


def create_provider(
    provider_name: str,
    provider_config: dict | None = None,
):
    """Create provider by name from config.

    Args:
        provider_name: "zai", "claude", "codex", "opencode", or "kilo"
        provider_config: Optional config dict from .g3/config.yaml providers section
                        Supports "type" key to override provider type

    Returns:
        Provider instance (ClaudeNativeProvider, CodexProvider, or OpenCodeProvider)

    Raises:
        ProviderError: If provider type is unknown
    """
    provider_config = provider_config or {}

    # Use type from config if specified, otherwise fall back to provider_name
    provider_type = provider_config.get("type", provider_name)

    if provider_type == "zai":
        zai_cfg = ZaiConfig(
            claude_home=provider_config.get("claude_home", "~/.claude-zai"),
            default_model=provider_config.get("default_model", "glm-5.1"),
        )
        return ZaiProvider(zai_cfg)

    if provider_type in ("claude_native", "claude"):
        native_cfg = ClaudeNativeConfig(
            claude_home=provider_config.get("claude_home", "~/.claude"),
            command=provider_config.get("command", "claude"),
            default_model=provider_config.get("default_model", "sonnet"),
        )
        return ClaudeNativeProvider(native_cfg)

    if provider_type in ("opencode_native", "opencode", "kilo_native", "kilo"):
        opencode_cfg = OpenCodeConfig(
            command=provider_config.get(
                "command",
                "kilo" if provider_type in ("kilo_native", "kilo") else "opencode",
            ),
            default_model=provider_config.get(
                "default_model",
                (
                    "kilo/xiaomi/mimo-v2-pro:free"
                    if provider_type in ("kilo_native", "kilo")
                    else "opencode/mimo-v2-pro-free"
                ),
            ),
            default_timeout=provider_config.get(
                "default_timeout", DEFAULT_PROVIDER_TIMEOUT_S
            ),
            display_name=provider_config.get(
                "display_name",
                "Kilo" if provider_type in ("kilo_native", "kilo") else "OpenCode",
            ),
        )
        return OpenCodeProvider(opencode_cfg)

    if provider_type == "codex":
        codex_cfg = CodexConfig(
            command=provider_config.get("command", "codex"),
            default_model=provider_config.get(
                "default_model", provider_config.get("model", "")
            ),
            default_timeout=provider_config.get(
                "default_timeout", DEFAULT_PROVIDER_TIMEOUT_S
            ),
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

    raise ProviderError(
        f"Unknown provider type: {provider_type} (name: {provider_name})"
    )


__all__ = [
    # Base
    "AgentProvider",
    # Claude Native
    "ClaudeNativeConfig",
    "ClaudeNativeProvider",
    # Codex
    "CodexConfig",
    "CodexProvider",
    # OpenCode
    "OpenCodeConfig",
    "OpenCodeProvider",
    # ZAI
    "ZaiConfig",
    "ZaiProvider",
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
