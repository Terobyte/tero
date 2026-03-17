# Batch Execution & Progress Visibility — Design Spec

**Date:** 2026-03-16
**Status:** Approved

---

## Problem

The current G3 coach-player loop executes one step per model call. A 136-step plan = 136
separate model invocations — slow (~4 hours), expensive, and invisible to the user.

Two problems:
1. **Cost & speed** — every step is a full round-trip
2. **No visibility** — user sees nothing during execution

---

## Solution Overview

**Approach A: Phase-Aware PlanTracker**

Extend `PlanTracker` with a `Phase` concept. A `BatchExecutor` groups steps into phases
automatically by step type, executes each phase in one Player turn, then runs Coach review
once per phase. Rich live dashboard integrated into `PlanTracker`.

Result: ~136 turns → ~10–15 turns, Rich progress dashboard, Coach review preserved.

---

## Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| When does Coach run? | After each phase | Preserves quality, reduces turns dramatically |
| Incomplete phase handling | Retry with context | Player gets list of done/remaining steps |
| Phase definition | Auto-grouped by step type | No manual YAML needed |
| Integration point | Extend PlanTracker | Minimal changes to existing code |

---

## Section 1: Data Model

### Step Type Detection

```python
# plan_tracker.py

STEP_TYPE_MAP = {
    "create": ["create", "add", "write", "generate", "implement"],
    "update": ["update", "modify", "change", "extend", "refactor"],
    "test":   ["test", "tests", "verify", "validate", "check"],
    "review": ["review", "analyze", "audit", "fix issue"],
}

# Max steps per phase by type
PHASE_SIZE = {"create": 6, "update": 4, "test": 3, "review": 1}
```

### Phase Dataclass

```python
@dataclass
class Phase:
    name: str         # e.g. "Create Providers"
    type: str         # "create" | "update" | "test" | "review"
    steps: list[Step] # existing Step objects from plan_tracker
    status: str = "pending"   # pending | in_progress | done | failed
    attempts: int = 0
```

### Auto-Grouping

```python
def auto_group_phases(steps: list[Step]) -> list[Phase]:
    """Group steps into phases by detected type, respecting PHASE_SIZE limits."""
    # 1. Detect type for each step (keyword match in step text)
    # 2. Group consecutive steps of the same type
    # 3. Split groups that exceed PHASE_SIZE[type]
    # 4. Name each phase: "{Type} {files/context}"
```

### PlanTracker Extension

`PlanTracker` gets two new fields and methods:

```python
class PlanTracker:
    # new fields
    phases: list[Phase] = field(default_factory=list)

    # new methods
    def phase_done(self, phase: Phase) -> None:
        """Mark all steps in phase as done and update progress."""

    def start_dashboard(self) -> None: ...
    def render_dashboard(self) -> None: ...
    def stop_dashboard(self) -> None: ...
```

---

## Section 2: BatchExecutor

**File:** `g3/src/batch_executor.py`

```python
class BatchExecutor:
    def __init__(self, session: CoachPlayerSession, tracker: PlanTracker):
        self.session = session
        self.tracker = tracker

    async def run(self):
        phases = auto_group_phases(self.tracker.steps)
        self.tracker.phases = phases
        self.tracker.start_dashboard()

        try:
            for phase in phases:
                phase.status = "in_progress"
                self.tracker.render_dashboard()

                success = await self._run_phase(phase)

                if success:
                    phase.status = "done"
                    self.tracker.phase_done(phase)
                else:
                    phase.status = "failed"
                    raise PhaseFailedError(phase)
        finally:
            self.tracker.stop_dashboard()

    async def _run_phase(self, phase: Phase) -> bool:
        """Execute all steps in one Player turn. Retry if incomplete."""
        MAX_ATTEMPTS = 3
        completed_steps: list[str] = []

        for attempt in range(MAX_ATTEMPTS):
            phase.attempts = attempt + 1
            self.tracker.render_dashboard()

            prompt = build_batch_prompt(phase, completed_steps)
            result = await self.session._run_player_turn(prompt)
            completed_steps = parse_completed_steps(result, phase)

            if len(completed_steps) == len(phase.steps):
                # All steps done — run Coach review
                verdict = await self.session._run_coach_turn(
                    phase=phase,
                    completed_steps=completed_steps,
                )
                if isinstance(verdict, Approved):
                    return True
                # Coach found issues — reset and retry whole phase
                completed_steps = []
            # else: incomplete — retry with context of what was done

        return False
```

### Batch Prompt Builder

