"""Start a run, walk phases, stop with the spec exit codes."""

from __future__ import annotations

import functools
import os
import shutil
from pathlib import Path
from typing import Callable

from src.config import Config, load_provider_configs
from src.hud.registry import current_pid_started
from src.loop.gates import detect
from src.loop.git import (
    checkout_run_branch,
    exclude_tero,
    is_repo,
    run_branch_exists,
)
from src.loop.ledger import Ledger, new_run_dir
from src.loop.lock import acquire
from src.loop.phase import AgentRunner, PhaseOutcome, run_phase
from src.loop.plan import load_phases, load_preamble
from src.loop.preflight import start_checks, start_gate
from src.loop.report import phase_summary_line, rounds_word, slugify
from src.loop.session import (
    RunOutcome,
    RunSession,
    _hang_after_s,
    _public_config,
    _refuse,
    _resume_config,
    _update,
    end_run,
    run_phases,
)
from src.loop.resume_step import _restore_resume_tree, _stopped_phases
from src.loop.state import RunState, Status
from src.loop.transition import resume_transition

__all__ = [
    "RunOutcome",
    "phase_summary_line",
    "rounds_word",
    "slugify",
    "_restore_resume_tree",
]


async def run(
    cfg: Config,
    *,
    agents: AgentRunner,
    check_ready: Callable[[str, str], tuple[bool, str]] | None = None,
    list_models: Callable[[], list[str]] | None = None,
    agy_preflight: Callable[[str, str, str], str | None] | None = None,
) -> RunOutcome:
    repo = Path(cfg.working_dir).expanduser().resolve()
    if not is_repo(repo):
        return RunOutcome(status="error", exit_code=1, reason="not a git repo")

    # exclude_tero() before any write into .tero/ (incl. run.lock, state.json):
    # a fresh clone without ".tero/" in exclude would otherwise read
    # "?? .tero/" as a dirty tree on every start.
    exclude_tero(repo)
    lock = acquire(repo)
    if lock is None:
        return RunOutcome(
            status="error", exit_code=1, reason="another run holds this repo"
        )

    # State dir exists before the dirty-tree check, so a failed start can
    # leave state.json=stopped for the HUD while history() still skips it
    # (history only lists dirs with ledger.jsonl, created below on success).
    slug = slugify(Path(cfg.plan_file).stem)
    run_dir = new_run_dir(repo, slug)
    state = RunState(run_dir / "state.json")
    pid_started = current_pid_started()
    session = RunSession(
        lock, run_dir, pid_started=pid_started, release_on_exit=False, hud_cfg=cfg
    )
    session.register()
    _update(
        state,
        status=Status.STARTING.value,
        pid=os.getpid(),
        pid_started=pid_started,
        plan=cfg.plan_file,
        doing="старт",
        config=_public_config(cfg),
        hang_after_s=_hang_after_s(cfg),
    )
    with session:

        def _fail_start(reason: str, doing: str = "", status: str = "error") -> RunOutcome:
            _update(
                state,
                status=Status.STOPPED.value,
                reason=reason,
                text=reason,
                doing=doing or "стоп",
            )
            session.release()
            return RunOutcome(status=status, exit_code=1, reason=reason)

        failed = start_checks(
            cfg, repo, state,
            check_ready=check_ready, list_models=list_models, agy_preflight=agy_preflight,
        )
        if failed is not None:
            return _fail_start(failed[0], status=failed[1])

        _update(state, doing="проверяю план …")
        plan_path = repo / cfg.plan_file
        if not plan_path.is_file():
            return _fail_start(f"plan not found: {plan_path}")

        if run_branch_exists(repo, slug):
            # Resume: the [x] marks live on the run branch, not on the branch we started from.
            checkout_run_branch(repo, slug)

        phases = load_phases(plan_path)
        if not any(phase.checkboxes for phase in phases):
            return _fail_start(f"no '- [ ]' checkboxes found in {plan_path}")
        if all(phase.done for phase in phases):
            return _nothing_to_do(session, run_dir, len(phases))

        _update(state, doing="стартовые тесты …")
        command = detect(
            repo,
            test_command=cfg.test_command,
            test_command_scoped=cfg.test_command_scoped,
        )
        if command is not None and not start_gate(cfg, repo, command):
            return _fail_start("red_start")

        checkout_run_branch(repo, slug)
        _update(state, status=Status.RUNNING.value, doing="фаза 1", config=_public_config(cfg))
        ledger = _start_ledger(run_dir, cfg, slug)

        def _stopped(reason: str, text: str) -> None:
            _update(state, status=Status.STOPPED.value, reason=reason, text=text, doing="стоп")
            session.release()

        def _done(shipped: int) -> None:
            end_run(ledger, "ship", shipped, len(phases))
            _update(state, status=Status.DONE.value, doing="стоп")
            session.release()

        return await run_phases(
            run_phase,
            phases,
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo,
                plan_path=plan_path,
                preamble=load_preamble(plan_path),
                cfg=cfg,
                agents=agents,
                test_command=command,
                pulse=session.pulse,
            ),
            run_dir=run_dir,
            on_pending=session.release,
            on_stop=lambda outcome: _stopped(outcome.reason, outcome.stop_text or outcome.reason),
            on_done=_done,
            on_interrupt=lambda: _stopped("interrupted", "interrupted"),
        )


