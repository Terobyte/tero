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


def phase_transition(
    ctx: PhaseContext, *, status: str, reason: str, text: str, ref: str = "", role: str = "judge"
) -> None:
    ctx.ledger.event(
        "judge_pending",
        status=status,
        reason=reason,
        ref=ref,
        text=text,
        round=ctx.st.round,
    )
    ctx.update_state(
        status=status,
        reason=reason,
        text=text,
        snapshot_ref=ref,
        doing="стоп",
    )
    ctx.emit(
        "stop",
        status=status,
        reason=reason,
        ref=ref,
        role=role,
        text=text,
    )


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
