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


class StatusBar:
    """Renders a persistent status line at the terminal's bottom row using ANSI escape codes."""

    def __init__(self) -> None:
        self._player_name = ""
        self._coach_name = ""
        self._ctx_pct: int = 0
        self._warning_text: Optional[str] = None
        self._warning_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def update(self, player_name: str, coach_name: str, ctx_pct: int) -> None:
        """Update displayed values and re-render immediately."""
        with self._lock:
            self._player_name = player_name
            self._coach_name = coach_name
            self._ctx_pct = ctx_pct
            self._warning_text = None
        self._render()

    def show_warning(self, text: str, duration_s: float = 3.0) -> None:
        """Temporarily show a warning message, then revert to normal status."""
        with self._lock:
            if self._warning_timer is not None:
                self._warning_timer.cancel()
            self._warning_text = text
        self._render()
        timer = threading.Timer(duration_s, self._revert_warning)
        with self._lock:
            self._warning_timer = timer
        timer.daemon = True
        timer.start()

    def _revert_warning(self) -> None:
        with self._lock:
            self._warning_text = None
            self._warning_timer = None
        self._render()

    def clear(self) -> None:
        """Erase the status bar line (called on session stop)."""
        try:
            h = os.get_terminal_size().lines
            sys.stdout.write(f"\0337\033[{h};1H\033[K\0338")
            sys.stdout.flush()
        except OSError:
            pass

    def _render(self) -> None:
        """Write the status bar to the terminal bottom line."""
        try:
            h = os.get_terminal_size().lines
        except OSError:
            return

        with self._lock:
            warning = self._warning_text
            if warning:
                line = f" ⚠ {warning}"
            else:
                line = (
                    f" Player: {self._player_name} [A/D]"
                    f"   Coach: {self._coach_name} [←/→]"
                    f"   Ctx: {self._ctx_pct}% [ESC=compact]"
                )

        sys.stdout.write(f"\0337\033[{h};1H\033[K{line}\0338")
        sys.stdout.flush()
