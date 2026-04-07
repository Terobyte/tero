"""Runtime controls: keyboard-driven coach/player switching and context status bar."""

from __future__ import annotations

import io
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
    if ch in ("r", "R"):
        return "reset_progress"
    if ch in ("\r", "\n"):
        return "confirm"
    return ""


def _batch_role_follows_coach(session, provider_attr: str, model_attr: str) -> bool:
    """Return True when a batch coach role is still mirroring the current coach."""
    return (
        getattr(session.config, provider_attr, "") == session.config.coach_provider
        and getattr(session.config, model_attr, "") == session.config.coach_model
    )


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

    def _open_input_fd(self) -> tuple[int | None, int | None]:
        """Return (fd, owned_fd) for keyboard input.

        Prefer the controlling terminal when stdin is redirected or wrapped,
        because hotkeys should still work in that case.
        """
        try:
            stdin_fd = sys.stdin.fileno()
            if os.isatty(stdin_fd):
                return stdin_fd, None
        except (io.UnsupportedOperation, OSError):
            pass

        try:
            tty_fd = os.open("/dev/tty", os.O_RDONLY | os.O_NONBLOCK)
            return tty_fd, tty_fd
        except OSError:
            return None, None

    def run(self) -> None:
        owned_fd: int | None = None
        old_settings = None
        try:
            fd, owned_fd = self._open_input_fd()
            if fd is None:
                return
            old_settings = termios.tcgetattr(fd)
        except (io.UnsupportedOperation, OSError, termios.error):
            return
        try:
            tty.setcbreak(fd)
            while not self._stop_event.is_set():
                # Non-blocking check: wait up to 0.1s for input
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    continue

                ch_raw = os.read(fd, 1)
                ch = ch_raw.decode(errors="ignore")
                if not ch:
                    break

                if ch == "\x1b":
                    # ESC: check for follow bytes within 100ms
                    follow_ready, _, _ = select.select([fd], [], [], 0.1)
                    if not follow_ready:
                        # Standalone ESC
                        action = _parse_escape_sequence(None)
                    else:
                        bracket = os.read(fd, 1).decode(errors="ignore")
                        if bracket == "[":
                            follow_ready2, _, _ = select.select(
                                [fd], [], [], 0.05
                            )
                            if follow_ready2:
                                final = os.read(fd, 1)
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
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except (OSError, termios.error):
                pass
            if owned_fd is not None:
                try:
                    os.close(owned_fd)
                except OSError:
                    pass


class StatusBar:
    """Renders a persistent status line at the terminal's bottom row using ANSI escape codes."""

    def __init__(self) -> None:
        self._player_name = ""
        self._coach_name = ""
        self._ctx_pct: int = 0
        self._warning_text: Optional[str] = None
        self._warning_timer: Optional[threading.Timer] = None
        self._paused = False
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

    def pause(self) -> None:
        """Pause rendering while Rich Live (or similar) owns the terminal."""
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """Resume rendering; immediately re-draws the status bar."""
        with self._lock:
            self._paused = False
        self._render()

    def clear_warning(self) -> None:
        """Immediately cancel any active warning and revert to normal status."""
        with self._lock:
            if self._warning_timer is not None:
                self._warning_timer.cancel()
                self._warning_timer = None
            self._warning_text = None
        self._render()

    def _revert_warning(self) -> None:
        with self._lock:
            self._warning_text = None
            self._warning_timer = None
        self._render()

    def clear(self) -> None:
        """Erase the status bar line (called on session stop)."""
        try:
            h = os.get_terminal_size().lines
            sys.stdout.write(f"\x1b7\x1b[{h};1H\x1b[K\x1b8")
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
            if getattr(self, "_paused", False):
                return
            warning = self._warning_text
            if warning:
                line = f" ⚠ {warning}"
            else:
                line = (
                    f" Player: {self._player_name} [A/D]"
                    f"   Coach: {self._coach_name} [←/→]"
                    f"   Ctx: {self._ctx_pct}% [ESC=compact, R=reset plan]"
                )

        sys.stdout.write(f"\x1b7\x1b[{h};1H\x1b[K{line}\x1b8")
        sys.stdout.flush()


# Model presets available at runtime
MODEL_PRESETS: list[tuple[str, str, str]] = [
    ("GLM-5", "black", "blackboxai/z-ai/glm-5"),
    ("Turbo", "turbo", "glm-5-turbo"),
    ("ZAI", "zai", "glm-5.1"),
    ("Sonnet", "claude", "claude-sonnet-4-6"),
    ("Opus", "claude", "claude-opus-4-6"),
    ("GPT-5.4", "codex", ""),
    ("o3", "codex", "o3"),
    ("o4-mini", "codex", "o4-mini"),
    ("MIMO-Pro", "opencode", "opencode/mimo-v2-pro-free"),
    ("MIMO-Omni", "opencode", "opencode/mimo-v2-omni-free"),
    ("MiniMax-2.5", "opencode", "opencode/minimax-m2.5-free"),
    ("Kimi-K2", "opencode", "openrouter/moonshotai/kimi-k2:free"),
    ("OpenCode GLM-5.1", "opencode", "zai/glm-5.1"),
    ("Nemotron-3", "opencode", "opencode/nemotron-3-super-free"),
    ("Kilo MIMO-Pro", "kilo", "kilo/xiaomi/mimo-v2-pro:free"),
    ("Kilo MiniMax", "kilo", "kilo/minimax/minimax-m2.5:free"),
]


