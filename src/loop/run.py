"""Start a run, walk phases, stop with the spec exit codes."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.config import Config, UnknownProviderError, normalize_provider
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
from src.loop.phase import AgentRunner, run_phase
from src.loop.plan import load_phases, load_preamble

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
    try:
        _validate_providers(cfg)
    except UnknownProviderError as exc:
        return RunOutcome(status="error", exit_code=1, reason=str(exc))

    if check_ready:
        print("проверка провайдеров …", flush=True)
        for role, provider, model in _roles(cfg):
            ok, why = check_ready(provider, model)
            if not ok:
                return RunOutcome(
                    status="error",
                    exit_code=1,
                    reason=f"{role} provider ({provider}) not ready: {why}",
                )

    if not is_repo(repo):
        return RunOutcome(status="error", exit_code=1, reason="not a git repo")

    dirty_status = status_short(repo)
    if dirty_status:
        branch = current_branch(repo)
        if branch.startswith("tero/"):
            hint = "dirty tero branch; run: git reset --hard && git clean -fd"
        else:
            hint = "dirty worktree; commit or stash before running tero go"
        return RunOutcome(status="error", exit_code=1, reason=_dirty_reason(hint, dirty_status))

    try:
        ensure_clean(repo)
    except DirtyWorktreeError:
        return RunOutcome(
            status="error", exit_code=1, reason=_dirty_reason("dirty worktree", status_short(repo))
        )

    plan_path = repo / cfg.plan_file
    if not plan_path.is_file():
        return RunOutcome(status="error", exit_code=1, reason=f"plan not found: {plan_path}")

    slug = slugify(Path(cfg.plan_file).stem)
    if run_branch_exists(repo, slug):
        # Resume: the [x] marks live on the run branch, not on the branch we started from.
        checkout_run_branch(repo, slug)

    phases = load_phases(plan_path)
    if not any(phase.checkboxes for phase in phases):
        return RunOutcome(
            status="error",
            exit_code=1,
            reason=f"no '- [ ]' checkboxes found in {plan_path}",
        )
    if all(phase.done for phase in phases):
        # No branch, no run dir, no test gate: there is nothing to judge.
        return RunOutcome(
            status="ok",
            exit_code=0,
            reason="nothing_to_do",
            phases_total=len(phases),
        )

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
            return RunOutcome(status="error", exit_code=1, reason="red_start")

    checkout_run_branch(repo, slug)
    exclude_tero(repo)
    run_dir = new_run_dir(repo, slug)
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
            )
            if outcome.status == "ship":
                shipped += 1
                continue
            _end(outcome.reason)
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
