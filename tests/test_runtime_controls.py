"""Tests for RuntimeControls components."""
import threading
import time
import queue
from unittest.mock import MagicMock, patch
import pytest


class TestKeyboardListener:
    """Test KeyboardListener without a real terminal."""

    def test_right_arrow_queues_coach_right(self):
        from src.runtime_controls import _parse_escape_sequence

        action = _parse_escape_sequence(b"C")
        assert action == "coach_right"

    def test_left_arrow_queues_coach_left(self):
        from src.runtime_controls import _parse_escape_sequence
        assert _parse_escape_sequence(b"D") == "coach_left"

    def test_standalone_esc_queues_compact(self):
        from src.runtime_controls import _parse_escape_sequence
        assert _parse_escape_sequence(None) == "compact"  # None = no follow bytes

    def test_d_key_queues_player_right(self):
        from src.runtime_controls import _char_to_action
        assert _char_to_action("d") == "player_right"
        assert _char_to_action("D") == "player_right"

    def test_a_key_queues_player_left(self):
        from src.runtime_controls import _char_to_action
        assert _char_to_action("a") == "player_left"
        assert _char_to_action("A") == "player_left"

    def test_enter_key_queues_confirm(self):
        from src.runtime_controls import _char_to_action
        assert _char_to_action("\r") == "confirm"
        assert _char_to_action("\n") == "confirm"

    def test_pop_action_returns_none_when_empty(self):
        from src.runtime_controls import KeyboardListener
        listener = KeyboardListener.__new__(KeyboardListener)
        listener._action_queue = queue.Queue()
        listener._stop_event = threading.Event()
        assert listener.pop_action() is None

    def test_pop_action_returns_queued_action(self):
        from src.runtime_controls import KeyboardListener
        listener = KeyboardListener.__new__(KeyboardListener)
        listener._action_queue = queue.Queue()
        listener._stop_event = threading.Event()
        listener._action_queue.put("coach_right")
        assert listener.pop_action() == "coach_right"
