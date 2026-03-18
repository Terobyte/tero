# Runtime Controls Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix batch executor auto-reject that bypasses the coach, and add live runtime coach/player switching with a persistent terminal status bar and ESC-triggered compaction.

**Architecture:** Feature 1 is a ~8-line deletion in `batch_executor.py`. Feature 2 introduces `src/runtime_controls.py` (three isolated classes: `KeyboardListener`, `StatusBar`, `Picker` composed by `RuntimeControls`) integrated into `CoachPlayerSession` via start/stop lifecycle and per-step `apply_pending` calls.

**Tech Stack:** Python stdlib only — `tty`, `termios`, `select`, `threading`, `os.get_terminal_size`, ANSI escape codes.

**Spec:** `docs/superpowers/specs/2026-03-17-runtime-controls-design.md`

---

## Chunk 1: Fix Batch Auto-Reject Without Coach

### Task 1: Remove the two auto-reject `continue` blocks

**Files:**
- Modify: `src/batch_executor.py` (lines ~361–369)
- Test: `tests/test_batch_executor.py`

**Context:** `_run_phase()` currently skips calling the coach when the player hasn't written `PHASE_COMPLETE` or report headers. The fix: delete both early-return blocks so control always falls through to `_review_strategy()` + `_run_coach_turn_for_phase()`. The coach already handles incomplete phases via `build_phase_coach_prompt()`.

- [ ] **Step 1.1: Read the relevant section first**

Read `src/batch_executor.py` lines 355–395 to confirm the exact code.
Read `tests/test_batch_executor.py` to understand existing test patterns.

- [ ] **Step 1.2: Write a failing test — coach IS called when PHASE_COMPLETE is missing**

In `tests/test_batch_executor.py`, add:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.batch_executor import BatchExecutor
from src.plan_tracker import Phase, PlanItem


def _make_phase(name: str, steps: list[str]) -> Phase:
    return Phase(name=name, steps=[PlanItem(text=s) for s in steps])


@pytest.mark.asyncio
async def test_coach_called_when_phase_complete_missing():
    """Coach must be called even when player output has no PHASE_COMPLETE marker."""
    phase = _make_phase("Update", ["Step A", "Step B"])

    # Player result: no PHASE_COMPLETE, no report headers
    player_result = MagicMock()
    player_result.text = "I explored the codebase."  # no markers

    session = MagicMock()
    session.config.max_turns = 3
    session.config.player_model = ""
    session.config.coach_model = ""
    session.config.player_timeout_s = 60
    session.config.player_provider = "ccg"
    session.config.coach_provider = "claude"
    session.config.batch_pre_judge_attempts = 3
    session.config.batch_judge_attempts = 1
    session.config.batch_post_judge_attempts = 1

    session._run_with_continuation = AsyncMock(return_value=player_result)

    # Coach returns Approved on first call
    from src.feedback import Approved
    session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
    session._snapshot_pids = MagicMock(return_value=set())
    session._kill_new_processes = MagicMock()
    session.build_provider_display = MagicMock(return_value="claude | model=sonnet")

    tracker = MagicMock()
    tracker.items = []
    tracker.render_dashboard = MagicMock()
    tracker.phase_done = MagicMock()

    executor = BatchExecutor(session, tracker)
    result = await executor._run_phase(phase)

    assert result is True
    # Coach must have been called despite missing PHASE_COMPLETE
    session._run_coach_turn_for_phase.assert_called_once()


@pytest.mark.asyncio
async def test_coach_called_when_report_headers_missing():
    """Coach must be called even when player output has PHASE_COMPLETE but no report headers."""
    phase = _make_phase("Update", ["Step A"])

    player_result = MagicMock()
    # Has PHASE_COMPLETE but no What changed / Evidence / Verification
    player_result.text = "Step 1 done: done\nPHASE_COMPLETE: Update"

    session = MagicMock()
    session.config.max_turns = 3
    session.config.player_model = ""
    session.config.coach_model = ""
    session.config.player_timeout_s = 60
    session.config.player_provider = "ccg"
    session.config.coach_provider = "claude"
    session.config.batch_pre_judge_attempts = 3
    session.config.batch_judge_attempts = 1
    session.config.batch_post_judge_attempts = 1

    session._run_with_continuation = AsyncMock(return_value=player_result)

    from src.feedback import Approved
    session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
    session._snapshot_pids = MagicMock(return_value=set())
    session._kill_new_processes = MagicMock()
    session.build_provider_display = MagicMock(return_value="claude | model=sonnet")

    tracker = MagicMock()
    tracker.items = []
    tracker.render_dashboard = MagicMock()
    tracker.phase_done = MagicMock()

    executor = BatchExecutor(session, tracker)
    result = await executor._run_phase(phase)

    assert result is True
    session._run_coach_turn_for_phase.assert_called_once()
