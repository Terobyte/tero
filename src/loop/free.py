"""Pick free opencode models at run start: today's free list, not yesterday's."""

from __future__ import annotations

import dataclasses
from typing import Callable

from src.config import Config, ConfigError, family_key
from src.providers.free_models import FREE, free_models, is_free

# judge last: it must land outside the families of every model that writes code
_ORDER = ("player", "escalation", "tester", "judge")


def resolve_free_models(
    cfg: Config, list_models: Callable[[], list[str]]
) -> tuple[Config, list[str]]:
    """Replace ``free``/empty/rotated-out opencode models; return cfg and notes to print."""
    if not any(_uses_free(cfg, role) for role in _ORDER):
        return cfg, []
    listed = list_models()
    free = free_models(listed)
    notes: list[str] = []
    changes: dict[str, str] = {}
    for role in _ORDER:
        if not _needs_pick(cfg, role, listed):
            continue
        old = getattr(cfg, f"{role}_model")
        if role == "judge":
            writers = {
                family_key(f"{getattr(cfg, f'{r}_provider')}:{changes.get(r, getattr(cfg, f'{r}_model'))}")
                for r in ("player", "escalation")
                if getattr(cfg, f"{r}_provider")
            }
            choices = [m for m in free if family_key(f"opencode:{m}") not in writers]
        else:
            writers = set()
            choices = free
        if not choices:
            if not free:
                raise ConfigError(
                    f"{role}: нет бесплатных моделей opencode (`opencode models` пуст или не запустился)"
                )
            raise ConfigError(
                f"{role}: все бесплатные модели opencode из семьи исполнителя "
                f"({', '.join(sorted(writers))}): {', '.join(free)}"
            )
        changes[role] = choices[0]
        if old and old.strip().lower() != FREE:
            notes.append(f"{role}: {old} больше нет в списке бесплатных, беру {choices[0]}")
        else:
            notes.append(f"{role}: бесплатная модель {choices[0]}")
    return dataclasses.replace(cfg, **{f"{r}_model": m for r, m in changes.items()}), notes


def _uses_free(cfg: Config, role: str) -> bool:
    if getattr(cfg, f"{role}_provider") != "opencode":
        return False
    model = getattr(cfg, f"{role}_model").strip()
    return not model or model.lower() == FREE or is_free(model)


def _needs_pick(cfg: Config, role: str, listed: list[str]) -> bool:
    if not _uses_free(cfg, role):
        return False
    model = getattr(cfg, f"{role}_model").strip()
    if not model or model.lower() == FREE:
        return True
    # a pinned free model is checked only against a list we actually got
    return bool(listed) and model not in listed
