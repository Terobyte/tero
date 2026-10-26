"""Gitignored files around the tester's turn (v4 BUG-007).

`suite_guard.revert_non_test` walks `git ls-files -co --exclude-standard`, so
gitignored paths (`.env` and friends) are invisible to it and a tester edit
there would survive both the revert and the rollback (`clean -fd` keeps
ignored files). Snapshot them before the tester and put them back after.
"""

from __future__ import annotations

from pathlib import Path

from src.loop.git import list_ignored_files

# run files, caches and dependency trees: not the tester's to touch, too big to snapshot
PRUNE_DIRS = {".git", ".tero", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv"}


def _pruned(rel: str) -> bool:
    return any(part in PRUNE_DIRS for part in Path(rel).parts)


def snapshot_ignored(repo: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for rel in list_ignored_files(repo):
        target = repo / rel
        if _pruned(rel) or target.is_symlink() or not target.is_file():
            continue
        try:
            snap[rel] = target.read_bytes()
        except OSError:
            continue
    return snap


def restore_ignored(repo: Path, snap: dict[str, bytes]) -> list[str]:
    """Put snapshotted files back, drop ignored files created since. Returns changed paths."""
    changed: list[str] = []
    now = {rel for rel in list_ignored_files(repo) if not _pruned(rel)}
    for rel in sorted(now | set(snap)):
        target = repo / rel
        original = snap.get(rel)
        try:
            if original is None:
                if target.is_symlink() or target.is_file():
                    target.unlink(missing_ok=True)
                    changed.append(rel)
                continue
            if not target.is_symlink() and target.is_file() and target.read_bytes() == original:
                continue
            if target.is_symlink():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
            changed.append(rel)
        except OSError:
            continue
    return changed
