"""Regression cases from the eleven-area audit (no real model or network calls)."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.loop import fakes

PLAN = "# Demo\n\n## One\n\n- [ ] done\n"


@pytest.mark.parametrize("shipped", [False, True])
def test_failed_crash_backup_preserves_dirty_tree(tmp_path, monkeypatch, shipped):
    from src.loop import resume_crash, snapshot

    repo = fakes.init_repo(tmp_path, PLAN)
    base = fakes.git(repo, "rev-parse", "HEAD").strip()
    plan = repo / "plan.md"
    plan.write_text("dirty plan")
    extra = repo / "new.txt"
    extra.write_text("unsaved")
    monkeypatch.setattr(resume_crash, "crash_phase_shipped", lambda *a: shipped)

    def fail(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(snapshot, "take_snapshot", fail)
    result = resume_crash.dispatch({"status": "running", "base": base},
                                   repo=repo, run_id="audit")
    assert result["action"] == "refuse"
    assert result["reason"] == "snapshot_failed"
    assert "disk full" in result["text"]
    assert plan.read_text() == "dirty plan"
    assert extra.read_text() == "unsaved"


def _plan_runner(repo, path):
    events = []
    runner = SimpleNamespace(repo=repo, plan_path=path, _plan_bytes=b"original",
        ledger=SimpleNamespace(event=lambda *a, **k: events.append(a[0])),
        ctx=SimpleNamespace(emit=lambda *a, **k: None),
        phase=SimpleNamespace(title="One"), state=SimpleNamespace(round=1))
    return runner, events


@pytest.mark.parametrize("contents", ["external", "original"])
def test_plan_restore_replaces_leaf_symlink_without_touching_target(tmp_path, contents):
    from src.loop.plan_guard import guard_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.write_text(contents)
    path = repo / "plan.md"
    path.symlink_to(outside)
    runner, events = _plan_runner(repo, path)
    guard_plan(runner, "player")
    assert outside.read_text() == contents
    assert not path.is_symlink()
    assert path.read_bytes() == b"original"
    assert events == ["plan_restored"]


def test_plan_restore_refuses_symlinked_parent(tmp_path):
    from src.loop.plan_guard import guard_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "plan.md").write_text("external")
    (repo / "docs").symlink_to(outside, target_is_directory=True)
    runner, events = _plan_runner(repo, repo / "docs" / "plan.md")
    guard_plan(runner, "player")
    assert (outside / "plan.md").read_text() == "external"
    assert events == ["plan_restore_failed"]


@pytest.mark.parametrize("kind", ["file", "link"])
def test_restore_ignored_path_replaced_by_directory(tmp_path, kind):
    from src.loop.ignored_guard import snapshot_ignored, restore_ignored

    repo = fakes.init_repo(tmp_path, PLAN)
    (repo / ".gitignore").write_text("secret.env\n")
    target = repo / "secret.env"
    if kind == "file":
        target.write_bytes(b"original secret")
    else:
        target.symlink_to("missing.env")
    saved = snapshot_ignored(repo)
    target.unlink()
    target.mkdir()
    (target / "payload").write_text("replacement")
    restore_ignored(repo, saved)
    if kind == "file":
        assert target.read_bytes() == b"original secret"
    else:
        assert target.is_symlink() and os.readlink(target) == "missing.env"


def test_ignored_restore_error_is_reported(tmp_path, monkeypatch):
    from src.loop.ignored_guard import restore_ignored

    repo = fakes.init_repo(tmp_path, PLAN)
    target = repo / "secret.env"
    original = Path.write_bytes

    def fail(path, value):
        if path == target:
            raise PermissionError("denied")
        return original(path, value)

    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError, match="cannot restore ignored path secret.env"):
        restore_ignored(repo, {"secret.env": ("file", b"saved")})


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionError("network"), RuntimeError("other failure")])
async def test_stop_during_retry_wait_prevents_second_provider_call(tmp_path, monkeypatch, error):
    from src.loop import agents as module
    from src.loop.schemas import ValidateCtx

    calls = []
    stopped = False
    sleeps = []

    class Provider:
        async def run(self, **kwargs):
            calls.append(1)
            raise error
            yield  # async stream

    monkeypatch.setattr(module, "stop_requested_flag", lambda path: stopped)
    original_sleep = module.asyncio.sleep

    async def sleep(seconds):
        nonlocal stopped
        sleeps.append(seconds)
        stopped = True
        await original_sleep(0)

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    agents = module.LiveAgents(str(tmp_path), factory=lambda name: Provider())
    result = await agents.run_role("player", provider="opencode", model="m",
                                   prompt="P", timeout=10, ctx=ValidateCtx())
    assert len(calls) == 1
    assert sleeps and sleeps[0] <= 0.25
    assert result["__unavailable__"]


def test_detach_preserves_escalation_threshold():
    from src.cli_entry import build_parser
    from src.loop.detach import owner_argv

    parser = build_parser()
    args = parser.parse_args(["go", "--detach", "--escalate-at", "3"])
    owner = parser.parse_args(owner_argv(args)[3:])
    assert owner.escalate_at == 3


def test_detach_timeout_reports_live_owner(tmp_path, capsys, monkeypatch):
    from src.loop import detach

    owner = SimpleNamespace(pid=12345, poll=lambda: None)
    monkeypatch.setattr(detach, "_maybe_caffeinate", lambda *a, **k: None)
    code = detach.detach_go(SimpleNamespace(working_dir=str(tmp_path)),
                            popen=lambda *a, **k: owner, ready_s=0)
    output = capsys.readouterr().out
    assert code == 0
    assert "12345" in output and "продолжает запуск" in output
    assert "detach.log" in output
    assert "не стартовал" not in output


@pytest.mark.parametrize("states", [("still_open", "resolved"), ("resolved", "resolved")])
def test_judge_rejects_duplicate_previous(states):
    from src.loop.schemas import ValidateCtx, validate

    data = {"verdict": "NO_SHIP", "findings": [], "user_question": "",
            "previous": [{"id": "F1", "state": state} for state in states]}
    assert any("unique" in e for e in validate("judge", data, ValidateCtx(open_ids=["F1"])))


@pytest.mark.parametrize("worktree", [False, True])
def test_resume_preserves_git_index_lock(tmp_path, monkeypatch, worktree):
    from src.loop import resume_lock
    from src.loop.resume_lock import check_index_lock
    from src.loop.git import GitError

    monkeypatch.setattr(resume_lock, "_git_running", lambda: True)

    (tmp_path / "repo").mkdir()
    repo = fakes.init_repo(tmp_path / "repo", PLAN)
    if worktree:
        linked = tmp_path / "linked"
        fakes.git(repo, "worktree", "add", "--detach", str(linked))
        repo = linked
    lock = Path(fakes.git(repo, "rev-parse", "--git-path", "index.lock").strip())
    if not lock.is_absolute():
        lock = repo / lock
    with lock.open("w") as held:
        held.write("active git")
        held.flush()
        with pytest.raises(GitError, match="Git index locked"):
            check_index_lock(repo)
        assert lock.read_text() == "active git"
    lock.unlink()
    check_index_lock(repo)


def test_judge_pending_budget_matches_advertised_actions():
    from src.loop.decision_actions import question_actions, full_actions
    from src.loop.inbox import Inbox

    state = {"budget": {"1": 3}}
    actions = question_actions("judge_pending", state, 1)
    assert actions == ["skip", "stop"]
    state["question"] = {"id": "1-4", "actions": actions,
                         "all_actions": full_actions("judge_pending")}
    assert Inbox._refuse({"action": "retry", "who": "human"}, actions, state) is None
    assert Inbox._refuse({"action": "retry", "who": "agent"}, actions, state)


def test_hud_does_not_delete_target_published_during_consumption(tmp_path, monkeypatch):
    from src.hud import single

    path = tmp_path / "hud-target"
    monkeypatch.setattr(single, "target_path", lambda: path)
    single.write_target(tmp_path / "old")
    original = Path.read_text

    def read_then_publish(claimed, *a, **k):
        text = original(claimed, *a, **k)
        single.write_target(tmp_path / "new")
        return text

    monkeypatch.setattr(Path, "read_text", read_then_publish)
    assert single.take_target() == tmp_path / "old"
    monkeypatch.setattr(Path, "read_text", original)
    assert single.take_target() == tmp_path / "new"
    assert single.take_target() is None
    assert not list(tmp_path.glob("*.claimed"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["snapshot", "lock"])
async def test_resume_setup_failure_is_retryable_and_releases_run_lock(tmp_path, monkeypatch, failure):
    from src.loop import snapshot
    from src.loop.run import resume
    from src.loop.lock import acquire
    from src.loop.state import RunState

    repo = fakes.init_repo(tmp_path, PLAN)
    base = fakes.git(repo, "rev-parse", "HEAD").strip()
    run_dir = repo / ".tero" / "runs" / "audit"
    run_dir.mkdir(parents=True)
    saved = {"status": "running", "base": base, "round": 1, "crash_resumes": 0,
             "config": {"fields": {"plan_file": "plan.md", "working_dir": str(repo)}}}
    RunState(run_dir / "state.json").update(**saved)
    (repo / "plan.md").write_text("dirty plan")
    if failure == "snapshot":
        def fail(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(snapshot, "take_snapshot", fail)
    else:
        from src.loop import resume_lock

        monkeypatch.setattr(resume_lock, "_git_running", lambda: True)
        (repo / ".git" / "index.lock").write_text("other git process")
    out = await resume(repo, run_dir, agents=fakes.FakeAgents(repo, []))
    assert out.exit_code == 1
    assert (repo / "plan.md").read_text() == "dirty plan"
    state = RunState.read(run_dir / "state.json")
    assert state["status"] == "running"
    assert state["crash_resumes"] == 0
    lock = acquire(repo)
    assert lock is not None
    lock.release()
    if failure == "lock":
        assert (repo / ".git" / "index.lock").read_text() == "other git process"


def test_unchanged_plan_link_inside_repo_is_preserved(tmp_path):
    from src.loop.plan_guard import guard_plan

    (tmp_path / "source.md").write_bytes(b"original")
    path = tmp_path / "plan.md"
    path.symlink_to("source.md")
    runner, events = _plan_runner(tmp_path, path)
    guard_plan(runner, "player")
    assert path.is_symlink()
    assert events == []


def test_plan_restore_replaces_symlink_loop(tmp_path):
    from src.loop.plan_guard import guard_plan

    path = tmp_path / "plan.md"
    path.symlink_to("plan.md")
    runner, events = _plan_runner(tmp_path, path)
    guard_plan(runner, "player")
    assert not path.is_symlink()
    assert path.read_bytes() == b"original"
    assert events == ["plan_restored"]


def test_stale_index_lock_without_git_process_is_removed(tmp_path, monkeypatch):
    """tero crashed mid-commit: no git alive, so the lock is ours and resume goes on."""
    from src.loop import resume_lock

    repo = fakes.init_repo(tmp_path, PLAN)
    lock = repo / ".git" / "index.lock"
    lock.write_text("left by a crashed run")
    monkeypatch.setattr(resume_lock, "_git_running", lambda: False)
    resume_lock.check_index_lock(repo)
    assert not lock.exists()


def test_ignored_restore_keeps_going_after_one_failure(tmp_path, monkeypatch):
    from src.loop.ignored_guard import restore_ignored

    repo = fakes.init_repo(tmp_path, PLAN)
    bad, good = repo / "secret.env", repo / "other.env"
    original = Path.write_bytes

    def fail(path, value):
        if path == bad:
            raise PermissionError("denied")
        return original(path, value)

    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError, match="secret.env"):
        restore_ignored(repo, {"secret.env": ("file", b"a"), "other.env": ("file", b"b")})
    assert good.read_bytes() == b"b"
