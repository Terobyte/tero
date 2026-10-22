"""RunState: atomic state.json snapshot that never breaks a run."""

import os

import pytest

from src.loop.state import RunState, Status


def test_status_has_unified_values():
    assert {m.value for m in Status} == {
        "starting",
        "running",
        "waiting_human",
        "waiting_agent",
        "waiting_limit",
        "judge_pending",
        "done",
        "stopped",
    }


def test_update_writes_and_reads_back(tmp_path):
    target = tmp_path / "run" / "state.json"
    state = RunState(target)
    state.update(status=Status.RUNNING.value)
    data = RunState.read(target)
    assert data is not None
    assert data["status"] == "running"
    assert "updated_at" in data


def test_update_is_atomic_and_never_raises(tmp_path, capsys):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("chmod не ограничивает root")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run_dir.chmod(0o500)
    try:
        state = RunState(run_dir / "state.json")
        state.update(status=Status.RUNNING.value)
        first = capsys.readouterr()
        assert first.err.strip() != ""
        state.update(status=Status.RUNNING.value)
        second = capsys.readouterr()
        assert second.err == ""
        assert not (run_dir / "state.json").exists()
    finally:
        run_dir.chmod(0o700)
