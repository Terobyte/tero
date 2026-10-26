"""Точка judge_pending (вынесена из judge_step.py, план 08-2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.loop.phase_ctx import PhaseOutcome
from src.loop.state import RunState
from src.loop.transition import phase_transition

if TYPE_CHECKING:
    from src.loop.judge_step import JudgeStep

def stop_judge_pending(step: JudgeStep, reason: str = "judge_failed") -> PhaseOutcome:
    """Код готов, но не принят: tero review + resume; с decisions — через DecisionStep."""
    from src.loop.state import Status

    state = RunState.read(step.ctx.run_dir / "state.json") or {}
    ref = state.get("snapshot_ref") or ""
    if reason in ("judge_auth", "judge_quota"):
        provider = step.ctx.cfg.judge_provider or "?"
        text = step.ctx.provider_text("judge", provider) + " `tero resume` после входа"
    elif reason == "needs_user":
        question = step.ctx.st.user_question or ""
        text = f"нужен человек: `{question}`; `tero review`, `tero resume`"
    else:
        text = f"код готов, не принят: `{ref}`; `tero review`, `tero resume`"
    try:
        decisions = bool(step.ctx.cfg.decisions)
    except Exception:
        decisions = False
    if decisions:
        from src.loop.decision_step import DecisionStep

        return DecisionStep(step.ctx, step.rollback_with_snapshot).jp_open(reason, text, ref)
    phase_transition(
        step.ctx,
        status=Status.JUDGE_PENDING.value,
        reason=reason,
        text=text,
        ref=ref,
    )
    return PhaseOutcome(
        status="judge_pending",
        reason=reason,
        rounds=step.ctx.st.round,
        escalated=step.ctx.st.escalated,
        stop_text=text,
    )
