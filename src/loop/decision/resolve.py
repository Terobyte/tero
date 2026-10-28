"""DecisionPoint resolution: skip-to-debt and answer handling.

Extracted from decision_step.py (ceiling room for the stop flag);
DecisionStep keeps thin delegating methods with the same names.
"""

from __future__ import annotations

from typing import Any

from src.loop.decision.actions import (
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
        situational = str(getattr(step.ctx.cfg, "escalation", "round") or "round") == "situational"
    except Exception:
        situational = False
    if situational:
        try:
            got = _skip_ship(step, reason, qid, who)
        except Exception:
            got = None
        if got is not None:
            return got
    return _skip_rollback(step, reason, qid, who)


def _skip_ship(step: Any, reason: str, qid: str, who: str) -> PhaseOutcome | None:
    """Situational skip: долг (why=skip), затем путь шипа фазы 11.

    None — шипать нельзя/не вышло: вызывающий идёт старым путём с откатом.
    Красный полный гейт — тоже старый путь, но с пометкой в долге.
    """
    ctx = step.ctx
    try:
        if step.blocked_secrets():
            return None
    except Exception:
        return None
    if ctx.test_command is None:
        gate = None
    else:
        from src.loop import gate_scope as _gates

        try:
            gate = _gates.full_gate(
                ctx.test_command, ctx.repo, ctx, ctx.ledger, ctx.st,
                timeout_s=int(ctx.cfg.test_timeout_s),
                enabled=ctx.cfg.gate_scope == "changed",
            )
        except Exception:
            return None
    if gate == "red":
        try:
            ctx.st.debt_pending = []
        except Exception:
            pass
        return _skip_rollback(step, reason, qid, who, note="гейт красный, откат")
    if gate not in (None, "green"):
        return None
    from types import SimpleNamespace as _NS

    from src.loop import debt as _debt
    from src.loop import ship_debt as _ship_debt

    try:
        entries = _debt.pending_debt_entries(ctx, None, "skip")
    except Exception:
        return None
    try:
        ctx.st.debt_pending = (
            [{"why": "skip", "entries": [dict(e) for e in entries]}] if entries else []
        )
    except Exception:
        return None
    try:
        ok = _ship_debt.commit_before_ship(_NS(ctx=ctx, state=ctx.st))
    except Exception:
        ok = False
    if not ok:
        return None
    try:
        if step.blocked_secrets():
            try:
                ctx.st.debt_pending = []
            except Exception:
                pass
            return None
    except Exception:
        return None
    try:
        sha = _ship_tree(step)
    except Exception:
        return None
    try:
        ctx.st.answer = ""
    except Exception:
        pass
    phase_transition(
        ctx, status=Status.RUNNING.value, reason=reason,
        text=f"фаза {ctx.phase.index + 1} в долг, работа сохранена",
        role="judge", event="skip", kind="skip", doing="фаза в долг",
        question=None, answer="", question_id=qid,
    )
    return PhaseOutcome(status="skip", reason=reason, rounds=ctx.st.round,
                        escalated=ctx.st.escalated, stop_text="", commit_sha=sha)


def _ship_tree(step: Any) -> str:
    """Коммит работы фазы как в `_PhaseRunner._ship` (без гейта: он уже зелёный)."""
    from src.loop.git import commit as _commit, stage_and_diff
    from src.loop.plan import mark_phase_done as _done

    ctx = step.ctx
    plan_bytes = ctx.plan_path.read_bytes()
    _done(ctx.plan_path, ctx.phase)
    stage_and_diff(ctx.repo, ctx.checkpoint)
    msg = ctx.phase.title.lower()[:60]
    trl = f"Tero-Phase: {ctx.run_dir.name}/{ctx.phase.index + 1}"
    try:
        sha = _commit(ctx.repo, msg, trailer=trl, allow_empty=True)
    except Exception:
        try:
            ctx.plan_path.write_bytes(plan_bytes)
        except Exception:
            pass
        raise
    try:
        ctx.ledger.event("ship", commit=sha, round=ctx.st.round,
                         phase=ctx.phase.title, role="judge")
    except Exception:
        pass
    try:
        ctx.emit("ship", commit=sha, round=ctx.st.round, role="judge")
    except Exception:
        pass
    try:
        ctx.update_state(commit=sha, phase_done=True)
    except Exception:
        pass
    return sha


def _skip_rollback(step: Any, reason: str, qid: str, who: str,
                   note: str | None = None) -> PhaseOutcome:
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
    if note:
        entry["note"] = note
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
