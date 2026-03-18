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


# Model presets available at runtime
MODEL_PRESETS: list[tuple[str, str, str]] = [
    ("GLM-1",         "ccg",    "blackboxai/z-ai/glm-5"),
    ("GLM-2",         "ccg2",   "blackboxai/z-ai/glm-5"),
    ("Sonnet",        "claude",  "claude-sonnet-4-6"),
    ("Opus",          "claude",  "claude-opus-4-6"),
    ("GPT-5.4 Med",   "codex",   "gpt-5.4-medium"),
    ("GPT-5.4 High",  "codex",   "gpt-5.4-high"),
    ("GPT-5.4 Ultra", "codex",   "gpt-5.4-ultra-high"),
]


class Picker:
    """Top-line model picker with state machine: CLOSED → OPEN → CONFIRMING → CLOSED."""

    DISMISS_DELAY_S = 1.5

    def __init__(self, presets: list[tuple[str, str, str]] = MODEL_PRESETS) -> None:
        self._presets = presets
        self.state = "CLOSED"
        self.role: str = "coach"
        self._open_idx: int = 0
        self._pending_change: Optional[tuple[str, str, str]] = None  # (role, provider, model)
        self._dismiss_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def handle_action(
        self, action: str, current_coach_idx: int, current_player_idx: int
    ) -> Optional[str]:
        """Process one action token. Returns 'compact' if ESC pressed in CLOSED state."""
        with self._lock:
            return self._handle_locked(action, current_coach_idx, current_player_idx)

    def _handle_locked(self, action: str, coach_idx: int, player_idx: int) -> Optional[str]:
        n = len(self._presets)

        if self.state == "CONFIRMING":
            # Any key cancels auto-dismiss and is re-processed in CLOSED state
            if self._dismiss_timer:
                self._dismiss_timer.cancel()
                self._dismiss_timer = None
            self.state = "CLOSED"
            self._clear_top_line()
            # Re-dispatch the action in CLOSED state
            return self._handle_locked(action, coach_idx, player_idx)

        if self.state == "CLOSED":
            if action == "coach_right":
                self.role, self._open_idx = "coach", coach_idx
                self.state = "OPEN"
                self._render()
            elif action == "coach_left":
                self.role, self._open_idx = "coach", coach_idx
                self.state = "OPEN"
                self._render()
            elif action == "player_right":
                self.role, self._open_idx = "player", player_idx
                self.state = "OPEN"
                self._render()
            elif action == "player_left":
                self.role, self._open_idx = "player", player_idx
                self.state = "OPEN"
                self._render()
            elif action == "compact":
                return "compact"
            # confirm / unknown: no-op in CLOSED
            return None

        if self.state == "OPEN":
            is_coach_action = action in ("coach_right", "coach_left")
            is_player_action = action in ("player_right", "player_left")

            if action == "compact":
                # ESC closes picker WITHOUT triggering compact
                self.state = "CLOSED"
                self._clear_top_line()
            elif action == "confirm":
                name, provider, model = self._presets[self._open_idx]
                self._pending_change = (self.role, provider, model)
                self.state = "CONFIRMING"
                self._render()  # show confirmation highlight
                self._schedule_dismiss()
            elif is_coach_action and self.role == "coach":
                step = 1 if action == "coach_right" else -1
                self._open_idx = (self._open_idx + step) % n
                self._render()
            elif is_player_action and self.role == "player":
                step = 1 if action == "player_right" else -1
                self._open_idx = (self._open_idx + step) % n
                self._render()
            elif is_player_action and self.role == "coach":
                # Switch to player picker
                self.role = "player"
                self._open_idx = player_idx
                self._render()
            elif is_coach_action and self.role == "player":
                # Switch to coach picker
                self.role = "coach"
                self._open_idx = coach_idx
                self._render()

        return None

    def pop_pending_change(self) -> Optional[tuple[str, str, str]]:
        """Return and clear a confirmed (role, provider, model) tuple, or None."""
        with self._lock:
            pending = self._pending_change
            self._pending_change = None
        return pending

    def _schedule_dismiss(self) -> None:
        timer = threading.Timer(self.DISMISS_DELAY_S, self._auto_dismiss)
        timer.daemon = True
        self._dismiss_timer = timer
        timer.start()

    def _auto_dismiss(self) -> None:
        with self._lock:
            self.state = "CLOSED"
            self._dismiss_timer = None
        self._clear_top_line()

    def _render(self) -> None:
        """Render picker at top line (row 1)."""
        try:
            labels = []
            for i, (name, _, _) in enumerate(self._presets):
                if i == self._open_idx:
                    if self.state == "CONFIRMING":
                        labels.append(f"✓{name}✓")
                    else:
                        labels.append(f"[{name}]")
                else:
                    labels.append(name)
            role_label = self.role.capitalize()
            line = f" {role_label}: ◄ {' | '.join(labels)} ►"
            sys.stdout.write(f"\0337\033[1;1H\033[K{line}\0338")
            sys.stdout.flush()
        except OSError:
            pass

    def _clear_top_line(self) -> None:
        try:
            sys.stdout.write("\0337\033[1;1H\033[K\0338")
            sys.stdout.flush()
        except OSError:
            pass


