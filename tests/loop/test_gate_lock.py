"""One gate at a time on the host: two runs must not run suites together."""

import fcntl
import os
import threading
import time
from pathlib import Path

from src.loop.gates import detect, run_all


def test_gate_waits_for_other_runs_gate(tmp_path: Path, monkeypatch):
    lock = tmp_path / "gate.lock"
    monkeypatch.setenv("TERO_GATE_LOCK", str(lock))
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    release_at = []

    def release():
        time.sleep(1.0)
        release_at.append(time.time())
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    threading.Thread(target=release).start()
    cmd = detect(tmp_path, test_command="date +%s.%N")
    started = time.time()
    result = run_all(cmd, tmp_path, timeout_s=1)
    assert result.ok, result.output
    # the wait is not counted against the suite timeout (1 s here)
    assert release_at and time.time() - started >= 0.9


def test_gate_releases_lock_after_run(tmp_path: Path, monkeypatch):
    lock = tmp_path / "gate.lock"
    monkeypatch.setenv("TERO_GATE_LOCK", str(lock))
    run_all(detect(tmp_path, test_command="false"), tmp_path, timeout_s=30)
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_gate_lock_off(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TERO_GATE_LOCK", "off")
    assert run_all(detect(tmp_path, test_command="true"), tmp_path, timeout_s=30).ok
