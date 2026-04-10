"""Provider registry for multi-provider and multi-account support."""

from dataclasses import dataclass
from typing import Any

from .base import AgentProvider
from .claude_native import ClaudeNativeProvider, ClaudeNativeConfig
from .zai import ZaiProvider, ZaiConfig


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""

    name: str
    type: str
    config: dict[str, Any]


class ProviderRegistry:
    """Registry for managing multiple providers with different accounts.

    Supports:
    - Multiple provider types (zai, claude, codex, opencode, kilo)
    - Parallel execution of providers
    """

    def __init__(self, provider_configs: dict[str, dict] | None = None):
        """Initialize registry with provider configurations.

        Args:
            provider_configs: Dict of provider_name -> config dict
                             (from .g3/config.yaml providers section)
        """
        self._configs = provider_configs or {}
        self._instances: dict[str, AgentProvider] = {}

    def get(self, provider_name: str) -> AgentProvider:
        """Get or create a provider instance by name.

        Args:
            provider_name: Provider name ("zai", "claude", "codex", "opencode", "kilo")

        Returns:
            Provider instance

        Raises:
            ValueError: If provider type is unknown
        """
        if provider_name in self._instances:
            return self._instances[provider_name]

        provider_config = self._configs.get(provider_name, {})
        provider_type = provider_config.get("type", provider_name)

        instance = self._create_provider(provider_name, provider_type, provider_config)
        self._instances[provider_name] = instance
        return instance

    def _create_provider(
        self,
        provider_name: str,
        provider_type: str,
        provider_config: dict,
    ) -> AgentProvider:
        """Create a provider instance."""
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

        if provider_type == "codex":
            # Import here to avoid circular dependency
            from .codex import CodexProvider, CodexConfig

            codex_cfg = CodexConfig(
                command=provider_config.get("command", "codex"),
                default_model=provider_config.get(
                    "default_model", provider_config.get("model", "")
                ),
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

        if provider_type in ("opencode_native", "opencode", "kilo_native", "kilo"):
            from .opencode import OpenCodeProvider, OpenCodeConfig

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
                default_timeout=provider_config.get("default_timeout", 900),
                display_name=provider_config.get(
                    "display_name",
                    "Kilo" if provider_type in ("kilo_native", "kilo") else "OpenCode",
                ),
            )
            return OpenCodeProvider(opencode_cfg)

        raise ValueError(
            f"Unknown provider type: {provider_type} (name: {provider_name})"
        )

    def get_parallel_providers(self, names: list[str]) -> list[AgentProvider]:
        """Get multiple provider instances for parallel execution.

        Args:
            names: List of provider names (e.g., ["zai", "claude"])

        Returns:
            List of provider instances
        """
        return [self.get(name) for name in names]

    def list_available(self) -> list[str]:
        """List all configured provider names."""
        return list(self._configs.keys())

    def check_all_ready(self) -> dict[str, tuple[bool, str]]:
        """Check readiness of all configured providers.

        Returns:
            Dict of provider_name -> (is_ready, reason)
        """
        results = {}
        for name in self._configs:
            try:
                provider = self.get(name)
                results[name] = provider.check_ready()
            except Exception as e:
                results[name] = (False, str(e))
        return results
