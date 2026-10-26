"""Plan file guard: remember bytes on entry, restore after a role."""

from __future__ import annotations

from typing import Any


def remember_plan(runner: Any) -> bytes | None:
    """Bytes of the plan at entry; None means untracked."""
    try:
        resolved = runner.plan_path.resolve()
        repo_root = runner.repo.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(repo_root):
        return None
    if runner.start_with_judge and runner.checkpoint:
        from src.loop.resume_step import _git_show_bytes

        return _git_show_bytes(runner.repo, runner.checkpoint, resolved)
    try:
        return runner.plan_path.read_bytes() if runner.plan_path.is_file() else None
    except OSError:
        return None


def guard_plan(runner: Any, role: str) -> None:
    """Restore the plan when a role changed it. Only tero writes."""
    if runner._plan_bytes is None:
        return
    try:
        current = runner.plan_path.read_bytes() if runner.plan_path.is_file() else None
    except OSError:
        current = None
    if current == runner._plan_bytes:
        return
    try:
        runner.plan_path.parent.mkdir(parents=True, exist_ok=True)
        runner.plan_path.write_bytes(runner._plan_bytes)
    except OSError as exc:
        print(f"  план изменён, восстановить не удалось: {exc}", flush=True)
        runner.ledger.event(
            "plan_restore_failed",
            path=runner.plan_path.name,
            phase=runner.phase.title,
            round=runner.state.round,
            role=role,
            error=str(exc),
        )
        runner.ctx.emit(
            "plan_restore_failed",
            role=role,
            text=f"{role} менял план, восстановить не удалось",
        )
        return
    runner.ledger.event(
        "plan_restored",
        path=runner.plan_path.name,
        phase=runner.phase.title,
        round=runner.state.round,
        role=role,
        text=f"{role} менял план, вернул",
    )
    runner.ctx.emit("plan_restored", role=role, text=f"{role} менял план, вернул")
