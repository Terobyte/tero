"""`tero wait` exit codes and the hang rule by `progress_ts`."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from src.hud.registry import current_pid_started
from src.loop.wait import status_payload, wait_run


class FakeClock:
    """Controllable monotonic clock; sleep() advances it."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


def _write_state(run_dir: Path, **fields) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "state.json"
    current: dict = {}
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
    current.update(fields)
    path.write_text(json.dumps(current), encoding="utf-8")


def _write_pulse(run_dir: Path, *, progress: int = 0, alive: float = 0.0,
                 progress_ts: float | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "pulse").write_text(
        json.dumps({"alive": alive, "progress": progress,
                    "progress_ts": (alive if progress_ts is None
                                    else progress_ts)}),
        encoding="utf-8",
    )


def _alive_pid() -> dict:
    return {"pid": os.getpid(), "pid_started": current_pid_started()}


def _dead_pid() -> dict:
    return {"pid": 999999999, "pid_started": 0.0}


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def test_wait_done_without_process(tmp_path: Path):
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"

    _write_state(run_dir, status="done", **_dead_pid())
    assert wait_run(run_dir, repo) == 0

    _write_state(run_dir, status="done", debt=[{"phase": "One"}])
    assert wait_run(run_dir, repo) == 4

    _write_state(run_dir, status="stopped")
    assert wait_run(run_dir, repo) == 2


def test_wait_dead_needs_free_lock(tmp_path: Path):
    from src.loop.lock import acquire

    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"
    _write_state(run_dir, status="running", **_dead_pid())

    assert wait_run(run_dir, repo) == 5

    lock = acquire(repo)
    assert lock is not None
    try:
        assert wait_run(run_dir, repo, timeout_s=0, poll_s=0) == 6
    finally:
        lock.release()


def test_wait_hang_by_progress(tmp_path: Path):
    repo = _repo(tmp_path)

    run_dir = tmp_path / "run1"
    _write_state(run_dir, status="running", hang_after_s=10, **_alive_pid())
    _write_pulse(run_dir, progress=3, alive=1234.5,
                 progress_ts=time.time() - 1000.0)
    fc = FakeClock()
    assert (
        wait_run(run_dir, repo, clock=fc.clock, sleep=fc.sleep, poll_s=30) == 5
    )

    # waiting_* ignores hang_after_s: 60 s of still progress is not death,
    # the timeout fires first.
    run_dir = tmp_path / "run2"
    _write_state(run_dir, status="waiting_human", hang_after_s=10, **_alive_pid())
    _write_pulse(run_dir, progress=3, alive=1234.5, progress_ts=time.time())
    fc = FakeClock()
    assert (
        wait_run(
            run_dir, repo, timeout_s=50,
            clock=fc.clock, sleep=fc.sleep, poll_s=30,
        )
        == 6
    )

    # ...but past 120 s in waiting_human it is a hang.
    run_dir = tmp_path / "run3"
    _write_state(run_dir, status="waiting_human", hang_after_s=10, **_alive_pid())
    _write_pulse(run_dir, progress=3, alive=1234.5,
                 progress_ts=time.time() - 1000.0)
    fc = FakeClock()
    assert (
        wait_run(run_dir, repo, clock=fc.clock, sleep=fc.sleep, poll_s=200) == 5
    )


def test_wait_sleep_is_not_death(tmp_path: Path):
    """Stale wall clock alone is not death: owner pid is gone, lock is free."""
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"
    _write_state(run_dir, status="running", hang_after_s=10, **_dead_pid())
    _write_pulse(run_dir, progress=7, alive=time.time(),
                 progress_ts=time.time())
    frozen = FakeClock(500.0)
    code = wait_run(
        run_dir, repo, timeout_s=0, poll_s=0,
        clock=frozen.clock, sleep=lambda s: None,
    )
    assert code == 5


def test_wait_fresh_progress_survives_short_waits(tmp_path: Path):
    """Fresh progress_ts below the hang threshold: short waits time out."""
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"
    _write_state(run_dir, status="running", hang_after_s=100, **_alive_pid())
    _write_pulse(run_dir, progress=1, alive=1234.5,
                 progress_ts=time.time())
    fc = FakeClock()
    kw = {"clock": fc.clock, "sleep": fc.sleep, "poll_s": 20}

    assert wait_run(run_dir, repo, timeout_s=30, **kw) == 6
    assert wait_run(run_dir, repo, timeout_s=30, **kw) == 6


def test_wait_judge_pending_is_3(tmp_path: Path):
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"

    _write_state(run_dir, status="judge_pending", **_dead_pid())
    assert wait_run(run_dir, repo) == 3

    _write_state(run_dir, status="waiting_agent", **_dead_pid())
    assert wait_run(run_dir, repo) == 3


def test_status_payload_fields_and_no_secrets(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "plan.md").write_text(
        "# Demo\n\n## One\n\n- [ ] x\n\n## Two\n\n- [ ] y\n"
    )
    run_dir = tmp_path / "run"
    secret = "tok-status-secret-99"
    monkeypatch.setenv("TERO_STATUS_TEST_TOKEN", secret)
    _write_state(
        run_dir,
        status="running",
        plan="plan.md",
        phase="One",
        phase_index=0,
        round=2,
        role="player",
        provider="muse",
        model="muse-spark-1.3",
        doing=f"проверяю {secret}",
        **_dead_pid(),
    )
    _write_pulse(run_dir, progress=1, alive=time.time())

    payload = status_payload(run_dir, repo)
    for key in (
        "phase", "round", "role", "provider", "model", "status", "doing",
        "question", "decisions", "debt", "pulse_age_s", "lease", "push_ok",
    ):
        assert key in payload, key
    assert payload["phase"] == "1/2"
    assert payload["round"] == 2
    assert payload["question"] is None
    assert payload["lease"] is None
    assert payload["push_ok"] is False
    assert secret not in json.dumps(payload, ensure_ascii=False)
