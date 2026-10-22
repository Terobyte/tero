"""Run lock: exactly one owner per repo."""

import subprocess
import sys

from src.loop.lock import acquire


def test_two_owners_second_exits(tmp_path):
    first = acquire(tmp_path)
    assert first is not None
    try:
        second = acquire(tmp_path)
        assert second is None
    finally:
        first.release()
    third = acquire(tmp_path)
    assert third is not None
    third.release()


def _other_process_gets_lock(repo) -> bool:
    code = (
        "import sys; from src.loop.lock import acquire; "
        f"sys.exit(0 if acquire({str(repo)!r}) else 1)"
    )
    return subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_second_process_cannot_take_lock(tmp_path):
    lock = acquire(tmp_path)
    assert lock is not None
    try:
        assert not _other_process_gets_lock(tmp_path)
    finally:
        lock.release()
    assert _other_process_gets_lock(tmp_path)


def test_child_process_does_not_inherit_lock(tmp_path):
    lock = acquire(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock.release()
        assert _other_process_gets_lock(tmp_path)
    finally:
        child.kill()
        child.wait()
