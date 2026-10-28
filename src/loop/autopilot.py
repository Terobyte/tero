"""Цикл human -> agent -> автопилот, аренда агента и wake_command (фаза 4)."""

from __future__ import annotations

import errno
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from src.fsio import atomic_write_json, read_json_dict
from src.loop.decision.actions import BUDGET_PER_PHASE, budget_used
from src.loop.inbox import Inbox
from src.loop.state import RunState, Status, owner_lock
from src.loop.transition import phase_transition
from src.loop.watchdog import stop_requested
from src.relay.iface import Answer, NoopRelay, RelayClient

__all__ = [
    "autopilot_answer", "_inbox_autopilot", "stop_as_answer",
    "wait_answer", "choose", "lease_valid", "write_lease",
]

PROVIDER_REASONS = frozenset({"provider_unavailable", "provider_auth", "provider_quota"})
NO_RELAY_GRACE_S, NOTIFY_COOLDOWN_S = 600, 1800
LEASE_REFRESH_S, LEASE_REFRESH_EVERY_S = 120, 30
NOT_CHAIN_TEXT = "остановил: две фазы подряд не идут"


def _phase_of(qid: Any) -> int:
    try: return int(str(qid).split("-", 1)[0])
    except (TypeError, ValueError): return 0


def _last_autopilot_skip(state: dict, current: int = 0) -> int | None:
    try:
        n = current or _phase_of(state.get("question_id") or "")
        prev = max((_phase_of(d.get("question_id") or "")
                    for d in (state.get("decisions") or [])
                    if isinstance(d, dict) and d.get("who") == "autopilot"
                    and d.get("action") == "skip"), default=0)
        return prev if prev and n and prev == n - 1 else None
    except (TypeError, ValueError):
        return None


def choose(decision: dict, state: dict) -> Answer:
    """Ответ автопилота по таблице спеки; цепочкой не пропускает."""
    reason = (decision or {}).get("reason") or ""
    qid = (decision or {}).get("question_id") or (decision or {}).get("id") or ""
    phase_n = (decision or {}).get("phase")
    try:
        phase_n = int(phase_n) if phase_n is not None else _phase_of(qid)
    except (TypeError, ValueError):
        phase_n = _phase_of(qid)
    prev = _last_autopilot_skip(state or {}, phase_n)
    if prev is not None and phase_n and prev == phase_n - 1:
        return Answer(question_id=qid, action="stop", text=NOT_CHAIN_TEXT,
                      who="autopilot", why="not_chain", uuid=f"autopilot-{qid}")
    if reason == "judge_pending":
        action = "retry" if budget_used(state or {}, phase_n) < BUDGET_PER_PHASE else "skip"
    elif reason in PROVIDER_REASONS:
        action = "skip" if budget_used(state or {}, phase_n) >= BUDGET_PER_PHASE else "wait"
    else:
        action = "skip"
    return Answer(question_id=qid, action=action, text="", who="autopilot",
                  why="provider" if reason in PROVIDER_REASONS else reason,
                  uuid=f"autopilot-{qid}")


