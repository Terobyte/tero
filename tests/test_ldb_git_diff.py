"""Tests for src/ldb/git_diff.py — diff parsing + target filtering."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ldb.git_diff import (
    _parse_unified_diff,
    filter_targets_by_diff,
    get_changed_lines,
)
from src.ldb.scope import LdbTarget


# ── _parse_unified_diff ──────────────────────────────────────────────────────

def test_parse_simple_addition():
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -10,0 +11,3 @@\n"
        "+new line A\n"
        "+new line B\n"
        "+new line C\n"
    )
    out = _parse_unified_diff(diff)
    assert out == {"foo.py": {11, 12, 13}}


def test_parse_multi_hunk_single_file():
    diff = (
        "+++ b/x.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "@@ -50,0 +60,2 @@\n"
        "+a\n"
        "+b\n"
    )
    out = _parse_unified_diff(diff)
    assert out == {"x.py": {1, 60, 61}}


def test_parse_multiple_files():
    diff = (
        "+++ b/a.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+x\n"
        "+y\n"
        "+++ b/b.py\n"
        "@@ -5 +5 @@\n"
        "-old\n"
        "+new\n"
    )
    out = _parse_unified_diff(diff)
    assert out == {"a.py": {1, 2}, "b.py": {5}}


def test_parse_pure_deletion_hunk_skipped():
    """Hunks where new-side count is 0 (pure deletion) should not add lines."""
    diff = (
        "+++ b/foo.py\n"
        "@@ -10,3 +9,0 @@\n"
        "-deleted A\n"
        "-deleted B\n"
        "-deleted C\n"
    )
    out = _parse_unified_diff(diff)
    # File still recorded (was touched) but with no changed new-side lines.
    assert out == {"foo.py": set()}


def test_parse_dev_null_target_ignored():
    """``+++ /dev/null`` (file deletion) should not register as a target."""
    diff = (
        "--- a/foo.py\n"
        "+++ /dev/null\n"
        "@@ -1,5 +0,0 @@\n"
        "-line 1\n"
        "-line 2\n"
        "-line 3\n"
        "-line 4\n"
        "-line 5\n"
    )
    out = _parse_unified_diff(diff)
    assert out == {}


def test_parse_strips_b_prefix():
    diff = (
        "+++ b/src/module/file.py\n"
        "@@ -0,0 +5 @@\n"
        "+x\n"
    )
    out = _parse_unified_diff(diff)
    assert "src/module/file.py" in out
    assert out["src/module/file.py"] == {5}


def test_parse_empty_diff_returns_empty():
    assert _parse_unified_diff("") == {}


# ── filter_targets_by_diff ───────────────────────────────────────────────────

def _t(file: str, name: str, lineno: int, end_lineno: int) -> LdbTarget:
    return LdbTarget(file=file, name=name, lineno=lineno, end_lineno=end_lineno)


def test_filter_keeps_overlapping_target():
    targets = [_t("foo.py", "bar", 10, 20)]
    changed = {"foo.py": {15}}
    assert filter_targets_by_diff(targets, changed) == targets


def test_filter_drops_target_outside_diff():
    targets = [_t("foo.py", "bar", 10, 20)]
    changed = {"foo.py": {50}}
    assert filter_targets_by_diff(targets, changed) == []


def test_filter_drops_target_in_unchanged_file():
    targets = [_t("foo.py", "bar", 10, 20)]
    changed = {"baz.py": {15}}
    assert filter_targets_by_diff(targets, changed) == []


def test_filter_untracked_sentinel_keeps_all_targets():
    """``None`` sentinel = file is brand-new; every target inside qualifies."""
    targets = [
        _t("new.py", "a", 1, 5),
        _t("new.py", "b", 100, 200),
    ]
    changed = {"new.py": None}
    assert filter_targets_by_diff(targets, changed) == targets


def test_filter_boundary_overlap_inclusive():
    """Overlap on the first or last line of the function counts."""
    targets = [
        _t("foo.py", "first", 10, 20),
        _t("foo.py", "last", 30, 40),
    ]
    changed = {"foo.py": {10, 40}}
    out = filter_targets_by_diff(targets, changed)
    assert {t.name for t in out} == {"first", "last"}


def test_filter_no_changes_returns_empty():
    targets = [_t("foo.py", "bar", 1, 10)]
    assert filter_targets_by_diff(targets, {}) == []


# ── get_changed_lines (integration with real git) ────────────────────────────

@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a tmp git repo with one committed file."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
    )
    (tmp_path / "tracked.py").write_text("def a():\n    return 1\n")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True
    )
    return tmp_path


def test_get_changed_lines_picks_up_unstaged_edit(tmp_git_repo: Path):
    """Edits to a tracked file (unstaged) appear in ``git diff HEAD``."""
    (tmp_git_repo / "tracked.py").write_text(
        "def a():\n    return 1\n\ndef b():\n    return 2\n"
    )
    out = get_changed_lines(tmp_git_repo)
    assert "tracked.py" in out
    bucket = out["tracked.py"]
    assert bucket is not None
    # Lines 3-5 are new (blank + def b + return 2).
    assert bucket & {3, 4, 5}


def test_get_changed_lines_includes_untracked_py_with_sentinel(
    tmp_git_repo: Path,
):
    (tmp_git_repo / "new_module.py").write_text("def x():\n    pass\n")
    out = get_changed_lines(tmp_git_repo)
    assert out.get("new_module.py") is None  # sentinel: all-new


def test_get_changed_lines_ignores_untracked_non_py(tmp_git_repo: Path):
    (tmp_git_repo / "notes.txt").write_text("hello\n")
    out = get_changed_lines(tmp_git_repo)
    assert "notes.txt" not in out


def test_get_changed_lines_returns_empty_when_no_changes(tmp_git_repo: Path):
    assert get_changed_lines(tmp_git_repo) == {}


def test_get_changed_lines_handles_non_git_dir(tmp_path: Path):
    """A non-git directory should return ``{}`` rather than raising."""
    assert get_changed_lines(tmp_path) == {}


def test_get_changed_lines_handles_missing_git_executable(tmp_path: Path):
    """If ``git`` is unavailable, return ``{}`` gracefully."""
    with patch(
        "src.ldb.git_diff.subprocess.run",
        side_effect=FileNotFoundError("git"),
    ):
        assert get_changed_lines(tmp_path) == {}