```

- [ ] **Step 1.3: Run tests to confirm they fail**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/test_batch_executor.py::test_coach_called_when_phase_complete_missing tests/test_batch_executor.py::test_coach_called_when_report_headers_missing -v
```

Expected: FAIL — coach is currently NOT called when markers are missing.

- [ ] **Step 1.4: Delete the two auto-reject `continue` blocks**

In `src/batch_executor.py`, in `_run_phase()`, locate and **delete** the two blocks (keep `parse_completed_steps` and `completed_steps` variable):

**Delete this block (~4 lines):**
```python
            if len(completed_steps) < len(phase.steps):
                coach_feedback = build_incomplete_phase_feedback(phase, completed_steps)
                streaming_ui.print_step_rejected(coach_feedback)
                continue
```

**Delete this block (~4 lines):**
```python
            if not has_required_completion_report(result.text):
                coach_feedback = build_missing_report_feedback(phase)
                streaming_ui.print_step_rejected(coach_feedback)
                continue
```

The remaining code after `completed_steps = parse_completed_steps(result, phase)` should go directly to `strategy = self._review_strategy(attempt_num)`.

- [ ] **Step 1.5: Run tests to confirm they pass**

```bash
python -m pytest tests/test_batch_executor.py::test_coach_called_when_phase_complete_missing tests/test_batch_executor.py::test_coach_called_when_report_headers_missing -v
```

Expected: PASS

- [ ] **Step 1.6: Run full test suite to catch regressions**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: same tests pass as before (no regressions).

- [ ] **Step 1.7: Commit**

```bash
git add src/batch_executor.py tests/test_batch_executor.py
git commit -m "fix: always call coach in batch executor, remove auto-reject bypasses"
```

---

## Chunk 2: RuntimeControls — Status Bar, Picker, Keyboard, Integration

### Task 2: `KeyboardListener` — background thread reading raw keypresses

**Files:**
- Create: `src/runtime_controls.py`
- Test: `tests/test_runtime_controls.py`

**Context:** Daemon thread runs `tty.setcbreak()` on stdin fd, reads escape sequences one byte at a time. Queues string action tokens. `finally: tcsetattr` restores terminal mode even on crash.

- [ ] **Step 2.1: Write failing tests for KeyboardListener**

Create `tests/test_runtime_controls.py`:

```python
"""Tests for RuntimeControls components."""
import threading
import time
import queue
from unittest.mock import MagicMock, patch
import pytest


class TestKeyboardListener:
    """Test KeyboardListener without a real terminal."""

    def _make_listener_with_fake_input(self, byte_sequences: list[bytes]):
        """Create a KeyboardListener that reads from a pipe instead of stdin."""
        from src.runtime_controls import KeyboardListener

        r_fd, w_fd = __import__("os").pipe()
        import io

        # Write all bytes into the write end
        for seq in byte_sequences:
            __import__("os").write(w_fd, seq)
        __import__("os").close(w_fd)

        listener = KeyboardListener.__new__(KeyboardListener)
        listener._action_queue = queue.Queue()
        listener._stop_event = threading.Event()
        listener._fd = r_fd
        return listener, r_fd

    def test_right_arrow_queues_coach_right(self):
        from src.runtime_controls import KeyboardListener

        actions = []
        listener = MagicMock(spec=KeyboardListener)
        listener._action_queue = queue.Queue()

        # Simulate the parsing logic directly
        import io
        stdin_mock = io.BytesIO(b"\x1b[C")  # right arrow ESC sequence

        with patch("sys.stdin", stdin_mock):
            # Test the _parse_escape helper
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
```

- [ ] **Step 2.2: Run to confirm tests fail**

```bash
python -m pytest tests/test_runtime_controls.py -v
```

