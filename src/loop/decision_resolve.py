"""DecisionPoint resolution: skip-to-debt and answer handling.

Extracted from decision_step.py (ceiling room for the stop flag);
DecisionStep keeps thin delegating methods with the same names.
"""

from __future__ import annotations

from typing import Any

from src.loop.decision_actions import (
    apply_action,
    judge_pending_action,
    wait_limit,
)
from src.loop.phase_ctx import PhaseOutcome
from src.loop.state import RunState, Status
from src.loop.transition import phase_transition
from src.loop.watchdog import stop_requested


def _skip_to_debt(step: Any, reason: str, qid: str, who: str) -> PhaseOutcome:
    try:
        step._snap = RunState.read(step.ctx.run_dir / "state.json") or {}
    except Exception:
        step._snap = {}
    try:
        ref = step.locked_rollback(reason)
    except Exception:
        ref = None
    step._repair_after_rollback()
    if ref is None:
        return step.stop_no_snapshot(reason)
    step._restore_journal()
    try:
        step.ctx.ledger.event("rollback", reason=reason, round=step.ctx.st.round)
    except Exception:
        pass
    try:
        findings = [f.as_prompt() for f in (step.ctx.st.open_findings or [])[-5:]]
    except Exception:
        findings = []
    entry = {"phase": step.ctx.phase.index + 1, "reason": reason,
             "findings": findings, "who": who, "ref": ref or ""}
    try:
        cur = RunState.read(step.ctx.run_dir / "state.json") or {}
        debt = list(cur.get("debt") or [])
        debt.append(entry)
    except Exception:
        debt = [entry]
    try:
        step.ctx.st.answer = ""
    except Exception:
        pass
    phase_transition(
        step.ctx, status=Status.RUNNING.value, reason=reason,
        text=f"фаза {step.ctx.phase.index + 1} в долг",
        role="judge", event="skip", kind="skip", doing="фаза в долг",
        question=None, answer="", debt=debt, question_id=qid,
    )
    return PhaseOutcome(status="skip", reason=reason, rounds=step.ctx.st.round,
                        escalated=step.ctx.st.escalated, stop_text="")


def resolve_step(step: Any) -> PhaseOutcome:
    """Дождаться ответа на открытый halt() вопрос и применить его."""
    reason, qid = step._reason, step._qid
    try:
        if stop_requested(step.ctx.run_dir):
            reason = "user_stop"
    except Exception:
        pass
    if reason == "user_stop":
        ref = step._rollback_keep_journal(reason)
        t = "снимок не сделан, дерево не тронуто" if ref is None else step.halt_text(reason, ref)
        step.write_stopped(reason, t)
        try:
            step._restore_journal()
        except Exception:
            pass
        return PhaseOutcome(
            status="stop", reason=reason, user_question=step.ctx.st.user_question,
            rounds=step.ctx.st.round, escalated=step.ctx.st.escalated, stop_text=t,
        )
    if step._auto_wait is not None:
        return wait_limit(step, reason, qid, step._auto_wait)
    opened_at = getattr(step, "_opened_at", None)
    ans = step.wait_answer(qid, opened_at)
    if ans is None:
        return step._old_halt(reason)
    if isinstance(ans, PhaseOutcome):  # F28: автопилот уже вернул стоп.
        return ans
    action = (ans.action or "").strip()
    who = (ans.who or "agent").strip() or "agent"
    if action == "stop":
        try:
            if stop_requested(step.ctx.run_dir):
                reason = "user_stop"
        except Exception:
            pass
        ref = step._rollback_keep_journal(reason)
        t = "снимок не сделан, дерево не тронуто" if ref is None else step.halt_text(reason, ref)
        step.write_stopped(reason, t)
        step._restore_journal()
        return PhaseOutcome(
            status="stop", reason=reason, user_question=step.ctx.st.user_question,
            rounds=step.ctx.st.round, escalated=step.ctx.st.escalated, stop_text=t,
        )
    if action == "skip":
        return _skip_to_debt(step, reason, qid, who)
    if action == "answer":
        txt = (ans.text or "").strip()
        from_phone = (getattr(ans, "via", "") or "") == "relay"
        try:
            step.ctx.st.answer = txt
            step.ctx.st.answer_from_phone = bool(from_phone)
        except Exception:
            pass
        phase_transition(
            step.ctx, status=Status.RUNNING.value, reason=reason, text=txt,
            role="judge", event="answer", kind="answer", doing="новый раунд",
            question=None, answer=txt, question_id=qid,
            extra={"answer_from_phone": bool(from_phone),
                   "answer_phase": step.ctx.phase.index},
        )
        return PhaseOutcome(
            status="answer", reason=reason, rounds=step.ctx.st.round,
            escalated=step.ctx.st.escalated, stop_text="", answer=txt,
        )
    if reason == "judge_pending" and action in ("retry", "accept", "reject"):
        got = judge_pending_action(step, action, reason, qid, ans)
        if got is not None:
            return got
    out = apply_action(step, action, reason, qid, ans)
    return out if out is not None else step._old_halt(reason)
