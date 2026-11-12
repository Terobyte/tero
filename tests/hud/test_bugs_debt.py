"""B06: debt[].phase пишется 0-based (src/loop/debt.py: ctx.phase.index), а не 1-based."""
from __future__ import annotations

import time

from src.hud.model import build_view


def _state(debt_phase: int) -> dict:
    return {
        "status": "running", "phase_index": 3, "total_phases": 5, "round": 1, "role": "player",
        "provider": "codex", "model": "opus", "phase_started": time.time() - 100,
        "phases": [{"title": f"p{i}", "done": i < 3} for i in range(5)],
        "debt": [{"phase": debt_phase, "reason": "fuse"}],
        "pid": 1, "pid_started": 1.0,
    }


def _orange(state: dict) -> list[int]:
    view = build_view(state, [], time.time(), lambda _s: True)
    return [i for i, s in enumerate(view["sectors"]) if s["color"] == "orange"]


def test_B06_debt_phase_is_zero_based_orange_on_exact_sector():
    # долг записан для фазы с индексом 2 (0-based) -> оранжевым должен быть сектор 2, не 1 и не 3
    assert _orange(_state(2)) == [2], "ожидали оранжевый сектор 2 (0-based debt.phase=2)"

