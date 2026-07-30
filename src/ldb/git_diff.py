"""Git-diff-driven scope for LDB — only scan functions that actually changed.

The default mode for ``tero ldb`` runs in ``--changed`` mode: we ask git for
the unified diff against a base ref (default ``HEAD``: working tree + staged),
extract the set of new-side line numbers per file, then keep only those
``LdbTarget`` items whose ``[lineno, end_lineno]`` range overlaps the diff.

Untracked ``.py`` files have no diff, but they are obviously "all new" — they
are marked with a sentinel ``None`` in the ``changed_lines`` mapping and the
filter accepts every target inside them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

# Mapping ``relative_path -> set of changed new-side line numbers``.
# A value of ``None`` means "treat every line as changed" (untracked file).
ChangedLines = dict[str, "set[int] | None"]


def _parse_unified_diff(diff_text: str) -> ChangedLines:
    """Parse ``git diff --unified=0`` output into per-file changed line sets."""
    changed: ChangedLines = {}
    current: str | None = None

    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path == "/dev/null":
                current = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            current = path
            existing = changed.get(current)
            if existing is None and current not in changed:
                changed[current] = set()
            continue

        if raw.startswith("@@") and current is not None:
            # Header form: ``@@ -old,c +new,c @@ optional context``
            parts = raw.split()
            if len(parts) < 3:
                continue
            new_range = parts[2]
            if not new_range.startswith("+"):
                continue
            new_range = new_range[1:]
            try:
                if "," in new_range:
                    start_s, count_s = new_range.split(",", 1)
                    start = int(start_s)
                    count = int(count_s)
                else:
                    start = int(new_range)
                    count = 1
            except ValueError:
                continue
            if count <= 0:
                # Pure deletion hunk — nothing on the new side to scan.
                continue
            bucket = changed.setdefault(current, set())
            if bucket is None:
                continue
            bucket.update(range(start, start + count))

    return changed


def _git(working_dir: str, *args: str, timeout: float = 30.0) -> str | None:
    """Run a git command, returning stdout on success or ``None`` on failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def get_changed_lines(
    working_dir: str | Path, base: str = "HEAD"
) -> ChangedLines:
    """Return per-file changed-line map for the working tree relative to ``base``.

    ``base`` defaults to ``HEAD`` so we capture both staged and unstaged edits.
    Use ``origin/main`` (or any other ref) for PR-style "what does this branch
    change" semantics.

    Untracked ``*.py`` files are added with value ``None`` (treat as all-new).
    """
    wd = str(Path(working_dir).expanduser().resolve())

    diff = _git(wd, "diff", "--unified=0", "--no-color", base, "--")
    changed: ChangedLines = _parse_unified_diff(diff) if diff else {}

    status = _git(wd, "status", "--porcelain", "--untracked-files=normal")
    if status:
        for line in status.splitlines():
            if not line.startswith("?? "):
                continue
            path = line[3:].strip()
            if not path.endswith(".py"):
                continue
            changed[path] = None  # sentinel: every line in this file is "new"

    return changed


def filter_targets_by_diff(
    targets: Iterable, changed_lines: ChangedLines
) -> list:
    """Keep targets whose ``[lineno, end_lineno]`` intersects changed lines."""
    out: list = []
    if not changed_lines:
        return out
    for t in targets:
        if t.file not in changed_lines:
            continue
        bucket = changed_lines[t.file]
        if bucket is None:  # untracked → everything is new
            out.append(t)
            continue
        # Cheap overlap check: any line of the function in the diff?
        for ln in range(t.lineno, t.end_lineno + 1):
            if ln in bucket:
                out.append(t)
                break
    return out