def pid_alive(pid: Any) -> bool:
    try:
        target = int(pid)
    except (TypeError, ValueError):
        return False
    if target <= 0:
        return False
    try:
        os.kill(target, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError as exc:  # F26: аренду убивает только ESRCH; EINTR — жив.
        if exc.errno is None or exc.errno == errno.ESRCH:
            return False
    except ValueError:
        return False
    try:
        from psutil import (AccessDenied as _AD, NoSuchProcess as _NS,  # type: ignore
                            Process as _P, STATUS_DEAD as _D, STATUS_ZOMBIE as _Z)
    except Exception:
        return True
    try:
        return _P(target).status() not in (_Z, _D)
    except _NS:
        return False
    except _AD:
        return True
    except Exception:  # F23: вердикт kill(0) уже есть — psutil его не отменяет.
        return True


def read_lease(run_dir: str | Path) -> dict | None:
    return read_json_dict(Path(run_dir) / "lease")


def lease_valid(run_dir: str | Path, now: float | None = None,
                alive: Callable[[Any], bool] | None = None) -> bool:
    data = read_lease(run_dir)
    if not isinstance(data, dict):
        return False
    try:
        until = float(data.get("until", 0) or 0)
    except (TypeError, ValueError):
        return False
    ts = time.time() if now is None else now
    if until <= ts:
        return False
    try:
        return bool((alive or pid_alive)(data.get("pid")))
    except Exception:
        return False


def write_lease(run_dir: str | Path, until: float, pid: int) -> bool:
    try:
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        atomic_write_json(Path(run_dir) / "lease",
                          {"until": until, "pid": int(pid)}, mode=0o600)
        return True
    except (OSError, ValueError, TypeError):
        return False


def extend_from_answer(run_dir: str | Path, agent_timeout_s: float,
                       agent_pid: int | None = None,
                       alive: Callable[[Any], bool] | None = None) -> None:
    try:
        given = int(agent_pid or 0)
    except (TypeError, ValueError):
        given = 0
    if given > 0:
        write_lease(run_dir, time.time() + float(agent_timeout_s), given)
        return
    check = alive or pid_alive
    try:
        keep = int((read_lease(run_dir) or {}).get("pid", 0) or 0)
    except (TypeError, ValueError):
        return
    if keep > 0 and check(keep):
        write_lease(run_dir, time.time() + float(agent_timeout_s), keep)


def should_notify(run_dir: str | Path, reason: str, now: float | None = None) -> bool:
    try:
        ts = time.time() if now is None else now
        prev = (read_json_dict(Path(run_dir) / ".notify_cache") or {}).get(reason)
        return prev is None or ts - float(prev or 0) >= NOTIFY_COOLDOWN_S
    except (TypeError, ValueError):
        return True


def mark_notified(run_dir: str | Path, reason: str, now: float | None = None) -> None:
    try:
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        fp = Path(run_dir) / ".notify_cache"
        data = read_json_dict(fp) or {}
        data[reason] = time.time() if now is None else now
        atomic_write_json(fp, data, mode=0o600)
    except OSError:
        pass


def notify_once(relay: RelayClient, run_dir: str | Path, reason: str, text: str,
                now: float | None = None) -> bool:
    if not should_notify(run_dir, reason, now):
        return False
    try:
        ok = bool(relay.notify(text))
    except Exception:
        return False
    if ok:
        mark_notified(run_dir, reason, now)
    return ok


def is_noop(relay: Any) -> bool:
    return isinstance(relay, NoopRelay)  # noqa: F401 — точка подмены из code-map


def agent_timeout_of(run_dir: str | Path, default: float = 1800) -> float:
    try:  # state.json хранит снимок config прогона (wait.py).
        cfg = (RunState.read(Path(run_dir) / "state.json") or {}).get("config")
        return float(cfg.get("agent_timeout_s", default) if isinstance(cfg, dict) else default)
    except (TypeError, ValueError):
        return default


def format_wake_command(tpl: str, run_dir: str | Path, qid: str) -> str:
    return str(tpl).replace("{run_dir}", str(run_dir)).replace("{question_id}", str(qid))


def woken_pid(run_dir: str | Path, question_id: str) -> int | None:
    return int(_wake_marker(run_dir, question_id) or 0) or None


def _wake_marker(run_dir: str | Path, question_id: str) -> Any:
    try:
        w = (RunState.read(Path(run_dir) / "state.json") or {}).get("woken")
        return w.get(question_id) if isinstance(w, dict) else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _remember_wake(run_dir: str | Path, question_id: str, pid: int) -> bool:
    try:  # F27: False, если маркер не зафиксирован — Popen не запускаем.
        # F30: чтение-обновление-проверка под общим owner_lock, без отката
        # всего state.json — чужая запись между update и проверкой не теряется.
        fp = Path(run_dir) / "state.json"
        with owner_lock(fp):
            w = dict((RunState.read(fp) or {}).get("woken") or {})
            RunState(fp).update(woken={**w, question_id: pid})
            return _wake_marker(run_dir, question_id) == pid
    except Exception:
        return False


def maybe_wake(run_dir: str | Path, question_id: str, wake_command: str,
               agent_timeout_s: float, popen: Any = subprocess.Popen,
               now: float | None = None,
               alive: Callable[[Any], bool] | None = None) -> int | None:
    if ((wake_command or "").strip() and _wake_marker(run_dir, question_id) is None
            and not lease_valid(run_dir, now=now, alive=alive)):
        if not _remember_wake(run_dir, question_id, 0):  # F24/F27: метка до Popen
            return None
        try:
            proc = popen([format_wake_command(p, run_dir, question_id)
                          for p in shlex.split(wake_command)],
                         start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
            pid = int(getattr(proc, "pid", 0) or 0)
        except Exception:
            return None
        if pid > 0:
            ts = time.time() if now is None else now
            write_lease(run_dir, ts + float(agent_timeout_s), pid)
            _remember_wake(run_dir, question_id, pid)
            return pid
    return None


def _cfg_num(cfg: Any, name: str, default: float) -> float:
    try: return float(getattr(cfg, name, default))
    except (TypeError, ValueError): return default


def _to_agent(ctx: Any, state: dict, qid: str, reason: str) -> str:
    try:
        phase_transition(ctx, status=Status.WAITING_AGENT.value,
            reason=reason or state.get("reason") or "", text=state.get("text") or "",
            role="judge", event="waiting_agent", kind="waiting_agent",
            doing="ожидание агента", question=state.get("question"), question_id=qid)
    except Exception:
        return ""
    try:  # F14: статус после перехода
        return (RunState.read(ctx.run_dir / "state.json") or {}).get("status") or ""
    except Exception:
        return ""


def wait_answer(step: Any, qid: str, opened_at: float | None = None) -> Any:
    """Цикл ожидания владельца: human -> agent -> автопилот."""
    ctx, cfg, reason = step.ctx, step.ctx.cfg, step._reason
    human_t = _cfg_num(cfg, "human_timeout_s", 900)
    agent_t = _cfg_num(cfg, "agent_timeout_s", 1800)
    grace = _cfg_num(cfg, "autopilot_grace_s", 900)
    opened = step._now() if opened_at is None else opened_at
    warned, warned_at = False, 0.0
    while True:
        try:
            if stop_requested(ctx.run_dir):
                return stop_as_answer(step, qid)
        except Exception:
            pass
        try:
            ans = Inbox(ctx.run_dir).take(qid, step.relay)
        except Exception:
            ans = None
        if ans is not None:
            if (ans.who or "") == "agent":
                try:
                    extend_from_answer(ctx.run_dir, agent_t,
                                       agent_pid=getattr(ans, "agent_pid", None))
                except Exception:
                    pass
            return ans
        try:
            ctx.progress()
        except Exception:
            pass
        try:
            state = RunState.read(ctx.run_dir / "state.json") or {}
        except Exception:
            state = {}
        status_now, now = state.get("status"), step._now()
        if (state.get("question") or {}).get("id") != qid:
            opened, warned = now, False
        if status_now == Status.WAITING_AGENT.value:
            opened = min(opened, now - human_t)
        elif now - opened >= human_t:
            status_now = _to_agent(ctx, state, qid, reason)
        leased = lease_valid(ctx.run_dir, now=now)
        # F14: wake — только в waiting_agent; новую аренду учесть сразу.
        if not leased and status_now == Status.WAITING_AGENT.value:
            try:
                leased = bool(maybe_wake(ctx.run_dir, qid,
                    str(getattr(cfg, "wake_command", "") or ""), agent_t,
                    popen=step._popen or subprocess.Popen, now=now))
            except Exception:
                pass
        if is_noop(step.relay) and not leased and now - opened >= NO_RELAY_GRACE_S:
            return autopilot_answer(step, qid, reason or state.get("reason") or "")
        if leased or status_now != Status.WAITING_AGENT.value:
            warned = False
        elif not warned:
            notify_once(step.relay, ctx.run_dir, reason or "",
                        f"агента нет, решу сам через {max(1, int(round(grace / 60)))} мин",
                        now=now)
            warned, warned_at = True, now
        elif now - warned_at >= grace:
            return autopilot_answer(step, qid, reason or state.get("reason") or "")
        try:
            step._sleep(step.poll_s)
        except Exception:
            pass


def autopilot_answer(step: Any, qid: str, reason: str) -> Any:
    from src.loop.autopilot_answer import autopilot_answer as _impl

    return _impl(step, qid, reason)


def _inbox_autopilot(ctx: Any, step: Any, ans: Any, qid: str) -> Any:
    from src.loop.autopilot_answer import _inbox_autopilot as _impl

    return _impl(ctx, step, ans, qid)


def stop_as_answer(step: Any, qid: str, reason: str = "user_stop") -> Any:
    """stop.json in a decision point acts as a `stop` answer."""
    from src.loop import autopilot_answer as _mod
    from src.loop.phase_ctx import PhaseOutcome as _Outcome

    try:
        ref = step._rollback_keep_journal(reason)
    except Exception:
        ref = None
    step._repair_after_rollback()
    text = (
        "снимок не сделан, дерево не тронуто"
        if ref is None else step.halt_text(reason, ref)
    )
    try:
        step.write_stopped(reason, text)
    except Exception:
        pass
    try:
        step._restore_journal()
    except Exception:
        pass
    try:
        _mod._stop_outcome(step, reason, text)
    except Exception:
        pass
    st = step.ctx.st
    return _Outcome(status="stop", reason=reason, user_question=st.user_question,
                    rounds=st.round, escalated=st.escalated, stop_text=text)
