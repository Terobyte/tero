"""Plan file guard: remember bytes on entry, restore after a role."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
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
        from src.loop.resume.step import _git_show_bytes

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
        from src.loop.guards.ignored import _safe_parent

        path = Path(os.path.abspath(runner.plan_path))
        rel = str(_plan_rel(runner.repo, path))
        safe = _safe_parent(runner.repo, rel) and path.resolve().is_relative_to(runner.repo.resolve())
        current = path.read_bytes() if safe and path.is_file() else None
    except (OSError, ValueError, RuntimeError):
        current = None
    if current == runner._plan_bytes:
        return
    try:
        _restore_plan(runner.repo, runner.plan_path, runner._plan_bytes)
    except (OSError, ValueError) as exc:
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


def _plan_rel(repo: Path, path: Path) -> Path:
    """Plan path relative to the repo, whether or not the repo path has a symlinked prefix."""
    try:
        return path.relative_to(os.path.abspath(repo))
    except ValueError:
        return path.relative_to(repo.resolve())


def _restore_plan(repo: Path, path: Path, data: bytes) -> None:
    """Replace the leaf without following role-created links, including parents."""
    root = repo.resolve()
    relative = _plan_rel(repo, Path(os.path.abspath(path)))
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".tero-plan-{uuid.uuid4().hex}.tmp"
    try:
        for part in relative.parts[:-1]:
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        mode = 0o644
        try:
            original = os.stat(relative.name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(original.st_mode):
                mode = stat.S_IMODE(original.st_mode)
        except FileNotFoundError:
            pass
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=fd)
        with os.fdopen(out, "wb") as stream:
            stream.write(data)
        os.replace(temporary, relative.name, src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(fd)
