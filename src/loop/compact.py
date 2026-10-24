"""Счётчик контекста: решение «пора делать мягкую смену сессии»."""

from __future__ import annotations


class Compacted(Exception):
    """Поток превысил порог контекста: чтение остановлено на границе шага."""

    def __init__(self, tokens: int) -> None:
        super().__init__(f"compact at {tokens} tokens")
        self.tokens = tokens


def should_compact(tokens: int, at_tokens: int, requested: bool) -> bool:
    """True, когда компакт запрошен вручную или порог превышен.

    ``at_tokens == 0`` — автокомпакт выключен: без явного запроса
    порог никогда не срабатывает.
    """
    if requested:
        return True
    return at_tokens > 0 and tokens >= at_tokens
