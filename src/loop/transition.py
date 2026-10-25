"""Status transitions: state.json + stream.jsonl + ledger in one place.

The phase and resume copies differ (write order, event kinds, error
handling) and stay separate here; unifying them is not this plan.
"""

from __future__ import annotations

import os
from typing import Callable

from src.loop.ledger import Ledger
from src.loop.phase_ctx import PhaseContext
from src.loop.state import RunState, Status
from src.loop.stream import Stream


_UNSET: Any = object()


def phase_transition(
    ctx: PhaseContext, *, status: str, reason: str, text: str, ref: str = "", role: str = "judge",
    event: str = "stop", kind: str = "judge_pending", doing: str = "стоп",
    question: Any = _UNSET, tree_sha: Any = _UNSET, decision_seq: Any = _UNSET,
    question_id: Any = _UNSET, debt: Any = _UNSET, answer: Any = _UNSET,
) -> None:
    """Single place writing status to state.json, stream.jsonl and ledger.

    Optional fields (question, tree_sha, decision_seq, question_id, debt,
    answer) are written only when passed; passing None explicitly clears
    the field in state.json.
    """
    state_fields: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "text": text,
        "doing": doing,
    }
    if ref:
        state_fields["snapshot_ref"] = ref
    for name, value in (
        ("question", question), ("tree_sha", tree_sha),
        ("decision_seq", decision_seq), ("question_id", question_id),
        ("debt", debt), ("answer", answer),
    ):
        if value is not _UNSET:
            state_fields[name] = value
    ctx.ledger.event(
        kind,
        status=status,
        reason=reason,
        ref=ref,
        text=text,
        round=ctx.st.round,
    )
    ctx.update_state(**state_fields)
    emit_fields: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "ref": ref,
        "role": role,
        "text": text,
    }
    if question is not _UNSET and question is not None:
        emit_fields["question"] = question
        try:
            qid = question.get("id") if isinstance(question, dict) else question_id
        except Exception:
            qid = question_id
        if qid is not None and qid is not _UNSET:
            emit_fields["question_id"] = qid
    elif question_id is not _UNSET and question_id is not None:
        emit_fields["question_id"] = question_id
    ctx.emit(event, **emit_fields)


def resume_transition(
    done_io: RunState,
    stream: Stream,
    ledger: Callable[[], Ledger],
    *,
    phase_title: str,
    pid_started: float | None,
    status: str,
    reason: str,
    text: str,
    round_no: int = 0,
    run_end: bool = False,
) -> None:
    done_io.update(
        status=status, reason=reason, text=text,
        doing="стоп" if status != Status.RUNNING.value else "resume",
        pid=os.getpid(),
        pid_started=pid_started,
    )
    try:
        stream.emit(
            "resume" if status == Status.RUNNING.value else "stop",
            status=status, reason=reason,
            phase=phase_title, round=round_no, role="judge",
            text=text,
        )
    except Exception:
        pass
    try:
        kind = (
            "resume" if status == Status.RUNNING.value else (
                "done" if status == Status.DONE.value else "stop"
            )
        )
        ledger().event(
            kind,
            status=status, reason=reason, text=text, round=round_no,
        )
    except Exception:
        pass
    if run_end:
        try:
            ledger().event("run_end", reason=reason)
        except Exception:
            pass
