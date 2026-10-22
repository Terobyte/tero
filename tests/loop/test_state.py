"""RunState: atomic state.json snapshot that never breaks a run."""

import json
import os
import subprocess
import sys

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


def test_failed_replace_keeps_old_file_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    state = RunState(target)
    state.update(status=Status.STARTING.value)

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    state.update(status=Status.RUNNING.value, text="x" * 10_000)

    assert RunState.read(target)["status"] == "starting"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_state_is_utf8_whatever_the_locale(tmp_path):
    # launchd/cron may start tero with LANG=C: default open() is ascii there
    target = tmp_path / "state.json"
    text = ascii("грязное дерево")  # the command line itself must stay ascii
    code = (
        "import sys; from src.loop.state import RunState; "
        f"s = RunState(sys.argv[1]); s.update(text={text}); "
        f"print(RunState.read(sys.argv[1])['text'] == {text})"
    )
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    out = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", code, str(target)],
        env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert out.stdout.strip() == "True", out.stderr
    assert json.loads(target.read_bytes().decode("utf-8"))["text"] == "грязное дерево"
