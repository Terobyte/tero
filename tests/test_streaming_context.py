"""Tests for streaming context-management helpers."""

from io import StringIO
import sys

from src import streaming as s


def capture(fn, *args):
    """Capture stdout from a streaming helper."""
    buf = StringIO()
    old = sys.stdout
    try:
        sys.stdout = buf
        fn(*args)
    finally:
        sys.stdout = old
    return buf.getvalue()


def test_print_compact_triggered():
    """Compact notification should mention token usage."""
    out = capture(s.print_compact_triggered, 91_000, 110_000)
    assert "91" in out or "compact" in out.lower()


def test_print_continuation_started():
    """Continuation notification should include role and attempt counters."""
    out = capture(s.print_continuation_started, "player", 1, 2)
    assert "1" in out and "2" in out
    assert "player" in out.lower() or "continuation" in out.lower()
