"""SHIP с долгом (план 08-11): коммит debt_pending после зелёного гейта.

Вынесено из phase.py под потолок 689: `_PhaseRunner` хранит только тонкий
вызов `commit_before_ship`. Не импортирует `phase`/`run`/`cli_entry`.
"""

from __future__ import annotations

from typing import Any


def pending_entries(state: Any) -> list[dict[str, Any]]:
    """Записи долга этого шипа: пусто — обычный SHIP без долга."""
    out: list[dict[str, Any]] = []
    for item in list(getattr(state, "debt_pending", None) or []):
        out.extend(list((item or {}).get("entries") or []))
    return out


def committed_entries(runner: Any) -> list[dict[str, Any]]:
    """Записи, зафиксированные перед этим шипом (снимок до коммита)."""
    if hasattr(runner, "_debt_committed"):
        return list(getattr(runner, "_debt_committed", None) or [])
    return pending_entries(runner.state)


def commit_before_ship(runner: Any) -> bool:
    """Коммит долга + проверка обоих артефактов. False — шип запрещён.

    Неуспешный коммит debt_pending не чистит (F10): следующий SHIP не
    должен пропустить долг мимо state.json/debt.md. Частично записанные
    пункты уже убраны из debt_pending самим `commit_pending`.
    """
    from src.loop import debt as debt_mod

    runner._debt_committed = pending_entries(runner.state)
    try:
        debt_mod.commit_pending(runner.ctx, runner.state)
    except Exception:
        return False
    try:
        return bool(debt_mod.verify_debt(runner.ctx.run_dir, runner._debt_committed))
    except Exception:
        return False
