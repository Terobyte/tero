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

    def test_open_input_fd_uses_stdin_when_tty(self):
        from src.runtime_controls import KeyboardListener

        listener = KeyboardListener()
        with patch("sys.stdin.fileno", return_value=7), patch(
            "os.isatty", return_value=True
        ):
            fd, owned_fd = listener._open_input_fd()

        assert fd == 7
        assert owned_fd is None

    def test_open_input_fd_falls_back_to_dev_tty_when_stdin_not_tty(self):
        from src.runtime_controls import KeyboardListener

        listener = KeyboardListener()
        with patch("sys.stdin.fileno", return_value=7), patch(
            "os.isatty", return_value=False
        ), patch("os.open", return_value=11) as mock_open:
            fd, owned_fd = listener._open_input_fd()

        assert fd == 11
        assert owned_fd == 11
        mock_open.assert_called_once()


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


MODEL_PRESETS_TEST = [
    ("GLM-5", "black", "blackboxai/z-ai/glm-5"),
    ("Sonnet", "claude", "claude-sonnet-4-6"),
    ("Opus", "claude", "claude-opus-4-6"),
]


class TestPicker:
    def _make_picker(self):
        from src.runtime_controls import Picker
        picker = Picker(presets=MODEL_PRESETS_TEST)
        return picker

    def test_initial_state_closed(self):
        picker = self._make_picker()
        assert picker.state == "CLOSED"

    def test_coach_right_opens_coach_picker(self):
        picker = self._make_picker()
        picker._current_coach_idx = 0
        picker._current_player_idx = 0
        with patch.object(picker, "_render"):
            picker.handle_action("coach_right", current_coach_idx=0, current_player_idx=0)
        assert picker.state == "OPEN"
        assert picker.role == "coach"

    def test_cycling_wraps_around(self):
        picker = self._make_picker()
        picker.state = "OPEN"
        picker.role = "coach"
        picker._open_idx = 2  # last item
        with patch.object(picker, "_render"):
            picker.handle_action("coach_right", current_coach_idx=0, current_player_idx=0)
        assert picker._open_idx == 0  # wrapped

    def test_enter_in_open_moves_to_confirming(self):
        picker = self._make_picker()
        picker.state = "OPEN"
        picker.role = "coach"
        picker._open_idx = 1  # Sonnet
        with patch.object(picker, "_render"), patch.object(picker, "_schedule_dismiss"):
            picker.handle_action("confirm", current_coach_idx=0, current_player_idx=0)
        assert picker.state == "CONFIRMING"
        pending = picker.pop_pending_change()
        assert pending is not None
        role, provider, model = pending
        assert role == "coach"
        assert provider == "claude"
        assert model == "claude-sonnet-4-6"

    def test_esc_in_open_closes_without_compact(self):
        picker = self._make_picker()
        picker.state = "OPEN"
        picker.role = "coach"
        with patch.object(picker, "_render"):
            picker.handle_action("compact", current_coach_idx=0, current_player_idx=0)
        assert picker.state == "CLOSED"
        # compact_requested must NOT be set by picker; that's RuntimeControls' job
        assert not hasattr(picker, "_compact_requested") or not picker._compact_requested

    def test_switch_role_while_open(self):
        picker = self._make_picker()
        picker.state = "OPEN"
        picker.role = "coach"
        picker._open_idx = 0
        with patch.object(picker, "_render"):
            # Press A/D while coach picker open → switch to player
            picker.handle_action("player_right", current_coach_idx=0, current_player_idx=0)
        assert picker.role == "player"
        assert picker.state == "OPEN"

    def test_pop_pending_change_returns_none_when_empty(self):
        picker = self._make_picker()
        assert picker.pop_pending_change() is None