Expected: ImportError / ModuleNotFoundError (file doesn't exist yet).

- [ ] **Step 2.3: Implement `KeyboardListener` and helper functions**

Create `src/runtime_controls.py` with just the keyboard-related parts:

```python
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
    """Convert the byte after ESC+[ to an action, or None follow → 'compact'."""
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
```

- [ ] **Step 2.4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_runtime_controls.py::TestKeyboardListener -v
```

Expected: PASS

- [ ] **Step 2.5: Commit**

```bash
git add src/runtime_controls.py tests/test_runtime_controls.py
git commit -m "feat: add KeyboardListener for runtime coach/player switching"
```

---

### Task 3: `StatusBar` — persistent ANSI status line at terminal bottom

**Files:**
- Modify: `src/runtime_controls.py`
- Modify: `tests/test_runtime_controls.py`

- [ ] **Step 3.1: Write failing tests for StatusBar**

Add to `tests/test_runtime_controls.py`:

```python
class TestStatusBar:
    def test_render_writes_ansi_to_stdout(self, capsys):
        from src.runtime_controls import StatusBar
        bar = StatusBar.__new__(StatusBar)
        bar._player_name = "GLM-1"
        bar._coach_name = "Sonnet"
        bar._ctx_pct = 42
        bar._warning_timer = None

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
        bar._warning_timer = None
        bar._lock = threading.Lock()

        render_calls = []
        with patch.object(bar, "_render", side_effect=lambda: render_calls.append(1)):
            # Use very short duration for test
            bar.show_warning("Coach not ready!", duration_s=0.05)
            time.sleep(0.15)  # wait for timer to fire
            # Should have been called at least twice: once for warning, once for revert
            assert len(render_calls) >= 2
```

- [ ] **Step 3.2: Run to confirm tests fail**

```bash
python -m pytest tests/test_runtime_controls.py::TestStatusBar -v
```

Expected: FAIL (StatusBar not yet implemented)

- [ ] **Step 3.3: Implement `StatusBar`**

Append to `src/runtime_controls.py`:

```python
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
```

- [ ] **Step 3.4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_runtime_controls.py::TestStatusBar -v
```

Expected: PASS

- [ ] **Step 3.5: Commit**

```bash
git add src/runtime_controls.py tests/test_runtime_controls.py
git commit -m "feat: add StatusBar with ANSI bottom-line rendering and show_warning"
```

---

### Task 4: `Picker` — top-line model selection UI with state machine

**Files:**
- Modify: `src/runtime_controls.py`
- Modify: `tests/test_runtime_controls.py`

**Context:** Full state machine: CLOSED → OPEN → CONFIRMING → CLOSED. ESC in OPEN = dismiss (no compact). ESC in CLOSED = compact (handled by RuntimeControls, not Picker).

- [ ] **Step 4.1: Write failing tests for Picker**

Add to `tests/test_runtime_controls.py`:

```python
MODEL_PRESETS_TEST = [
    ("GLM-1", "ccg", "blackboxai/z-ai/glm-5"),
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
```

- [ ] **Step 4.2: Run to confirm tests fail**

```bash
python -m pytest tests/test_runtime_controls.py::TestPicker -v
```

Expected: FAIL

- [ ] **Step 4.3: Implement `Picker`**

Append to `src/runtime_controls.py`:

```python
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
```

- [ ] **Step 4.4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_runtime_controls.py::TestPicker -v
```

Expected: PASS

- [ ] **Step 4.5: Commit**

```bash
git add src/runtime_controls.py tests/test_runtime_controls.py
git commit -m "feat: add Picker with full CLOSED/OPEN/CONFIRMING state machine"
```

---

### Task 5: `RuntimeControls` — orchestrator class

**Files:**
- Modify: `src/runtime_controls.py`
- Modify: `tests/test_runtime_controls.py`

- [ ] **Step 5.1: Write failing tests for RuntimeControls**

Add to `tests/test_runtime_controls.py`:

```python
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
        return controls

    def test_update_context_computes_percentage(self):
        controls = self._make_controls()
        controls.update_context(tokens=50_000, context_window=100_000)
        controls._status_bar.update.assert_called_once()
        call_kwargs = controls._status_bar.update.call_args
        # Third positional arg or kwarg 'ctx_pct' should be 50
        args = call_kwargs[0] if call_kwargs[0] else []
        assert args[2] == 50

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
        session.config.coach_provider = "ccg"
        session.config.coach_model = ""
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (True, "")
        session._get_or_create_provider.return_value = mock_provider
        session._build_role_display.return_value = "claude | model=claude-sonnet-4-6"
        session.player_model = "GLM-1"
        session.coach_model = "GLM-1"

        controls.apply_pending(session)

        assert session.config.coach_provider == "claude"
        assert session.config.coach_model == "claude-sonnet-4-6"
        session._get_or_create_provider.assert_called_with("claude")

    def test_apply_pending_skips_if_provider_not_ready(self):
        controls = self._make_controls()
        controls._picker.pop_pending_change.return_value = ("coach", "codex", "gpt-5.4-high")

        session = MagicMock()
        session.config.coach_provider = "ccg"
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (False, "Proxy not reachable")
        session._get_or_create_provider.return_value = mock_provider

        controls.apply_pending(session)

        # coach_provider should NOT have changed
        assert session.config.coach_provider == "ccg"
        controls._status_bar.show_warning.assert_called_once()
```

- [ ] **Step 5.2: Run to confirm they fail**

```bash
python -m pytest tests/test_runtime_controls.py::TestRuntimeControls -v
```

Expected: FAIL

- [ ] **Step 5.3: Implement `RuntimeControls`**

Append to `src/runtime_controls.py`:

```python
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
```

- [ ] **Step 5.4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_runtime_controls.py::TestRuntimeControls -v
```

Expected: PASS

- [ ] **Step 5.5: Run all runtime_controls tests**

```bash
python -m pytest tests/test_runtime_controls.py -v
```

Expected: all pass.

- [ ] **Step 5.6: Commit**

```bash
git add src/runtime_controls.py tests/test_runtime_controls.py
git commit -m "feat: add RuntimeControls orchestrator with apply_pending and compact_requested"
```

---

### Task 6: Integrate `RuntimeControls` into `CoachPlayerSession`

**Files:**
- Modify: `src/coach_player.py`

**Context:** `RuntimeControls` is started in `run()`, stopped in `finally`. `apply_pending` called at top of outer step loop. `update_context` called inside `_run_turn`. ESC compact handled before player turn. Note: the player now uses `self.config.player_turns_per_session` instead of a hardcoded 30 for `max_turns` — this was already fixed in the source code and no additional change is needed here.

- [ ] **Step 6.1: Read the relevant sections of coach_player.py**

Read `src/coach_player.py` lines 74–120 (init) and lines 322–450 (run loop) to understand exact insertion points.

- [ ] **Step 6.2: Write a failing integration test**

In `tests/test_coach_player.py`, add:

```python
@pytest.mark.asyncio
async def test_runtime_controls_started_and_stopped(tmp_path, monkeypatch):
    """RuntimeControls.start() is called when run() begins and stop() in finally."""
    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)

    with patch("src.coach_player.RuntimeControls") as MockRC:
        mock_rc = MagicMock()
        mock_rc.compact_requested = False
        MockRC.return_value = mock_rc

        cfg = Config(working_dir=str(tmp_path), plan_file="requirements.md", max_turns=1)
        session = CoachPlayerSession(cfg, "1. Ship feature")
        mock_provider = MagicMock()
        mock_provider.check_ready = MagicMock(return_value=(True, ""))
        mock_provider.display_name = "Mock"
        session.player_provider = mock_provider
        session.coach_provider = mock_provider

        # Make it return immediately (all steps done)
        with patch.object(session, "_run_turn", new_callable=AsyncMock):
            with patch("src.coach_player.parse_requirements", return_value=[]):
                await session.run()

        mock_rc.start.assert_called_once()
        mock_rc.stop.assert_called_once()


@pytest.mark.asyncio
async def test_apply_pending_called_each_step(tmp_path, monkeypatch):
    """apply_pending is called once per step at the top of the outer loop."""
    from src.plan_tracker import PlanItem
    from src.feedback import Approved

    monkeypatch.setattr("src.streaming.stream_messages", lambda msg, verbose=False, role="": 0)

    with patch("src.coach_player.RuntimeControls") as MockRC:
        mock_rc = MagicMock()
        mock_rc.compact_requested = False
        MockRC.return_value = mock_rc

        cfg = Config(working_dir=str(tmp_path), plan_file="requirements.md", max_turns=1)
        session = CoachPlayerSession(cfg, "1. Ship feature")
        mock_provider = MagicMock()
        mock_provider.check_ready = MagicMock(return_value=(True, ""))
        mock_provider.display_name = "Mock"
        session.player_provider = mock_provider
        session.coach_provider = mock_provider

        two_steps = [PlanItem(text="step 1"), PlanItem(text="step 2")]

        with patch("src.coach_player.parse_requirements", return_value=two_steps):
            with patch.object(session, "_run_turn", new_callable=AsyncMock) as mock_turn:
                mock_turn.return_value = MagicMock(
                    text="What changed:\n- done\nEvidence:\n- file\nVerification:\n- check",
                    duration_s=1.0, tools_used=0, tokens_used=0, messages=[]
                )
                with patch("src.coach_player.parse_coach_output", return_value=Approved()):
                    await session.run()

        # apply_pending called once per step (2 steps)
        assert mock_rc.apply_pending.call_count == 2
```

- [ ] **Step 6.3: Run to confirm tests fail**

```bash
python -m pytest tests/test_coach_player.py::test_runtime_controls_started_and_stopped tests/test_coach_player.py::test_apply_pending_called_each_step -v 2>&1 | tail -20
```

Expected: FAIL

- [ ] **Step 6.4: Add `RuntimeControls` to `__init__` and wire `_run_turn`**

In `src/coach_player.py`:

**In `__init__` after existing setup:**
```python
from src.runtime_controls import RuntimeControls
self.runtime_controls = RuntimeControls()
```

**In `_run_turn()`, after the `streaming_ui.print_turn_timing(...)` call (line ~700):**
```python
        # Update status bar context percentage
        if hasattr(self, "runtime_controls"):
            self.runtime_controls.update_context(tokens_used, context_window)
```

- [ ] **Step 6.5: Add lifecycle and apply_pending into `run()`**

In `src/coach_player.py`, `run()`:

**After `self._setup_interrupt_handler()` (line ~324):**
```python
        self.runtime_controls.start(
            player_name=self.player_model,
            coach_name=self.coach_model,
        )
```

**Wrap the existing `try:` block to add `finally: self.runtime_controls.stop()`:**
```python
        try:
            for step_index in range(start_index, total_steps):
                # ... existing loop body ...
        except Exception as exc:
            # ... existing ...
        finally:
            self.runtime_controls.stop()
```

**At the top of the outer `for step_index` loop, before the `attempt` loop:**
```python
                # Runtime controls: apply any pending coach/player switches
                self.runtime_controls.apply_pending(self)
                self._last_turn_result: TurnResult | None = None  # reset per step
```

- [ ] **Step 6.6: Add ESC compact handling before player turn**

In the `for attempt in range(...)` loop, before `player_prompt = build_player_step_prompt(...)`:

```python
                    # ESC compact: override player prompt with compact continuation
                    compact_prompt_override = None
                    if self.runtime_controls.compact_requested:
                        self.runtime_controls.clear_compact()
                        if self._last_turn_result is not None:
                            from src.context_manager import _build_compact_summary
                            summary = _build_compact_summary(self._last_turn_result.messages)
                            compact_prompt_override = (
                                f"Context compacted. Summary of previous work:\n{summary}\n\n"
                                f"Continue implementing the current step: {step.text}\n"
                                "When done, include:\nWhat changed: ...\nEvidence: ...\nVerification: ..."
                            )
```

Then change the player_prompt assignment to:
```python
                    player_prompt = compact_prompt_override or build_player_step_prompt(
                        current_step=step.text,
                        step_num=step_num,
                        total_steps=total_steps,
                        completed_steps=completed_steps,
                        feedback=feedback.text if feedback else None,
                    )
```

After successful player turn (after `player_result = await self._run_turn(...)`):
```python
                    self._last_turn_result = player_result
```

- [ ] **Step 6.7: Run integration tests**

```bash
python -m pytest tests/test_coach_player.py::test_runtime_controls_started_and_stopped tests/test_coach_player.py::test_apply_pending_called_each_step -v
```

Expected: PASS

- [ ] **Step 6.8: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: no regressions.

- [ ] **Step 6.9: Commit**

```bash
git add src/coach_player.py tests/test_coach_player.py
git commit -m "feat: integrate RuntimeControls into CoachPlayerSession (status bar, picker, compact)"
```

---

### Task 7: Smoke test in real terminal

**No automated test — manual verification only.**

- [ ] **Step 7.1: Run tero with a simple plan**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python g3.py go --no-menu --player-provider=ccg --coach-provider=claude -n 2
```

Verify:
- Status bar appears at bottom of terminal: `Player: ... [A/D]   Coach: ... [←/→]   Ctx: 0% [ESC=compact]`
- Pressing `→` shows the coach picker at top
- Pressing `→` again cycles the selection
- Pressing `Enter` confirms and picker disappears after 1.5s
- Status bar updates after each turn with new ctx %
- Pressing `ESC` (when picker is closed) shows compact message on next player turn
- Ctrl+C still interrupts the session cleanly
- Terminal mode is restored after exit (text input works normally after)

- [ ] **Step 7.2: Final commit tag**

```bash
git add .
git commit -m "feat: runtime controls complete — status bar, coach/player picker, ESC compact"
```
