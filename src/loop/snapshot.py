"""Work snapshots in refs/tero/... via a temporary git index.

The snapshot never touches the working index or the worktree: all
plumbing runs with a private GIT_INDEX_FILE that is removed in `finally`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

from src.loop import git


def ref_name(run_id: str, phase_index: int, round: int) -> str:
    return f"refs/tero/{run_id}/phase-{phase_index:02d}-round{round:02d}"


def _default_deny() -> tuple[str, ...]:
    return (*git.SECRET_PATTERNS, "node_modules/", ".tero/")


def _denied(rel: str, deny: tuple[str, ...]) -> bool:
    for rule in deny:
        if rule.endswith("/"):
            if rel == rule[:-1] or rel.startswith(rule):
                return True
    return git.is_secret_path(rel)


def _git_plain(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise git.GitError(f"git {' '.join(args)} failed: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise git.GitError(f"git {' '.join(args)} failed: {err}")
    return proc.stdout


def diff_for(repo: Path, base: str, ref: str) -> str:
    """`git diff base ref` — committed objects only, ignores the worktree."""
    return _git_plain(Path(repo), "diff", base, ref)


def verify(repo: Path, ref: str) -> bool:
    """True iff <ref>^{tree} resolves — i.e. the snapshot was recorded."""
    try:
        _git_plain(Path(repo), "cat-file", "-e", f"{ref}^{{tree}}")
    except git.GitError:
        return False
    return True


def restore_snapshot(repo: Path, ref: str) -> None:
    """Make index and worktree the snapshot tree; HEAD does not move."""
    git.restore_tree(Path(repo), f"{ref}^{{tree}}")


def _git_tmp(repo: Path, index_file: str, *args: str) -> str:
    env = {**os.environ, "GIT_INDEX_FILE": index_file}
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except OSError as exc:
        raise git.GitError(f"git {' '.join(args)} failed: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise git.GitError(f"git {' '.join(args)} failed: {err}")
    return proc.stdout


def _git_dir(repo: Path) -> Path:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise git.GitError(f"git rev-parse --absolute-git-dir failed: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise git.GitError(f"git rev-parse --absolute-git-dir failed: {err}")
    out = Path(proc.stdout.strip())
    return out if out.is_absolute() else repo / out


def take_snapshot(
    repo: Path,
    base: str,
    ref: str,
    deny: Iterable[str] | None = None,
) -> str:
    rules = tuple(deny) if deny is not None else _default_deny()
    repo = Path(repo)
    fd, index_file = tempfile.mkstemp(prefix="tero-snapshot-", dir=str(_git_dir(repo)))
    os.close(fd)
    try:
        _git_tmp(repo, index_file, "read-tree", base)
        _git_tmp(repo, index_file, "add", "-A")
        tracked = _git_tmp(repo, index_file, "ls-files", "-z", "--cached").split("\0")
        denied = sorted({p for p in tracked if p and _denied(p, rules)})
        if denied:
            _git_tmp(
                repo,
                index_file,
                "rm",
                "--cached",
                "-q",
                "--",
                *[f":(literal){p}" for p in denied],
            )
        tree = _git_tmp(repo, index_file, "write-tree").strip()
        sha = _git_tmp(
            repo, index_file, "commit-tree", tree, "-p", base, "-m", f"tero snapshot {ref}"
        ).strip()
        _git_tmp(repo, index_file, "update-ref", ref, sha)
        return sha
    finally:
        try:
            os.unlink(index_file)
        except OSError:
            pass
