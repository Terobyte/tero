"""Provider factory for coding-agent CLI providers."""

from src.constants import DEFAULT_PROVIDER_TIMEOUT_S
from src.errors import ProviderError
from .claude_native import ClaudeNativeConfig, ClaudeNativeProvider
from .codex import CodexConfig, CodexProvider
from .opencode import OpenCodeConfig, OpenCodeProvider
from .gemini import GeminiConfig, GeminiProvider
from .muse import muse_from_config
from .cursor import CursorConfig, CursorProvider


def create_provider(
    provider_name: str,
    provider_config: dict | None = None,
):
    """Create a provider by name ("claude", "codex", "opencode", "gemini", "muse", "cursor").

    provider_config is an optional dict from .tero/config.yaml providers section.
    """
    provider_config = provider_config or {}

    # Use type from config if specified, otherwise fall back to provider_name
    provider_type = provider_config.get("type", provider_name)

    if provider_type in ("claude_native", "claude"):
        native_cfg = ClaudeNativeConfig(
            command=provider_config.get("command", "claude"),
            default_model=provider_config.get("default_model", "claude-sonnet-5"),
        )
        return ClaudeNativeProvider(native_cfg)

    if provider_type in ("opencode_native", "opencode"):
        opencode_cfg = OpenCodeConfig(
            command=provider_config.get("command", "opencode"),
            default_model=provider_config.get(
                "default_model", OpenCodeConfig.default_model
            ),
            default_timeout=provider_config.get(
                "default_timeout", DEFAULT_PROVIDER_TIMEOUT_S
            ),
            display_name=provider_config.get("display_name", "OpenCode"),
            stall_timeout_s=provider_config.get("stall_timeout_s", 0),
        )
        return OpenCodeProvider(opencode_cfg)

    if provider_type == "gemini":
        gemini_cfg = GeminiConfig(
            command=provider_config.get("command", "gemini"),
            default_model=provider_config.get("default_model", "gemini-3.1-pro-preview"),
            display_name=provider_config.get("display_name", "Gemini"),
            default_timeout=provider_config.get(
                "default_timeout", DEFAULT_PROVIDER_TIMEOUT_S
            ),
            yolo=provider_config.get("yolo", True),
        )
        return GeminiProvider(gemini_cfg)

    if provider_type in ("cursor", "cursor_cli", "cursor_headless"):
        cursor_cfg = CursorConfig(
            command=provider_config.get("command", "agent"),
            default_model=provider_config.get("default_model", "composer-2.5"),
            display_name=provider_config.get("display_name", "Cursor"),
            force=provider_config.get("force", provider_config.get("yolo", True)),
            trust=provider_config.get("trust", True),
        )
        return CursorProvider(cursor_cfg)

    if provider_type == "muse":
        return muse_from_config(provider_config)

    if provider_type == "codex":
        codex_cfg = CodexConfig(
            command=provider_config.get("command", "codex"),
            default_model=provider_config.get(
                "default_model", provider_config.get("model", "gpt-5.6-terra")
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
            model_reasoning_effort=provider_config.get(
                "model_reasoning_effort", "max"
            ),
        )
        return CodexProvider(codex_cfg)

    raise ProviderError(
        f"Unknown provider type: {provider_type} (name: {provider_name})"
    )


__all__ = [
    "create_provider",
]
