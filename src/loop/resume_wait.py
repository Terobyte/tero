"""Resume into an already-open decision: same round/question, fresh timeout."""

from __future__ import annotations

from typing import Any


def resume_waiting_outcome(runner: Any) -> Any:
    """Waiting outcome with a live decider; timeout restarts in resolve()."""
    from src.loop.decision_step import DecisionStep
    from src.loop.phase_ctx import PhaseOutcome

    saved = getattr(runner, "start_question", None) or {}
    status = str(saved.get("status") or "waiting_human")
    reason = str(saved.get("reason") or "")
    step = DecisionStep(runner.ctx, runner._rollback_with_snapshot,
                        round_limit=runner.round_limit)
    step._reason = reason
    step._qid = str(saved.get("qid") or "")
    step._opened_at = None
    step._auto_wait = None
    if status == "waiting_limit":
        try:
            waits = saved.get("waits")
            step._auto_wait = int(waits) if waits is not None else 0
        except (TypeError, ValueError):
            step._auto_wait = 0
    st = runner.ctx.st
    return PhaseOutcome(
        status=status, reason=reason,
        user_question=st.user_question, rounds=runner.state.round,
        escalated=st.escalated, stop_text="", decider=step,
    )
