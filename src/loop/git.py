"""Git operations for a v4 run. All commands go through subprocess."""

from __future__ import annotations

import fnmatch
import hashlib
import subprocess
from pathlib import Path

SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.p12",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "*secret*",
    "*credentials*",
)


class GitError(Exception):
    """A required git command failed."""


class DirtyWorktreeError(GitError):
    """Working tree is not clean."""


def ensure_clean(repo: str | Path) -> None:
    if status_short(repo):
        raise DirtyWorktreeError("working tree is dirty")


def checkout_run_branch(repo: str | Path, slug: str) -> str:
    name = slug if slug.startswith("tero/") else f"tero/{slug}"
    if _branch_exists(repo, name):
        _git(repo, "checkout", name)
    else:
        _git(repo, "checkout", "-b", name)
    return name


def head(repo: str | Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def stage_and_diff(repo: str | Path, base: str) -> str:
    _git(repo, "add", "-A")
    return _git(repo, "diff", "--cached", "-W", base)


def write_tree(repo: str | Path) -> str:
    _git(repo, "add", "-A")
    return _git(repo, "write-tree").strip()


def restore_tree(repo: str | Path, tree: str) -> None:
    _git(repo, "read-tree", tree)
    _git(repo, "checkout-index", "-a", "-f")
    _git(repo, "clean", "-fd")


def file_hashes(repo: str | Path, paths: list[str]) -> dict[str, str]:
    root = Path(repo)
    hashes: dict[str, str] = {}
    for rel in paths:
        path = root / rel
        if not path.is_file():
            continue
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def rollback(repo: str | Path, base: str) -> None:
    _git(repo, "reset", "--hard", base)
    _git(repo, "clean", "-fd")


def commit(repo: str | Path, msg: str) -> str:
    _git(repo, "commit", "-m", msg)
    return head(repo)


def exclude_tero(repo: str | Path) -> None:
    exclude = Path(repo) / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    if ".tero/" in existing.splitlines() or existing.rstrip().endswith(".tero/"):
        return
    if ".tero/" in {line.strip() for line in existing.splitlines()}:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    exclude.write_text(existing + f"{prefix}.tero/\n")


def status_short(repo: str | Path) -> str:
    return _git(repo, "status", "--short").strip()


def secret_paths(repo: str | Path) -> list[str]:
    names = [
        line
        for line in _git(repo, "diff", "--cached", "--name-only").splitlines()
        if line
    ]
    return [name for name in names if is_secret_path(name)]


def is_secret_path(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name
    for pattern in SECRET_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def checkout_paths(repo: str | Path, base: str, paths: list[str]) -> None:
    if not paths:
        return
    _git(repo, "checkout", base, "--", *paths)


def list_files(repo: str | Path) -> list[str]:
    return [line for line in _git(repo, "ls-files").splitlines() if line]


def list_worktree_files(repo: str | Path) -> list[str]:
    return [
        line
        for line in _git(repo, "ls-files", "-co", "--exclude-standard").splitlines()
        if line
    ]


def current_branch(repo: str | Path) -> str:
    return _git(repo, "branch", "--show-current").strip()


def is_repo(repo: str | Path) -> bool:
    try:
        _git(repo, "rev-parse", "--is-inside-work-tree")
        return True
    except GitError:
        return False


def _branch_exists(repo: str | Path, name: str) -> bool:
    try:
        _git(repo, "rev-parse", "--verify", f"refs/heads/{name}")
        return True
    except GitError:
        return False


def _git(repo: str | Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"git {' '.join(args)} failed: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise GitError(f"git {' '.join(args)} failed: {err}")
    return proc.stdout
