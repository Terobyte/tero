"""Autopilot fallback answer: choose + inbox delivery.

Extracted from autopilot.py (ceiling room for the stop flag);
autopilot.py keeps thin wrappers with the same names.
"""

from __future__ import annotations

from typing import Any

from src.loop.autopilot import (
    NOT_CHAIN_TEXT,
    _phase_of,
    choose,
    notify_once,
)
from src.loop.inbox import Inbox, write_inbox_file
from src.relay.poller import adapter_for
from src.loop.state import RunState


def _stop_outcome(step: Any, reason: str, text: str) -> Any:
    from src.loop.phase_ctx import PhaseOutcome as _Outcome
    st = step.ctx.st
    return _Outcome(status="stop", reason=reason, user_question=st.user_question,
                    rounds=st.round, escalated=st.escalated, stop_text=text)


def autopilot_answer(step: Any, qid: str, reason: str) -> Any:
    ctx = step.ctx
    try:
        state = RunState.read(ctx.run_dir / "state.json") or {}
    except Exception:
        state = {}
    try:
        n = _phase_of(qid) or ctx.phase.index + 1
    except Exception:
        n = ctx.phase.index + 1
    ans = choose({"reason": reason, "question_id": qid, "id": qid, "phase": n}, state)
    if ans.action == "stop" and (ans.why or "") == "not_chain":
        try:
            notify_once(step.relay, ctx.run_dir, f"{reason}:not_chain", NOT_CHAIN_TEXT,
                        now=step._now())
            got = _inbox_autopilot(ctx, step, ans, qid)
        except Exception:
            got = None
        decided = got is not None
        snap = RunState.read(ctx.run_dir / "state.json") or {}
        try:
            ref = step.locked_rollback(reason)
        except Exception:
            ref = None
        step._repair_after_rollback()
        if decided:
            step._snap = snap
            step._restore_journal()
        try:
            ctx.ledger.event("rollback", reason=reason, round=ctx.st.round)
        except Exception:
            pass
        text = step.halt_text(reason, ref) if ref else step.halt_text(reason, None)
        if ref is None and not decided:
            text = "снимок не сделан, дерево не тронуто"
        step.write_stopped(reason, text)
        if decided:
            step._restore_journal()
            try:  # F16: .result атомарно через fsio.
                write_inbox_file(ctx.run_dir, f"{ans.uuid}.result", "принято")
            except Exception:
                pass
        return _stop_outcome(step, reason, text)
    try:
        return _inbox_autopilot(ctx, step, ans, qid)
    except Exception:
        return ans


def _inbox_autopilot(ctx: Any, step: Any, ans: Any, qid: str) -> Any:
    import json as _j

    ans.uuid = ans.uuid or f"autopilot-{qid}"
    try:
        write_inbox_file(ctx.run_dir, f"{ans.uuid}.json", _j.dumps(
            {"uuid": ans.uuid, "question_id": qid, "action": ans.action,
             "text": ans.text or "", "who": "autopilot", "why": ans.why or ""},
            ensure_ascii=False))
    except Exception:
        pass
    try:
        return Inbox(ctx.run_dir).take(qid, adapter_for(ctx.run_dir, step.relay)) or ans
    except Exception:
        return ans
