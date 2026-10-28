"""What `run` and `resume` share: the run outcome, the session around a run, the phase loop."""

from __future__ import annotations

import asyncio
import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.config import Config
from src.hud.app import maybe_launch_hud
from src.hud.registry import Registry
from src.loop import pulse as pulse_mod
from src.loop.crash_step import crash_outcome, phase_base
from src.loop.ledger import Ledger
from src.loop.phase_ctx import PhaseOutcome
from src.loop.plan import Phase
from src.loop.report import _print_phase_summary
from src.loop.state import RunState, Status


@dataclass
class RunOutcome:
    status: str
    exit_code: int
    reason: str = ""
    phases_shipped: int = 0
    phases_total: int = 0
    run_dir: str = ""
    debt: int = 0


class RunSession:
    """Repo lock, registry entry, pulse and (for `run`) the HUD of one run.

    ``register()`` goes first; ``with`` starts the pulse (and the HUD when
    ``hud_cfg`` is set) and on exit stops the pulse, releases the lock when
    ``release_on_exit`` and drops the registry entry. ``run`` releases the
    lock itself on each exit; ``resume`` leaves it to the exit.
    """

    def __init__(
        self,
        lock: Any,
        run_dir: Path,
        *,
        pid_started: float | None,
        release_on_exit: bool,
        hud_cfg: Config | None = None,
        on_pulse_failed: Callable[[], None] | None = None,
    ) -> None:
        self.lock = lock
        self.run_dir = run_dir
        self.pid_started = pid_started
        self.release_on_exit = release_on_exit
        self.hud_cfg = hud_cfg
        self.on_pulse_failed = on_pulse_failed
        self.registry = Registry()
        self.pulse: Any = None

    def register(self) -> None:
        try:
            self.registry.register(
                self.run_dir.name, self.run_dir / "state.json",
                pid=os.getpid(), pid_started=self.pid_started,
            )
        except Exception:
            pass

    def release(self) -> None:
        try:
            self.lock.release()
        except Exception:
            pass

    def _unregister(self) -> None:
        try:
            self.registry.unregister(self.run_dir.name)
        except Exception:
            pass

    def __enter__(self) -> RunSession:
        try:
            self.pulse = pulse_mod.Pulse(self.run_dir)
        except Exception:
            # F4: a ticker that never started must surface — but without
            # leaking the repo lock or the registry entry.
            if self.on_pulse_failed is not None:
                self.on_pulse_failed()
            self.release()
            self._unregister()
            raise
        if self.hud_cfg is not None:
            try:
                maybe_launch_hud(self.hud_cfg, self.run_dir)
            except BaseException:
                self._close()
                raise
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._close()
        return False

    def _close(self) -> None:
        try:
            if self.pulse is not None:
                self.pulse.stop()
        except Exception:
            pass
        if self.release_on_exit:
            self.release()
        self._unregister()


def end_run(ledger: Ledger, reason: str, shipped: int, total: int) -> None:
    ledger.event(
        "run_end",
        reason=reason,
        phases_shipped=shipped,
        phases_total=total,
    )


async def run_phases(
    run_phase: Callable[..., Awaitable[PhaseOutcome]],
    phases: list[Phase],
    *,
    ledger: Callable[[], Ledger],
    phase_kwargs: dict[str, Any],
    run_dir: Path,
    on_pending: Callable[[], None],
    on_stop: Callable[[PhaseOutcome], None],
    on_done: Callable[[int], None],
    on_interrupt: Callable[[], None] | None = None,
    start_index: int = 0,
    shipped: int = 0,
    judge_index: int | None = None,
    first_kwargs: dict[str, Any] | None = None,
) -> RunOutcome:
    """Next unshipped phase → run_phase → summary, until a stop or the end.

    ``run`` and ``resume`` differ only in what they pass: resume starts at
    ``start_index`` with ``shipped`` phases done, its first phase gets
    ``first_kwargs`` and the phase ``judge_index`` starts with the judge;
    only ``run`` turns Ctrl+C into ``interrupted`` (``on_interrupt``).
    """
    total = len(phases)

    def _outcome(status: str, code: int, reason: str) -> RunOutcome:
        return RunOutcome(
            status=status, exit_code=code, reason=reason,
            phases_shipped=shipped, phases_total=total, run_dir=str(run_dir),
        )

    index = start_index
    first = True
    pending_extra: dict[str, Any] = {}
    phase_cfg: tuple[int, Any] | None = None  # model: роль сменена до конца фазы
    try:
        while index < total:
            current = phases[index]
            if current.done:
                index += 1
                continue
            extra = dict(first_kwargs or {}) if first else {}
            if judge_index is not None:
                extra["start_with_judge"] = current.index == judge_index
            if pending_extra:
                extra.update(pending_extra)
                pending_extra = {}
            first = False
            if phase_cfg is not None and phase_cfg[0] != index:
                phase_cfg = None
            kwargs = {**phase_kwargs, **extra}
            if phase_cfg is not None:
                kwargs["cfg"] = phase_cfg[1]
            outcome = await _run_one(run_phase, current, ledger(), total, kwargs, run_dir)
            _print_phase_summary(current, outcome, total=total)
            if outcome.status == "ship":
                shipped += 1
                index += 1
                continue
            if outcome.status in ("waiting_human", "waiting_limit") and getattr(outcome, "decider", None) is not None:
                resolved = _resolve(outcome)
                if resolved is None or getattr(resolved, "status", "") == "waiting_human":
                    outcome = resolved if resolved is not None else outcome
                elif resolved.status == "answer":
                    nxt = getattr(resolved, "rounds", 0) or 0
                    if resolved.answer:
                        pending_extra["start_answer"] = resolved.answer
                    # F19: счётчик продолжается (nxt), grace-лимит — в round_limit.
                    pending_extra["start_round"] = nxt
                    continue
                elif resolved.status == "ship" and outcome.reason == "judge_pending":
                    shipped += 1
                    index += 1
                    continue
                elif resolved.status == "continue":
                    more = dict(resolved.extra or {})
                    if "cfg" in more:
                        phase_cfg = (index, more.pop("cfg"))
                    pending_extra.update(more)
                    continue
                elif resolved.status == "skip":
                    if getattr(resolved, "commit_sha", ""):
                        shipped += 1
                    index += 1
                    continue
                else:
                    outcome = resolved
            end_run(ledger(), outcome.reason, shipped, total)
            if outcome.status == "judge_pending":
                on_pending()
                return _outcome("judge_pending", 3, outcome.reason)
            on_stop(outcome)
            return _outcome("stop", 2, outcome.reason)
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        if on_interrupt is None:
            raise
        # Under asyncio.run, Ctrl+C reaches this coroutine as CancelledError.
        ledger().event("interrupted")
        end_run(ledger(), "interrupted", shipped, total)
        on_interrupt()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return _outcome("interrupted", 130, "interrupted")
    debt_n = _debt_count(run_dir)
    try:
        on_done(shipped)
    except Exception:
        pass
    _done_transition(ledger, run_dir, debt_n)
    if debt_n:
        from src.hud.app import debt_line

        print(debt_line(debt_n), flush=True)
        oc = _outcome("ok", 4, "debt")
        oc.debt = debt_n
        return oc
    return _outcome("ok", 0, "ship")


