"""Git operations for a v4 run, including the secrets deny-list."""

from pathlib import Path

import pytest

from src.loop.git import (
    DirtyWorktreeError,
    checkout_run_branch,
    commit,
    ensure_clean,
    exclude_tero,
    file_hashes,
    head,
    rollback,
    secret_paths,
    stage_and_diff,
    status_short,
    write_tree,
    restore_tree,
)


def _git(repo: Path, *args: str) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _init(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tero@test")
    _git(tmp_path, "config", "user.name", "tero")
    (tmp_path / "README").write_text("hi\n")
    _git(tmp_path, "add", "README")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def test_ensure_clean_rejects_dirty_tree(tmp_path: Path):
    repo = _init(tmp_path)
    (repo / "README").write_text("dirty\n")
    with pytest.raises(DirtyWorktreeError):
        ensure_clean(repo)


def test_ensure_clean_ok_when_clean(tmp_path: Path):
    ensure_clean(_init(tmp_path))


def test_checkout_run_branch_creates_and_reuses(tmp_path: Path):
    repo = _init(tmp_path)
    name = checkout_run_branch(repo, "parse-empty")
    assert name == "tero/parse-empty"
    assert _git(repo, "branch", "--show-current").strip() == "tero/parse-empty"
    (repo / "x.py").write_text("1\n")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-m", "wip")
    _git(repo, "checkout", "-")
    again = checkout_run_branch(repo, "parse-empty")
    assert again == "tero/parse-empty"
    assert (repo / "x.py").exists()


def test_head_and_rollback(tmp_path: Path):
    repo = _init(tmp_path)
    base = head(repo)
    (repo / "new.txt").write_text("x\n")
    _git(repo, "add", "new.txt")
    rollback(repo, base)
    assert not (repo / "new.txt").exists()
    assert head(repo) == base


def test_write_tree_and_restore(tmp_path: Path):
    repo = _init(tmp_path)
    (repo / "a.py").write_text("one\n")
    _git(repo, "add", "-A")
    tree = write_tree(repo)
    (repo / "a.py").write_text("two\n")
    _git(repo, "add", "-A")
    restore_tree(repo, tree)
    assert (repo / "a.py").read_text() == "one\n"


def test_file_hashes_change_when_file_changes(tmp_path: Path):
    repo = _init(tmp_path)
    path = repo / "t.py"
    path.write_text("a\n")
    first = file_hashes(repo, ["t.py"])
    path.write_text("b\n")
    second = file_hashes(repo, ["t.py"])
    assert first["t.py"] != second["t.py"]


def test_stage_and_diff_includes_function_context(tmp_path: Path):
    repo = _init(tmp_path)
    (repo / "src.py").write_text("def f():\n    return 1\n")
    _git(repo, "add", "src.py")
    _git(repo, "commit", "-m", "src")
    base = head(repo)
    (repo / "src.py").write_text("def f():\n    return 2\n")
    diff = stage_and_diff(repo, base)
    assert "def f()" in diff
    assert "+    return 2" in diff


def test_commit_and_status_short(tmp_path: Path):
    repo = _init(tmp_path)
    (repo / "ok.py").write_text("print(1)\n")
    stage_and_diff(repo, head(repo))
    sha = commit(repo, "add ok.py")
    assert len(sha) >= 7
    assert status_short(repo) == ""


def test_exclude_tero_does_not_show_in_status(tmp_path: Path):
    repo = _init(tmp_path)
    exclude_tero(repo)
    run_dir = repo / ".tero" / "runs" / "x"
    run_dir.mkdir(parents=True)
    (run_dir / "ledger.jsonl").write_text("{}\n")
    assert ".tero" not in status_short(repo)


def test_secret_paths_flags_env_and_pem(tmp_path: Path):
    repo = _init(tmp_path)
    (repo / ".env").write_text("KEY=1\n")
    (repo / "key.pem").write_text("x\n")
    (repo / "ok.py").write_text("print(1)\n")
    _git(repo, "add", "-A")
    found = secret_paths(repo)
    assert ".env" in found
    assert "key.pem" in found
    assert "ok.py" not in found
