"""Crash recovery paths: refuse/shipped/waiting/rerun, else the judge path."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _checkout_run_head(repo_path: Path, slug: str) -> None:
    from src.loop import git as g
    from src.loop.resume_lock import check_index_lock
    from src.loop.resume_step import run_branch_head

    check_index_lock(repo_path)
    name = slug if str(slug).startswith("tero/") else f"tero/{slug}"
    head = run_branch_head(repo_path, slug)
    if not head:
        raise RuntimeError(f"нет ветки прогона {name}")
    g.force_checkout_run_branch(repo_path, slug)
    g.rollback(repo_path, head)


def _bump_crashes(state: dict) -> int:
    try:
        idx = int((state or {}).get("phase_index", 0) or 0)
    except (TypeError, ValueError):
        idx = 0
    try:
        saved_phase = int((state or {}).get("crash_phase_index", idx))
    except (TypeError, ValueError):
        saved_phase = idx
    try:
        crashes = int((state or {}).get("crash_resumes", 0) or 0)
    except (TypeError, ValueError):
        crashes = 0
    if saved_phase != idx:
        crashes = 0
    return crashes + 1, idx


def _store_crashes(run_path: Path, crashes: int, phase_index: int) -> None:
    from src.loop.state import RunState as _RS

    try:
        _RS(run_path / "state.json").update(
            crash_resumes=crashes, crash_phase_index=phase_index)
    except Exception:
        pass


def run_state_of(run_path: Path) -> dict:
    from src.loop.state import RunState as _Reader

    try:
        return _Reader.read(run_path / "state.json") or {}
    except Exception:
        return {}


async def _wait_question_after_crash(repo_path: Path, run_path: Path, cfg: Any,
                                     state: dict, status: str, crashes: int, *,
                                     agents: Any, session: Any, write: Any,
                                     ledger: Any, restore_pending: Any,
                                     slug: str, base: str, round_no: int,
                                     phase_runner: Any, qid: str):
    """Ожидание восстановлено: тот же раунд и вопрос, timeout с начала."""
    from src.loop.session import _refuse as _deny

    try:
        from src.loop.plan import load_phases, load_preamble

        plan_path = repo_path / cfg.plan_file
        if not plan_path.is_file():
            restore_pending()
            return _deny(run_path, f"plan not found: {plan_path}")
        preamble = load_preamble(plan_path)
        phases = load_phases(plan_path)
        try:
            cur = int(state.get("phase_index", 0) or 0)
        except (TypeError, ValueError):
            cur = 0
        phase = next((p for p in phases if p.index == cur),
                     next((p for p in phases if not p.done), None))
        if phase is None:
            restore_pending()
            return _deny(run_path, "фаза не найдена")
        pos = next((i for i, p in enumerate(phases) if p.index == phase.index),
                   phase.index)
        runner = _wait_runner(phase, repo_path, plan_path, preamble, cfg,
                              agents, session, ledger, base, round_no, state)
        from src.loop.resume_wait import resume_waiting_outcome
        from src.loop.session import _resolve as _decide

        outcome = resume_waiting_outcome(runner)
        decided = _decide(outcome)
        return await _after_wait_decision(
            decided if decided is not None else outcome,
            repo_path, run_path, cfg, phases, preamble, plan_path,
            agents=agents, session=session, ledger=ledger,
            restore_pending=restore_pending, base=base, round_no=round_no,
            phase_runner=phase_runner, pos=pos, write=write)
    except Exception:
        restore_pending()
        raise


async def _after_wait_decision(outcome: Any, repo_path: Path, run_path: Path,
                               cfg: Any, phases: Any, preamble: str,
                               plan_path: Path, *, agents: Any, session: Any,
                               ledger: Any, restore_pending: Any, base: str,
                               round_no: int, phase_runner: Any, pos: int,
                               write: Any):
    """Применённый ответ на восстановленный вопрос: дальше как run_phases."""
    from src.loop.gates import detect
    from src.loop.session import RunOutcome as _Outcome
    from src.loop.session import _refuse as _deny
    from src.loop.session import end_run as _end
    from src.loop.session import run_phases as _rp
    from src.loop.state import Status as _St

    total = len(phases)
    shipped = sum(1 for p in phases if p.done)
    command = detect(repo_path, test_command=cfg.test_command,
                     test_command_scoped=cfg.test_command_scoped)

    def _stopped(final: Any) -> Any:
        try:
            _end(ledger(), final.reason, shipped, total)
        except Exception:
            pass
        write_status(write, _St.STOPPED.value, final.reason,
                     final.stop_text or final.reason, round_no)
        return _Outcome(status="stop", exit_code=2, reason=final.reason,
                        run_dir=str(run_path))

    def _done(_shipped: int) -> None:
        write_status(write, _St.DONE.value, "ship", "ship",
                     round_no, run_end=True)

    status = getattr(outcome, "status", "") or ""
    if status == "answer":
        nxt = getattr(outcome, "rounds", 0) or 0
        first: dict[str, Any] | None = {
            "start_checkpoint": base, "start_round": nxt,
            "start_answer": getattr(outcome, "answer", "") or "",
        }
        start_cfg, start_index = cfg, pos
    elif status == "continue":
        more = dict(getattr(outcome, "extra", None) or {})
        start_cfg = more.pop("cfg", cfg)
        first = {"start_checkpoint": base, **more}
        start_index = pos
    elif status == "skip":
        if getattr(outcome, "commit_sha", ""):
            shipped += 1
        first, start_cfg, start_index = None, cfg, pos + 1
    elif status == "ship":
        try:
            _end(ledger(), "ship", shipped + 1, total)
        except Exception:
            pass
        _done(shipped + 1)
        return _Outcome(status="ok", exit_code=0, reason="ship",
                        run_dir=str(run_path))
    else:
        return _stopped(outcome)
    # skip on the last phase: run_phases with nothing left ends the run (done/debt)
    if start_index >= total and status != "skip":
        restore_pending()
        return _deny(run_path, "фаза не найдена")
    return await _rp(
        phase_runner, phases, ledger=ledger,
        phase_kwargs=dict(repo=repo_path, plan_path=plan_path,
                          preamble=preamble, cfg=start_cfg, agents=agents,
                          test_command=command, pulse=session.pulse),
        run_dir=run_path, on_pending=lambda: None, on_stop=_stopped,
        on_done=_done, start_index=start_index,
        shipped=shipped, first_kwargs=first)


def write_status(write: Any, status: str, reason: str, text: str,
                 round_no: int, run_end: bool = False) -> None:
    """Один переход статуса восстановленного ожидания."""
    try:
        write(status=status, reason=reason, text=text,
              round_no=round_no, run_end=run_end)
    except TypeError:
        write(status=status, reason=reason, text=text, round_no=round_no)
    except Exception:
        pass


def _wait_runner(phase: Any, repo_path: Path, plan_path: Path,
                 preamble: str, cfg: Any, agents: Any, session: Any,
                 ledger: Any, base: str, round_no: int, state: dict):
    """Делегирует построение _PhaseRunner в resume_step (там уже лежит phase)."""
    from src.loop.resume_step import make_wait_runner

    return make_wait_runner(phase, repo_path, plan_path, preamble, cfg,
                            agents, session, ledger, base, round_no, state)


async def crash_resume(repo_path: Path, run_path: Path, cfg: Any, state: dict,
                       force: bool, *, agents: Any, session: Any, write: Any,
                       ledger: Any, restore_pending: Any, slug: str, base: str,
                       ref: str, phase_index: int, phase_title: str,
                       round_no: int, status: str, phase_runner: Any,
                       judge_resume: Any):
    """Full crash recovery: refuse/shipped/waiting/rerun, else judge path."""
    from src.loop.resume_crash import dispatch
    from src.loop.resume_lock import check_index_lock
    from src.loop.state import RunState as _RS
    from src.loop.state import Status as _St

    check_index_lock(repo_path)
    if status == _St.JUDGE_PENDING.value:
        return await judge_resume(
            repo_path, run_path, cfg, agents=agents, session=session,
            write=write, ledger=ledger, restore_pending=restore_pending,
            slug=slug, base=base, ref=ref, phase_index=phase_index,
            phase_title=phase_title, round_no=round_no, status=status)
    plan = dispatch(state, repo=repo_path, run_id=run_path.name, force=force)
    action = plan.get("action") or ""
    if action == "refuse" and plan.get("reason") == "snapshot_failed":
        # Storage failures are retryable setup failures, not crashes of a phase.
        # Keep the saved status/counter so resume can be retried after disk repair.
        from src.loop.session import _refuse as _deny

        return _deny(run_path, plan.get("text", "snapshot failed"))
    crashes, crash_idx = _bump_crashes(state)
    if action == "refuse":
        try:
            ledger().event("crash_resume", status=status, round=round_no,
                           action="refuse", crashes=crashes)
        except Exception:
            pass
        write(status=_St.STOPPED.value, reason=plan.get("reason", "crash_loop"),
              text=plan.get("text", ""), round_no=round_no)
        _store_crashes(run_path, crashes, crash_idx)
        session.release()
        from src.loop.session import RunOutcome as _Outcome

        return _Outcome(status="error", exit_code=1,
                        reason=plan.get("text", ""), run_dir=str(run_path))
    if action == "waiting":
        q = plan.get("question") or {}
        qid = plan.get("question_id") or q.get("id") or ""
        text = q.get("text") or state.get("text") or ""
        try:
            ledger().event("crash_resume", status=status, round=round_no,
                           action="waiting", crashes=crashes)
        except Exception:
            pass
        write(status=status, reason="resume", text=text, round_no=round_no)
        try:
            _RS(run_path / "state.json").update(
                question=dict(q), question_id=qid,
                crash_resumes=crashes, crash_phase_index=crash_idx)
        except Exception:
            pass
        return await _wait_question_after_crash(
            repo_path, run_path, cfg, state, status, crashes,
            agents=agents, session=session, write=write, ledger=ledger,
            restore_pending=restore_pending, slug=slug, base=base,
            round_no=round_no, phase_runner=phase_runner, qid=qid)
    if action == "shipped":
        _checkout_run_head(repo_path, slug)
        return await _continue_after_crash(
            repo_path, run_path, cfg, state, status, crashes,
            agents=agents, session=session, write=write, ledger=ledger,
            restore_pending=restore_pending, slug=slug, base=base,
            round_no=round_no, phase_runner=phase_runner, advance=True)
    _checkout_run_head(repo_path, slug)
    return await _continue_after_crash(
        repo_path, run_path, cfg, state, status, crashes,
        agents=agents, session=session, write=write, ledger=ledger,
        restore_pending=restore_pending, slug=slug, base=base,
        round_no=round_no, phase_runner=phase_runner, advance=False)


async def _continue_after_crash(repo_path: Path, run_path: Path, cfg: Any,
                                state: dict, status: str, crashes: int, *,
                                agents: Any, session: Any, write: Any,
                                ledger: Any, restore_pending: Any, slug: str,
                                base: str, round_no: int, phase_runner: Any,
                                advance: bool):
    """Checkout done: ledger event, state catch-up, run_phases onward."""
    from src.loop.gates import detect
    from src.loop.plan import load_phases, load_preamble
    from src.loop.session import run_phases as _rp
    from src.loop.state import Status as _St

    try:
        plan_path = repo_path / cfg.plan_file
        if not plan_path.is_file():
            restore_pending()
            from src.loop.session import _refuse as _deny

            return _deny(run_path, f"plan not found: {plan_path}")
        preamble, phases = load_preamble(plan_path), load_phases(plan_path)
        try:
            cur = int(state.get("phase_index", 0) or 0)
        except (TypeError, ValueError):
            cur = 0
        if advance:
            nxt = next((p for p in phases if p.index == cur + 1), None)
            title = nxt.title if nxt is not None else ""
            field_entries = [{"title": p.title, "done": bool(p.done)} for p in phases]
            write(status=_St.RUNNING.value, reason="resume",
                  text=f"resume: фаза {cur + 1} сдана, догоняю git",
                  round_no=round_no, phases=field_entries,
                  total_phases=len(field_entries))
            from src.loop.state import RunState as _RS

            _store_crashes(run_path, 0, cur + 1)
            try:
                _RS(run_path / "state.json").update(
                    phase_index=cur + 1, phase=title)
            except Exception:
                pass
            try:
                ledger().event("crash_resume", status=status, round=round_no,
                               action="shipped", crashes=0)
            except Exception:
                pass
            start, first = cur + 1, None
        else:
            field_entries = [{"title": p.title, "done": bool(p.done)} for p in phases]
            write(status=_St.RUNNING.value, reason="resume",
                  text=f"resume: перезапускаю фазу после краша (раунд {round_no})",
                  round_no=round_no, phases=field_entries,
                  total_phases=len(field_entries))
            _store_crashes(run_path, crashes, cur)
            try:
                ledger().event("crash_resume", status=status, round=round_no,
                               action="rerun", crashes=crashes)
            except Exception:
                pass
            start = cur
            from src.loop.resume_step import run_branch_head

            checkpoint = run_branch_head(repo_path, slug) or base
            first = {"start_checkpoint": checkpoint, "start_round": round_no}
            # Ответ, применённый до краша, не теряется: тот же текст снова игроку.
            if state.get("answer") and state.get("answer_phase") == cur:
                first["start_answer"] = str(state["answer"])
        phase = next((p for p in phases if p.index == start),
                     next((p for p in phases if not p.done), None))
        if phase is None:
            if advance:
                write(status=_St.DONE.value, reason="ship", text="ship",
                      round_no=round_no, run_end=True)
                from src.loop.session import RunOutcome as _Outcome

                return _Outcome(status="ok", exit_code=0, reason="ship",
                                run_dir=str(run_path))
            restore_pending()
            from src.loop.session import _refuse as _deny

            return _deny(run_path, "фаза не найдена")
        command = detect(repo_path, test_command=cfg.test_command,
                         test_command_scoped=cfg.test_command_scoped)
        pos = next((i for i, p in enumerate(phases) if p.index == phase.index),
                   phase.index)

        def _stopped(outcome: Any) -> None:
            write(status=_St.STOPPED.value, reason=outcome.reason,
                  text=outcome.stop_text or outcome.reason,
                  round_no=round_no)

        def _done(shipped: int) -> None:
            try:
                saved_now = run_state_of(run_path)
                try:
                    _cur = int(saved_now.get("phase_index", start) or 0)
                except (TypeError, ValueError):
                    _cur = start
                _store_crashes(run_path, 0, _cur)
            except Exception:
                pass
            write(status=_St.DONE.value, reason="ship",
                  text="ship", round_no=round_no, run_end=True)

        return await _rp(
            phase_runner, phases, ledger=ledger,
            phase_kwargs=dict(repo=repo_path, plan_path=plan_path,
                              preamble=preamble, cfg=cfg, agents=agents,
                              test_command=command, pulse=session.pulse),
            run_dir=run_path, on_pending=lambda: None, on_stop=_stopped,
            on_done=_done,
            start_index=pos, shipped=sum(1 for p in phases if p.done),
            first_kwargs=first)
    except Exception:
        restore_pending()
        raise
