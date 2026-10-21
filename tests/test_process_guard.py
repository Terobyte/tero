from __future__ import annotations

import subprocess

import psutil

from src.process_guard import ProcessGuard


def test_snapshot_pids_sees_running_child():
    guard = ProcessGuard()
    proc = subprocess.Popen(["sleep", "30"])
    try:
        assert proc.pid in guard.snapshot_pids()
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_kill_new_processes_kills_new_but_not_old():
    guard = ProcessGuard()
    old_proc = subprocess.Popen(["sleep", "30"])
    try:
        before = guard.snapshot_pids()
        assert old_proc.pid in before
        new_proc = subprocess.Popen(["sleep", "30"])
        try:
            guard.kill_new_processes(before)
            assert new_proc.wait(timeout=5) is not None
            assert old_proc.poll() is None
        finally:
            if new_proc.poll() is None:
                new_proc.kill()
                new_proc.wait(timeout=5)
    finally:
        old_proc.kill()
        old_proc.wait(timeout=5)


def test_snapshot_pids_returns_empty_on_no_such_process(monkeypatch):
    def raising(*args, **kwargs):
        raise psutil.NoSuchProcess(pid=1)

    monkeypatch.setattr(psutil, "Process", raising)
    assert ProcessGuard().snapshot_pids() == set()
