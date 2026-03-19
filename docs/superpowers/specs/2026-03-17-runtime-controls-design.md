# Runtime Controls Design
**Date:** 2026-03-17
**Scope:** Two features — (1) fix batch auto-reject without coach, (2) runtime coach/player switching with persistent status bar

---

## Feature 1: Fix Auto-Reject Without Coach

### Problem

In `batch_executor._run_phase()`, lines 361–369, when the player finishes without writing `PHASE_COMPLETE` or the required report headers, the system auto-rejects and skips the coach via two `continue` statements:

```python
if len(completed_steps) < len(phase.steps):
    coach_feedback = build_incomplete_phase_feedback(...)
    streaming_ui.print_step_rejected(coach_feedback)
    continue  # ← coach never called

if not has_required_completion_report(result.text):
    coach_feedback = build_missing_report_feedback(phase)
    streaming_ui.print_step_rejected(coach_feedback)
    continue  # ← coach never called
```

The player may have done the actual work but forgotten to write the markers. The coach should be the judge.

### Fix

Remove both `if ... continue` blocks. Control falls through to the existing `_review_strategy()` + `_run_coach_turn_for_phase()` call at line 380.

`_run_coach_turn_for_phase()` already accepts `completed_steps` including empty/partial lists. `build_phase_coach_prompt()` (called inside) already handles incomplete phases — when steps are missing it instructs the coach: "The Player did NOT finish all steps — reject immediately with numbered missing-step feedback." The coach inspects the filesystem and provides specific, grounded feedback rather than mechanical template text.

**Important:** do NOT also remove the `parse_completed_steps()` call — the `completed_steps` list must still be computed and passed to `_run_coach_turn_for_phase()` so the coach knows which steps were marked done.

### Changes

**`src/batch_executor.py` — `_run_phase()`:**
- Remove the `if len(completed_steps) < len(phase.steps): ... continue` block (~4 lines)
- Remove the `if not has_required_completion_report(...): ... continue` block (~4 lines)
- Keep `parse_completed_steps()` call; pass result to `_run_coach_turn_for_phase()`

---

## Feature 2: Runtime Controls

### Overview

A persistent status bar at the bottom of the terminal and an interactive model picker at the top. The user can switch coach and player providers mid-session with keyboard shortcuts. ESC triggers context compaction before the next turn.

Environment: always a real Unix terminal (macOS). `tty`/`termios` always available.

### UX Behavior

**Status bar (always visible at bottom):**
```
 Player: GLM-1 [A/D]   Coach: Sonnet [←/→]   Ctx: 81% [ESC=compact]
```

**Picker (appears at top on keypress, dismissed automatically after Enter):**
```
 Coach: ◄ GLM-1 | GLM-2 | [Sonnet] | Opus | GPT-5.4 Med | GPT-5.4 High | GPT-5.4 Ultra ►
```

**Key bindings:**
| Key | Action |
|-----|--------|
| `←` / `→` | Open coach picker / cycle selection |
| `A` / `D` | Open player picker / cycle selection |
| While coach picker open: `A`/`D` | Switch picker to player mode |
| While player picker open: `←`/`→` | Switch picker to coach mode |
| `Enter` | Confirm selection; picker stays 1.5s as confirmation indicator, then auto-dismisses |
| Same role key again after picker dismissed | Reopens picker at current selection |
| `ESC` (standalone) | Request compact for the next upcoming turn |

**Picker state machine:**

```
CLOSED
  ──[←/→]──► OPEN(role=coach, idx=current_coach)
  ──[A/D]──► OPEN(role=player, idx=current_player)

OPEN(role=R, idx=I)
  ──[←/→ and R=coach]──► OPEN(role=coach, idx=cycle(I, dir))
  ──[A/D and R=player]──► OPEN(role=player, idx=cycle(I, dir))
  ──[←/→ and R=player]──► OPEN(role=coach, idx=current_coach)   ← switch role
  ──[A/D and R=coach]──► OPEN(role=player, idx=current_player)   ← switch role
  ──[Enter]──► CONFIRMING(role=R, selected=PRESETS[I])
  ──[ESC]──► CLOSED  (ESC closes picker WITHOUT triggering compact)

CONFIRMING(role=R, selected=P)   ← picker visible 1.5s as confirmation
  ──[1.5s elapsed]──► CLOSED
  ──[any key]──► process key in CLOSED state (new keypress cancels dismiss timer)
```

