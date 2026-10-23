"""take_snapshot: work snapshots in refs/tero/... via a temp index."""

from pathlib import Path

from src.loop import snapshot
from tests.loop.fakes import git as _git


def _init(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tero@test")
    _git(tmp_path, "config", "user.name", "tero")
    (tmp_path / "tracked.txt").write_text("v1\n")
    (tmp_path / "keep.txt").write_text("keep\n")
    (tmp_path / "del.txt").write_text("bye\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _ls_tree(repo: Path, ref: str) -> set[str]:
    out = _git(repo, "ls-tree", "-r", "--name-only", ref)
    return {line for line in out.splitlines() if line}


def test_snapshot_contains_tracked_and_new_files(tmp_path: Path):
    repo = _init(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "tracked.txt").write_text("v2\n")
    (repo / "new.txt").write_text("new\n")
    (repo / "del.txt").unlink()
    ref = snapshot.ref_name("run1", 1, 1)
    assert ref == "refs/tero/run1/phase-01-round01"
    sha = snapshot.take_snapshot(repo, base, ref)
    assert _git(repo, "rev-parse", "--verify", ref).strip() == sha
    assert _ls_tree(repo, ref) == {"tracked.txt", "keep.txt", "new.txt"}
    assert _git(repo, "show", f"{ref}:tracked.txt") == "v2\n"


def test_snapshot_excludes_deny_list(tmp_path: Path):
    repo = _init(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / ".env").write_text("KEY=1\n")
    (repo / "key.pem").write_text("x\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x.js").write_text("x\n")
    (repo / ".tero").mkdir()
    (repo / ".tero" / "state.json").write_text("{}\n")
    (repo / "ok.py").write_text("print(1)\n")
    ref = snapshot.ref_name("run1", 2, 3)
    snapshot.take_snapshot(repo, base, ref)
    tree = _ls_tree(repo, ref)
    assert ".env" not in tree
    assert "key.pem" not in tree
    assert "node_modules/x.js" not in tree
    assert ".tero/state.json" not in tree
    assert "ok.py" in tree


def test_snapshot_survives_stale_index_lock(tmp_path: Path):
    repo = _init(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "tracked.txt").write_text("v2\n")
    cached_before = _git(repo, "diff", "--cached")
    index_before = (repo / ".git" / "index").read_bytes()
    lock = repo / ".git" / "index.lock"
    lock.write_text("stale\n")
    ref = snapshot.ref_name("run1", 1, 2)
    sha = snapshot.take_snapshot(repo, base, ref)
    assert _git(repo, "rev-parse", "--verify", ref).strip() == sha
    assert lock.read_text() == "stale\n"
    assert (repo / ".git" / "index").read_bytes() == index_before
    assert _git(repo, "diff", "--cached") == cached_before


def test_snapshot_leaves_worktree_alone(tmp_path: Path):
    repo = _init(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "tracked.txt").write_text("v2\n")
    (repo / "new.txt").write_text("new\n")
    (repo / "staged.txt").write_text("staged\n")
    _git(repo, "add", "staged.txt")
    status_before = _git(repo, "status", "--porcelain")
    cached_before = _git(repo, "diff", "--cached")
    ref = snapshot.ref_name("run1", 1, 3)
    snapshot.take_snapshot(repo, base, ref)
    assert _git(repo, "status", "--porcelain") == status_before
    assert _git(repo, "diff", "--cached") == cached_before
    assert list((repo / ".git").glob("tero-snapshot-*")) == []


def test_snapshot_in_linked_worktree(tmp_path: Path):
    repo = _init(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", str(wt))
    try:
        assert (wt / ".git").is_file()
        (wt / "tracked.txt").write_text("v2\n")
        (wt / "new.txt").write_text("new\n")
        status_before = _git(wt, "status", "--porcelain")
        ref = snapshot.ref_name("run1", 1, 4)
        sha = snapshot.take_snapshot(wt, base, ref)
        assert _git(wt, "rev-parse", "--verify", ref).strip() == sha
        assert _ls_tree(wt, ref) == {"tracked.txt", "keep.txt", "del.txt", "new.txt"}
        assert _git(wt, "show", f"{ref}:tracked.txt") == "v2\n"
        assert _git(wt, "status", "--porcelain") == status_before
        assert list((repo / ".git").rglob("tero-snapshot-*")) == []
    finally:
        _git(repo, "worktree", "remove", "--force", str(wt))


def test_deny_matching_uses_is_secret_path(tmp_path: Path, monkeypatch):
    repo = _init(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "notes.txt").write_text("notes\n")
    seen: list[str] = []
    real = snapshot.git.is_secret_path

    def spy(path: str) -> bool:
        seen.append(path)
        return real(path)

    monkeypatch.setattr(snapshot.git, "is_secret_path", spy)
    ref = snapshot.ref_name("run1", 9, 9)
    snapshot.take_snapshot(repo, base, ref)
    assert "notes.txt" in seen
