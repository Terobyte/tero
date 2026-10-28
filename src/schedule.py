"""Расписание игроков фазы по раундам (player_schedule)."""

from __future__ import annotations

import dataclasses
from typing import Any

from src.config import normalize_provider, split_role_spec


def parse_schedule(value: str) -> list[tuple[str, str, int]]:
    """"provider:model×n, ..." → [(provider, model, n)]; ×n можно писать как xn или *n."""
    out: list[tuple[str, str, int]] = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        spec, n = part, 1
        for sep in ("×", "*", "x"):
            head, _, tail = part.rpartition(sep)
            if head and tail.strip().isdigit():
                spec, n = head.strip(), int(tail.strip())
                break
        provider, model = split_role_spec(spec)
        if not provider or n < 1:
            raise ValueError(f"player_schedule: плохой элемент {part!r}")
        out.append((normalize_provider(provider), model, n))
    return out


def schedule_player(value: str, round_no: int) -> tuple[str, str] | None:
    """Игрок раунда round_no (с 1) по расписанию; после конца — последний."""
    try:
        items = parse_schedule(value)
    except ValueError:
        return None
    if not items:
        return None
    left = max(int(round_no), 1)
    for provider, model, n in items:
        if left <= n:
            return provider, model
        left -= n
    return items[-1][0], items[-1][1]


def apply_schedule(cfg: Any, value: str | None = None) -> Any:
    """Расписание задано → эскалация round, без stuck, лимит раундов = сумма.

    Пример: "opencode:muse-spark×3,claude:sonnet×2,claude:opus×2".
    """
    if value:
        cfg = dataclasses.replace(cfg, player_schedule=str(value))
    total = sum(n for _, _, n in parse_schedule(cfg.player_schedule))
    return dataclasses.replace(cfg, escalation="round", escalate_at_round=0,
                               max_rounds=total or cfg.max_rounds)