**ESC behavior:** ESC closes an open picker without action. ESC when picker is CLOSED requests compact.

**Timing:** Coach/player changes apply between turns (never interrupt a running turn). The compact flag is checked and cleared before the start of the NEXT turn.

### Model Presets

```python
MODEL_PRESETS = [
    ("GLM-1",        "ccg",    "blackboxai/z-ai/glm-5"),
    ("GLM-2",        "ccg2",   "blackboxai/z-ai/glm-5"),
    ("Sonnet",       "claude", "claude-sonnet-4-6"),
    ("Opus",         "claude", "claude-opus-4-6"),
    ("GPT-5.4 Med",  "codex",  "gpt-5.4-medium"),
    ("GPT-5.4 High", "codex",  "gpt-5.4-high"),
    ("GPT-5.4 Ultra","codex",  "gpt-5.4-ultra-high"),
]
```

### Architecture

#### New file: `src/runtime_controls.py`

**`KeyboardListener`** (daemon thread)

Uses `tty.setcbreak(fd)` for single-byte reads; Ctrl+C still works. Reads escape sequences character by character:
- `\x1b` received → start 100ms window from first byte arrival via `select.select(stdin, [], [], 0.1)`
  - If no more bytes arrive within 100ms → ESC key (standalone)
  - If `[` arrives → read one more byte: `C`=right arrow, `D`=left arrow
- `a`/`A` → player-left; `d`/`D` → player-right
- `\r` or `\n` → confirm/Enter

