"""Suite protection: tests, test hooks and agent configs the player may not touch."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from src.loop.git import checkout_paths, file_hashes, list_files, list_worktree_files

SUITE_CONFIG_NAMES = {
    "pytest.ini",
    "conftest.py",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
}
# A new one of these can skip or rewrite tests without touching a test file.
SUITE_HOOK_NAMES = {"conftest.py", "pytest.ini"}
# The judge's CLI loads these from the workspace (MCP servers, hooks, permissions).
AGENT_CONFIG_DIRS = {".claude", ".codex", ".cursor", ".gemini", ".opencode"}
AGENT_INSTRUCTION_NAMES = {
    ".mcp.json",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    "AGENTS.override.md",
    "GEMINI.md",
    ".cursorrules",
    "opencode.json",
    "opencode.jsonc",
}
_GUARDED_NAMES = SUITE_HOOK_NAMES | AGENT_INSTRUCTION_NAMES
_GUARDED_FOLDED = {name.casefold() for name in _GUARDED_NAMES}
_AGENT_DIRS_FOLDED = {name.casefold() for name in AGENT_CONFIG_DIRS}
_SUITE_HOOKS_FOLDED = {name.casefold() for name in SUITE_HOOK_NAMES}
_PRUNE_DIRS = {".git", ".tero", "node_modules"}


def is_test_path(rel: str) -> bool:
    path = Path(rel)
    if any(part in {"tests", "test", "__tests__"} for part in path.parts):
        return True
    name = path.name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith(("_test.py", "_test.go")):
        return True
    return name.endswith(
        (".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.tsx", ".spec.js")
    )


def _suite_paths(repo: Path) -> list[str]:
    paths: list[str] = []
    for rel in list_files(repo):
        if is_test_path(rel) or Path(rel).name in SUITE_CONFIG_NAMES:
            paths.append(rel)
    return paths


def _remove_guarded(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def _strip_unsafe_ancestors(repo: Path, rel: str) -> list[str]:
    # Never resolve outside the repo: an ancestor that is a symlink (or a
    # plain file where a dir should be) is unlinked as itself, never followed.
    stripped: list[str] = []
    parts = Path(rel).parts
    for i in range(1, len(parts)):
        anc = Path(*parts[:i])
        full = repo / anc
        if full.is_symlink() or (full.exists() and not full.is_dir()):
            full.unlink()
            stripped.append(anc.as_posix())
    return stripped


# kind, payload: ("file", bytes), ("link", os.readlink target) or ("dir", b"")
Entry = tuple[str, Any]


def _base_guarded_entries(
    repo: Path, excluded: dict[str, str]
) -> dict[str, Entry]:
    # os.walk, not git: nested repos' worktrees and gitignored paths are walked too.
    entries: dict[str, Entry] = {}
    for walk_root, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _PRUNE_DIRS]
        try:
            rel_root = Path(walk_root).relative_to(repo)
        except ValueError:
            continue
        for name in [*dirs, *files]:
            rel = rel_root / name if rel_root.parts else Path(name)
            posix = rel.as_posix()
            if posix in excluded:
                continue
            full = repo / rel
            folded = name.casefold()
            in_config_dir = any(
                part.casefold() in _AGENT_DIRS_FOLDED for part in rel.parts[:-1]
            )
            guarded = folded in _GUARDED_FOLDED or folded in _AGENT_DIRS_FOLDED
            if full.is_symlink():
                # one entry for the link itself; os.walk never descends into it
                if in_config_dir or guarded:
                    try:
                        entries[posix] = ("link", os.readlink(full))
                    except OSError:
                        continue
            elif (in_config_dir or guarded) and full.is_file():
                entries[posix] = ("file", full.read_bytes())
    return entries


def _follow_link(
    repo: Path, root: Path, link: str, excluded: dict[str, str]
) -> str | None:
    # One lexical step along a symlink chain; None when the chain ends here.
    try:
        payload = os.readlink(repo / link)
    except OSError:
        return None
    if os.path.isabs(payload):
        target = Path(os.path.normpath(payload))
        for base in (root, repo.absolute()):
            try:
                rel = target.relative_to(base)
                break
            except ValueError:
                continue
        else:
            return None
    else:
        rel = Path(os.path.normpath((Path(link).parent / payload).as_posix()))
        if not rel.parts or rel.parts[0] == "..":
            return None
    posix = rel.as_posix()
    if posix == "." or posix in excluded:
        return None
    if any(part in _PRUNE_DIRS for part in rel.parts):
        return None
    return posix


def _chain_end(
    repo: Path, root: Path, link: str, excluded: dict[str, str]
) -> tuple[list[str], str | None]:
    # Step through the chain lexically via os.readlink; every intermediate
    # link inside the repo is returned so the caller can record it. None end
    # means a cycle or a step leaving the repo — the chain is dropped.
    steps: list[str] = []
    seen: set[str] = {link}
    cur = link
    while (repo / cur).is_symlink():
        nxt = _follow_link(repo, root, cur, excluded)
        if nxt is None or nxt in seen:
            return steps, None
        seen.add(nxt)
        steps.append(nxt)
        if not (repo / nxt).is_symlink():
            return steps, nxt
        cur = nxt
    # The start stopped being a symlink (retargeted mid-walk).
    return steps, (cur if steps else None)


def _resolve_in_repo(root: Path, rel: str) -> bool:
    # Lexically inside the repo and not the root itself; .resolve() only
    # confirms it — nothing is written through the checked path.
    if not rel or rel == "." or Path(rel).is_absolute():
        return False
    if ".." in Path(rel).parts:
        return False
    try:
        under = (root / rel).resolve()
    except OSError:
        return False
    return under != root and under.is_relative_to(root)


def _walk_target(
    repo: Path, target: str, entries: dict[str, Entry], excluded: dict[str, str]
) -> None:
    # Never walk or read a target through a link: neither the target itself
    # nor any ancestor inside the repo may be a symlink.
    probe = repo
    for part in Path(target).parts:
        probe = probe / part
        if probe.is_symlink():
            return
    full = repo / target
    if full.is_file():
        if target not in excluded and target not in entries:
            entries[target] = ("file", full.read_bytes())
    elif full.is_dir():
        if target not in excluded and target not in entries:
            entries[target] = ("dir", b"")
        for walk_root, walk_dirs, walk_files in os.walk(full, followlinks=False):
            walk_dirs[:] = [d for d in walk_dirs if d not in _PRUNE_DIRS]
            for name in [*walk_dirs, *walk_files]:
                sub = Path(walk_root) / name
                try:
                    rel = (Path(target) / sub.relative_to(full)).as_posix()
                except ValueError:
                    continue
                if rel in excluded or rel in entries:
                    continue
                if sub.is_symlink():
                    try:
                        entries[rel] = ("link", os.readlink(sub))
                    except OSError:
                        continue
                elif sub.is_file():
                    entries[rel] = ("file", sub.read_bytes())


def _snapshot_guarded(
    repo: Path,
    suite_hashes: dict[str, str] | None = None,
    fixed_targets: set[str] | None = None,
    fixed_paths: set[str] | None = None,
) -> tuple[dict[str, Entry], set[str]]:
    excluded = suite_hashes or {}
    entries = _base_guarded_entries(repo, excluded)
    if fixed_targets is not None:
        for target in sorted(fixed_targets):
            _walk_target(repo, target, entries, excluded)
        for path in fixed_paths or ():
            probe = repo
            unsafe = False
            for part in Path(path).parts[:-1]:
                probe = probe / part
                if probe.is_symlink() or (probe.exists() and not probe.is_dir()):
                    unsafe = True
                    break
            if unsafe:
                entries.pop(path, None)
                continue
            full = repo / path
            if full.is_symlink():
                try:
                    entries[path] = ("link", os.readlink(full))
                except OSError:
                    entries.pop(path, None)
            elif full.is_file():
                entries[path] = ("file", full.read_bytes())
            elif full.is_dir() and not full.is_symlink():
                entries[path] = ("dir", b"")
            else:
                entries.pop(path, None)
        return entries, set(fixed_targets)
    root = repo.resolve()
    targets: set[str] = set()
    worklist = [posix for posix, (kind, _) in entries.items() if kind == "link"]
    visited: set[str] = set()

    def _drain() -> None:
        # Record every intermediate link along each chain with its current
        # payload, and pin + walk each chain end.
        while worklist:
            posix = worklist.pop()
            if posix in visited:
                continue
            visited.add(posix)
            steps, end = _chain_end(repo, root, posix, excluded)
            for step in steps:
                if step not in entries and (repo / step).is_symlink():
                    try:
                        entries[step] = ("link", os.readlink(repo / step))
                    except OSError:
                        continue
                    worklist.append(step)
            if end is not None and end not in targets:
                if _resolve_in_repo(root, end):
                    targets.add(end)
                    _walk_target(repo, end, entries, excluded)
                    for sub, (kind, _) in list(entries.items()):
                        if kind == "link" and sub not in visited:
                            worklist.append(sub)

    _drain()
    return entries, targets


def protect_tests(
    repo: Path,
    state: Any,
    *,
    ledger: Any,
    checkpoint: str,
    suite_hashes: dict[str, str],
    guarded: dict[str, Entry],
    link_targets: set[str],
) -> bool:
    # Restore every violation in one pass: one per round would burn a round
    # (and look like no progress) for each extra file the player touched.
    state.suite_delta = []
    violated = False
    for path, test in list(state.protected.items()):
        current = file_hashes(repo, [path]).get(path)
        if current == test.digest:
            continue
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(test.content)
        ledger.event("protected_violation", path=path, round=state.round)
        print(f"  защищённый тест изменён: {path} — откат", flush=True)
        violated = True

    for path, digest in suite_hashes.items():
        if not (repo / path).exists():
            checkout_paths(repo, checkpoint, [path])
            ledger.event("suite_violation", path=path, round=state.round)
            print(f"  тест удалён вне протокола: {path} — восстановлен", flush=True)
            violated = True
            continue
        current = file_hashes(repo, [path]).get(path)
        if current and current != digest:
            state.suite_delta.append(path)

    # Not a burned round: the player's code still goes to the gate and judge.
    current, _ = _snapshot_guarded(
        repo, suite_hashes, link_targets, set(guarded)
    )
    removed_dirs: set[str] = set()
    for path in sorted(set(current) | set(guarded), key=lambda p: (p in guarded, p)):
        if any(path.startswith(done + "/") for done in removed_dirs):
            # A parent dir was already removed wholesale (rmtree) above;
            # this entry is stale — nothing left to do for it.
            continue
        target = repo / path
        original = guarded.get(path)
        now = current.get(path)
        if original is not None and now is not None and now == original:
            continue
        if target.is_dir() and not target.is_symlink():
            removed_dirs.add(path)
        for anc in _strip_unsafe_ancestors(repo, path):
            ledger.event(
                "agent_config_violation",
                path=anc,
                round=state.round,
            )
            print(
                f"  конфиг агента изменён вне протокола: {anc} — откат",
                flush=True,
            )
        _remove_guarded(target)
        if original is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            kind, payload = original
            if kind == "link":
                os.symlink(payload, target)
            elif kind == "dir":
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.write_bytes(payload)
        hook = Path(path).name.casefold() in _SUITE_HOOKS_FOLDED
        ledger.event(
            "suite_violation" if hook else "agent_config_violation",
            path=path,
            round=state.round,
        )
        what = "хук тестов" if hook else "конфиг агента"
        print(f"  {what} изменён вне протокола: {path} — откат", flush=True)
    return violated


def inside_repo(repo: Path, path: str) -> bool:
    root = repo.resolve()
    return (root / path).resolve().is_relative_to(root)


def revert_non_test(repo: Path, before_tree: str, before_files: set[str]) -> None:
    for path in list_worktree_files(repo):
        if is_test_path(path):
            continue
        revert_path(repo, before_tree, before_files, path)


def revert_path(repo: Path, before_tree: str, before_files: set[str], path: str) -> None:
    target = repo / path
    if path in before_files:
        checkout_paths(repo, before_tree, [path])
    elif target.exists():
        target.unlink()
