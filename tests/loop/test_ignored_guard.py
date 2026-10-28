"""ignored_guard is fail-safe: pre-existing ignored symlinks survive, no writes through symlinked dirs."""

from __future__ import annotations

import os
from pathlib import Path

from src.loop.guards.ignored import restore_ignored, snapshot_ignored
from tests.loop import fakes as scripted


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "r").mkdir()
    repo = scripted.init_repo(tmp_path / "r", "# Demo\n\n## A\n\n- [ ] a\n")
    scripted.write_file(repo, ".gitignore", "secret.env\nlink.env\nlocal/\nnew.env\n")
    scripted.git(repo, "add", ".gitignore")
    scripted.git(repo, "commit", "-m", "ignore")
    return repo


def test_preexisting_ignored_symlink_survives_and_is_restored(tmp_path):
    repo = _repo(tmp_path)
    (repo / "secret.env").write_text("ORIGINAL\n")
    os.symlink("secret.env", repo / "link.env")
    snap = snapshot_ignored(repo)
    (repo / "link.env").unlink()
    (repo / "link.env").write_text("HACKED\n")
    (repo / "secret.env").write_text("HACKED\n")
    (repo / "new.env").write_text("created\n")
    changed = restore_ignored(repo, snap)
    assert (repo / "link.env").is_symlink() and os.readlink(repo / "link.env") == "secret.env"
    assert (repo / "secret.env").read_text() == "ORIGINAL\n"
    assert not (repo / "new.env").exists()
    assert set(changed) == {"link.env", "secret.env", "new.env"}


def test_untouched_symlink_is_not_deleted(tmp_path):
    repo = _repo(tmp_path)
    (repo / "secret.env").write_text("x\n")
    os.symlink("secret.env", repo / "link.env")
    snap = snapshot_ignored(repo)
    assert restore_ignored(repo, snap) == []
    assert (repo / "link.env").is_symlink()


def test_no_write_through_symlinked_parent(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "local").mkdir()
    (repo / "local" / "a.env").write_text("ORIGINAL\n")
    snap = snapshot_ignored(repo)
    (repo / "local" / "a.env").unlink()
    (repo / "local").rmdir()
    os.symlink(outside, repo / "local")
    restore_ignored(repo, snap)
    assert not (outside / "a.env").exists()
