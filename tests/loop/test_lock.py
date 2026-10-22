"""Run lock: exactly one owner per repo."""

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
