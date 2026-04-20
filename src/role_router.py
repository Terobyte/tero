"""Role Router — maps abstract runtime roles to concrete provider instances.

Replaces ~7 duplicated provider-resolution methods from CoachPlayerSession
with a single ``_ROLE_CONFIG_MAP`` table and thin helper methods.
"""

from __future__ import annotations

from src.errors import ProviderNotReadyError

_ROLE_CONFIG_MAP: dict[str, tuple[str, str]] = {
    "player": ("player_provider", "player_model"),
    "coach": ("coach_provider", "coach_model"),
    "test_writer": ("test_writer_provider", "test_writer_model"),
    "preplanner": ("preplan_provider", "preplan_model"),
    "reviewer": ("review_provider", "review_model"),
    "coach_fallback": ("coach_fallback_provider", "coach_fallback_model"),
    "judge": ("batch_judge_provider", "batch_judge_model"),
}


class RoleRouter:
    """Resolves abstract roles (player, coach, …) to provider instances."""

    def get_provider_by_name(self, name: str):
        """Return a provider instance by provider name (public API)."""
        return self._get_or_create_provider(name)

    def __init__(
        self,
        config,
        get_or_create_provider,
        *,
        player_provider=None,
        coach_provider=None,
    ):
        self.config = config
        self._get_or_create_provider = get_or_create_provider
        self._player_provider = player_provider
        self._coach_provider = coach_provider

    def provider_name_for(self, role: str) -> str:
        """Return the configured provider name backing a runtime role."""
        if role == "reviewer":
            return self._resolve_review_provider_name()

        provider_attr, _ = self._role_config(role)
        name = getattr(self.config, provider_attr, "")

        if not name:
            if role in ("test_writer", "coach_fallback"):
                return self.config.coach_provider
            if role == "judge":
                return "codex"  # matches BatchExecutor._judge_provider fallback
            return ""

        return name

    def provider_for(self, role: str):
        """Return the provider instance backing a role."""
        if role == "player":
            return self._player_provider
        if role == "coach":
            return self._coach_provider
        if role == "reviewer":
            return self._resolve_review_provider()

        provider_attr, _ = self._role_config(role)
        name = getattr(self.config, provider_attr, "")

        if not name:
            if role == "test_writer":
                name = self.config.coach_provider
            elif role == "coach_fallback":
                name = self.config.coach_provider
            else:
                raise ValueError(f"No provider configured for role: {role}")

        return self._get_or_create_provider(name)

    def display_label_for(self, role: str) -> str:
        """Build a stable label showing provider, model, and account."""
        provider_name = self.provider_name_for(role)
        provider = self.provider_for(role)
        if role == "reviewer":
            model_override = self._resolve_review_model()
        else:
            _, model_attr = self._role_config(role)
            model_override = getattr(self.config, model_attr, "")
        return format_provider_display(provider_name, provider, model_override)

    def check_roles_ready(self, roles: list[str]) -> None:
        """Verify all given roles have ready providers; raise if not."""
        for role in roles:
            provider_name = self.provider_name_for(role)
            provider = self.provider_for(role)
            ok, reason = provider.check_ready()
            if not ok:
                raise ProviderNotReadyError(
                    f"{role} provider ({provider_name}) not ready: {reason}"
                )

    def switch_role(self, role: str, provider_name: str, model: str) -> str:
        """Apply a live provider/model switch and return the new display label.

        Only ``coach`` and ``player`` support runtime switching.
        When switching the coach, ``batch_pre_*`` and ``batch_post_*`` are
        synced when they were previously aligned with the old coach values.
        """
        if role not in ("coach", "player"):
            raise ValueError(f"Unsupported runtime role switch: {role}")

        provider = self._get_or_create_provider(provider_name)
        ok, reason = provider.check_ready()
        if not ok:
            raise ProviderNotReadyError(
                f"{role} provider ({provider_name}) not ready: {reason}"
            )

        provider_attr, model_attr = self._role_config(role)

        snapshot = {
            provider_attr: getattr(self.config, provider_attr),
            model_attr: getattr(self.config, model_attr),
            "batch_pre_provider": getattr(self.config, "batch_pre_provider", ""),
            "batch_pre_model": getattr(self.config, "batch_pre_model", ""),
            "batch_post_provider": getattr(self.config, "batch_post_provider", ""),
            "batch_post_model": getattr(self.config, "batch_post_model", ""),
            "coach_fallback_provider": getattr(
                self.config, "coach_fallback_provider", ""
            ),
            "coach_fallback_model": getattr(self.config, "coach_fallback_model", ""),
            "review_provider": getattr(self.config, "review_provider", ""),
            "review_model": getattr(self.config, "review_model", ""),
            "test_writer_provider": getattr(self.config, "test_writer_provider", ""),
            "test_writer_model": getattr(self.config, "test_writer_model", ""),
            "player_provider": getattr(self.config, "player_provider", ""),
            "player_model": getattr(self.config, "player_model", ""),
            "coach_provider": getattr(self.config, "coach_provider", ""),
            "coach_model": getattr(self.config, "coach_model", ""),
            "_coach_provider": self._coach_provider,
            "_player_provider": self._player_provider,
        }

        try:
            setattr(self.config, provider_attr, provider_name)
            setattr(self.config, model_attr, model)

            if role == "coach":
                self._coach_provider = provider

                old_coach_prov = snapshot["coach_provider"]
                old_coach_mod = snapshot["coach_model"]

                sync_batch_pre = (
                    snapshot["batch_pre_provider"] == old_coach_prov
                    and snapshot["batch_pre_model"] == old_coach_mod
                )
                sync_batch_post = (
                    snapshot["batch_post_provider"] == old_coach_prov
                    and snapshot["batch_post_model"] == old_coach_mod
                )

                if sync_batch_pre:
                    self.config.batch_pre_provider = provider_name
                    self.config.batch_pre_model = model
                if sync_batch_post:
                    self.config.batch_post_provider = provider_name
                    self.config.batch_post_model = model
            else:
                self._player_provider = provider

            return self.display_label_for(role)
        except Exception:
            for key, val in snapshot.items():
                if key == "_coach_provider":
                    self._coach_provider = val
                elif key == "_player_provider":
                    self._player_provider = val
                elif hasattr(self.config, key):
                    setattr(self.config, key, val)
            raise

    def update_pre_resolved(self, *, player_provider=None, coach_provider=None) -> None:
        """Update cached player/coach provider references after a switch."""
        if player_provider is not None:
            self._player_provider = player_provider
        if coach_provider is not None:
            self._coach_provider = coach_provider

    # --- review helpers ---

    def _resolve_review_provider_name(self) -> str:
        explicit_review = (getattr(self.config, "review_provider", "") or "").strip()
        if explicit_review:
            return explicit_review
        return self.config.coach_provider

    def _resolve_review_provider(self):
        name = self._resolve_review_provider_name()
        if name == self.config.coach_provider:
            return self._coach_provider
        return self._get_or_create_provider(name)

    def _resolve_review_model(self) -> str:
        if getattr(self.config, "review_model", ""):
            return self.config.review_model
        if self._resolve_review_provider_name() == self.config.coach_provider:
            return self.config.coach_model
        return ""

    # --- internal ---

    @staticmethod
    def _role_config(role: str) -> tuple[str, str]:
        if role not in _ROLE_CONFIG_MAP:
            raise ValueError(f"Unknown role: {role}")
        return _ROLE_CONFIG_MAP[role]


def _provider_model(provider) -> str:
    """Best-effort lookup of the model that provider will use by default."""
    env = getattr(provider, "env", None)
    if env is not None:
        model = getattr(env, "model", "")
        if model:
            return model

    provider_config = getattr(provider, "config", None)
    if provider_config is not None:
        for attr in ("default_model", "model"):
            value = getattr(provider_config, attr, "")
            if value:
                return value

    return ""


def _provider_account(provider) -> str:
    """Best-effort lookup of an account label for display."""
    env = getattr(provider, "env", None)
    if env is not None:
        return getattr(env, "account_label", "") or ""
    return ""


def format_provider_display(
    provider_name: str, provider, model_override: str = ""
) -> str:
    """Build a display label for any provider/model combination."""
    resolved_model = model_override or _provider_model(provider) or "default"
    account = _provider_account(provider)

    parts = [provider_name, f"model={resolved_model}"]
    if account:
        parts.append(f"account={account}")
    return " | ".join(parts)