class Picker:
    """Inline model selector: press ←/→ (coach) or A/D (player) to cycle,
    Enter to apply, ESC to cancel. Selection shown in status bar."""

    CANCEL_DELAY_S = 5.0  # auto-cancel if no key within this many seconds

    def __init__(
        self,
        presets: list[tuple[str, str, str]],
        status_bar: "StatusBar | None" = None,
    ) -> None:
        self._presets = presets
        self._status_bar = status_bar or StatusBar()
        self._role: Optional[str] = None  # None = idle, "coach"/"player" = selecting
        self._idx: int = 0
        self._pending_change: Optional[tuple[str, str, str]] = None
        self._cancel_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        """Backward-compatible picker state for tests and older callers."""
        if self._pending_change is not None:
            return "CONFIRMING"
        if self._role is not None:
            return "OPEN"
        return "CLOSED"

    @state.setter
    def state(self, value: str) -> None:
        if value == "OPEN":
            if self._role is None:
                self._role = "coach"
            self._pending_change = None
            return
        if value == "CONFIRMING":
            self._pending_change = self._pending_change or ("coach", "", "")
            return
        self._role = None
        self._pending_change = None

    @property
    def role(self) -> Optional[str]:
        return self._role

    @role.setter
    def role(self, value: Optional[str]) -> None:
        self._role = value

    @property
    def _open_idx(self) -> int:
        return self._idx

    @_open_idx.setter
    def _open_idx(self, value: int) -> None:
        self._idx = value

    def handle_action(
        self, action: str, current_coach_idx: int, current_player_idx: int
    ) -> Optional[str]:
        """Process one action token.

        Returns an out-of-band runtime action such as ``compact`` or
        ``reset_progress`` when triggered from the idle state.
        """
        with self._lock:
            return self._handle_locked(action, current_coach_idx, current_player_idx)

    def _handle_locked(
        self, action: str, coach_idx: int, player_idx: int
    ) -> Optional[str]:
        n = len(self._presets)
        is_coach = action in ("coach_right", "coach_left")
        is_player = action in ("player_right", "player_left")

        # Always cancel the existing idle-timeout on any keypress
        if self._cancel_timer:
            self._cancel_timer.cancel()
            self._cancel_timer = None

        if self._role is None:  # ── IDLE ──────────────────────────────────
            if is_coach:
                self._role = "coach"
                step = 1 if action == "coach_right" else -1
                self._idx = (coach_idx + step) % n
                self._render()
                self._schedule_dismiss()
            elif is_player:
                self._role = "player"
                step = 1 if action == "player_right" else -1
                self._idx = (player_idx + step) % n
                self._render()
                self._schedule_dismiss()
            elif action == "compact":
                return "compact"
            elif action == "reset_progress":
                return "reset_progress"
            return None

        # ── SELECTING ─────────────────────────────────────────────────────
        if is_coach and self._role == "coach":
            step = 1 if action == "coach_right" else -1
            self._idx = (self._idx + step) % n
            self._render()
            self._schedule_dismiss()
        elif is_player and self._role == "player":
            step = 1 if action == "player_right" else -1
            self._idx = (self._idx + step) % n
            self._render()
            self._schedule_dismiss()
        elif is_coach and self._role == "player":
            # Switch to coach selection, step from current coach idx
            self._role = "coach"
            step = 1 if action == "coach_right" else -1
            self._idx = (coach_idx + step) % n
            self._render()
            self._schedule_dismiss()
        elif is_player and self._role == "coach":
            # Switch to player selection, step from current player idx
            self._role = "player"
            step = 1 if action == "player_right" else -1
            self._idx = (player_idx + step) % n
            self._render()
            self._schedule_dismiss()
        elif action == "confirm":
            name, provider, model = self._presets[self._idx]
            self._pending_change = (self._role, provider, model)
            self._status_bar.show_warning(
                f"✓ {self._role.capitalize()}: {name}", duration_s=2.0
            )
            self._role = None
        elif action == "compact":
            # ESC cancels selection; immediately clear the preview
            self._role = None
            self._status_bar.clear_warning()

        return None

    def pop_pending_change(self) -> Optional[tuple[str, str, str]]:
        """Return and clear a confirmed (role, provider, model) tuple, or None."""
        with self._lock:
            pending = self._pending_change
            self._pending_change = None
        return pending

    def _show_preview(self) -> None:
        name, _, _ = self._presets[self._idx]
        keys = "[A/D]" if self._role == "player" else "[←/→]"
        role_label = self._role.capitalize()
        self._status_bar.show_warning(
            f"{role_label} ► {name}  {keys} navigate  Enter apply  ESC cancel",
            duration_s=self.CANCEL_DELAY_S,
        )

    def _render(self) -> None:
        """Backward-compatible preview renderer."""
        self._show_preview()

    def _schedule_cancel(self) -> None:
        timer = threading.Timer(self.CANCEL_DELAY_S, self._auto_cancel)
        timer.daemon = True
        self._cancel_timer = timer
        timer.start()

    def _schedule_dismiss(self) -> None:
        """Backward-compatible alias for tests/older callers."""
        self._schedule_cancel()

    def _auto_cancel(self) -> None:
        with self._lock:
            self._role = None
            self._cancel_timer = None


