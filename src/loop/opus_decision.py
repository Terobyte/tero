"""Opus-решение (план 08-11): один вызов роли `decider`, `claude:opus`.

Вызов собирает этот модуль, `agents.py` не тронут: существующий раннер
провайдера, текстовый JSON-ответ `{decision: ship_with_debt|more|ask_owner, why}`,
разбор как у судьи на текстовой схеме, таймаут 10 мин. Сбой вызова
(таймаут, не-JSON, нет CLI) → `ask_owner`. `stop` во время вызова — убить
процесс и вернуть `None` (вызывающий выходит как обычный `stop`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

DECISION_TIMEOUT_S = 600
VALID_DECISIONS = ("ship_with_debt", "more", "ask_owner")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_prompt(
    *,
    phase: str = "",
    round_no: int = 0,
    findings: list[dict[str, Any]] | None = None,
    situation: str = "",
    reason: str = "",
) -> str:
    """Промпт decider: только данные ситуации, без секретов и телефона."""
    rows = []
    for item in findings or []:
        if isinstance(item, dict):
            lvl = f" [{item.get('level', '')}]" if item.get("level") else ""
            rows.append(f"- {item.get('problem', '')} → {item.get('expected', '')}{lvl}")
    body = "\n".join(rows) or "(нет открытых находок)"
    return (
        "Реши исход фазы. Ответ — строго JSON "
        '{"decision": "ship_with_debt|more|ask_owner", "why": "<коротко>"}.\n'
        f"Фаза: {phase}, раунд: {round_no}, ситуация: {situation} ({reason}).\n"
        f"Открытые находки:\n{body}\n"
        "ship_with_debt — мелочи в долг и шип; more — ещё раунд игрока плана; "
        "ask_owner — вопрос хозяину."
    )


def parse_decision(text: Any) -> dict[str, str] | None:
    """Разбор текстового JSON-ответа, как у судьи на текстовой схеме. `None` — не-JSON."""
    from src.loop.role_report import extract_json

    if isinstance(text, str):
        found = extract_json(text)
        if found is not None:
            text = found
    if isinstance(text, dict):
        data = text
    elif isinstance(text, str):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            match = _JSON_RE.search(text)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
            except (ValueError, TypeError):
                return None
    else:
        return None
    if not isinstance(data, dict):
        return None
    if set(data) != {"decision", "why"}:
        return None
    decision = data.get("decision")
    why = data.get("why")
    if decision not in VALID_DECISIONS:
        return None
    if not isinstance(why, str) or not why.strip():
        return None
    return {"decision": decision, "why": why}


def _kill(agents: Any) -> None:
    """Best-effort: убить процессы вызова при `stop` через ProcessGuard раннера."""
    try:
        guard = getattr(agents, "guard", None)
        if guard is None:
            return
        kill = getattr(guard, "kill_new_processes", None)
        if not callable(kill):
            return
        before = getattr(guard, "snapshot_pids", None)
        if callable(before):
            try:
                kill(before())
                return
            except TypeError:
                pass
        kill()
    except Exception:
        pass


async def resolve(ctx: Any, sit_res: dict[str, Any], raw_level: Any = None) -> str | None:
    """Один вызов Opus по stuck_strong/mole.

    Возврат: "ship" — долг записан (why opus), идти путём SHIP;
    None — more: opus_round записан, играет игрок плана;
    "stop" — ask_owner/сбой/stop: точка решения через halt("stuck").
    """
    from src.loop.stop_step import check_stop

    st = getattr(ctx, "st", None)
    try:
        round_no = int(getattr(st, "round", 0) or 0)
    except (TypeError, ValueError):
        round_no = 0
    try:
        phase_index = int(getattr(getattr(ctx, "phase", None), "index", 0) or 0)
    except (TypeError, ValueError):
        phase_index = 0
    phase_title = str(getattr(getattr(ctx, "phase", None), "title", "") or "")
    rows: list[dict[str, Any]] = []
    for item in list(getattr(st, "open_findings", None) or []):
        if isinstance(item, dict):
            rows.append({"problem": str(item.get("problem", "")),
                         "expected": str(item.get("expected", "")),
                         "level": str(item.get("level", ""))})
        else:
            rows.append({"problem": str(getattr(item, "problem", "")),
                         "expected": str(getattr(item, "expected", "")),
                         "level": str(getattr(item, "level", "") or "")})
    prompt = build_prompt(phase=phase_title, round_no=round_no, findings=rows,
                          situation=str(sit_res.get("situation") or ""),
                          reason=str(sit_res.get("reason") or ""))
    try:
        from src.errors import scrub_secrets

        prompt = scrub_secrets(prompt)
    except Exception:
        pass

    def _stopped() -> bool:
        try:
            return bool(check_stop(ctx.run_dir))
        except Exception:
            return False

    res = await decide(run_dir=ctx.run_dir, agents=ctx.agents, prompt=prompt,
                       stop_checker=_stopped)
    try:
        if _stopped():
            _kill(ctx.agents)
            return "stop"
    except Exception:
        pass
    decision = res.get("decision") if isinstance(res, dict) else None
    try:
        ctx.ledger.event("decider", decision=decision, round=round_no,
                         role="judge", phase=phase_title)
        ctx.emit("decider", decision=decision, round=round_no, role="judge",
                 text=f"opus: {decision}")
    except Exception:
        pass
    if res is None or decision == "ask_owner":
        return "stop"
    if decision == "ship_with_debt":
        from src.loop import debt as debt_mod

        entries = debt_mod.pending_debt_entries(ctx, raw_level, why="opus")
        if len(entries or []) != len(list(getattr(st, "open_findings", None) or [])):
            return "stop"
        try:
            getattr(st, "debt_pending", []).append(
                {"why": "opus", "entries": [dict(e) for e in entries],
                 "raw_level": dict(raw_level or {}) if isinstance(raw_level, dict) else {}}
            )
        except Exception:
            pass
        return "ship"
    if decision == "more":
        from src.loop import situation_step as sit_mod

        try:
            sit_mod.note_opus_round(ctx.run_dir, phase_index, round_no)
        except Exception:
            pass
        return None
    return "stop"


async def decide(
    *,
    run_dir: str | Path,
    agents: Any,
    prompt: str,
    stop_checker: Callable[[], bool] | None = None,
    timeout: float = DECISION_TIMEOUT_S,
) -> dict[str, str] | None:
    """Один вызов Opus. `None` — `stop` во время вызова; сбой — `ask_owner`."""
    import asyncio as _asyncio

    _ = run_dir
    before: set[int] | None = None
    try:
        guard = getattr(agents, "guard", None)
        if guard is not None and hasattr(guard, "snapshot_pids"):
            before = set(guard.snapshot_pids())
    except Exception:
        before = None

    def _kill_before() -> None:
        try:
            guard = getattr(agents, "guard", None)
            kill = getattr(guard, "kill_new_processes", None)
            if callable(kill) and before is not None:
                kill(before)
        except Exception:
            pass

    async def _stop_wait() -> bool:
        try:
            while True:
                if stop_checker is not None and stop_checker():
                    return True
                await _asyncio.sleep(0.2)
        except _asyncio.CancelledError:
            raise
        except Exception:
            return False
    try:
        if stop_checker is not None and stop_checker():
            _kill_before()
            return None
        task = _asyncio.ensure_future(agents.run_role(
            role="decider",
            provider="claude",
            model="opus",
            effort="",
            prompt=prompt,
            timeout=timeout,
        ))
        waiter = _asyncio.ensure_future(_stop_wait()) if stop_checker is not None else None
        data: Any = None
        try:
            if waiter is None:
                data = await task
            else:
                done, _ = await _asyncio.wait(
                    {task, waiter}, return_when=_asyncio.FIRST_COMPLETED)
                if waiter in done:
                    _kill_before()
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass
                    return None
                data = task.result()
        finally:
            if waiter is not None and not waiter.done():
                waiter.cancel()
                try:
                    await waiter
                except BaseException:
                    pass
    except Exception:
        return {"decision": "ask_owner", "why": "opus call failed"}
    try:
        if stop_checker is not None and stop_checker():
            _kill_before()
            return None
    except Exception:
        pass
    if isinstance(data, dict) and data.get("__unavailable__"):
        return {"decision": "ask_owner", "why": "opus unavailable"}
    parsed = parse_decision(data)
    if parsed is None:
        return {"decision": "ask_owner", "why": "opus bad reply"}
    return parsed
