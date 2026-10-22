"""Start a run, walk phases, stop with the spec exit codes."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.config import Config, UnknownProviderError, family_of, normalize_provider
from src.loop.gates import detect, print_red_tail, run_all
from src.loop.git import (
    DirtyWorktreeError,
    checkout_run_branch,
    current_branch,
    ensure_clean,
    exclude_tero,
    is_repo,
    run_branch_exists,
    status_short,
)
from src.loop.ledger import Ledger, new_run_dir
from src.loop.lock import acquire
from src.loop.phase import AgentRunner, PhaseOutcome, run_phase
from src.loop.plan import Phase, load_phases, load_preamble
from src.loop.state import RunState, Status

_SLUG = re.compile(r"[^a-zA-Z0-9]+")


@dataclass
class RunOutcome:
    status: str
    exit_code: int
    reason: str = ""
    phases_shipped: int = 0
    phases_total: int = 0
    run_dir: str = ""


async def run(
    cfg: Config,
    *,
    agents: AgentRunner,
    check_ready: Callable[[str, str], tuple[bool, str]] | None = None,
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
    try:
        state.update(
            status=Status.STARTING.value,
            pid=os.getpid(),
            plan=cfg.plan_file,
            doing="старт",
        )
    except Exception:
        pass

    def _fail_start(reason: str, doing: str = "") -> RunOutcome:
        try:
            state.update(
                status=Status.STOPPED.value,
                reason=reason,
                text=reason,
                doing=doing or "стоп",
            )
        except Exception:
            pass
        try:
            lock.release()
        except Exception:
            pass
        return RunOutcome(status="error", exit_code=1, reason=reason)

    try:
        state.update(doing="проверяю дерево …")
    except Exception:
        pass
    dirty_status = status_short(repo)
    if dirty_status:
        branch = current_branch(repo)
        if branch.startswith("tero/"):
            hint = "dirty tero branch; run: git reset --hard && git clean -fd"
        else:
            hint = "dirty worktree; commit or stash before running tero go"
        return _fail_start(_dirty_reason(hint, dirty_status))

    try:
        ensure_clean(repo)
    except DirtyWorktreeError:
        return _fail_start(_dirty_reason("dirty worktree", status_short(repo)))

    try:
        state.update(doing="проверяю провайдеров …")
    except Exception:
        pass
    try:
        _validate_providers(cfg)
    except UnknownProviderError as exc:
        return _fail_start(str(exc))

    player_family = family_of(f"{cfg.player_provider}:{cfg.player_model}")
    judge_family = family_of(f"{cfg.judge_provider}:{cfg.judge_model}")
    if player_family == judge_family:
        return _fail_start(
            f"судья из семьи исполнителя ({judge_family}): "
            f"player {cfg.player_provider}:{cfg.player_model}, "
            f"judge {cfg.judge_provider}:{cfg.judge_model}",
        )

    if check_ready:
        print("проверка провайдеров …", flush=True)
        for role, provider, model in _roles(cfg):
            ok, why = check_ready(provider, model)
            if not ok:
                return _fail_start(f"{role} provider ({provider}) not ready: {why}")

    try:
        state.update(doing="проверяю план …")
    except Exception:
        pass
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
        try:
            lock.release()
        except Exception:
            pass
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
            phases_total=len(phases),
        )

    try:
        state.update(doing="стартовые тесты …")
    except Exception:
        pass
    command = detect(
        repo,
        test_command=cfg.test_command,
        test_command_scoped=cfg.test_command_scoped,
    )
    if command is not None:
        cmd_str = " ".join(command.run_all)
        detected = "" if cfg.test_command.strip() else f" (автодетект: {command.name})"
        print(f"стартовый прогон тестов: {cmd_str}{detected} …", flush=True)
        gate = run_all(command, repo=repo, timeout_s=cfg.test_timeout_s)
        if not gate.ok:
            print(f"стартовый прогон тестов: КРАСНЫЕ (код {gate.exit_code})", flush=True)
            print_red_tail(gate.output)
            return _fail_start("red_start")

    checkout_run_branch(repo, slug)
    try:
        state.update(status=Status.RUNNING.value, doing="фаза 1")
    except Exception:
        pass
    ledger = Ledger(run_dir)
    ledger.event("run_start", plan=cfg.plan_file, slug=slug)

    preamble = load_preamble(plan_path)
    shipped = 0

    def _end(reason: str) -> None:
        ledger.event(
            "run_end",
            reason=reason,
            phases_shipped=shipped,
            phases_total=len(phases),
        )

    try:
        for phase in phases:
            if phase.done:
                continue
            outcome = await run_phase(
                phase,
                repo=repo,
                plan_path=plan_path,
                preamble=preamble,
                cfg=cfg,
                agents=agents,
                test_command=command,
                ledger=ledger,
                total_phases=len(phases),
            )
            _print_phase_summary(phase, outcome, total=len(phases))
            if outcome.status == "ship":
                shipped += 1
                continue
            _end(outcome.reason)
            try:
                state.update(
                    status=Status.STOPPED.value,
                    reason=outcome.reason,
                    text=outcome.reason,
                    doing="стоп",
                )
            except Exception:
                pass
            try:
                lock.release()
            except Exception:
                pass
            return RunOutcome(
                status="stop",
                exit_code=2,
                reason=outcome.reason,
                phases_shipped=shipped,
                phases_total=len(phases),
                run_dir=str(run_dir),
            )
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # Under asyncio.run, Ctrl+C reaches this coroutine as CancelledError.
        ledger.event("interrupted")
        _end("interrupted")
        try:
            state.update(
                status=Status.STOPPED.value,
                reason="interrupted",
                text="interrupted",
                doing="стоп",
            )
        except Exception:
            pass
        try:
            lock.release()
        except Exception:
            pass
        if isinstance(exc, asyncio.CancelledError):
            raise
        return RunOutcome(
            status="interrupted",
            exit_code=130,
            reason="interrupted",
            phases_shipped=shipped,
            phases_total=len(phases),
            run_dir=str(run_dir),
        )

    _end("ship")
    try:
        state.update(status=Status.DONE.value, doing="стоп")
    except Exception:
        pass
    try:
        lock.release()
    except Exception:
        pass
    return RunOutcome(
        status="ok",
        exit_code=0,
        reason="ship",
        phases_shipped=shipped,
        phases_total=len(phases),
        run_dir=str(run_dir),
    )


def _dirty_reason(hint: str, status: str) -> str:
    """Dirty-worktree reason: starts with 'dirty' so string-matching callers
    still work, but carries the hint and up to 10 status lines for the console."""
    lines = status.splitlines()[:10]
    body = "\n".join(f"    {line}" for line in lines)
    return f"dirty: {hint}" + (f"\n{body}" if body else "")


def rounds_word(rounds: int) -> str:
    if 11 <= (rounds % 100) <= 14:
        return "раундов"
    last = rounds % 10
    if last == 1:
        return "раунд"
    if 2 <= last <= 4:
        return "раунда"
    return "раундов"


def phase_summary_line(phase: Phase, outcome: PhaseOutcome, *, total: int) -> str:
    rounds = outcome.rounds
    text = f"фаза {phase.index + 1}/{total} «{phase.title}»: "
    if outcome.status == "ship":
        sha = (outcome.commit_sha or "")[:7]
        return f"{text}SHIP за {rounds} {rounds_word(rounds)} → {sha}"
    return f"{text}STOP ({outcome.reason}) за {rounds} {rounds_word(rounds)}"


def _print_phase_summary(phase: Phase, outcome: PhaseOutcome, *, total: int) -> None:
    print(phase_summary_line(phase, outcome, total=total), flush=True)


def slugify(name: str) -> str:
    text = _SLUG.sub("-", name).strip("-").lower()
    return text or "plan"


def _validate_providers(cfg: Config) -> None:
    for _role, provider, _model in _roles(cfg):
        if provider:
            normalize_provider(provider)


def _roles(cfg: Config) -> list[tuple[str, str, str]]:
    rows = [
        ("player", cfg.player_provider, cfg.player_model),
        ("judge", cfg.judge_provider, cfg.judge_model),
        ("tester", cfg.tester_provider, cfg.tester_model),
    ]
    if cfg.escalation_provider:
        rows.append(("escalation", cfg.escalation_provider, cfg.escalation_model))
    return rows