async def resume(
    repo: str | Path,
    run_dir: str | Path,
    *,
    agents: AgentRunner,
    answer: str = "",
) -> RunOutcome:
    """Continue a stopped run from its state.json. Only judge_pending yet."""
    from src.loop.stream import Stream

    repo_path = Path(repo).expanduser().resolve()
    run_path = Path(run_dir).expanduser().resolve()
    state = RunState.read(run_path / "state.json") or {}
    status = state.get("status") or ""
    cfg, refusal = _resume_config(state, answer)
    if cfg is None:
        return _refuse(run_path, refusal)
    ref = state.get("snapshot_ref") or ""
    base = state.get("base") or ""
    phase_title = state.get("phase") or ""
    phase_index = state.get("phase_index", 0) or 0
    round_no = state.get("round", 1) or 1

    from src.loop.resume_step import pre_lock_refusal as _e

    if (_o := _e(repo_path, run_path, cfg, state)) is not None:
        return _o
    lock = acquire(repo_path)
    if lock is None:
        return _refuse(run_path, "another run holds this repo")

    done_io = RunState(run_path / "state.json")
    stream = Stream(run_path / "stream.jsonl")
    try:
        saved_hang = state.get("hang_after_s")
        if not isinstance(saved_hang, (int, float)):
            done_io.update(hang_after_s=_hang_after_s(cfg))
    except Exception:
        pass
    slug = slugify(Path(cfg.plan_file).stem)
    pid_started = current_pid_started()
    ledger: Ledger | None = None

    def _ledger() -> Ledger:
        nonlocal ledger
        if ledger is None:
            ledger = Ledger(run_path)
        return ledger

    _write = functools.partial(
        resume_transition, done_io, stream, _ledger,
        phase_title=phase_title, pid_started=pid_started,
    )

    def _restore_stopped() -> None:
        try:
            r = state.get("reason") or ""
            _write(status=Status.STOPPED.value, reason=r,
                   text=state.get("text") or "", round_no=state.get("round", 1) or 1)
        except Exception:
            pass

    def _restore_pending() -> None:
        try:
            _write(status=Status.JUDGE_PENDING.value, reason="resume_setup_failed",
                   text="resume: настройка не удалась, повтор возможен", round_no=round_no)
        except Exception:
            pass

    session = RunSession(
        lock, run_path, pid_started=pid_started, release_on_exit=True,
        on_pulse_failed=_restore_stopped if status == Status.STOPPED.value else _restore_pending,
    )
    session.register()

    with session:
        if status == Status.STOPPED.value:
            return await _stopped_phases(
                repo_path, run_path, cfg,
                agents=agents, session=session, write=_write, ledger=_ledger,
                saved=state, slug=slug, answer=answer, phase_runner=run_phase,
                restore_stopped=_restore_stopped,
            )
        return await _resume_phases(
            repo_path, run_path, cfg,
            agents=agents, session=session, write=_write, ledger=_ledger,
            restore_pending=_restore_pending, slug=slug, base=base, ref=ref,
            phase_index=phase_index, phase_title=phase_title, round_no=round_no,
            status=status,
        )


