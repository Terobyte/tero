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
            api_url=provider_config.get("api_url", provider_config.get("base_url", "http://localhost:8765")),
            api_key=provider_config.get("api_key", ""),
            model=provider_config.get("model", provider_config.get("default_model", "gpt-5.4-medium")),
            default_timeout=provider_config.get("default_timeout", 900),
            auto_start=provider_config.get("auto_start", False),
            proxy_repo_path=provider_config.get("proxy_repo_path", ""),
            proxy_config_path=provider_config.get("proxy_config_path", ""),
            proxy_log_path=provider_config.get("proxy_log_path", ""),
            proxy_pid_path=provider_config.get("proxy_pid_path", ""),
            startup_timeout_s=provider_config.get("startup_timeout_s", 45),
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
