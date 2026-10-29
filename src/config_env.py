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

# Читаются только из ~/.tero/config.yaml: репо пишет игрок с bypassPermissions,
# окружение наследуется от кого угодно, снимок в state.json лежит в репо.
GLOBAL_ONLY = ("wake_command", "host", "phone_text_answers")


def drop_global_only(defaults: dict[str, Any], is_global: bool) -> dict[str, Any]:
    """`defaults` without GLOBAL_ONLY keys unless this is the global file."""
    if is_global or not isinstance(defaults, dict):
        return defaults
    return {k: v for k, v in defaults.items() if k not in GLOBAL_ONLY}


def global_defaults() -> dict[str, Any]:
    """`defaults:` of ~/.tero/config.yaml, {} when missing or broken."""
    from pathlib import Path

    import yaml

    try:
        data = yaml.safe_load((Path.home() / ".tero" / "config.yaml").read_text(encoding="utf-8"))
    except Exception:
        return {}
    section = data.get("defaults") if isinstance(data, dict) else None
    return dict(section) if isinstance(section, dict) else {}


def phone_text_allowed() -> bool:
    """`phone_text_answers` of the global config (default True); state.json is not consulted."""
    value = global_defaults().get("phone_text_answers")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


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
