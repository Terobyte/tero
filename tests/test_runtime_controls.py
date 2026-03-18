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


class TestStatusBar:
    def test_render_writes_ansi_to_stdout(self, capsys):
        from src.runtime_controls import StatusBar
        bar = StatusBar.__new__(StatusBar)
        bar._player_name = "GLM-1"
        bar._coach_name = "Sonnet"
        bar._ctx_pct = 42
        bar._warning_text = None
        bar._warning_timer = None
        bar._lock = threading.Lock()

        with patch("os.get_terminal_size", return_value=MagicMock(lines=40)):
            with patch("sys.stdout") as mock_stdout:
                bar._render()
                mock_stdout.write.assert_called_once()
                written = mock_stdout.write.call_args[0][0]
                assert "GLM-1" in written
                assert "Sonnet" in written
                assert "42%" in written
                assert "\0337" in written  # cursor save
                assert "\0338" in written  # cursor restore

    def test_update_stores_values(self):
        from src.runtime_controls import StatusBar
        bar = StatusBar.__new__(StatusBar)
        bar._player_name = ""
        bar._coach_name = ""
        bar._ctx_pct = 0
        bar._warning_text = None
        bar._warning_timer = None
        bar._lock = threading.Lock()

        with patch.object(bar, "_render"):
            bar.update("GLM-2", "Opus", 77)
            assert bar._player_name == "GLM-2"
            assert bar._coach_name == "Opus"
            assert bar._ctx_pct == 77

    def test_show_warning_reverts_after_duration(self):
        from src.runtime_controls import StatusBar
        bar = StatusBar.__new__(StatusBar)
        bar._player_name = "GLM-1"
        bar._coach_name = "Sonnet"
        bar._ctx_pct = 0
        bar._warning_text = None
        bar._warning_timer = None
        bar._lock = threading.Lock()

        render_calls = []
        with patch.object(bar, "_render", side_effect=lambda: render_calls.append(1)):
            # Use very short duration for test
            bar.show_warning("Coach not ready!", duration_s=0.05)
            time.sleep(0.15)  # wait for timer to fire
            # Should have been called at least twice: once for warning, once for revert
            assert len(render_calls) >= 2