Thread-safe action queue (not single-slot, so fast keypresses aren't lost).

Crash safety: the listener thread wraps its body in `try/finally: termios.tcsetattr(fd, TCSADRAIN, old_settings)` to guarantee terminal mode restoration even if an exception occurs inside the thread.

```python
class KeyboardListener(threading.Thread):
    def run(self):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not self._stop_event.is_set():
                # ... read and queue actions
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
```

**`StatusBar`**

Renders at terminal bottom line using ANSI cursor save/restore:
```python
def render(self):
    h = os.get_terminal_size().lines
    line = f" Player: {self.player_name} [A/D]   Coach: {self.coach_name} [←/→]   Ctx: {self.ctx_pct}% [ESC=compact]"
    sys.stdout.write(f"\0337\033[{h};1H\033[K{line}\0338")
    sys.stdout.flush()
```

API:
- `update(player_name, coach_name, ctx_pct)` — updates fields and re-renders immediately (thread-safe)
- `show_warning(text: str, duration_s: float = 3.0)` — temporarily replaces the status line with `text` for `duration_s` seconds, then reverts to normal status. Uses `threading.Timer` to schedule revert. If another `show_warning` is called while timer is active, the previous timer is cancelled.
- `clear()` — clears the bottom line and cancels any active warning timer so no delayed redraw fires after shutdown.

Handles `SIGWINCH` (terminal resize) by re-rendering at new height.

**`Picker`**

Renders at terminal row 1. Manages the state machine above. `confirm()` marks the pending change and schedules auto-dismiss via `threading.Timer(1.5, dismiss)`. Any keypress during CONFIRMING cancels the timer and immediately processes the new key in CLOSED state.

**`RuntimeControls`** (main class, used by CoachPlayerSession)

```python
class RuntimeControls:
    def start(self) -> None          # start listener thread, render initial status bar
    def stop(self) -> None           # stop listener, restore terminal, clear status bar
    def update_context(self, tokens: int, context_window: int) -> None
    def apply_pending(self, session) -> None   # apply queued coach/player changes
    @property
    def compact_requested(self) -> bool
    def clear_compact(self) -> None
```

**`apply_pending(session)` logic:**
```python
pending = self._picker.pop_pending_changes()  # returns list of (role, provider_name, model)
for role, provider_name, model in pending:
    if role == "coach":
        ok, reason = session._get_or_create_provider(provider_name).check_ready()
        if not ok:
            self._status_bar.show_warning(f"Coach {provider_name} not ready: {reason}")
            continue
        session.config.coach_provider = provider_name
        session.config.coach_model = model
        session.coach_provider = session._get_or_create_provider(provider_name)
        session.coach_model = session._build_role_display("coach")
    elif role == "player":
        # same pattern
    self._status_bar.update(session.player_model, session.coach_model, self._ctx_pct)
```

#### Changes to `src/coach_player.py`

**`__init__`:** add `self.runtime_controls: RuntimeControls | None = None`

**`run()`:**
- After the all-done / resume checks, create and start `self.runtime_controls`
- Wrap the interactive run body in `try/finally: self.runtime_controls.stop()`
- Call `self.runtime_controls.apply_pending(self)` at each turn boundary, immediately before player turns and immediately before coach turns
- Before each player turn (start of attempt loop): check `self.runtime_controls.compact_requested` → if True, apply compact and call `self.runtime_controls.clear_compact()`

**Context tracking — `_run_turn()`:** after computing `tokens_used` and `context_window`, call:
```python
if self.runtime_controls is not None and self.runtime_controls.is_running:
    self.runtime_controls.update_context(tokens_used, context_window)
```
`_run_turn()` already has access to `self` so no parameter threading needed.

**`last_player_result` tracking:** in the outer step loop, maintain `self._last_turn_result: TurnResult | None = None`. After each `_run_turn()` (for player turns), assign `self._last_turn_result = player_result`. Used by compact flow.

**ESC compact flow:**
```python
# At top of attempt loop, before the build_player_step_prompt() call:
compact_prompt_override: str | None = None
if self.runtime_controls.compact_requested:
    self.runtime_controls.clear_compact()
    if self._last_turn_result is not None:
        from src.context_manager import _build_compact_summary
        summary = _build_compact_summary(self._last_turn_result.messages)
        # Step-mode continuation prompt (NOT batch PHASE_COMPLETE format):
        compact_prompt_override = (
            f"Context compacted. Summary of previous work:\n{summary}\n\n"
            f"Continue implementing the current step: {step.text}\n"
            f"When done, include:\nWhat changed: ...\nEvidence: ...\nVerification: ..."
        )

# Later, build the actual prompt:
player_prompt = compact_prompt_override or build_player_step_prompt(
    current_step=step.text, ...
)
```

`_build_continuation_prompt` from `context_manager.py` is NOT used here — it produces batch-mode `PHASE_COMPLETE` markers which are wrong for step-by-step sessions. The compact prompt is inlined above.

**`self._last_turn_result` initialization:** initialized to `None` once before the outer `for step_index in range(...)` loop. Updated to `player_result` after each successful player turn inside the attempt loop. Reset to `None` at the start of each new step (top of the for-loop body, before the attempt loop).

**Batch-mode isolation:** batch execution still reuses `session._run_turn()`, so runtime controls must stay disabled unless the interactive `run()` path explicitly created and started them.

#### Changes to `src/streaming.py`

`print_turn_timing()` — no signature change needed. The context update happens inside `_run_turn()` via `self.runtime_controls` directly.

### Failure Modes

| Situation | Behavior |
|-----------|----------|
| Provider not ready after switch | `apply_pending` calls `check_ready()`, skips change, shows warning in status bar |
| ESC pressed when `_last_turn_result` is None | compact flag cleared, no action, no crash |
| SIGWINCH terminal resize | StatusBar re-renders at new terminal height |
| `KeyboardListener` internal exception | `finally: tcsetattr` restores terminal mode; exception logged; listener stops but session continues |

---

## Files Changed

| File | Change |
|------|--------|
| `src/runtime_controls.py` | **New** — KeyboardListener, StatusBar, Picker, RuntimeControls |
| `src/batch_executor.py` | Remove 2 auto-reject `continue` blocks (~8 lines) |
| `src/coach_player.py` | Integrate RuntimeControls: start/stop, apply_pending at step loop top, compact check, `_last_turn_result` tracking |

---

## Implementation Order

1. **`batch_executor.py` fix** — small, standalone, immediate value
2. **`KeyboardListener`** — core input primitive with full escape sequence parsing and crash-safe `finally`
3. **`StatusBar`** — ANSI rendering with SIGWINCH handling
4. **`Picker`** — state machine with full transitions, auto-dismiss timer
5. **`RuntimeControls`** — orchestrates above, exposes `apply_pending` / `compact_requested`
6. **`coach_player.py` integration** — start/stop, apply_pending, compact flow, `_last_turn_result`
7. **Tests** — mock terminal fd, verify KeyboardListener action queue; verify Picker state transitions; verify apply_pending updates session fields correctly
