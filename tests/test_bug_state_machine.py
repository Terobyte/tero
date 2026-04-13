"""Tests for BugStatus state machine in src/debugger_bugs.py."""

import pytest

from src.debugger_bugs import BugEntry, BugStatus, _VALID_TRANSITIONS, transition_bug
from src.errors import DebuggerError


def _make_bug(status: str = BugStatus.OPEN) -> BugEntry:
    return BugEntry(
        id=1,
        file="src/foo.py",
        line=42,
        description="null pointer",
        severity="HIGH",
        status=status,
    )


class TestBugStatusEnum:
    def test_str_values_match_legacy_strings(self):
        assert BugStatus.OPEN == "open"
        assert BugStatus.CONFIRMED == "confirmed"
        assert BugStatus.FALSE_POSITIVE == "false_positive"
        assert BugStatus.INVALID_TEST == "invalid_test"
        assert BugStatus.FIXED == "fixed"

    def test_is_str_subclass(self):
        assert isinstance(BugStatus.OPEN, str)

    def test_all_five_statuses_defined(self):
        statuses = {s.value for s in BugStatus}
        assert statuses == {"open", "confirmed", "false_positive", "invalid_test", "fixed"}


class TestValidTransitions:
    def test_open_can_go_to_confirmed(self):
        assert BugStatus.CONFIRMED in _VALID_TRANSITIONS[BugStatus.OPEN]

    def test_open_can_go_to_false_positive(self):
        assert BugStatus.FALSE_POSITIVE in _VALID_TRANSITIONS[BugStatus.OPEN]

    def test_open_can_go_to_invalid_test(self):
        assert BugStatus.INVALID_TEST in _VALID_TRANSITIONS[BugStatus.OPEN]

    def test_confirmed_can_go_to_invalid_test(self):
        assert BugStatus.INVALID_TEST in _VALID_TRANSITIONS[BugStatus.CONFIRMED]

    def test_confirmed_can_go_to_fixed(self):
        assert BugStatus.FIXED in _VALID_TRANSITIONS[BugStatus.CONFIRMED]

    def test_terminal_states_have_no_exits(self):
        for terminal in (BugStatus.FALSE_POSITIVE, BugStatus.INVALID_TEST, BugStatus.FIXED):
            assert _VALID_TRANSITIONS[terminal] == set(), (
                f"{terminal} should be a terminal state"
            )


class TestTransitionBug:
    def test_open_to_confirmed(self):
        bug = _make_bug("open")
        transition_bug(bug, BugStatus.CONFIRMED)
        assert bug.status == BugStatus.CONFIRMED

    def test_open_to_false_positive(self):
        bug = _make_bug("open")
        transition_bug(bug, BugStatus.FALSE_POSITIVE)
        assert bug.status == "false_positive"

    def test_confirmed_to_fixed(self):
        bug = _make_bug("confirmed")
        transition_bug(bug, BugStatus.FIXED)
        assert bug.status == "fixed"

    def test_confirmed_to_invalid_test(self):
        bug = _make_bug("confirmed")
        transition_bug(bug, BugStatus.INVALID_TEST)
        assert bug.status == "invalid_test"

    def test_invalid_transition_raises_debugger_error(self):
        bug = _make_bug("open")
        with pytest.raises(DebuggerError, match="open.*fixed|fixed.*open"):
            transition_bug(bug, BugStatus.FIXED)

    def test_terminal_state_transition_raises(self):
        for terminal in ("false_positive", "invalid_test", "fixed"):
            bug = _make_bug(terminal)
            with pytest.raises(DebuggerError):
                transition_bug(bug, BugStatus.OPEN)

    def test_same_status_is_invalid(self):
        """Self-transitions are blocked — there's no no-op in the graph."""
        bug = _make_bug("open")
        with pytest.raises(DebuggerError):
            transition_bug(bug, BugStatus.OPEN)

    def test_error_message_contains_bug_id(self):
        bug = _make_bug("open")
        bug.id = 99
        with pytest.raises(DebuggerError, match="99"):
            transition_bug(bug, BugStatus.FIXED)

    def test_error_is_tero_error(self):
        from src.errors import TeroError

        bug = _make_bug("fixed")
        with pytest.raises(TeroError):
            transition_bug(bug, BugStatus.OPEN)


class TestBugEntryDefaultStatus:
    def test_default_status_is_open(self):
        bug = BugEntry(id=1, file="f.py", line=1, description="d", severity="LOW")
        assert bug.status == "open"
        assert bug.status == BugStatus.OPEN
