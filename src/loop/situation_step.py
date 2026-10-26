"""Точка вызова ситуации раунда (план 08-2): jev-тень; diagnose — фаза 3."""

from __future__ import annotations

import os
from typing import Any, Callable

from src.loop import git as git_mod
from src.loop import jev as jev_mod
from src.relay.redact import redact, redact_strict


def eligible_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Тот же список, что берёт _new_blocking: без discard_test."""
    return [f for f in findings if f.get("route") != "discard_test"]


def _history_closed(ctx: Any, n_closed: int) -> int:
    """findings_fixed_in_earlier_rounds: закрытые за всю фазу, не только этот вердикт.

    next_id растёт на каждую новую находку (1-based), open_findings — текущие,
    значит закрытых за фазу = next_id - 1 - len(open_findings); n_closed вердикта
    прибавляем только если находка ещё не снята с open_findings.
    """
    open_n = len(getattr(getattr(ctx, "st", None), "open_findings", None) or [])
    next_id = int(getattr(getattr(ctx, "st", None), "next_id", 1) or 1)
    return max(0, next_id - 1 - open_n) + max(0, int(n_closed or 0))


def maybe_query_jev(
    ctx: Any,
    findings: list[dict[str, Any]],
    *,
    n_closed: int = 0,
    post: Callable[..., tuple[int, dict]] | None = None,
    timeout_s: float = 20.0,
) -> dict[str, Any] | None:
    """jev при jev_enabled; сбой — событие jev_sev с error, раунд идёт дальше.

    Без secret_paths запрос не уходит: секретный путь в problem/expected
    иначе уйдёт наружу без redact.
    """
    if not getattr(getattr(ctx, "cfg", None), "jev_enabled", False):
        return None
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    phase = str(getattr(getattr(ctx, "phase", None), "title", "") or "")
    round_no = int(getattr(getattr(ctx, "st", None), "round", 0) or 0)
    try:
        secret_paths = list(git_mod.secret_paths(getattr(ctx, "repo", "") or ""))
    except Exception:
        # Списка нет — запрос наружу не уходит, phase в событие — только строгим фильтром.
        res: dict[str, Any] = {"error": "secret paths unavailable"}
        return _emit(ctx, redact_strict(phase), round_no, res)
    res = jev_mod.request_sev(eligible_findings(findings), phase=phase, round_no=round_no, rounds_spent=max(0, round_no - 1), n_closed=_history_closed(ctx, n_closed), secret_paths=secret_paths, api_key=api_key, post=post, timeout_s=timeout_s)
    return _emit(ctx, redact(phase, secret_paths), round_no, res)


def _emit(ctx: Any, phase: str, round_no: int, res: dict[str, Any]) -> dict[str, Any]:
    """Полный итог — только в ledger; в stream — короткая строка."""
    error = res.get("error")
    try:
        if error:
            ctx.ledger.event(
                "jev_sev", error=error, phase=phase, role="judge",
                round=round_no,
            )
            ctx.emit("jev_sev", error=error, phase=phase, round=round_no, role="judge", text="jev: сбой")
        else:
            ctx.ledger.event(
                "jev_sev", sev=res.get("sev"), tok=res.get("tok", 0), phase=phase,
                role="judge", round=round_no,
            )
            ctx.emit("jev_sev", phase=phase, round=round_no, role="judge", text="jev: тяжесть")
    except Exception:
        pass
    return res