async def _run_one(run_phase: Callable[..., Awaitable[PhaseOutcome]], current: Phase,
                   ledger: Ledger, total: int, kwargs: dict[str, Any], run_dir: Path) -> PhaseOutcome:
    """One phase; a bug in tero is a stop with snapshot + rollback, not a dirty-tree crash (v4 BUG-001)."""
    base = phase_base(kwargs.get("repo"))
    try:
        return await run_phase(current, ledger=ledger, total_phases=total, **kwargs)
    except Exception as exc:
        return crash_outcome(kwargs.get("repo"), base, run_dir, current.index, exc)


def _resolve(outcome: PhaseOutcome) -> PhaseOutcome | None:
    """Ответ на точку решения; сбой применения — стоп с текстом сбоя."""
    try:
        return outcome.decider.resolve()
    except Exception as exc:
        return PhaseOutcome(
            status="stop", reason=outcome.reason,
            user_question=getattr(outcome, "user_question", ""),
            rounds=getattr(outcome, "rounds", 0),
            escalated=getattr(outcome, "escalated", False),
            stop_text=f"решение не применено: {exc}",
        )


def _done_transition(ledger: Callable[[], Ledger], run_dir: Path, debt_n: int) -> None:
    """Финал через transition: reason debt/ship един в state, stream и ledger."""
    from src.loop.state import RunState
    from src.loop.stream import Stream
    from src.loop.transition import resume_transition

    reason = "debt" if debt_n else "ship"
    io = RunState(run_dir / "state.json")
    try:
        saved = RunState.read(run_dir / "state.json") or {}
        saved_pid = saved.get("pid")
        saved_started = saved.get("pid_started")
        saved_round = saved.get("round", 0) or 0
    except Exception:
        saved_pid, saved_started, saved_round = None, None, 0
    try:
        resume_transition(
            io, Stream(run_dir / "stream.jsonl"),
            ledger, phase_title="", pid_started=saved_started,
            status=Status.DONE.value, reason=reason, text=reason,
            round_no=saved_round, run_end=True,
        )
        try:
            if isinstance(saved_pid, int):
                io.update(pid=saved_pid)
        except Exception:
            pass
    except Exception:
        try:
            RunState(run_dir / "state.json").update(
                status=Status.DONE.value, reason=reason, doing="стоп")
        except Exception:
            pass


def _debt_count(run_dir: Path) -> int:
    try:
        state = RunState.read(run_dir / "state.json") or {}
        debt = state.get("debt") or []
        return len(debt) if isinstance(debt, list) else 0
    except Exception:
        return 0


def _update(state: RunState, **fields: Any) -> None:
    try:
        state.update(**fields)
    except Exception:
        pass


def _hang_after_s(cfg: Config) -> float:
    return (
        max(
            cfg.player_timeout_s,
            cfg.judge_timeout_s,
            cfg.tester_timeout_s,
            cfg.test_timeout_s,
            cfg.stall_timeout_s,
        )
        + 300
    )


def _is_secret_name(name: str) -> bool:
    folded = name.lower()
    return "key" in folded or "token" in folded or "secret" in folded


def _public_config(cfg: Config) -> dict:
    return {
        f.name: getattr(cfg, f.name)
        for f in dataclasses.fields(Config)
        if not _is_secret_name(f.name)
    }


from src.loop.resume.rules import (  # noqa: F401
    _STOPPED_RESUME_REASONS,
    _cfg_from_state_fields,
    _resume_config,
    resume_refusal,
)


def _refuse(run_path: Path, reason: str) -> RunOutcome:
    print(f"стоп: {reason} (код 1)", flush=True)
    return RunOutcome(
        status="error", exit_code=1, reason=reason, run_dir=str(run_path)
    )
