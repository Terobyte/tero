"""Pure checks over the phase state for K_max and stall detection."""

from __future__ import annotations

from typing import Any

from src.loop.phase_ctx import PhaseState


def in_continuation(st: PhaseState) -> bool:
    # K_max считает только раунды-продолжения после падения исполнителя
    # (фаза 9), дошедшие до вердикта судьи NO_SHIP. Раунды без падения —
    # обычный цикл: _stalled/_no_progress работают как раньше.
    return st.had_crash and st.resume_active

def findings_key(findings: list[dict[str, Any]]) -> tuple[int, frozenset]:
    blocking = [item for item in findings if item.get("severity") == "blocking"]
    return (
        len(blocking),
        frozenset((item.get("file", ""), item.get("line")) for item in blocking),
    )

def not_shrunk(st: PhaseState, current: tuple[int, frozenset]) -> bool:
    prev = st.kmax_prev
    if prev is None:
        return True
    count_now, keys_now = current
    count_prev, keys_prev = prev
    return count_now >= count_prev and not keys_now < keys_prev

def stalled(st: PhaseState, previous: dict[str, str]) -> bool:
    # discard_test changes the tree; that is progress, not a stall.
    return (
        bool(st.open_findings)
        and not any(previous.get(item.id) == "resolved" for item in st.open_findings)
        and not st.discarded
    )
