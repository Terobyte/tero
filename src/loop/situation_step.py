"""Точка вызова ситуации раунда (план 08-2): jev-тень; diagnose — фаза 3."""

from __future__ import annotations

import os
from typing import Any, Callable

from src.errors import scrub_secrets
from src.loop import git as git_mod
from src.loop import jev as jev_mod
from src.loop import situation as situation_mod
from src.loop.state import RunState
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


def _history_path(ctx: Any) -> Any:
    try:
        return ctx.run_dir / "state.json"
    except Exception:
        return None


def situation_state(ctx: Any, phase_index: int | None = None) -> dict[str, Any]:
    """История фазы для diagnose: state.json под ключом situation[N]."""
    try:
        idx = ctx.phase.index if phase_index is None else phase_index
    except Exception:
        idx = 0
    key = f"situation[{idx}]"
    try:
        state = RunState.read(_history_path(ctx)) or {}
    except Exception:
        return {"rounds": [], "opus_round": None, "owner_round": None, "sonnet_n": 0}
    hist = state.get(key)
    if not isinstance(hist, dict):
        return {"rounds": [], "opus_round": None, "owner_round": None, "sonnet_n": 0}
    rounds = hist.get("rounds")
    return {
        "rounds": list(rounds) if isinstance(rounds, list) else [],
        "opus_round": hist.get("opus_round"),
        "owner_round": hist.get("owner_round"),
        "sonnet_n": int(hist.get("sonnet_n") or 0),
    }


