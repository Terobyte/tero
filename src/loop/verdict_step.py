"""Применение вердикта судьи (план 08-11): вынесено из judge_step.py под потолок.

JudgeStep хранит тонкие методы-обёртки с теми же именами и реэкспортирует
модуль, чтобы существующие точки подмены (`JudgeStep._apply_verdict` и т.д.)
продолжали работать.
"""

from __future__ import annotations

from typing import Any

from src.loop.findings import findings_key, in_continuation, not_shrunk, stalled
from src.loop.phase_ctx import (
    K_MAX,
    OpenFinding,
    _Next,
    _dump,
)
from src.loop.judge_prompt import (
    _print_verdict,
    verdict_state,
)
from src.loop import situation_step as _situation_step


async def accept_verdict(step: Any, data: dict[str, Any]) -> Any:
    summary = verdict_state(data)
    step.ctx.emit("verdict", verdict=data.get("verdict"), blocking=summary["blocking"],
                  first=summary["first"], round=step.ctx.st.round, role="judge")
    step.ctx.update_state(
        role="judge",
        provider=step.ctx.cfg.judge_provider,
        model=step.ctx.cfg.judge_model,
        verdict=summary,
    )
    step.ctx.ledger.event(
        "judge",
        verdict=data.get("verdict"),
        round=step.ctx.st.round,
    )
    step.ctx.ledger.artifact(step.ctx.round_file("judge.json"), _dump(data))
    _print_verdict(data)
    return await apply_verdict(step, data)


async def apply_verdict(step: Any, data: dict[str, Any]) -> Any:
    verdict = data.get("verdict")
    if verdict == "SHIP":
        return _Next.SHIP
    if verdict == "NEEDS_USER":
        step.ctx.st.user_question = data.get("user_question", "")
        return _Next.USER

    raw_level = {
        item.get("file", "") + "\x00" + item.get("problem", ""): item.get("level")
        for item in data.get("findings") or [] if isinstance(item, dict)
    }
    findings = data.get("findings") or []
    discard_tests(step, findings)
    previous = {row["id"]: row["state"] for row in data.get("previous") or []}
    sit_res = _situation_step.record_situation(step.ctx, findings, previous, data, raw_level) or {}
    if in_continuation(step.ctx.st):
        return await apply_verdict_continuation(step, findings, previous)
    try:
        situational = str(getattr(step.ctx.cfg, "escalation", "round")) == "situational"
    except Exception:
        situational = False
    # situational: stalled() не зовётся, действия — фаза 11.
    if not situational and stalled(step.ctx.st, previous) and step.no_progress():
        return _Next.STOP
    still_open = [
        item
        for item in step.ctx.st.open_findings
        if previous.get(item.id) != "resolved"
    ]
    new_blocking = new_blocking_findings(step, findings)
    if situational:
        # Долг пишется из актуального списка: still_open + новые находки
        # вердикта, иначе в долг попадает прошлый раунд.
        step.ctx.st.open_findings = still_open + new_blocking
        decided = await situational_action(step, sit_res, raw_level)
        if decided is not None:
            return decided
        if any(item.route == "tester" for item in new_blocking) and step.ctx.tester_enabled:
            return _Next.TESTER
        return _Next.NEXT
    step.ctx.st.open_findings = still_open + new_blocking
    if any(item.route == "tester" for item in new_blocking) and step.ctx.tester_enabled:
        return _Next.TESTER
    return _Next.NEXT


