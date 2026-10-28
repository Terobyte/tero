"""Действия точки решения more, model, retry, allow, wait и бюджет продолжений.

Каждое действие отвечает исходом `continue`: session.run_phases запускает
фазу заново с `extra` (номер раунда, лимит, ResumeFrom, роли), дерево
исполнителя на месте, кроме retry после secrets.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from src.config import KNOWN_PROVIDERS, split_role_spec
from src.loop.decision import actions_for
from src.loop.human_key import sign_list, trusted_list
from src.loop.phase_ctx import PhaseOutcome
from src.loop.resume_step import ResumeFrom
from src.loop.state import RunState, Status

MORE_ROUNDS = 5
BUDGET_PER_PHASE = 3
BUDGETED = ("more", "retry", "wait", "model")
LIMIT_WAITS_S = (300, 900, 1800)
LIMIT_REASONS = ("provider_unavailable", "provider_auth", "provider_quota")
MODEL_ROLES = ("player", "tester")


def _limit_sleep(seconds: float) -> None:
    time.sleep(seconds)


def full_actions(reason: str) -> list[str]:
    """Все действия причины, включая judge_pending (фаза 5)."""
    return actions_for(reason)


def judge_pending_action(step: Any, action: str, reason: str, qid: str, ans: Any) -> PhaseOutcome | None:
    """retry — только судья на том же ref; accept/reject — только human (см. inbox)."""
    if action == "retry":
        state = RunState.read(step.ctx.run_dir / "state.json") or {}
        rounds = step.ctx.st.round
        rf = _resume_from(step, max(_limit(step), rounds + 1),
                          ref=state.get("snapshot_ref") or "",
                          base=step.ctx.checkpoint, judge_reuse_ref=True)
        return _go_on(step, reason, qid, "retry", "повтор вызова судьи",
                      {"start_round": rounds, "start_with_judge": True,
                       "start_checkpoint": step.ctx.checkpoint, "resume_from": rf})
    if action == "accept":
        return _accept_manual(step, reason, qid)
    if action == "reject":
        return _reject(step, reason, qid, ans)
    return None


def _accept_manual(step: Any, reason: str, qid: str) -> PhaseOutcome:
    """accept от человека: фаза сдана кодом из snapshot_ref (F3)."""
    from src.loop import snapshot as _snap
    from src.loop.git import commit as _commit, stage_and_diff as _stage
    from src.loop.plan import mark_phase_done as _done
    from src.loop.transition import phase_transition as _pt

    ctx = step.ctx
    state = RunState.read(ctx.run_dir / "state.json") or {}
    ref = state.get("snapshot_ref") or ""
    # B10: only this run's snapshot namespace; state.json lives in the player's tree
    if not ref or not ref.startswith(f"refs/tero/{ctx.run_dir.name}/"):
        return step.stop_no_snapshot(reason)
    try:
        if not _snap.verify(ctx.repo, ref):
            raise RuntimeError(f"snapshot not found: {ref}")
        _snap.restore_snapshot(ctx.repo, ref)
    except Exception:
        return step.stop_no_snapshot(reason)
    _done(ctx.plan_path, ctx.phase)
    _stage(ctx.repo, ctx.checkpoint)
    msg = ctx.phase.title.lower()[:60]
    trl = f"Tero-Phase: {ctx.run_dir.name}/{ctx.phase.index + 1}"
    sha = _commit(ctx.repo, msg, trailer=trl, allow_empty=True)
    ctx.ledger.event("ship", commit=sha, rounds=ctx.st.round, ref=ref)
    ctx.emit("ship", commit=sha, round=ctx.st.round, role="judge")
    ctx.update_state(commit=sha, phase_done=True)
    _pt(ctx, status=Status.RUNNING.value, reason=reason,
        text=f"фаза {ctx.phase.index + 1} сдана вручную",
        role="judge", event="accept", kind="accept", doing="фаза сдана",
        question=None, question_id=qid)
    return PhaseOutcome(status="ship", reason="ship", rounds=ctx.st.round,
                        escalated=ctx.st.escalated, commit_sha=sha)


def _reject(step: Any, reason: str, qid: str, ans: Any) -> PhaseOutcome:
    """reject от человека: новый раунд исполнителя в пределах лимита."""
    rounds = step.ctx.st.round
    rf = _resume_from(step, max(_limit(step), rounds + 1))
    return _go_on(step, reason, qid, "reject", "новый раунд исполнителя",
                  {"start_round": rounds, "resume_from": rf})


def budget_used(state: dict, phase_n: int) -> int:
    raw = state.get("budget") if isinstance(state, dict) else None
    try:
        return int((raw or {}).get(str(phase_n), 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def question_actions(reason: str, state: dict, phase_n: int) -> list[str]:
    """Действия в вопросе: бюджет фазы исчерпан → только skip|stop."""
    full = full_actions(reason)
    if reason == "judge_pending":
        full = ["retry", "skip", "stop"]
    if budget_used(state, phase_n) >= BUDGET_PER_PHASE:
        return [a for a in full if a in ("skip", "stop")]
    return full


def phase_of(question_id: str) -> int:
    try:
        return int(str(question_id).split("-", 1)[0])
    except ValueError:
        return 0


def refuse_model(text: Any) -> str | None:
    """`model <player|tester> <провайдер[:модель]>`: проверка при приёме."""
    words = str(text or "").strip().split()
    if words and words[0].lower() == "judge":
        return "отклонено: судью менять нельзя"
    if len(words) != 2 or words[0].lower() not in MODEL_ROLES:
        return "отклонено: формат model <player|tester> <провайдер[:модель]>"
    provider, _model = split_role_spec(words[1])
    if provider not in KNOWN_PROVIDERS:
        return f"отклонено: неизвестный провайдер {provider or '?'}"
    return None


def refuse_budget(state: dict, question_id: str, action: Any, who: Any) -> str | None:
    if who == "human" or action not in BUDGETED:
        return None
    if budget_used(state, phase_of(question_id)) >= BUDGET_PER_PHASE:
        return "отклонено: бюджет исчерпан"
    return None


def spend_budget(state: dict, question_id: str, action: Any, who: Any) -> dict | None:
    """Новое значение `budget` после принятого ответа, None — не тратится."""
    if who == "human" or action not in BUDGETED:
        return None
    budget = dict(state.get("budget") or {}) if isinstance(state.get("budget"), dict) else {}
    key = str(phase_of(question_id))
    budget[key] = budget_used(state, int(key)) + 1
    return budget


def _resume_from(step: Any, limit: int, **fields: Any) -> ResumeFrom:
    ctx = step.ctx
    return ResumeFrom(
        round_limit=limit, suite_hashes=ctx.suite_hashes, guarded=ctx.guarded,
        link_targets=ctx.link_targets, **fields,
    )


def _limit(step: Any) -> int:
    try:
        return int(step._round_limit()) if step._round_limit else int(step.ctx.cfg.max_rounds)
    except Exception:
        return int(step.ctx.cfg.max_rounds)


def _go_on(step: Any, reason: str, qid: str, action: str, text: str, extra: dict,
           state_extra: dict | None = None) -> PhaseOutcome:
    from src.loop.transition import phase_transition

    phase_transition(
        step.ctx, status=Status.RUNNING.value, reason=reason, text=text,
        role="judge", event=action, kind=action, doing=text,
        question=None, question_id=qid, extra=state_extra,
    )
    return PhaseOutcome(
        status="continue", reason=reason, rounds=step.ctx.st.round,
        escalated=step.ctx.st.escalated, extra=extra,
    )


def _more(step: Any, reason: str, qid: str, ans: Any) -> PhaseOutcome:
    rounds = step.ctx.st.round
    until = rounds + MORE_ROUNDS
    rf = _resume_from(step, until, grace_until=until)
    if reason == "stuck":
        # Ответ more хозяина на точку stuck: окно stuck/mole считается
        # после этого раунда (план 08-11).
        try:
            from src.loop import situation_step as _sit

            _sit.note_owner_round(step.ctx.run_dir, step.ctx.phase.index, rounds)
        except Exception:
            pass
    return _go_on(step, reason, qid, "more", f"ещё {MORE_ROUNDS} раундов",
                  {"start_round": rounds, "resume_from": rf})


def _model(step: Any, reason: str, qid: str, ans: Any) -> PhaseOutcome:
    role, spec = str(ans.text or "").split()[:2]
    provider, model = split_role_spec(spec)
    role = role.lower()
    fields = {f"{role}_provider": provider}
    if model:
        fields[f"{role}_model"] = model
    cfg = dataclasses.replace(step.ctx.cfg, **fields)
    rounds = step.ctx.st.round
    rf = _resume_from(step, max(_limit(step), rounds + 1))
    return _go_on(step, reason, qid, "model", f"{role} → {spec}",
                  {"start_round": rounds, "resume_from": rf, "cfg": cfg})


def _retry(step: Any, reason: str, qid: str, ans: Any) -> PhaseOutcome:
    rounds = step.ctx.st.round
    if reason == "secrets":
        paths = list(step.secret_paths or [])
        ref = step.locked_rollback(reason)
        if ref is None:
            return step.stop_no_snapshot(reason)
        rf = _resume_from(step, rounds + int(step.ctx.cfg.max_rounds), forbidden=paths)
        return _go_on(step, reason, qid, "retry", "фаза заново",
                      {"start_round": rounds, "resume_from": rf})
    rf = _resume_from(step, max(_limit(step), rounds))
    return _go_on(step, reason, qid, "retry", "повтор вызова",
                  {"start_round": max(rounds - 1, 0), "resume_from": rf})


def _allow(step: Any, reason: str, qid: str, ans: Any) -> PhaseOutcome:
    ctx = step.ctx
    path = str(ans.text or "").strip()
    state = RunState.read(ctx.run_dir / "state.json") or {}
    allowed = trusted_list(ctx.run_dir.name, state.get("allowed_secrets"),
                           state.get("allowed_secrets_sig"))
    if path and path not in allowed:
        allowed.append(path)
    rounds = ctx.st.round
    rf = _resume_from(
        step, max(_limit(step), rounds), ref=str(state.get("snapshot_ref") or ""),
        base=ctx.checkpoint, gate_first=True,
    )
    extra = {"start_round": rounds, "start_with_judge": True,
             "start_checkpoint": ctx.checkpoint, "resume_from": rf}
    return _go_on(step, reason, qid, "allow", f"разрешён {path}", extra,
                  {"allowed_secrets": allowed, "allowed_secrets_sig": sign_list(ctx.run_dir.name, allowed)})


def limit_chain(state: dict, phase_n: int) -> int | None:
    """Сколько пауз уже было в цепочке wait этой фазы; None — цепочки нет."""
    chain = state.get("limit_wait") if isinstance(state, dict) else None
    if not isinstance(chain, dict) or chain.get("phase") != phase_n:
        return None
    try:
        return int(chain.get("waits", 0) or 0)
    except (TypeError, ValueError):
        return None


def wait_limit(step: Any, reason: str, qid: str, waits: int) -> PhaseOutcome:
    """Пауза waiting_limit (5 → 15 → 30 мин), затем повтор вызова провайдера."""
    from src.loop.transition import phase_transition

    ctx = step.ctx
    n = ctx.phase.index + 1
    reset = getattr(ctx.st, "last_provider_reset_s", None)
    pause = float(reset) if reset else LIMIT_WAITS_S[min(waits, len(LIMIT_WAITS_S) - 1)]
    phase_transition(
        ctx, status=Status.WAITING_LIMIT.value, reason=reason,
        text=f"жду лимит {pause:g} с ({waits + 1}/{len(LIMIT_WAITS_S)})",
        role=ctx.role or "player", event="waiting_limit", kind="waiting_limit",
        doing="ожидание лимита", question=None, question_id=qid,
        extra={"limit_wait": {"phase": n, "waits": waits + 1}},
    )
    _limit_sleep(pause)
    try:
        ctx.progress()
    except Exception:
        pass
    return _retry(step, reason, qid, None)


def _wait(step: Any, reason: str, qid: str, ans: Any) -> PhaseOutcome:
    return wait_limit(step, reason, qid, 0)


APPLY = {"more": _more, "model": _model, "retry": _retry, "allow": _allow, "wait": _wait}


def apply_action(step: Any, action: str, reason: str, qid: str, ans: Any) -> PhaseOutcome | None:
    fn = APPLY.get(action)
    return fn(step, reason, qid, ans) if fn else None
