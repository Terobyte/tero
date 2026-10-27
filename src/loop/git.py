"""Git operations for a run. All commands go through subprocess."""

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
    name = _run_branch(slug)
    if _branch_exists(repo, name):
        _git(repo, "checkout", name)
    else:
        _git(repo, "checkout", "-b", name)
    return name


def run_branch_exists(repo: str | Path, slug: str) -> bool:
    return _branch_exists(repo, _run_branch(slug))


def force_checkout_run_branch(repo: str | Path, slug: str) -> str:
    """Вернуться на ветку прогона, не теряя незакоммиченную работу.

    before-resume уже снят до вызова, поэтому конфликтующие правки можно
    сбросить: checkout идёт с `-f`, дерево дальше выравнивается на ref.
    Новую ветку (её ещё нет) создаём как обычно.
    """
    name = slug if slug.startswith("tero/") else f"tero/{slug}"
    if run_branch_exists(repo, slug):
        _force_checkout(repo, name)
    else:
        checkout_run_branch(repo, slug)
    return name


def _force_checkout(repo: str | Path, name: str) -> None:
    try:
        proc = subprocess.run(
            ["git", "checkout", "-f", name],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"git checkout -f {name} failed: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise GitError(f"git checkout -f {name} failed: {err}")


def _run_branch(slug: str) -> str:
    return slug if slug.startswith("tero/") else f"tero/{slug}"


def head(repo: str | Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def stage_and_diff(repo: str | Path, base: str) -> str:
    _git(repo, "add", "-A")
    return _git(repo, "diff", "--cached", "-W", base)


def write_tree(repo: str | Path) -> str:
    _git(repo, "add", "-A")
    return _git(repo, "write-tree").strip()


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


def has_staged_changes(repo: str | Path) -> bool:
    try:
        _git(repo, "diff", "--cached", "--quiet")
    except GitError:
        return True
    return False


def commit(repo: str | Path, msg: str, *, trailer: str = "", allow_empty: bool = False) -> str:
    args = ["commit", "-m", msg]
    if allow_empty:
        args.append("--allow-empty")
    if trailer:
        args += ["--trailer", trailer]
    _git(repo, *args)
    return head(repo)


def phase_shipped(repo: str | Path, base: str, run_id: str, n: int) -> bool:
    """Есть ли коммит с трейлером `Tero-Phase: <run_id>/<n>` в `base..HEAD`."""
    want = f"{run_id}/{n}"
    log = _git(repo, "log", f"{base}..HEAD", "--format=%B%x1e")
    for chunk in log.split("\x1e"):
        if _trailers(chunk).get("Tero-Phase") == want:
            return True
    return False


def _trailers(body: str) -> dict[str, str]:
    """Git-трейлеры: только концевой `Key: value` блок после пустой строки."""
    lines = body.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    found: dict[str, str] = {}
    for line in reversed(lines):
        s = line.strip()
        if not s:
            break
        if ":" not in s:
            break
        key, _, val = s.partition(":")
        key = key.strip()
        if not key or any(c.isspace() for c in key):
            break
        found.setdefault(key, val.strip())
    return found


def exclude_tero(repo: str | Path) -> None:
    exclude = Path(repo) / _git(repo, "rev-parse", "--git-path", "info/exclude").strip()
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    if ".tero/" in {line.strip() for line in existing.splitlines()}:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    exclude.write_text(existing + f"{prefix}.tero/\n")


def status_short(repo: str | Path) -> str:
    return _git(repo, "status", "--short").strip()


def secret_paths(repo: str | Path) -> list[str]:
    names = _porcelain_names(repo)
    names += [n for n in _names(repo, "diff", "--cached", "--name-only") if n not in names]
    return [name for name in names if is_secret_path(name)]


def staged_secret_paths(repo: str | Path) -> list[str]:
    """Secrets already staged: what a commit would record (F12 `_ship` order)."""
    names = _names(repo, "diff", "--cached", "--name-only")
    return [name for name in names if is_secret_path(name)]


def _porcelain_names(repo: str | Path) -> list[str]:
    """Worktree changes incl. untracked: parse `git status --porcelain=v1 -z`."""
    try:
        raw = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    except GitError:
        return []
    out: list[str] = []
    for chunk in raw.split("\0"):
        if not chunk:
            continue
        # porcelain v1 -z: XY<space>path[\0orig\0new for renames]
        path = chunk[3:] if len(chunk) > 3 else chunk.strip()
        if path:
            out.append(path)
    return out


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
    return _names(repo, "ls-files")


def list_worktree_files(repo: str | Path) -> list[str]:
    return _names(repo, "ls-files", "-co", "--exclude-standard")


def list_ignored_files(repo: str | Path) -> list[str]:
    return _names(repo, "ls-files", "--others", "--ignored", "--exclude-standard")


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


def _names(repo: str | Path, cmd: str, *args: str) -> list[str]:
    # -z: git quotes non-ASCII, tab and quote characters in plain path output.
    # It goes right after the subcommand so a "--" pathspec list cannot swallow it.
    return [name for name in _git(repo, cmd, "-z", *args).split("\0") if name]


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