import signal


class RuntimeControls:
    """Orchestrates KeyboardListener, StatusBar, and Picker for runtime coach/player switching."""

    def __init__(self) -> None:
        self._listener = KeyboardListener()
        self._status_bar = StatusBar()
        self._picker = Picker()
        self._compact_requested = False
        self._current_coach_idx: int = 0
        self._current_player_idx: int = 0
        self._ctx_pct: int = 0
        self._player_name: str = ""
        self._coach_name: str = ""
        self._running = False

    def start(self, player_name: str = "", coach_name: str = "") -> None:
        """Start listener thread and render initial status bar."""
        self._player_name = player_name
        self._coach_name = coach_name
        self._status_bar.update(player_name, coach_name, 0)
        self._listener.start()
        self._running = True
        # Handle terminal resize
        try:
            signal.signal(signal.SIGWINCH, lambda *_: self._status_bar._render())
        except (OSError, ValueError):
            pass  # SIGWINCH not available on all platforms

    def stop(self) -> None:
        """Stop listener thread and clear status bar."""
        if not self._running:
            return
        self._listener.stop()
        self._status_bar.clear()
        self._running = False

    def update_context(self, tokens: int, context_window: int) -> None:
        """Update context percentage in the status bar."""
        if context_window > 0:
            self._ctx_pct = int(100 * tokens / context_window)
        else:
            self._ctx_pct = 0
        self._status_bar.update(self._player_name, self._coach_name, self._ctx_pct)

    @property
    def compact_requested(self) -> bool:
        return self._compact_requested

    def clear_compact(self) -> None:
        self._compact_requested = False

    def apply_pending(self, session) -> None:
        """Process queued keypresses and apply any confirmed coach/player changes."""
        # Drain the keyboard action queue
        while True:
            action = self._listener.pop_action()
            if action is None:
                break
            result = self._picker.handle_action(
                action,
                current_coach_idx=self._current_coach_idx,
                current_player_idx=self._current_player_idx,
            )
            if result == "compact":
                self._compact_requested = True

        # Apply any confirmed model change
        pending = self._picker.pop_pending_change()
        if pending is None:
            return

        role, provider_name, model = pending

        # Verify provider is ready before switching
        try:
            provider = session._get_or_create_provider(provider_name)
            ok, reason = provider.check_ready()
        except Exception as e:
            ok, reason = False, str(e)

        if not ok:
            self._status_bar.show_warning(f"{role.capitalize()} {provider_name} not ready: {reason}")
            return

        if role == "coach":
            session.config.coach_provider = provider_name
            session.config.coach_model = model
            session.coach_provider = session._get_or_create_provider(provider_name)
            session.coach_model = session._build_role_display("coach")
            self._coach_name = session.coach_model
            # Find and store new index
            self._current_coach_idx = next(
                (i for i, (_, p, m) in enumerate(MODEL_PRESETS) if p == provider_name and m == model),
                self._current_coach_idx,
            )
        elif role == "player":
            session.config.player_provider = provider_name
            session.config.player_model = model
            session.player_provider = session._get_or_create_provider(provider_name)
            session.player_model = session._build_role_display("player")
            self._player_name = session.player_model
            self._current_player_idx = next(
                (i for i, (_, p, m) in enumerate(MODEL_PRESETS) if p == provider_name and m == model),
                self._current_player_idx,
            )

        self._status_bar.update(self._player_name, self._coach_name, self._ctx_pct)
