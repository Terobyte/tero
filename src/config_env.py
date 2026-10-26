"""Env-разбор конфига: _ENV_MAP и apply_env (вынесено из src/config.py, план 08-2)."""

from __future__ import annotations

from typing import Any, Callable

_ENV_MAP = {
    "TERO_PLAYER_PROVIDER": "player_provider",
    "TERO_PLAYER_MODEL": "player_model",
    "TERO_JUDGE_PROVIDER": "judge_provider",
    "TERO_JUDGE_MODEL": "judge_model",
    "TERO_TESTER_PROVIDER": "tester_provider",
    "TERO_TESTER_MODEL": "tester_model",
    "TERO_ESCALATION_PROVIDER": "escalation_provider",
    "TERO_ESCALATION_MODEL": "escalation_model",
    "TERO_TEST_COMMAND": "test_command",
    "TERO_TEST_COMMAND_SCOPED": "test_command_scoped",
    "TERO_GATE_SCOPE": "gate_scope",
    "TERO_JEV_ENABLED": "jev_enabled",
}


def apply_env(merged: dict[str, Any], normalize_provider: Callable[[str], str]) -> None:
    """Наложить TERO_* поверх merged (та же семантика, что была инлайн в resolve_config)."""
    import os

    from src.config import Config, UnknownProviderError

    for env_key, cfg_key in _ENV_MAP.items():
        raw = os.environ.get(env_key)
        if not raw:
            continue
        if cfg_key.endswith("_provider"):
            current = merged.get(cfg_key, Config.__dataclass_fields__[cfg_key].default)
            try:
                same = normalize_provider(raw) == normalize_provider(str(current))
            except UnknownProviderError:
                same = raw == current
            if not same:
                # Как в меню: новый провайдер не наследует старую модель.
                merged[cfg_key.replace("_provider", "_model")] = ""
        merged[cfg_key] = raw