def _append_history(ctx: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Дописать раунд в situation[N]; не роняет прогон."""
    try:
        idx = ctx.phase.index
    except Exception:
        idx = 0
    key = f"situation[{idx}]"
    path = _history_path(ctx)
    hist = situation_state(ctx, idx)
    hist["rounds"] = [*hist["rounds"], row]
    hist["sonnet_n"] = sum(1 for r in hist["rounds"] if r.get("player") == "sonnet")
    try:
        state = RunState.read(path) or {}
    except Exception:
        state = {}
    state[key] = hist
    try:
        RunState(path).update(**{key: hist})
    except Exception:
        pass
    return hist


def _actual_player(ctx: Any) -> str:
    """Фактический игрок раунда: играл ли этот раунд escalation_provider.

    Сравниваем player_identity() при том же st.round с escalation-тройкой
    из cfg (флаг escalated в режиме round не сбрасывается — флаг не читаем).
    Без cfg — с дефолтами Config (escalation claude/opus).
    """
    try:
        current = ctx.player_identity()
    except Exception:
        return "muse"
    try:
        if isinstance(current, tuple):
            model = str(current[0] if len(current) > 0 else "")
            provider = str(current[1] if len(current) > 1 else "")
        elif isinstance(current, dict):
            model = str(current.get("model", ""))
            provider = str(current.get("provider", ""))
        elif isinstance(current, (list,)):
            model = str(current[0] if len(current) > 0 else "")
            provider = str(current[1] if len(current) > 1 else "")
        else:
            return "muse"
    except Exception:
        return "muse"
    try:
        cfg = getattr(ctx, "cfg", None)
        esc_provider = getattr(cfg, "escalation_provider", None)
        esc_model = getattr(cfg, "escalation_model", None)
        if esc_provider is None or esc_model is None:
            from src.config import Config as _Config
            esc_provider, esc_model = _Config().escalation_provider, _Config().escalation_model
    except Exception:
        return "muse"
    if esc_provider and provider == esc_provider and model == esc_model:
        return "sonnet"
    return "muse"


def record_situation(
    ctx: Any,
    findings: list[dict[str, Any]],
    previous: dict[str, str],
    data: dict[str, Any],
    raw_level: dict[str, str | None] | None = None,
    *,
    post: Callable[..., tuple[int, dict]] | None = None,
    timeout_s: float = 20.0,
) -> dict[str, Any] | None:
    """Тень situation после NO_SHIP: diagnose + событие + история. Результат никто не читает."""
    round_no = int(getattr(getattr(ctx, "st", None), "round", 0) or 0)
    n_findings = len(eligible_findings(findings))
    resolved = sum(1 for v in previous.values() if v == "resolved")
    still_open = sum(1 for v in previous.values() if v == "still_open")
    def _key(item: dict[str, Any]) -> str:
        return str(item.get("file", "")) + "\x00" + str(item.get("problem", ""))
    if raw_level is None:
        raw_level = {}
        for item in findings or []:
            if isinstance(item, dict):
                raw_level[_key(item)] = item.get("level")
    level_by_key: dict[str, str | None] = {}
    for item in findings or []:
        if isinstance(item, dict):
            level_by_key[_key(item)] = item.get("level")
    for key, level in (raw_level or {}).items():
        level_by_key.setdefault(key, level)
    judge_level = [level_by_key.get(_key(item)) for item in eligible_findings(findings)]
    player = _actual_player(ctx)
    hist = _append_history(
        ctx,
        {
            "round": round_no,
            "n_findings": n_findings,
            "resolved": resolved,
            "still_open": still_open,
            "sev": [],
            "judge_level": judge_level,
            "player": player,
        },
    )
    sev = maybe_query_jev(ctx, findings, n_closed=resolved, post=post, timeout_s=timeout_s)
    for row in hist["rounds"]:
        if isinstance(row, dict) and row.get("round") == round_no and not row.get("sev"):
            row["sev"] = (sev or {}).get("sev") or []
    try:
        idx = ctx.phase.index
    except Exception:
        idx = 0
    try:
        RunState(_history_path(ctx)).update(**{f"situation[{idx}]": hist})
    except Exception:
        pass
    res: dict[str, Any] = {"rounds": hist["rounds"]}
    if hist.get("opus_round") is not None:
        res["opus_round"] = hist["opus_round"]
    if hist.get("owner_round") is not None:
        res["owner_round"] = hist["owner_round"]
    try:
        res = {**res, **situation_mod.diagnose(hist["rounds"], situation_mod.Cfg())}
    except Exception as exc:
        err = "diagnose failed"
        try:
            scrub_secrets(str(exc) or type(exc).__name__)
        except Exception:
            pass
        _emit_situation(ctx, round_no, {"error": err})
        return {"error": err}
    _emit_situation(ctx, round_no, res)
    return res


def _emit_situation(ctx: Any, round_no: int, res: dict[str, Any]) -> None:
    """Событие ledger situation + строка в stream. Поведение не меняется."""
    error = res.get("error")
    try:
        if error:
            ctx.ledger.event(
                "situation", error=error, round=round_no, role="judge",
                phase=str(getattr(getattr(ctx, "phase", None), "title", "") or ""),
                action="", reason="", situation="",
            )
            ctx.emit("situation", error=error, round=round_no, role="judge",
                     text="ситуация: сбой")
        else:
            ctx.ledger.event(
                "situation", situation=res.get("situation"), action=res.get("action"),
                reason=res.get("reason"), round=round_no, role="judge",
                phase=str(getattr(getattr(ctx, "phase", None), "title", "") or ""),
            )
            ctx.emit("situation", situation=res.get("situation"), action=res.get("action"),
                     reason=res.get("reason"), round=round_no, role="judge",
                     text="ситуация")
    except Exception:
        pass


def next_player(ctx: Any) -> tuple[str, str, str]:
    """Игрок следующего раунда после NO_SHIP при situational.

    diagnose читает историю state.json (sonnet_n — лимит по всей фазе,
    переживает more/model). polish/stuck_strong/mole/ask_owner ведут себя
    как converging (действие — фаза 11): играет игрок плана.
    """
    cfg = getattr(ctx, "cfg", None)
    plan = ("", "", "")
    try:
        plan = (cfg.player_model, cfg.player_provider, cfg.player_effort)
    except Exception:
        pass
    try:
        hist = situation_state(ctx)
    except Exception:
        return plan
    rounds = hist.get("rounds") or []
    if not rounds:
        return plan
    try:
        raw_max = getattr(cfg, "sonnet_max", 2)
        try:
            limit = max(0, int(raw_max))
        except (TypeError, ValueError):
            limit = 2
        res = situation_mod.diagnose(
            rounds,
            situation_mod.Cfg(sonnet_max=limit),
            opus_round=hist.get("opus_round"),
            owner_round=hist.get("owner_round"),
        )
    except Exception:
        return plan
    if res.get("situation") == "stuck":
        try:
            esc = (cfg.escalation_model, cfg.escalation_provider, cfg.escalation_effort)
        except Exception:
            return plan
        if esc[0] and esc[1]:
            return (str(esc[0]), str(esc[1]), str(esc[2] or ""))
    return plan


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
