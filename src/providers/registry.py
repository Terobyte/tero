"""Provider registry for multi-provider and multi-account support."""

from dataclasses import dataclass
from typing import Any

from .base import AgentProvider
from .ccg import CcgProvider
from .claude_native import ClaudeNativeProvider, ClaudeNativeConfig
from src.config import CcgEnv


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""
    name: str
    type: str
    config: dict[str, Any]


class ProviderRegistry:
    """Registry for managing multiple providers with different accounts.

    Supports:
    - Multiple provider types (ccg, claude, codex)
    - Multiple accounts of the same type (ccg, ccg2)
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
            provider_name: Provider name ("ccg", "ccg2", "claude", "codex")

        Returns:
            Provider instance

        Raises:
            ValueError: If provider unknown or no token for CCG
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
        if provider_type in ("claude_glm", "ccg", "ccg2"):
            # CCG providers use CcgEnv for multi-account support
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
            # Import here to avoid circular dependency
            from .codex import CodexProvider, CodexConfig
            codex_cfg = CodexConfig(
                api_url=provider_config.get("api_url", "http://localhost:8765"),
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

    def get_parallel_providers(self, names: list[str]) -> list[AgentProvider]:
        """Get multiple provider instances for parallel execution.

        Args:
            names: List of provider names (e.g., ["ccg", "ccg2"])

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
