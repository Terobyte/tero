"""Runtime controls: keyboard-driven coach/player switching and context status bar."""
from __future__ import annotations

import os
import queue
import select
import sys
import termios
import threading
import tty
from typing import Optional


# --- Helpers (module-level, testable without class instantiation) ---

def _parse_escape_sequence(follow_byte: Optional[bytes]) -> str:
    """Convert the byte after ESC+[ to an action, or None follow -> 'compact'."""
    if follow_byte is None:
        return "compact"
    if follow_byte == b"C":
        return "coach_right"
    if follow_byte == b"D":
        return "coach_left"
    return ""  # unknown sequence, ignore


def _char_to_action(ch: str) -> str:
    """Map a single printable character to an action string."""
    if ch in ("a", "A"):
        return "player_left"
    if ch in ("d", "D"):
        return "player_right"
    if ch in ("\r", "\n"):
        return "confirm"
    return ""


class KeyboardListener(threading.Thread):
    """Daemon thread that reads keypresses from stdin in cbreak mode.

    Queues string action tokens consumable via pop_action().
    Terminal mode is always restored on exit, even on crash.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="KeyboardListener")
        self._action_queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def pop_action(self) -> Optional[str]:
        """Return and remove the next queued action, or None if queue is empty."""
        try:
            return self._action_queue.get_nowait()
        except queue.Empty:
            return None

    def run(self) -> None:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not self._stop_event.is_set():
                # Non-blocking check: wait up to 0.1s for input
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if not ready:
                    continue

                ch = sys.stdin.read(1)
                if not ch:
                    break

                if ch == "\x1b":
                    # ESC: check for follow bytes within 100ms
                    follow_ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if not follow_ready:
                        # Standalone ESC
                        action = _parse_escape_sequence(None)
                    else:
                        bracket = sys.stdin.read(1)
                        if bracket == "[":
                            follow_ready2, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if follow_ready2:
                                final = sys.stdin.read(1).encode()
                                action = _parse_escape_sequence(final)
                            else:
                                action = ""
                        else:
                            action = ""
                else:
                    action = _char_to_action(ch)

                if action:
                    self._action_queue.put(action)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