async def _resume_phases(
    repo_path: Path,
    run_path: Path,
    cfg: Config,
    *,
    agents: AgentRunner,
    session: RunSession,
    write: Callable[..., None],
    ledger: Callable[[], Ledger],
    restore_pending: Callable[[], None],
    slug: str,
    base: str,
    ref: str,
    phase_index: int,
    phase_title: str,
    round_no: int,
    status: str,
) -> RunOutcome:
    """Restore the ref tree and walk the phases from the judge of the stopped one."""
    # F14: статус running — только после успешной настройки, иначе
    # любая ошибка checkout/восстановления оставляет judge_pending
    # и resume можно повторить.
    try:
        from src.loop.resume_step import _restore_resume_tree as _restore

        _restore(repo_path, run_path, slug, base, ref)
        plan_path = repo_path / cfg.plan_file
        if not plan_path.is_file():
            restore_pending()
            return _refuse(run_path, f"plan not found: {plan_path}")
        preamble = load_preamble(plan_path)
        phases = load_phases(plan_path)
        phase = next(
            (p for p in phases if p.index == phase_index or p.title == phase_title),
            None,
        )
        if phase is None:
            restore_pending()
            return _refuse(run_path, f"фаза не найдена: {phase_title or phase_index}")

        command = detect(
            repo_path,
            test_command=cfg.test_command,
            test_command_scoped=cfg.test_command_scoped,
        )
        # F8: resume owns the run again — running from the start, so a later
        # player snapshot can never be mistaken for a gate-passed ref.
        # judge_pending returns only via the judge failing again below.
        write(
            status=Status.RUNNING.value, reason="resume",
            text=f"resume: продолжаю с судьи (раунд {round_no})",
            round_no=round_no,
        )
        ledger().event("resume", status=status, round=round_no, ref=ref)

        def _stopped(outcome: PhaseOutcome) -> None:
            write(
                status=Status.STOPPED.value,
                reason=outcome.reason,
                text=outcome.stop_text or outcome.reason,
                round_no=round_no,
            )

        return await run_phases(
            run_phase,
            phases,
            ledger=ledger,
            phase_kwargs=dict(
                repo=repo_path,
                plan_path=plan_path,
                preamble=preamble,
                cfg=cfg,
                agents=agents,
                test_command=command,
                pulse=session.pulse,
            ),
            run_dir=run_path,
            on_pending=lambda: None,
            on_stop=_stopped,
            on_done=lambda _shipped: write(
                status=Status.DONE.value, reason="ship", text="ship",
                round_no=round_no, run_end=True,
            ),
            start_index=next(
                (i for i, p in enumerate(phases) if p.index == phase.index),
                phase.index,
            ),
            shipped=sum(1 for p in phases if p.done),
            judge_index=phase.index,
            first_kwargs={"start_checkpoint": base, "start_round": round_no},
        )
    except Exception:
        restore_pending()
        raise


def _nothing_to_do(session: RunSession, run_dir: Path, total: int) -> RunOutcome:
    session.release()
    try:
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.parent.rmdir()
    except Exception:
        pass
    # No branch, no run dir, no test gate: there is nothing to judge.
    return RunOutcome(
        status="ok",
        exit_code=0,
        reason="nothing_to_do",
        phases_total=total,
    )


def _start_ledger(run_dir: Path, cfg: Config, slug: str) -> Ledger:
    ledger = Ledger(run_dir)
    ledger.event("run_start", plan=cfg.plan_file, slug=slug)
    ledger.event(
        "compact_config",
        player_at_tokens=cfg.compact_player_at_tokens,
        judge_at_tokens=cfg.compact_judge_at_tokens,
    )
    return ledger


def agy_preflight_check(role: str, model: str, working_dir: str) -> str | None:
    """Pre-flight `agy` до очереди: список моделей, короткий вызов.

    Возвращает текст стопа или None, когда прогон идёт дальше.
    """
    from src.providers import agy as agy_mod

    try:
        configs = load_provider_configs(working_dir)
    except Exception:
        configs = {}
    entry = dict(configs.get("agy") or {})
    command = entry.get("command", "agy")
    model = agy_mod._resolve_model(
        model or entry.get("default_model") or agy_mod.AgyConfig.default_model
    )
    try:
        listed = agy_mod.list_models(command)
    except Exception:
        listed = []
    if listed and model and model not in listed:
        print(f"`agy`: модели `{model}` нет в `agy models`, прогон идёт", flush=True)
    try:
        ok, detail = agy_mod.preflight(
            command, model, timeout_s=agy_mod.AGY_PREFLIGHT_TIMEOUT_S
        )
    except Exception as exc:
        ok, detail = False, f"`agy` не отвечает: `{exc}`"
    if not ok:
        return (
            f"{role} provider (agy) not ready: {detail}. "
            "Войди: запусти `agy` в терминале"
        )
    return None
