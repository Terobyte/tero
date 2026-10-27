"""Лимиты провайдеров для виджета и status (план 08-9).

Чего CLI не сообщает — «неизвестно». У codex в потоке только токены
(input_tokens из turn.completed.usage) и квота (kind=quota из фазы 07):
их и показываем, без выдуманных процентов.
"""

from __future__ import annotations

from typing import Any

UNKNOWN = "неизвестно"


def describe(provider: str, instance: Any = None, kind: str = "") -> str:
    """Одна строка лимита для провайдера."""
    if kind == "quota":
        return "квота"
    if kind == "auth":
        return "нужен вход"
    tokens = 0
    try:
        tokens = int(getattr(instance, "last_context_tokens", 0) or 0)
    except (TypeError, ValueError):
        tokens = 0
    if tokens > 0:
        return f"токены {tokens}"
    return UNKNOWN


def normalize_limits(saved: Any, providers: tuple[str, ...] = ("codex",)) -> dict[str, str]:
    """limits из state.json: чего нет — «неизвестно»."""
    out: dict[str, str] = {}
    known = saved if isinstance(saved, dict) else {}
    for name in providers:
        value = known.get(name)
        out[name] = value if isinstance(value, str) and value else UNKNOWN
    return out