class TestRuntimeControls:
    def _make_controls(self):
        from src.runtime_controls import RuntimeControls
        with patch("src.runtime_controls.KeyboardListener") as MockKB:
            mock_kb = MagicMock()
            mock_kb.pop_action.return_value = None
            MockKB.return_value = mock_kb
            controls = RuntimeControls.__new__(RuntimeControls)
            controls._listener = mock_kb
            controls._status_bar = MagicMock()
            controls._picker = MagicMock()
            controls._picker.pop_pending_change.return_value = None
            controls._compact_requested = False
            controls._current_coach_idx = 0
            controls._current_player_idx = 0
            controls._player_name = ""
            controls._coach_name = ""
            controls._ctx_pct = 0
        return controls

    def test_update_context_computes_percentage(self):
        controls = self._make_controls()
        controls.update_context(tokens=50_000, context_window=100_000)
        controls._status_bar.update.assert_called_once()
        call_args = controls._status_bar.update.call_args[0]
        assert call_args[2] == 50

    def test_compact_requested_false_initially(self):
        controls = self._make_controls()
        assert controls.compact_requested is False

    def test_clear_compact_resets_flag(self):
        controls = self._make_controls()
        controls._compact_requested = True
        controls.clear_compact()
        assert controls.compact_requested is False

    def test_apply_pending_updates_session_coach(self):
        controls = self._make_controls()
        controls._picker.pop_pending_change.return_value = ("coach", "claude", "claude-sonnet-4-6")

        session = MagicMock()
        session.config.coach_provider = "black"
        session.config.coach_model = ""
        session.config.batch_pre_provider = "black"
        session.config.batch_pre_model = ""
        session.config.batch_post_provider = "black"
        session.config.batch_post_model = ""
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (True, "")
        session._get_or_create_provider.return_value = mock_provider
        session._build_role_display.return_value = "claude | model=claude-sonnet-4-6"
        session.player_model = "GLM-5"
        session.coach_model = "GLM-5"

        controls.apply_pending(session)

        assert session.config.coach_provider == "claude"
        assert session.config.coach_model == "claude-sonnet-4-6"
        assert session.config.batch_pre_provider == "claude"
        assert session.config.batch_pre_model == "claude-sonnet-4-6"
        assert session.config.batch_post_provider == "claude"
        assert session.config.batch_post_model == "claude-sonnet-4-6"
        session._get_or_create_provider.assert_called_with("claude")

    def test_apply_pending_keeps_custom_batch_roles_when_switching_coach(self):
        controls = self._make_controls()
        controls._picker.pop_pending_change.return_value = (
            "coach",
            "opencode",
            "opencode/minimax-m2.5-free",
        )

        session = MagicMock()
        session.config.coach_provider = "black"
        session.config.coach_model = ""
        session.config.batch_pre_provider = "claude"
        session.config.batch_pre_model = "claude-sonnet-4-6"
        session.config.batch_post_provider = "codex"
        session.config.batch_post_model = "o3"
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (True, "")
        session._get_or_create_provider.return_value = mock_provider
        session._build_role_display.return_value = (
            "opencode | model=opencode/minimax-m2.5-free"
        )

        controls.apply_pending(session)

        assert session.config.coach_provider == "opencode"
        assert session.config.coach_model == "opencode/minimax-m2.5-free"
        assert session.config.batch_pre_provider == "claude"
        assert session.config.batch_pre_model == "claude-sonnet-4-6"
        assert session.config.batch_post_provider == "codex"
        assert session.config.batch_post_model == "o3"

    def test_apply_pending_skips_if_provider_not_ready(self):
        controls = self._make_controls()
        controls._picker.pop_pending_change.return_value = ("coach", "codex", "gpt-5.4")

        session = MagicMock()
        session.config.coach_provider = "black"
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (False, "Proxy not reachable")
        session._get_or_create_provider.return_value = mock_provider

        controls.apply_pending(session)

        # coach_provider should NOT have changed
        assert session.config.coach_provider == "black"
        controls._status_bar.show_warning.assert_called_once()

    def test_apply_pending_uses_session_runtime_switch_when_available(self):
        controls = self._make_controls()
        controls._picker.pop_pending_change.return_value = (
            "coach",
            "kilo",
            "kilo/minimax/minimax-m2.5:free",
        )

        class Session:
            def __init__(self):
                self.config = MagicMock()
                self.config.coach_provider = "black"
                self.config.coach_model = "blackboxai/z-ai/glm-5"
                self._provider = MagicMock()
                self._provider.check_ready.return_value = (True, "")
                self._get_or_create_provider = MagicMock(return_value=self._provider)
                self._switch_mock = MagicMock(
                    return_value="kilo | model=kilo/minimax/minimax-m2.5:free"
                )

            def switch_runtime_role(self, role, provider_name, model):
                return self._switch_mock(role, provider_name, model)

        session = Session()

        controls.apply_pending(session)

        session._switch_mock.assert_called_once_with(
            "coach", "kilo", "kilo/minimax/minimax-m2.5:free"
        )
        assert (
            controls._coach_name
            == "kilo | model=kilo/minimax/minimax-m2.5:free"
        )

    def test_apply_pending_shows_warning_if_runtime_switch_fails(self):
        controls = self._make_controls()
        controls._picker.pop_pending_change.return_value = (
            "player",
            "opencode",
            "opencode/mimo-v2-pro-free",
        )

        class Session:
            def __init__(self):
                self.config = MagicMock()
                self.config.player_provider = "black"
                self.config.player_model = "blackboxai/z-ai/glm-5"
                self._provider = MagicMock()
                self._provider.check_ready.return_value = (True, "")
                self._get_or_create_provider = MagicMock(return_value=self._provider)
                self._switch_mock = MagicMock(side_effect=RuntimeError("boom"))

            def switch_runtime_role(self, role, provider_name, model):
                return self._switch_mock(role, provider_name, model)

        session = Session()

        controls.apply_pending(session)

        assert controls._player_name == ""
        controls._status_bar.show_warning.assert_called_once()
