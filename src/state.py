"""Session state management."""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class SessionState(Enum):
    """Session state machine states."""
    CREATED = "created"
    PREPARING_WORKSPACES = "preparing_workspaces"
    AGENTS_RUNNING = "agents_running"
    BUG_DETECTION = "bug_detection"
    JUDGING = "judging"
    WINNER_SELECTED = "winner_selected"
    PROMOTING = "promoting"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    RETRY = "retry"
    SYNTHESIZING = "synthesizing"
    ROUND_FAILED = "round_failed"


# Valid state transitions
_VALID_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.CREATED: {
        SessionState.PREPARING_WORKSPACES,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.PREPARING_WORKSPACES: {
        SessionState.AGENTS_RUNNING,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.AGENTS_RUNNING: {
        SessionState.BUG_DETECTION,
        SessionState.ROUND_FAILED,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.ROUND_FAILED: {
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.BUG_DETECTION: {
        SessionState.JUDGING,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.JUDGING: {
        SessionState.WINNER_SELECTED,
        SessionState.RETRY,
        SessionState.SYNTHESIZING,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.WINNER_SELECTED: {
        SessionState.PROMOTING,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.PROMOTING: {
        SessionState.COMPLETED,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.RETRY: {
        SessionState.AGENTS_RUNNING,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.SYNTHESIZING: {
        SessionState.COMPLETED,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    # Terminal states
    SessionState.COMPLETED: set(),
    SessionState.FAILED: set(),
    SessionState.STOPPED: set(),
}


@dataclass
class SessionManager:
    """Manages session state with persistence."""

    session_dir: str

    def __post_init__(self):
        self._state: dict[str, Any] = {}
        self._state_file = Path(self.session_dir) / "session.json"

    def create(self, session_id: str, config: dict[str, Any]) -> None:
        """Create a new session."""
        self._state = {
            "session_id": session_id,
            "state": SessionState.CREATED.value,
            "config": config,
            "rounds": [],
            "created_at": self._now(),
        }
        self._save()

    def transition(self, new_state: SessionState, data: dict[str, Any] | None = None) -> None:
        """Transition to a new state.

        Raises:
            ValueError: If transition is invalid
        """
        current = SessionState(self._state.get("state", "created"))

        # Allow transition from any state to terminal states during error handling
        if new_state in (SessionState.FAILED, SessionState.STOPPED):
            pass  # Always allowed
        elif current not in _VALID_TRANSITIONS:
            raise ValueError(f"No valid transitions from {current}")
        elif new_state not in _VALID_TRANSITIONS.get(current, set()):
            raise ValueError(f"Invalid transition: {current} -> {new_state}")

        self._state["state"] = new_state.value
        self._state["transitioned_at"] = self._now()
        if data:
            self._state.update(data)
        self._save()

    def add_round_result(self, result: dict[str, Any]) -> None:
        """Add a round result to the session."""
        rounds = self._state.get("rounds", [])
        rounds.append(result)
        self._state["rounds"] = rounds
        self._save()

    def load(self) -> dict[str, Any]:
        """Load session state from disk."""
        if self._state_file.exists():
            try:
                self._state = json.loads(self._state_file.read_text())
            except (json.JSONDecodeError, ValueError):
                self._state = {}
        return self._state

    def _save(self) -> None:
        """Save session state to disk."""
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._state, indent=2, default=str))

    def _now(self) -> str:
        """Get current timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
