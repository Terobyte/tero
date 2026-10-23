"""Free opencode models rotate: the user picks one, tero checks it is still free."""

from __future__ import annotations

from typing import Callable

from src.config import Config, ConfigError
from src.providers.free_models import free_models, is_free

_ROLES = ("player", "escalation", "tester", "judge")


def check_free_models(cfg: Config, list_models: Callable[[], list[str]]) -> None:
    """Raise if a chosen free opencode model is gone from ``opencode models``.

    Nothing is swapped silently: the error names the roles and today's free list.
    No list (CLI missing or failed) means no verdict, the run goes on.
    """
    chosen = [
        (role, getattr(cfg, f"{role}_model").strip())
        for role in _ROLES
        if getattr(cfg, f"{role}_provider") == "opencode" and is_free(getattr(cfg, f"{role}_model"))
    ]
    if not chosen:
        return
    listed = list_models()
    if not listed:
        return
    gone = [(role, model) for role, model in chosen if model not in listed]
    if not gone:
        return
    names = ", ".join(f"{role} {model}" for role, model in gone)
    today = ", ".join(free_models(listed)) or "нет ни одной"
    raise ConfigError(
        f"больше не бесплатны в opencode: {names}. Сейчас бесплатные: {today}. "
        "Выбери модель в меню или флагом, например --judge opencode:<id>"
    )
