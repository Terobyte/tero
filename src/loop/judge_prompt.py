"""The judge's prompt and how its verdict is reported (state.json, console)."""

from __future__ import annotations

from typing import Any

from src.loop.phase_ctx import _JUDGE_MAX_FAILURES, PhaseContext, PhaseState
from src.loop.prompts import judge_prompt
from src.loop.schemas import ValidateCtx


def build_prompt(ctx: PhaseContext, reask_question: str) -> str:
    return judge_prompt(
        preamble=ctx.preamble,
        contract=ctx.contract,
        open_findings=[item.as_dict() for item in ctx.st.open_findings],
        protected_ids=list(ctx.st.protected_by_id),
        suite_delta=ctx.st.suite_delta,
        needs_user_question=reask_question,
        answer=ctx.st.answer,
    )


def validate_ctx(st: PhaseState) -> ValidateCtx:
    return ValidateCtx(
        open_ids=[item.id for item in st.open_findings],
        protected_ids=set(st.protected_by_id),
    )


def verdict_state(data: dict[str, Any]) -> dict[str, Any]:
    """The `verdict` field of state.json: verdict, blocking count, first blocking finding."""
    blocking = [
        item for item in data.get("findings") or [] if item.get("severity") == "blocking"
    ]
    first = blocking[0] if blocking else {}
    return {
        "verdict": data.get("verdict"),
        "blocking": len(blocking),
        "first": {
            "issue": first.get("problem", ""),
            "file": first.get("file", ""),
            "line": first.get("line"),
        },
    }


def _print_verdict(data: dict[str, Any]) -> None:
    findings = data.get("findings") or []
    blocking = [item for item in findings if item.get("severity") == "blocking"]
    print(
        f"  judge: {data.get('verdict')}, blocking {len(blocking)}",
        flush=True,
    )
    for item in blocking[:5]:
        where = item.get("file") or "?"
        if item.get("line"):
            where += f":{item['line']}"
        print(f"    - {where}: {item.get('problem', '')}", flush=True)
    if data.get("user_question"):
        print(f"    вопрос: {data['user_question']}", flush=True)


def _judge_failed(ctx: PhaseContext, attempt: int, error: str) -> None:
    text = f"судья: сбой {attempt}/{_JUDGE_MAX_FAILURES} — {error or 'без деталей'}"
    ctx.ledger.event(
        "judge_failed",
        attempt=attempt,
        error=error,
        round=ctx.st.round,
    )
    ctx.emit(
        "judge_failed",
        attempt=attempt,
        error=error,
        round=ctx.st.round,
        role="judge",
        text=text,
    )
    print(f"  {text}", flush=True)
