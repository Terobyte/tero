"""Вынесенный _no_progress из phase.py (план 08-10): место под ветку без роста phase.py."""

from __future__ import annotations

from typing import Any


def is_situational(cfg: Any) -> bool:
    try:
        return str(getattr(cfg, "escalation", "round") or "round") == "situational"
    except Exception:
        return False


def check_no_progress(state: Any, cfg: Any, ledger: Any, resume_from: Any = None) -> bool:
    """Старый механизм stuck: escalate_once → stop. При situational без ветки escalate_at_round."""
    situational = is_situational(cfg)
    if not situational and cfg.escalate_at_round:
        return state.round >= cfg.escalate_at_round
    if not state.escalated and cfg.escalation_provider:
        state.escalated = True
        ledger.event(
            "escalate",
            round=state.round,
            provider=cfg.escalation_provider,
            model=cfg.escalation_model,
        )
        print(
            "  нет прогресса → эскалация: "
            f"{cfg.escalation_provider}({cfg.escalation_model})",
            flush=True,
        )
        return False
    if state.round <= getattr(resume_from, "grace_until", 0):
        return False  # действие more: эти раунды без stuck
    print("  нет прогресса → откат фазы", flush=True)
    return True