```python
def build_batch_prompt(phase: Phase, completed_steps: list[str]) -> str:
    """Build Player prompt for a full phase."""
    remaining = [s for s in phase.steps if s.name not in completed_steps]
    done_text = ""
    if completed_steps:
        done_text = "Already done:\n" + "\n".join(f"  ✅ {s}" for s in completed_steps) + "\n\n"

    steps_text = "\n".join(f"  {i+1}. {s.name}" for i, s in enumerate(remaining))

    return f"""{done_text}Execute ALL of the following steps in sequence.
Complete ALL steps before returning.

Phase: {phase.name}
Steps:
{steps_text}

After completing each step, confirm:
  Step X done: [brief description]

When ALL steps are complete, return:
  PHASE_COMPLETE: {phase.name}
"""
```

### Completed Steps Parser

```python
def parse_completed_steps(result: TurnResult, phase: Phase) -> list[str]:
    """Extract which steps were confirmed done from Player output."""
    # Look for "Step X done:" lines in output
    # Match against phase.steps by index or name
    # Return list of completed step names
```

---

## Section 3: Progress Dashboard

Integrated into `PlanTracker` using Rich:

```python
from rich.live import Live
from rich.table import Table

class PlanTracker:
    _live: Live | None = None

    def start_dashboard(self):
        self._live = Live(self._build_table(), refresh_per_second=4)
        self._live.__enter__()

    def render_dashboard(self):
        if self._live:
            self._live.update(self._build_table())

    def stop_dashboard(self):
        if self._live:
            self._live.__exit__(None, None, None)

    def _build_table(self) -> Table:
        table = Table(title="G3 Execution", show_header=False, box=None)
        for i, phase in enumerate(self.phases):
            done = sum(1 for s in phase.steps if s.done)
            total = len(phase.steps)
            pct = done * 100 // total if total else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            icon = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}[phase.status]
            attempts = f" (attempt {phase.attempts})" if phase.attempts > 1 else ""
            table.add_row(
                f"{icon} Phase {i+1}: {phase.name}{attempts}",
                f"{bar} {pct}%",
            )
        total_steps = sum(len(p.steps) for p in self.phases)
        done_steps = sum(s.done for p in self.phases for s in p.steps)
        table.add_row("", f"Steps: {done_steps}/{total_steps}")
        return table
```

**Example output:**
```
          G3 Execution
✅ Phase 1: Create Providers          ████████████████████ 100%
🔄 Phase 2: Update Config (attempt 2) ██████████░░░░░░░░░░  50%
⏳ Phase 3: Tests                     ░░░░░░░░░░░░░░░░░░░░   0%
⏳ Phase 4: Code Review               ░░░░░░░░░░░░░░░░░░░░   0%
                                      Steps: 8/21
```

---

## Section 4: CLI & Config

### Config

```python
# config.py
@dataclass
class Config:
    # ... existing fields ...
    batch_mode: bool = False
```

**CLI:** `--batch` flag
**Env:** `G3_BATCH_MODE=true`

### g3.py Entry Point

```python
if config.batch_mode:
    executor = BatchExecutor(session, tracker)
    await executor.run()
else:
    await session.run()  # existing path, unchanged
```

### Menu Toggle

```
─── режимы ──────────────────────────────
    TDD Mode:       выкл
    Code Review:    выкл
    Batch Mode:     выкл
```

---

## File Changes

### New files
```
g3/src/batch_executor.py     — BatchExecutor, build_batch_prompt, parse_completed_steps
g3/tests/test_batch_executor.py
```

### Modified files
```
g3/src/plan_tracker.py       — Phase dataclass, auto_group_phases, dashboard methods
g3/src/config.py             — batch_mode field
g3/src/menu.py               — Batch Mode toggle
g3/g3.py                     — --batch CLI flag, entry point routing
```

---

## Metrics: Before vs After

| Metric | Before | After |
|---|---|---|
| Turns for 136 steps | 136 | ~10–15 |
| Time | ~4 hours | ~30 min |
| Cost | $$$$ | $$ |
| Progress visibility | none | Rich live dashboard |
| Coach review | every step | every phase |
| Resume support | no | yes (phase granularity) |

---

## Implementation Order

1. `Phase` dataclass + `auto_group_phases()` in `plan_tracker.py`
2. Dashboard methods in `PlanTracker`
3. `BatchExecutor` + `build_batch_prompt()` + `parse_completed_steps()`
4. `Config.batch_mode` + CLI flag + menu toggle
5. Entry point routing in `g3.py`
6. Tests
