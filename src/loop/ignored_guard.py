"""Gitignored files around the tester's turn (v4 BUG-007).

`suite_guard.revert_non_test` walks `git ls-files -co --exclude-standard`, so
gitignored paths (`.env` and friends) are invisible to it and a tester edit
there would survive both the revert and the rollback (`clean -fd` keeps
ignored files). Snapshot them before the tester and put them back after.

Fail-safe: every path that existed before is recorded (files by content,
symlinks by target, unreadable files as "keep"), and only paths absent from
that record are deleted. Nothing is written through a symlinked parent dir.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.loop.git import list_ignored_files

# run files, caches and dependency trees: not the tester's to touch, too big to snapshot
PRUNE_DIRS = {".git", ".tero", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv"}

_KEEP = None  # existed before, contents not readable: never delete, never rewrite


def _pruned(rel: str) -> bool:
    return any(part in PRUNE_DIRS for part in Path(rel).parts)


def _safe_parent(repo: Path, rel: str) -> bool:
    """No symlink among the parents of `rel` inside the repo, and it stays inside."""
    root = repo.resolve()
    cur = repo
    for part in Path(rel).parts[:-1]:
        cur = cur / part
        if cur.is_symlink():
            return False
    try:
        return (repo / rel).parent.resolve().is_relative_to(root)
    except OSError:
        return False


def snapshot_ignored(repo: Path) -> dict[str, tuple[str, bytes | str | None]]:
    """rel -> ("file", bytes) | ("link", target) | ("keep", None)."""
    snap: dict[str, tuple[str, bytes | str | None]] = {}
    for rel in list_ignored_files(repo):
        if _pruned(rel):
            continue
        target = repo / rel
        try:
            if target.is_symlink():
                snap[rel] = ("link", os.readlink(target))
            elif target.is_file():
                snap[rel] = ("file", target.read_bytes())
        except OSError:
            snap[rel] = ("keep", _KEEP)
    return snap


def _same(target: Path, kind: str, value: bytes | str | None) -> bool:
    if kind == "link":
        return target.is_symlink() and os.readlink(target) == value
    return not target.is_symlink() and target.is_file() and target.read_bytes() == value


def restore_ignored(repo: Path, snap: dict[str, tuple[str, bytes | str | None]]) -> list[str]:
    """Put recorded paths back, drop ignored files created since. Returns changed paths."""
    changed: list[str] = []
    now = {rel for rel in list_ignored_files(repo) if not _pruned(rel)}
    for rel in sorted(now | set(snap)):
        target = repo / rel
        if not _safe_parent(repo, rel):
            continue  # a symlinked parent dir: never write or delete through it
        kind, value = snap.get(rel, ("new", None))
        try:
            if kind == "keep":
                continue
            if kind == "new":
                if target.is_symlink() or target.is_file():
                    target.unlink(missing_ok=True)
                    changed.append(rel)
                continue
            if _same(target, kind, value):
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "link":
                os.symlink(str(value), target)
            else:
                target.write_bytes(value)  # type: ignore[arg-type]
            changed.append(rel)
        except OSError:
            continue
    return changed
