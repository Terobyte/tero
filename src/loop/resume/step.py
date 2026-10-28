"""Stopped-resume helpers; run.py/phase.py stay thin via small entry points."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from src.loop.resume.kill import kill_hung_owner  # noqa: F401  (re-export)


from src.loop.resume.crash import dispatch  # noqa: F401  (re-export)


@dataclass
class ResumeFrom:
    ref: str = ""
    base: str = ""
    answer: str = ""
    round_limit: int | None = None
    gate_first: bool = False
    judge_reuse_ref: bool = False
    suite_hashes: Any = None
    guarded: Any = None
    link_targets: Any = None
    grace_until: int = 0
    forbidden: Any = None


def resume_limit(entry: Any, fallback: int) -> int:
    return int(entry.round_limit) if getattr(entry, "round_limit", None) else fallback


def resume_entry(entry: Any) -> ResumeFrom:
    return entry if entry is not None else ResumeFrom()


def baseline_suite(repo, resume_from):
    """Suite baseline: base-commit hashes for a resumed first phase, else disk."""
    from src.loop.git import file_hashes as _hashes
    from src.loop.suite_guard import _snapshot_guarded, _suite_paths

    rf = (getattr(resume_from, "suite_hashes", None),
          getattr(resume_from, "guarded", None),
          getattr(resume_from, "link_targets", None))
    if all(v is not None for v in rf):
        return rf
    hashes = _hashes(repo, _suite_paths(repo))
    return (hashes,) + _snapshot_guarded(repo, hashes)


def _parse_plan_bytes(raw: bytes):
    """Preamble + phases из байтов base-плана без чтения диска."""
    from src.loop import plan as pm

    L = raw.decode("utf-8", errors="replace").splitlines()
    heads = [i for i, x in enumerate(L) if pm._H2.match(x)]
    if not heads:
        out = [pm.Phase(index=n, title=m.group(2), body=x, checkboxes=[pm._parse_box(m)], start_line=i, end_line=i + 1)
               for n, t in enumerate((t for i, x in enumerate(L) for t in [(i, x, pm._CHECKBOX.match(x))] if t[2])) for i, x, m in [t]]
        return "\n".join(L[: next((i for i, x in enumerate(L) if pm._CHECKBOX.match(x)), len(L))]).strip(), out
    pre, phases = "\n".join(L[: heads[0]]).strip(), []
    for p, s in enumerate(heads):
        e = heads[p + 1] if p + 1 < len(heads) else len(L)
        if b := pm._checkboxes(L[s + 1: e]):
            phases.append(pm.Phase(index=len(phases), title=pm._H2.match(L[s]).group(1),
                                   body="\n".join(L[s + 1: e]).strip("\n"),
                                   checkboxes=b, start_line=s, end_line=e))
    return pre, phases


def _find_phase(phases, saved):
    t, i = saved.get("phase") or "", saved.get("phase_index", 0) or 0
    return next((p for p in phases if p.index == i or p.title == t), None)


async def _stopped_setup(repo_path, cfg, saved, plan_bytes=None):
    """Plan + phase lookup; (plan_path, preamble, phases, phase|None)."""
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre

    plan_path = repo_path / cfg.plan_file
    if plan_bytes is None:
        preamble, phases = _lpre(plan_path), _lp(plan_path)
    else:
        preamble, phases = _parse_plan_bytes(plan_bytes)
    return plan_path, preamble, phases, _find_phase(phases, saved)


def _on_stop_write(write):
    return lambda o: write(status="stopped", reason=o.reason,
                           text=o.stop_text or o.reason, round_no=o.rounds)


def _stopped_first_kwargs(base, ref, cleaned, round0, cfg, triplet):
    hb, gb, tb = triplet
    rf = ResumeFrom(ref=ref, base=base, answer=cleaned, round_limit=round0 + cfg.max_rounds,
                    gate_first=True, suite_hashes=hb, guarded=gb, link_targets=tb)
    return {"start_checkpoint": base, "start_round": round0,
            "start_with_judge": True, "resume_from": rf}


def _stopped_run_kwargs(repo, run_path, cfg, agents, session, command,
                        preamble, plan_path, phases, phase, ref, base,
                        cleaned, round0, write, ledger, suite_triplet, phase_runner):
    from src.loop.session import run_phases as _rp

    index = next((i for i, p in enumerate(phases) if p.index == phase.index), phase.index)
    pk = dict(repo=repo, plan_path=plan_path, preamble=preamble, cfg=cfg,
                agents=agents, test_command=command, pulse=session.pulse)
    return dict(run=_rp, run_phase=phase_runner, phases=phases, ledger=ledger,
        phase_kwargs=pk, run_dir=run_path, on_pending=lambda: None,
        on_stop=_on_stop_write(write), on_done=lambda _s, outcome_rounds=0: None,
        start_index=index, shipped=sum(1 for p in phases if p.done),
        first_kwargs=_stopped_first_kwargs(base, ref, cleaned, round0, cfg, suite_triplet))


def _make_restore_stopped(write, restore_stopped, reason0, text0, round0):
    def _go() -> None:
        if restore_stopped is not None:
            try:
                restore_stopped()
            except Exception:
                pass
    return _go


async def _stopped_phases(repo_path, run_path, cfg, *, agents, session,
                          write, ledger, saved, slug, answer,
                          phase_runner, restore_stopped=None):
    from src.loop.state import RunState as _RS
    from src.loop.state import Status as _St

    ref = saved.get("snapshot_ref") or ""
    base = saved.get("base") or ""
    reason0 = saved.get("reason") or ""
    round0 = saved.get("round", 1) or 1
    text0 = saved.get("text") or ""
    _restore_stopped = _make_restore_stopped(write, restore_stopped,
                                             reason0, text0, round0)

    try:
        from src.loop.session import _refuse as _deny

        from src.loop.gates import detect as _detect

        plan_path = repo_path / cfg.plan_file
        tmp_base = _git_show_bytes(repo_path, base, plan_path)
        if tmp_base is None and not plan_path.is_file():
            _restore_stopped()
            return _deny(run_path, f"plan not found: {plan_path}")
        plan_path, preamble, phases, phase = await _stopped_setup(
            repo_path, cfg, saved, plan_bytes=tmp_base,
        )
        if phase is None:
            _restore_stopped()
            saved_title, saved_index = saved.get("phase") or "", saved.get("phase_index", 0) or 0
            return _deny(run_path, f"фаза не найдена: {saved_title or saved_index}")
        try:
            triplet = base_suite_state(repo_path, base)
        except Exception:
            _restore_stopped()
            return _deny(run_path, "снимок base недоступен, запусти `tero go`")
        _restore_resume_tree(repo_path, run_path, slug, base, ref)
        command = _detect(
            repo_path,
            test_command=cfg.test_command,
            test_command_scoped=cfg.test_command_scoped,
        )
        cleaned = clean_answer(answer)
        # done/stop записывают фактический раунд фазы через обёртки ниже.
        def _done_write(shipped: int) -> None:
            try:
                round_now = (_RS.read(run_path / "state.json") or {}).get("round", round0)
                round_now = round_now or round0
            except Exception:
                round_now = round0
            write(status=_St.DONE.value, reason="ship", text="ship",
                  round_no=round_now, run_end=True)

        write(
            status=_St.RUNNING.value, reason="resume",
            text=f"resume: продолжаю со снимка (раунд {round0})", round_no=round0,
            phases=[{"title": p.title, "done": bool(p.done)} for p in phases], total_phases=len(phases),
        )
        ledger().event(
            "resume", status="stopped", round=round0, ref=ref,
            from_reason=reason0,
        )
        args = _stopped_run_kwargs(
            repo_path, run_path, cfg, agents, session, command,
            preamble, plan_path, phases, phase, ref, base,
            cleaned, round0, write, ledger, triplet, phase_runner,
        )
        args["on_done"] = _done_write
        runner, phased = args.pop("run"), args.pop("run_phase")
    except Exception:
        _restore_stopped()
        raise
    # Первая фаза стартовала: её стопы пишут свой статус — исходный не восстанавливаем.
    return await runner(phased, **args)


def clean_answer(text: str) -> str:
    from src.errors import scrub_secrets
    try:
        return scrub_secrets(text or "")[:2000]
    except Exception:
        return (text or "")[:2000]


def _git_show_bytes(repo: Path, base: str, plan: Path) -> bytes | None:
    try:
        rel = plan.relative_to(repo).as_posix()
    except ValueError:
        return None
    try:
        proc = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=repo, capture_output=True, check=False)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _continuation_block(repo: Path, base: str, ref: str) -> str:
    if not base or not ref:
        return ""
    from src.loop import snapshot as sm
    try:
        diff = sm.diff_for(repo, base, ref)
    except Exception:
        return f"обрезано, полный дифф: `git diff {base} {ref}`"
    return diff[:20_000] + f"\n…обрезано, полный дифф: `git diff {base} {ref}`" if len(diff) > 20_000 else diff


def make_wait_runner(phase, repo_path, plan_path, preamble, cfg, agents,
                     session, ledger, base, round_no, state):
    """_PhaseRunner с восстановленным вопросом: тот же id и round."""
    from src.loop.phase import _PhaseRunner

    question = state.get("question") if isinstance(state.get("question"), dict) else {}
    limit = state.get("limit_wait") if isinstance(state.get("limit_wait"), dict) else {}
    runner = _PhaseRunner(
        phase=phase, repo=repo_path, plan_path=plan_path, preamble=preamble,
        cfg=cfg, agents=agents, test_command=None, ledger=ledger(),
        total_phases=0, start_with_judge=False, start_checkpoint=base,
        start_round=round_no, resume_from=None, pulse=session.pulse,
    )
    runner.start_question = {
        "status": state.get("status") or "", "reason": state.get("reason") or "",
        "qid": question.get("id") or state.get("question_id") or "",
        "waits": limit.get("waits", 0),
    }
    return runner


def run_branch_head(repo_path, slug):
    """HEAD ветки прогона (не текущего checkout) через rev-parse."""
    name = slug if str(slug).startswith("tero/") else f"tero/{slug}"
    proc = subprocess.run(["git", "rev-parse", name], cwd=repo_path, capture_output=True, text=True, check=False)
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def pre_lock_refusal(repo_path, run_path, cfg, state):
    """Отказ resume до lock и любых записей (ветка ушла вперёд)."""
    from src.loop.report import slugify as _slugify
    from src.loop.session import _refuse as _deny
    from src.loop.state import Status as _St

    if (state.get("status") or "") != _St.STOPPED.value:
        return None
    if run_branch_ahead(repo_path, _slugify(Path(cfg.plan_file).stem), state.get("base") or ""):
        return _deny(run_path, "ветка прогона ушла вперёд, запусти `tero go`")
    return None


def run_branch_ahead(repo_path, slug, base: str) -> bool:
    """Ветка прогона ушла вперёд base (до lock/checkout, без записей)."""
    if not base:
        return False
    try:
        head = run_branch_head(repo_path, slug)
    except Exception:
        head = ""
    if not head:
        try:
            from src.loop.git import head as _git_head

            head = _git_head(repo_path)
        except Exception:
            head = ""
    return bool(head) and head != base


def _next_before_ref(repo_path, run_path):
    from src.loop import snapshot as sm
    base_ref = f"refs/tero/{run_path.name}/before-resume"
    n = 1
    while sm.verify(repo_path, base_ref if n == 1 else f"{base_ref}-{n}"):
        n += 1
    return base_ref if n == 1 else f"{base_ref}-{n}"


def _restore_resume_tree(repo_path, run_path, slug, base, ref):
    from src.loop import snapshot as sm
    from src.loop.git import force_checkout_run_branch, head as git_head

    before, saved = _next_before_ref(repo_path, run_path), False
    if sm.tree_of(repo_path, base) != sm.ref_tree(repo_path, ref):
        sm.take_snapshot(repo_path, git_head(repo_path), before)
        saved = True
    force_checkout_run_branch(repo_path, slug)
    if sm.tree_of(repo_path, base) != sm.ref_tree(repo_path, ref):
        if not saved:
            before = _next_before_ref(repo_path, run_path)
            sm.take_snapshot(repo_path, git_head(repo_path), before)
        sm.restore_snapshot(repo_path, ref)


def _base_suite_paths(tmpp: Path):
    from src.loop import suite_guard as sg
    rels = [f.relative_to(tmpp).as_posix() for f in sorted(tmpp.rglob("*")) if f.is_file()]
    return [r for r in rels if sg.is_test_path(r) or Path(r).name in sg.SUITE_CONFIG_NAMES]


def base_suite_state(repo, base):
    from src.loop.git import file_hashes
    from src.loop.suite_guard import _snapshot_guarded

    with tempfile.TemporaryDirectory(prefix="tero-base-") as tmp:
        tmpp = Path(tmp)
        if (proc := subprocess.run(["git", "archive", base], cwd=repo, capture_output=True, check=False)).returncode != 0 or not proc.stdout:
            raise RuntimeError("archive failed")
        if subprocess.run(["tar", "-x", "-C", str(tmpp)], input=proc.stdout, capture_output=True, check=False).returncode != 0:
            raise RuntimeError("untargz failed")
        hashes = file_hashes(tmpp, _base_suite_paths(tmpp))
        guarded, targets = _snapshot_guarded(tmpp, hashes)
        return hashes, guarded, targets


from src.loop.resume.crash import (  # noqa: F401  (re-export)
    crash_decision,
    crash_phase_shipped,
    crash_rollback_to_head,
    crash_tree_of,
    dispatch,
)
from src.loop.resume.lock import (  # noqa: F401  (re-export)
    check_index_lock,
    acquire_for_crash,
)
from src.loop.resume.recover import crash_resume  # noqa: F401  (re-export)
from src.loop.resume.entry import ResumeStep  # noqa: F401  (re-export)
