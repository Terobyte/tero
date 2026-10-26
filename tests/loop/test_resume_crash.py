"""Resume after crash (phase 2). Red first."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout


def _init_repo(path: Path, plan: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "tero@test")
    _git(path, "config", "user.name", "tero")
    (path / "plan.md").write_text(plan)
    (path / "src").mkdir(exist_ok=True)
    (path / "src/__init__.py").write_text("")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    return path


PLAN = "# Demo\n\n## One\n\n- [ ] a\n\n## Two\n\n- [ ] b\n"


def test_crash_between_tick_and_commit(tmp_path: Path):
    """Checkboxes marked, no trailer commit -> rollback, phase anew, boxes [ ]."""
    from src.loop import git as g
    from src.loop import resume_step as rs

    repo = _init_repo(tmp_path / "r1", PLAN)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "plan.md").write_text("# Demo\n\n## One\n\n- [x] a\n\n## Two\n\n- [ ] b\n")
    (repo / "dirty.txt").write_text("work\n")
    assert not g.phase_shipped(repo, base, "run1", 1)
    out = rs.dispatch(
        {"status": "running", "base": base, "phase_index": 0, "crash_resumes": 0},
        repo=repo, run_id="run1",
    )
    assert out["action"] == "rerun"
    assert "- [ ] a" in (repo / "plan.md").read_text()
    assert not (repo / "dirty.txt").exists()


def test_crash_after_commit_before_state(tmp_path: Path):
    """Trailer commit exists, state.json on old phase -> phase counted."""
    from src.loop import resume_step as rs

    repo = _init_repo(tmp_path / "r2", PLAN)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "plan.md").write_text("# Demo\n\n## One\n\n- [x] a\n\n## Two\n\n- [ ] b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ship one", "--trailer", "Tero-Phase: run2/1")
    out = rs.dispatch(
        {"status": "running", "base": base, "phase_index": 0, "crash_resumes": 0},
        repo=repo, run_id="run2",
    )
    assert out["action"] == "shipped"


def test_crash_in_decision_point_restores_question(tmp_path: Path):
    """Tree matches tree_sha -> waiting_human, same id and text."""
    from src.loop import resume_step as rs
    from src.loop import snapshot as sm

    repo = _init_repo(tmp_path / "r3", PLAN)
    base = _git(repo, "rev-parse", "HEAD").strip()
    tree = sm.ref_tree(repo, sm.take_snapshot(repo, base, "refs/tero/r3/snap1"))
    out = rs.dispatch(
        {"status": "waiting_human", "base": base, "phase_index": 0,
         "question": {"id": "1-2", "text": "q?"}, "question_id": "1-2",
         "tree_sha": tree, "round": 3, "crash_resumes": 0},
        repo=repo, run_id="r3",
    )
    assert out["action"] == "waiting"
    assert out["question"]["id"] == "1-2"
    assert out["question"]["text"] == "q?"


def test_resume_kills_hung_owner(tmp_path: Path, monkeypatch):
    """Stub owner alive, progress stale past threshold -> killed; fresh -> alive."""
    from src.loop import resume_step as rs
    from src.loop import wait as w

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        now = time.time()
        state = {"status": "running", "pid": proc.pid,
                 "pid_started": 1.0, "hang_after_s": 10}
        monkeypatch.setattr(w, "pid_is_alive", lambda p, s: True)
        assert w.is_hung(state, {"progress": 1, "progress_ts": now - 1000.0}, now)
        rs.kill_hung_owner(proc.pid)
        time.sleep(0.3)
        assert proc.poll() is not None, "hung owner not killed"
        assert not w.is_hung(state, {"progress": 1, "progress_ts": now}, now)
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def test_third_crash_resume_refused():
    """Third resume in a row -> stopped crash_loop; --force passes."""
    from src.loop import resume_rules as rr
    from src.loop import resume_step as rs

    assert hasattr(rs, "dispatch"), "dispatch entry missing"
    state = {"status": "running", "phase_index": 0, "crash_resumes": 2}
    out = rs.dispatch(state)
    assert out["action"] == "refuse"
    assert "падает" in out["text"]
    assert rs.dispatch(state, force=True)["action"] != "refuse"
    assert "пока не умеет" in rr.resume_refusal({"status": "done"})
    assert rr.resume_refusal({"status": "running", "config": {}}) != ""
    assert rr.resume_refusal(
        {"status": "running", "config": {"fields": {}}}, force=True) == ""
    assert rs.dispatch({"status": "done"})["action"] == "other"
    assert rs.dispatch({"status": "stopped"})["action"] == "other"
    assert rs.dispatch({"status": "judge_pending"})["action"] == "other"


def test_wait_run_uses_progress_ts_directly(tmp_path: Path, monkeypatch):
    """Stale progress_ts past threshold is DEAD at once."""
    import time as _time

    from src.loop import wait as w

    class _Clock:
        def __init__(self) -> None:
            self.now = 1000.0

        def clock(self) -> float:
            return self.now

        def sleep(self, s: float) -> None:
            sleeps.append(s)
            self.now += s

    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runw"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        '{"status": "running", "hang_after_s": 10, "pid": 1, "pid_started": 1.0}',
        encoding="utf-8",
    )
    (run_dir / "pulse").write_text(
        '{"alive": 1.0, "progress": 3, "progress_ts": 100.0}', encoding="utf-8"
    )
    monkeypatch.setattr(w, "pid_is_alive", lambda p, s: True)
    monkeypatch.setattr(_time, "time", lambda: 1000.0)
    sleeps: list = []
    fc = _Clock()
    assert w.wait_run(run_dir, repo, clock=fc.clock, sleep=fc.sleep, poll_s=30) == 5
    assert sleeps == []


def test_crash_checkout_failure_keeps_current_branch(tmp_path: Path, monkeypatch):
    """Checkout failure stops recovery: current branch untouched."""
    import pytest

    from src.loop import git as g
    from src.loop import resume_recover as rc

    repo = _init_repo(tmp_path / "r4", PLAN)
    cur = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    g.checkout_run_branch(repo, "demo")
    _git(repo, "checkout", cur)
    (repo / "dirty.txt").write_text("work\n")

    def _boom(*a: object, **k: object) -> str:
        raise g.GitError("dirty")

    monkeypatch.setattr(g, "force_checkout_run_branch", _boom)
    with pytest.raises(Exception):
        rc._checkout_run_head(repo, "demo")
    assert (repo / "dirty.txt").read_text() == "work\n"
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == cur


def test_crash_counter_resets_on_phase_change():
    """Crashes from the previous phase do not count toward this one."""
    from src.loop import resume_step as rs

    state = {"status": "running", "phase_index": 1,
             "crash_resumes": 2, "crash_phase_index": 0}
    assert rs.dispatch(state)["action"] != "refuse"


def test_waiting_resume_keeps_question_and_round(tmp_path: Path):
    """Waiting restore: same round/qid, owner alive to apply the answer."""
    from src.loop.resume_recover import _wait_runner
    from src.loop.resume_wait import resume_waiting_outcome

    repo = _init_repo(tmp_path / "r5", PLAN)
    run_dir = repo / ".tero" / "run-w"
    run_dir.mkdir(parents=True, exist_ok=True)
    import json

    from src.loop.plan import load_phases, load_preamble

    state = {"status": "waiting_human", "reason": "stuck",
             "phase_index": 0, "round": 3,
             "question": {"id": "1-2", "text": "q?",
                         "actions": ["skip", "stop"]},
             "question_id": "1-2", "base": "b", "tree_sha": "t"}
    (run_dir / "state.json").write_text(json.dumps(state))
    (run_dir / "stream.jsonl").write_text("")
    (repo / ".tero-run-ledger").mkdir(exist_ok=True)
    from src.config import Config

    cfg = Config(plan_file="plan.md", decisions=True)
    phases = load_phases(repo / "plan.md")
    pulse = type("P", (), {"progress": lambda self: None,
                           "stop": lambda self: None})()
    session = type("S", (), {"pulse": pulse})()
    from src.loop.ledger import Ledger as _L

    ledger = _L(run_dir)
    runner = _wait_runner(phases[0], repo, repo / "plan.md",
                          load_preamble(repo / "plan.md"), cfg, None,
                          session, lambda: ledger, "b", 3, state)
    out = resume_waiting_outcome(runner)
    assert out.status == "waiting_human" and out.reason == "stuck"
    assert out.rounds == 3 and out.decider is not None
    assert out.decider._qid == "1-2" and out.decider._reason == "stuck"


def test_restored_question_skip_on_last_phase_ends_run(tmp_path: Path):
    """Skip on the last phase after a crash restore finishes the run, not «фаза не найдена»."""
    import asyncio
    import json
    from types import SimpleNamespace

    from src.config import Config
    from src.loop.plan import load_phases
    from src.loop.resume_recover import _after_wait_decision

    repo = _init_repo(tmp_path / "r6", PLAN.replace("- [ ] a", "- [x] a"))
    run_dir = repo / ".tero" / "runs" / "run-s"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(
        {"status": "waiting_human", "phase_index": 1, "round": 2,
         "debt": [{"phase": 2, "reason": "skip"}]}))
    events: list = []
    ledger = SimpleNamespace(event=lambda *a, **k: events.append((a, k)))
    writes: list = []
    phases = load_phases(repo / "plan.md")
    assert phases[0].done and not phases[1].done

    async def _never(*a, **k):
        raise AssertionError("no phase left to run")

    out = asyncio.run(_after_wait_decision(
        SimpleNamespace(status="skip"), repo, run_dir, Config(plan_file="plan.md"),
        phases, "", repo / "plan.md", agents=None, session=SimpleNamespace(pulse=None),
        ledger=lambda: ledger, restore_pending=lambda: None, base="b", round_no=2,
        phase_runner=_never, pos=1, write=lambda **k: writes.append(k)))
    assert out.status == "ok", out
    assert out.reason in ("ship", "debt")
    assert "фаза не найдена" not in (out.reason or "")