import signal


class RuntimeControls:
    """Orchestrates KeyboardListener, StatusBar, and Picker for runtime coach/player switching."""

    def __init__(self) -> None:
        self._listener = KeyboardListener()
        self._status_bar = StatusBar()
        self._picker = Picker(MODEL_PRESETS, self._status_bar)
        self._compact_requested = False
        self._reset_requested = False
        self._current_coach_idx: int = 0
        self._current_player_idx: int = 0
        self._ctx_pct: int = 0
        self._player_name: str = ""
        self._coach_name: str = ""
        self._running = False

    @staticmethod
    def _preset_index_for(provider_name: str, model: str, fallback: int = 0) -> int:
        """Return the preset index matching the active provider/model."""
        return next(
            (
                i
                for i, (_, preset_provider, preset_model) in enumerate(MODEL_PRESETS)
                if preset_provider == provider_name and preset_model == model
            ),
            fallback,
        )

    def _sync_selection_indices(self, session) -> None:
        """Align picker indices with the session's current live config."""
        self._current_coach_idx = self._preset_index_for(
            getattr(session.config, "coach_provider", ""),
            getattr(session.config, "coach_model", ""),
            self._current_coach_idx,
        )
        self._current_player_idx = self._preset_index_for(
            getattr(session.config, "player_provider", ""),
            getattr(session.config, "player_model", ""),
            self._current_player_idx,
        )

    def start(self, player_name: str = "", coach_name: str = "") -> None:
        """Start listener thread and render initial status bar."""
        if self._running:
            return
        self._player_name = player_name
        self._coach_name = coach_name
        self._status_bar.update(player_name, coach_name, 0)
        self._listener.start()
        self._running = True
        # Handle terminal resize
        try:
            signal.signal(signal.SIGWINCH, lambda *_: self._status_bar._render())
        except (OSError, ValueError, RuntimeError):
            pass  # SIGWINCH not available on all platforms

    def stop(self) -> None:
        """Stop listener thread and clear status bar."""
        if not self._running:
            return
        self._listener.stop()
        self._status_bar.clear()
        self._running = False

    def pause_render(self) -> None:
        """Pause status bar rendering (call when Rich Live takes over the terminal)."""
        self._status_bar.pause()

    def resume_render(self) -> None:
        """Resume status bar rendering after Rich Live stops."""
        self._status_bar.resume()

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

    @property
    def reset_requested(self) -> bool:
        return self._reset_requested

    def clear_reset(self) -> None:
        self._reset_requested = False

    def apply_pending(self, session) -> None:
        """Process queued keypresses and apply any confirmed coach/player changes."""
        self._sync_selection_indices(session)

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
            elif result == "reset_progress":
                self._reset_requested = True

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
            self._status_bar.show_warning(
                f"{role.capitalize()} {provider_name} not ready: {reason}"
            )
            return

        try:
            runtime_switch = getattr(type(session), "switch_runtime_role", None)
            if runtime_switch is not None:
                display = session.switch_runtime_role(role, provider_name, model)
            elif role == "coach":
                sync_batch_pre = _batch_role_follows_coach(
                    session, "batch_pre_provider", "batch_pre_model"
                )
                sync_batch_post = _batch_role_follows_coach(
                    session, "batch_post_provider", "batch_post_model"
                )
                session.config.coach_provider = provider_name
                session.config.coach_model = model
                if sync_batch_pre:
                    session.config.batch_pre_provider = provider_name
                    session.config.batch_pre_model = model
                if sync_batch_post:
                    session.config.batch_post_provider = provider_name
                    session.config.batch_post_model = model
                session.coach_provider = session._get_or_create_provider(provider_name)
                session.coach_model = session._build_role_display("coach")
                display = session.coach_model
            else:
                session.config.player_provider = provider_name
                session.config.player_model = model
                session.player_provider = session._get_or_create_provider(provider_name)
                session.player_model = session._build_role_display("player")
                display = session.player_model
        except Exception as e:
            self._status_bar.show_warning(
                f"{role.capitalize()} switch failed: {e}"
            )
            return

        if role == "coach":
            self._coach_name = display
            self._current_coach_idx = self._preset_index_for(
                provider_name, model, self._current_coach_idx
            )
        elif role == "player":
            self._player_name = display
            self._current_player_idx = self._preset_index_for(
                provider_name, model, self._current_player_idx
            )

        self._status_bar.update(self._player_name, self._coach_name, self._ctx_pct)