async def situational_action(step: Any, sit_res: dict[str, Any], raw_level: dict[str, Any]) -> Any | None:
    """Действия эскалации по diagnose (план 08-11). None — обычный цикл."""
    action = sit_res.get("action")
    if action == "ship_with_debt":
        from src.loop import debt as debt_mod

        entries = debt_mod.pending_debt_entries(step.ctx, raw_level, why="polish")
        if len(entries or []) != len(list(step.ctx.st.open_findings or [])):
            return _Next.NEXT
        if debt_mod.over_cap(step.ctx, len(entries)):
            # кэп долга на прогон: дальше решает хозяин
            try:
                step.ctx.ledger.event("debt_cap", count=len(entries), round=step.ctx.st.round,
                                      role="judge")
            except Exception:
                pass
            return _Next.STOP
        step.ctx.st.debt_pending = [
            {"why": "polish", "entries": [dict(e) for e in entries],
             "raw_level": dict(raw_level or {})}
        ]
        return _Next.SHIP
    if action == "ask_owner":
        # Повторное окно stuck/mole после opus_round/owner_round: решает
        # хозяин через точку решения stuck; more пишет owner_round (F16/F17).
        return _Next.STOP
    if action == "opus_decides":
        from src.loop.decision import opus as opus_mod

        res = await opus_mod.resolve(step.ctx, sit_res, raw_level)
        if res == "ship":
            return _Next.SHIP
        if res == "stop":
            return _Next.STOP
        return None
    return None


async def apply_verdict_continuation(
    step: Any, findings: list[dict[str, Any]], previous: dict[str, str]
) -> Any:
    # В раунде-продолжении _stalled/_no_progress не проверяются:
    # решает счётчик K_max.
    key = findings_key(findings)
    # Закрытая судьёй находка — прогресс, даже если на её месте новая:
    # иначе «закрыл одну, нашёл одну» считается застоем и стирает дерево.
    resolved = any(previous.get(item.id) == "resolved"
                   for item in step.ctx.st.open_findings)
    if not resolved and not_shrunk(step.ctx.st, key):
        step.ctx.st.kmax_flat += 1
    else:
        step.ctx.st.kmax_flat = 0
    step.ctx.st.kmax_prev = key
    if step.ctx.st.kmax_flat >= K_MAX:
        clean_retry(step)
    still_open = [
        item
        for item in step.ctx.st.open_findings
        if previous.get(item.id) != "resolved"
    ]
    new_blocking = new_blocking_findings(step, findings)
    step.ctx.st.open_findings = still_open + new_blocking
    if any(item.route == "tester" for item in new_blocking) and step.ctx.tester_enabled:
        return _Next.TESTER
    return _Next.NEXT


def new_blocking_findings(step: Any, findings: list[dict[str, Any]]) -> list[OpenFinding]:
    new_blocking: list[OpenFinding] = []
    for item in findings:
        if item.get("severity") != "blocking":
            continue
        if item.get("route") == "discard_test":
            continue
        route = item.get("route") or "player"
        if route == "tester" and not step.ctx.tester_enabled:
            route = "player"
        found = OpenFinding(
            id=f"F{step.ctx.st.next_id}",
            severity="blocking",
            route=route,
            file=item.get("file", ""),
            line=item.get("line"),
            problem=item.get("problem", ""),
            expected=item.get("expected", ""),
            level=str(item.get("level") or ""),
        )
        step.ctx.st.next_id += 1
        new_blocking.append(found)
    return new_blocking


def clean_retry(step: Any) -> None:
    ref = step.rollback_with_snapshot("clean_retry")
    step.ctx.ledger.event(
        "clean_retry",
        reason="clean_retry",
        ref=ref,
        round=step.ctx.st.round,
    )
    # Чистая попытка: блок продолжения убирается, старый ref остаётся.
    step.ctx.st.resume_active = False
    step.ctx.st.had_crash = False
    step.ctx.st.kmax_flat = 0
    step.ctx.st.kmax_prev = None


def discard_tests(step: Any, findings: list[dict[str, Any]]) -> None:
    step.ctx.st.discarded = []
    for item in findings:
        if item.get("route") != "discard_test":
            continue
        fid = item.get("id") or ""
        paths = step.ctx.st.protected_by_id.pop(fid, None)
        if not paths:
            continue
        note = "тест снят, чини код"
        for path in paths:
            target = step.ctx.repo / path
            target.unlink(missing_ok=True)
            step.ctx.st.protected.pop(path, None)
            step.ctx.st.discarded.append(f"{fid}: {note}")
        for found in step.ctx.st.open_findings:
            if found.id == fid:
                found.test_paths = []
                found.note = note
