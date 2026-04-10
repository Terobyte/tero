from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_json_logging_uses_loguru_message_record(capsys):
    from src.utils.structured_logger import get_logger, setup_logging

    setup_logging(json_output=True, level="INFO")

    get_logger("tests.structured").info("hello", request_id="abc123")

    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    payload = json.loads(lines[-1])
    assert payload["message"] == "hello"
    assert payload["module"] == "tests.structured"
    assert payload["extra"]["request_id"] == "abc123"


def test_monthly_total_only_counts_requested_month(tmp_path):
    from src.utils.cost_tracker import CostTracker

    history_file = tmp_path / "costs" / "history.jsonl"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2024-01-15T10:00:00+00:00",
                        "provider": "gemini",
                        "model": "gemini-1.5-flash",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cost_usd": 1.25,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2024-02-01T00:00:00+00:00",
                        "provider": "gemini",
                        "model": "gemini-1.5-flash",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cost_usd": 9.75,
                    }
                ),
            ]
        )
        + "\n"
    )

    tracker = CostTracker(storage_path=history_file)

    total = tracker.get_monthly_total(datetime(2024, 1, 20, tzinfo=timezone.utc))
    assert total == 1.25


def test_load_history_skips_invalid_provider_and_continues(tmp_path):
    from src.utils.cost_tracker import CostTracker

    history_file = tmp_path / "costs" / "history.jsonl"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2024-01-01T00:00:00+00:00",
                        "provider": "not-a-provider",
                        "model": "broken",
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cost_usd": 1.0,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2024-01-02T00:00:00+00:00",
                        "provider": "gemini",
                        "model": "gemini-1.5-flash",
                        "input_tokens": 10,
                        "output_tokens": 10,
                        "cost_usd": 2.0,
                    }
                ),
            ]
        )
        + "\n"
    )

    tracker = CostTracker(storage_path=history_file)

    assert len(tracker._entries) == 1
    assert tracker._entries[0].cost_usd == 2.0


def test_status_bar_update_preserves_active_warning():
    from src.runtime_controls import StatusBar

    bar = StatusBar.__new__(StatusBar)
    bar._player_name = "player"
    bar._coach_name = "coach"
    bar._ctx_pct = 1
    bar._warning_text = "keep me"
    bar._warning_timer = None
    bar._paused = False
    bar._lock = threading.Lock()

    with patch.object(bar, "_render"):
        bar.update("new-player", "new-coach", 42)

    assert bar._warning_text == "keep me"


def test_runtime_controls_can_restart_after_stop():
    from src.runtime_controls import RuntimeControls

    class DummyListener:
        def __init__(self) -> None:
            self._stop_event = threading.Event()
            self.started = 0
            self.stopped = 0

        def start(self) -> None:
            if self.started:
                raise RuntimeError("threads can only be started once")
            self.started += 1

        def stop(self) -> None:
            self.stopped += 1
            self._stop_event.set()

        def join(self, timeout: float | None = None) -> None:
            return None

        def pop_action(self):
            return None

    first = DummyListener()
    second = DummyListener()

    with patch("src.runtime_controls.KeyboardListener", side_effect=[first, second]), patch(
        "src.runtime_controls.signal.signal"
    ):
        controls = RuntimeControls()
        controls._status_bar = MagicMock()

        controls.start("player", "coach")
        controls.stop()
        controls.start("player", "coach")

    assert first.started == 1
    assert first.stopped == 1
    assert second.started == 1
    assert controls._listener is second


def test_transition_to_failed_works_even_with_invalid_persisted_state(tmp_path):
    from src.state import SessionManager, SessionState

    manager = SessionManager(str(tmp_path))
    manager._state = {"state": "totally-invalid"}

    manager.transition(SessionState.FAILED)

    assert manager._state["state"] == SessionState.FAILED.value


def test_resume_style_transition_back_to_agents_running_is_allowed(tmp_path):
    from src.state import SessionManager, SessionState

    manager = SessionManager(str(tmp_path))
    manager._state = {"state": SessionState.JUDGING.value}

    manager.transition(SessionState.AGENTS_RUNNING, {"current_round": 2})

    assert manager._state["state"] == SessionState.AGENTS_RUNNING.value
    assert manager._state["current_round"] == 2


def test_config_report_marks_missing_ai_keys_as_warnings(capsys):
    from src.utils.config_validator import print_config_report

    env = {
        "APPLICANT_EMAIL": "person@example.com",
        "APPLICANT_FIRST_NAME": "Test",
        "APPLICANT_LAST_NAME": "User",
        "GEMINI_API_KEY": "gemini-key",
    }

    with patch.dict("os.environ", env, clear=True):
        print_config_report()

    output = capsys.readouterr().out
    assert "✅ GEMINI_API_KEY" in output
    assert "⚠️ ANTHROPIC_API_KEY" in output
    assert "⚠️ OPENAI_API_KEY" in output
