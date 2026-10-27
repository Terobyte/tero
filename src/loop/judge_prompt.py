"""The judge's prompt and how its verdict is reported (state.json, console)."""

from __future__ import annotations

from typing import Any

from src.loop.ledger import Ledger
from src.loop.phase_ctx import _JUDGE_MAX_FAILURES, PhaseContext, PhaseState
from src.loop.prompts import judge_prompt
from src.loop.schemas import ValidateCtx


def build_prompt(ctx: PhaseContext, reask_question: str) -> str:
    # Текст с телефона — данные для игрока, не команда судье: сюда не попадает.
    # CLI resume-ответ — прежнее поведение (судья видит ответ владельца).
    phone = bool(getattr(ctx.st, "answer_from_phone", False))
    return judge_prompt(
        preamble=ctx.preamble,
        contract=ctx.contract,
        open_findings=[item.as_dict() for item in ctx.st.open_findings],
        protected_ids=list(ctx.st.protected_by_id),
        suite_delta=ctx.st.suite_delta,
        needs_user_question=reask_question,
        answer="" if phone else ctx.st.answer,
        gate_fact=gate_fact_of(ctx.st),
        from_phone=phone,
    )


def gate_fact_of(st: PhaseState) -> str:
    """Факт последнего гейта для промпта судьи. Пусто — гейт ещё не прошёл."""
    from src.loop.gate_scope import format_gate_fact
    from src.loop.phase_ctx import _tail

    if not st.gate_command and not st.gate_output:
        return ""
    scope = st.gate_scope or "full"
    return format_gate_fact(
        command=st.gate_command,
        scope=scope,
        files=list(st.gate_files),
        exit_code=st.gate_exit_code,
        tail=_tail(st.gate_output),
    )


def validate_ctx(
    st: PhaseState, ledger: Ledger | None = None, phase: str = ""
) -> ValidateCtx:
    return ValidateCtx(
        open_ids=[item.id for item in st.open_findings],
        protected_ids=set(st.protected_by_id),
        ledger=ledger,
        round=st.round,
        phase=phase,
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
