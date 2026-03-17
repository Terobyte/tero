# Batch Execution Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce G3 plan execution from 136 model calls to ~10-15 by grouping steps into phases, with a Rich live progress dashboard.

**Architecture:** A new `PlanTracker` class wraps `plan_tracker.py` free functions and owns a Rich live dashboard. A `BatchExecutor` auto-groups `PlanItem` steps into phases by keyword type, runs each phase in one Player turn, then calls Coach once per phase.

**Tech Stack:** Python 3.11+, pytest, rich (Live/Table), existing `AdaptedMessage` types from `message_adapter.py`

**Spec:** `docs/superpowers/specs/2026-03-16-batch-execution-design.md`

**All commands run from:** `g3/` directory

---

## Chunk 1: Data Model (plan_tracker.py)

### Task 1: Phase dataclass + type constants

**Files:**
- Modify: `g3/src/plan_tracker.py` — add constants and Phase at top
- Test: `g3/tests/test_batch_executor.py` — create file, add Phase tests

- [ ] **Step 1: Create test file and write Phase tests**

```python
# g3/tests/test_batch_executor.py
"""Tests for batch execution components."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.plan_tracker import PlanItem, Phase, STEP_TYPE_MAP, PHASE_SIZE, DEFAULT_STEP_TYPE


class TestPhaseDataclass:
    def test_phase_default_status(self):
        items = [PlanItem(text="create x.py")]
        phase = Phase(name="Create", type="create", steps=items)
        assert phase.status == "pending"
        assert phase.attempts == 0

    def test_phase_fields(self):
        items = [PlanItem(text="update config"), PlanItem(text="update menu")]
        phase = Phase(name="Update", type="update", steps=items)
        assert phase.name == "Update"
        assert phase.type == "update"
        assert len(phase.steps) == 2

    def test_step_type_map_has_required_keys(self):
        assert set(STEP_TYPE_MAP.keys()) == {"create", "update", "test", "review"}

    def test_phase_size_covers_all_types(self):
        assert set(PHASE_SIZE.keys()) == set(STEP_TYPE_MAP.keys())

    def test_default_step_type_is_update(self):
        assert DEFAULT_STEP_TYPE == "update"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
pytest tests/test_batch_executor.py::TestPhaseDataclass -v
```
Expected: `ERROR` — `Phase` cannot be imported yet.

- [ ] **Step 3: Add constants and Phase to plan_tracker.py**

Add after the existing `PlanItem` dataclass (after line 11):

```python
# --- Batch execution types ---

STEP_TYPE_MAP: dict[str, list[str]] = {
    "create": ["create", "add", "write", "generate", "implement"],
    "update": ["update", "modify", "change", "extend", "refactor"],
    "test":   ["test", "tests", "verify", "validate", "check"],
    "review": ["review", "analyze", "audit", "fix issue"],
}

PHASE_SIZE: dict[str, int] = {"create": 6, "update": 4, "test": 3, "review": 1}

DEFAULT_STEP_TYPE = "update"


@dataclass
class Phase:
    """A batch of PlanItems grouped by step type."""
    name: str
    type: str            # "create" | "update" | "test" | "review"
    steps: list["PlanItem"]
    status: str = "pending"  # pending | in_progress | done | failed
    attempts: int = 0
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_batch_executor.py::TestPhaseDataclass -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/plan_tracker.py tests/test_batch_executor.py
git commit -m "feat: add Phase dataclass and type constants to plan_tracker"
```

---

### Task 2: detect_step_type

**Files:**
- Modify: `g3/src/plan_tracker.py` — add `detect_step_type`
- Test: `g3/tests/test_batch_executor.py` — add `TestDetectStepType`

- [ ] **Step 1: Write failing tests**

Add to `test_batch_executor.py`:

```python
from src.plan_tracker import detect_step_type


class TestDetectStepType:
    def test_create_keyword(self):
        assert detect_step_type(PlanItem(text="Create providers/base.py")) == "create"

    def test_update_keyword(self):
        assert detect_step_type(PlanItem(text="Update config.py with new fields")) == "update"

    def test_test_keyword(self):
        assert detect_step_type(PlanItem(text="Write tests for provider")) == "test"

    def test_review_keyword(self):
        assert detect_step_type(PlanItem(text="Review all changes")) == "review"

    def test_case_insensitive(self):
        assert detect_step_type(PlanItem(text="CREATE the module")) == "create"

    def test_unknown_keyword_returns_default(self):
        assert detect_step_type(PlanItem(text="Do something completely unknown")) == DEFAULT_STEP_TYPE

    def test_first_match_wins(self):
        # "implement" is in create; "refactor" is in update — "implement" comes first
        assert detect_step_type(PlanItem(text="implement and refactor")) == "create"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestDetectStepType -v
```
Expected: `ImportError` for `detect_step_type`.

- [ ] **Step 3: Implement in plan_tracker.py**

Add after the `Phase` dataclass:

```python
def detect_step_type(item: "PlanItem") -> str:
    """Return step type by keyword match. Case-insensitive. First match wins."""
    text = item.text.lower()
    for step_type, keywords in STEP_TYPE_MAP.items():
        if any(kw in text for kw in keywords):
            return step_type
    return DEFAULT_STEP_TYPE
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_batch_executor.py::TestDetectStepType -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/plan_tracker.py tests/test_batch_executor.py
git commit -m "feat: add detect_step_type to plan_tracker"
```

---

### Task 3: auto_group_phases

**Files:**
- Modify: `g3/src/plan_tracker.py` — add `_make_phase`, `auto_group_phases`
- Test: `g3/tests/test_batch_executor.py` — add `TestAutoGroupPhases`

- [ ] **Step 1: Write failing tests**

Add to `test_batch_executor.py`:

```python
from src.plan_tracker import auto_group_phases


class TestAutoGroupPhases:
    def test_empty_list_returns_empty(self):
        assert auto_group_phases([]) == []

    def test_single_item_creates_one_phase(self):
        items = [PlanItem(text="create x.py")]
        phases = auto_group_phases(items)
        assert len(phases) == 1
        assert phases[0].type == "create"

    def test_groups_by_type(self):
        items = [
            PlanItem(text="create a.py"),
            PlanItem(text="create b.py"),
            PlanItem(text="update config"),
            PlanItem(text="update menu"),
        ]
        phases = auto_group_phases(items)
        assert len(phases) == 2
        assert phases[0].type == "create"
        assert phases[1].type == "update"

    def test_splits_at_phase_size_limit(self):
        # PHASE_SIZE["create"] == 6 — 7 create items → 2 phases
        items = [PlanItem(text=f"create file{i}.py") for i in range(7)]
        phases = auto_group_phases(items)
        assert len(phases) == 2
        assert len(phases[0].steps) == 6
        assert len(phases[1].steps) == 1

    def test_unknown_type_uses_default(self):
        items = [PlanItem(text="do something unknown")]
        phases = auto_group_phases(items)
        assert phases[0].type == DEFAULT_STEP_TYPE

    def test_type_change_starts_new_phase(self):
        items = [
            PlanItem(text="create a.py"),
            PlanItem(text="update b.py"),  # type change
            PlanItem(text="update c.py"),
        ]
        phases = auto_group_phases(items)
        assert len(phases) == 2

    def test_phase_name_contains_type_and_count(self):
        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phases = auto_group_phases(items)
        assert "create" in phases[0].name.lower()
        assert "2" in phases[0].name
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestAutoGroupPhases -v
```
Expected: `ImportError` for `auto_group_phases`.

- [ ] **Step 3: Implement in plan_tracker.py**

Add after `detect_step_type`:

```python
def _make_phase(ptype: str, items: list["PlanItem"]) -> "Phase":
    name = f"{ptype.capitalize()} ({len(items)} steps)"
    return Phase(name=name, type=ptype, steps=items)


def auto_group_phases(items: list["PlanItem"]) -> list["Phase"]:
    """Group PlanItems into phases by type, splitting at PHASE_SIZE[type] boundary."""
    if not items:
        return []

    phases: list[Phase] = []
    current_type = detect_step_type(items[0])
    current_batch: list[PlanItem] = []

    for item in items:
        itype = detect_step_type(item)
        if itype != current_type or len(current_batch) >= PHASE_SIZE[current_type]:
            if current_batch:
                phases.append(_make_phase(current_type, current_batch))
            current_type = itype
            current_batch = []
        current_batch.append(item)

    if current_batch:
        phases.append(_make_phase(current_type, current_batch))

    return phases
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_batch_executor.py::TestAutoGroupPhases -v
```
Expected: 7 passed.

- [ ] **Step 5: Run full test suite to check no regressions**

```bash
pytest tests/ -q
```
Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/plan_tracker.py tests/test_batch_executor.py
git commit -m "feat: add auto_group_phases to plan_tracker"
```

---

### Task 4: PlanTracker class

**Files:**
- Modify: `g3/src/plan_tracker.py` — add `PlanTracker` class at bottom
- Test: `g3/tests/test_batch_executor.py` — add `TestPlanTracker`

- [ ] **Step 1: Write failing tests**

Add to `test_batch_executor.py`:

```python
from src.plan_tracker import PlanTracker


class TestPlanTracker:
    def test_init_stores_items(self):
        items = [PlanItem(text="create a.py"), PlanItem(text="update b.py")]
        tracker = PlanTracker(items)
        assert tracker.items == items
        assert tracker.phases == []

    def test_phase_done_marks_all_steps(self):
        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        tracker = PlanTracker(items)
        phase = Phase(name="Create", type="create", steps=items, status="done")
        tracker.phases = [phase]
        tracker.phase_done(phase)
        assert all(s.done for s in phase.steps)

    def test_phase_done_does_not_require_live(self):
        """phase_done should work even when dashboard is not started."""
        items = [PlanItem(text="create a.py")]
        tracker = PlanTracker(items)
        phase = Phase(name="Create", type="create", steps=items)
        tracker.phase_done(phase)  # should not raise

    def test_render_dashboard_noop_when_not_started(self):
        """render_dashboard should not raise when _live is None."""
        tracker = PlanTracker([])
        tracker.render_dashboard()  # should not raise

    def test_stop_dashboard_noop_when_not_started(self):
        """stop_dashboard should not raise when _live is None."""
        tracker = PlanTracker([])
        tracker.stop_dashboard()  # should not raise
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestPlanTracker -v
```
Expected: `ImportError` for `PlanTracker`.

- [ ] **Step 3: Add PlanTracker class to plan_tracker.py**

Add at the end of the file:

```python
class PlanTracker:
    """Tracks phase/step progress and owns the Rich live dashboard."""

    def __init__(self, items: list[PlanItem]):
        self.items = items
        self.phases: list[Phase] = []
        self._live = None  # Rich Live instance

    def phase_done(self, phase: Phase) -> None:
        """Mark all PlanItems in phase as done and re-render dashboard."""
        for item in phase.steps:
            item.done = True
        self.render_dashboard()

    def start_dashboard(self) -> None:
        """Start Rich Live display. Call once before execution loop."""
        from rich.live import Live
        self._live = Live(self._build_table(), refresh_per_second=4)
        self._live.__enter__()

    def render_dashboard(self) -> None:
        """Update live display. Idempotent — safe to call extra times."""
        if self._live is not None:
            self._live.update(self._build_table())

    def stop_dashboard(self) -> None:
        """Stop Rich Live display. Always called in finally block."""
        if self._live is not None:
            self._live.__exit__(None, None, None)
            self._live = None

    def _build_table(self):
        """Build Rich Table showing phase progress."""
        from rich.table import Table
        table = Table(title="G3 Execution", show_header=False, box=None)
        for i, phase in enumerate(self.phases):
            done = sum(1 for s in phase.steps if s.done)
            total = len(phase.steps)
            pct = done * 100 // total if total else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            icon = {
                "pending": "⏳", "in_progress": "🔄",
                "done": "✅", "failed": "❌",
            }.get(phase.status, "❓")
            attempts_str = f" (attempt {phase.attempts})" if phase.attempts > 1 else ""
            table.add_row(
                f"{icon} Phase {i + 1}: {phase.name}{attempts_str}",
                f"{bar} {pct}%",
            )
        total_steps = sum(len(p.steps) for p in self.phases)
        done_steps = sum(s.done for p in self.phases for s in p.steps)
        table.add_row("", f"Steps: {done_steps}/{total_steps}")
        return table
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_batch_executor.py::TestPlanTracker -v
```
Expected: 5 passed.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/plan_tracker.py tests/test_batch_executor.py
git commit -m "feat: add PlanTracker class with dashboard to plan_tracker"
```

---

## Chunk 2: BatchExecutor Core

### Task 5: build_batch_prompt + parse_completed_steps

**Files:**
- Create: `g3/src/batch_executor.py`
- Test: `g3/tests/test_batch_executor.py` — add `TestBuildBatchPrompt`, `TestParseCompletedSteps`

- [ ] **Step 1: Write failing tests**

Add to `test_batch_executor.py`:

```python
from src.batch_executor import build_batch_prompt, parse_completed_steps


class TestBuildBatchPrompt:
    def _phase(self, texts):
        items = [PlanItem(text=t) for t in texts]
        return Phase(name="Test Phase", type="create", steps=items)

    def test_first_attempt_no_extra_sections(self):
        phase = self._phase(["create a.py", "create b.py"])
        prompt = build_batch_prompt(phase, [], "")
        assert "REJECTED" not in prompt
        assert "Already completed" not in prompt
        assert "create a.py" in prompt
        assert "create b.py" in prompt
        assert "PHASE_COMPLETE" in prompt

    def test_with_done_steps_shows_already_completed(self):
        phase = self._phase(["create a.py", "create b.py"])
        prompt = build_batch_prompt(phase, ["create a.py"], "")
        assert "Already completed" in prompt
        assert "create a.py" in prompt
        # b.py should still be in steps list (remaining)
        assert "create b.py" in prompt

    def test_with_coach_feedback_shows_rejection(self):
        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, [], "Missing error handling")
        assert "REJECTED BY COACH" in prompt
        assert "Missing error handling" in prompt

    def test_step_confirmation_instructions_present(self):
        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, [], "")
        assert "Step 1 done:" in prompt

    def test_phase_complete_marker_present(self):
        phase = self._phase(["create a.py"])
        prompt = build_batch_prompt(phase, [], "")
        assert f"PHASE_COMPLETE: {phase.name}" in prompt


class TestParseCompletedSteps:
    def _result(self, text):
        r = MagicMock()
        r.text = text
        return r

    def _phase(self, texts):
        items = [PlanItem(text=t) for t in texts]
        return Phase(name="P", type="create", steps=items)

    def test_phase_complete_returns_all(self):
        phase = self._phase(["create a.py", "create b.py"])
        result = self._result("I did stuff\nPHASE_COMPLETE: P")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create a.py", "create b.py"]

    def test_step_done_by_index(self):
        phase = self._phase(["create a.py", "create b.py", "create c.py"])
        result = self._result("Step 1 done: created it")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create a.py"]

    def test_partial_completion(self):
        phase = self._phase(["create a.py", "create b.py", "create c.py"])
        result = self._result("Step 1 done: x\nStep 3 done: y")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create a.py", "create c.py"]

    def test_empty_output_returns_empty(self):
        phase = self._phase(["create a.py"])
        result = self._result("I tried but got confused")
        completed = parse_completed_steps(result, phase)
        assert completed == []

    def test_out_of_range_index_ignored(self):
        phase = self._phase(["create a.py"])
        result = self._result("Step 99 done: whatever")
        completed = parse_completed_steps(result, phase)
        assert completed == []

    def test_case_insensitive_step_done(self):
        phase = self._phase(["create a.py"])
        result = self._result("STEP 1 DONE: created")
        completed = parse_completed_steps(result, phase)
        assert completed == ["create a.py"]

    def test_phase_complete_unconditional(self):
        """PHASE_COMPLETE overrides even if no Step X done lines."""
        phase = self._phase(["create a.py", "create b.py"])
        result = self._result("PHASE_COMPLETE: P\n(forgot to write step confirmations)")
        completed = parse_completed_steps(result, phase)
        assert len(completed) == 2
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestBuildBatchPrompt tests/test_batch_executor.py::TestParseCompletedSteps -v
```
Expected: `ImportError` — `batch_executor` doesn't exist yet.

- [ ] **Step 3: Create batch_executor.py with these two functions**

```python
# g3/src/batch_executor.py
"""Batch execution: groups PlanItems into phases, executes one Player turn per phase."""

import re
from dataclasses import dataclass

from src.plan_tracker import PlanItem, Phase


# --- Prompt / parser ---

def build_batch_prompt(
    phase: Phase,
    completed_steps: list[str],
    coach_feedback: str = "",
) -> str:
    """Build Player prompt for executing a phase.

    completed_steps: PlanItem.text values done in a previous attempt.
    coach_feedback:  Coach rejection message (empty on first try).
    Player must output 'Step X done: [desc]' per step and
    'PHASE_COMPLETE: <phase.name>' at the end.
    """
    remaining = [s for s in phase.steps if s.text not in completed_steps]
    sections: list[str] = []

    if coach_feedback:
        sections.append(
            f"PREVIOUS ATTEMPT REJECTED BY COACH:\n{coach_feedback}\n\n"
            "Fix the issues and redo all steps."
        )

    if completed_steps:
        done_list = "\n".join(f"  ✅ {s}" for s in completed_steps)
        sections.append(f"Already completed in this attempt:\n{done_list}")

    steps_list = "\n".join(f"  {i + 1}. {s.text}" for i, s in enumerate(remaining))
    sections.append(
        f"Execute ALL of the following steps in sequence.\n"
        f"Complete ALL steps before returning.\n\n"
        f"Phase: {phase.name}\n"
        f"Steps:\n{steps_list}"
    )
    sections.append(
        "After completing each step, output exactly:\n"
        "  Step X done: [one-line description]\n\n"
        f"When ALL steps are complete, output exactly:\n"
        f"  PHASE_COMPLETE: {phase.name}"
    )

    return "\n\n".join(sections)


_STEP_DONE_RE = re.compile(r"step\s+(\d+)\s+done\s*:", re.IGNORECASE)
_PHASE_COMPLETE_RE = re.compile(r"PHASE_COMPLETE\s*:", re.IGNORECASE)


def parse_completed_steps(result, phase: Phase) -> list[str]:
    """Extract confirmed-done step texts from Player output (result.text).

    PHASE_COMPLETE anywhere → return all step texts (unconditional).
    Otherwise scan for 'Step X done:' (1-based, case-insensitive).
    Out-of-range indices are ignored.
    """
    text = result.text

    if _PHASE_COMPLETE_RE.search(text):
        return [s.text for s in phase.steps]

    confirmed: set[int] = set()
    for match in _STEP_DONE_RE.finditer(text):
        idx = int(match.group(1)) - 1  # 1-based → 0-based
        if 0 <= idx < len(phase.steps):
            confirmed.add(idx)

    return [phase.steps[i].text for i in sorted(confirmed)]
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_batch_executor.py::TestBuildBatchPrompt tests/test_batch_executor.py::TestParseCompletedSteps -v
```
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/batch_executor.py tests/test_batch_executor.py
git commit -m "feat: add build_batch_prompt and parse_completed_steps"
```

---

### Task 6: PhaseFailedError

**Files:**
- Modify: `g3/src/batch_executor.py` — add `PhaseFailedError`
- Test: `g3/tests/test_batch_executor.py` — add `TestPhaseFailedError`

- [ ] **Step 1: Write failing test**

Add to `test_batch_executor.py`:

```python
from src.batch_executor import PhaseFailedError


class TestPhaseFailedError:
    def test_str_contains_phase_name(self):
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create (1 steps)", type="create", steps=items)
        err = PhaseFailedError(phase=phase, attempts=3)
        assert "Create (1 steps)" in str(err)

    def test_str_contains_attempt_count(self):
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        err = PhaseFailedError(phase=phase, attempts=3)
        assert "3" in str(err)

    def test_is_exception(self):
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        err = PhaseFailedError(phase=phase, attempts=1)
        assert isinstance(err, Exception)

    def test_args_populated(self):
        """e.args must be non-empty (needed for proper exception propagation)."""
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        err = PhaseFailedError(phase=phase, attempts=2)
        assert len(err.args) > 0
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestPhaseFailedError -v
```
Expected: `ImportError` for `PhaseFailedError`.

- [ ] **Step 3: Add to batch_executor.py**

Add after the imports at the top of `batch_executor.py`:

```python
@dataclass
class PhaseFailedError(Exception):
    """Raised when a phase exhausts all retry attempts."""
    phase: Phase
    attempts: int

    def __post_init__(self):
        # @dataclass does not call Exception.__init__ — do it manually
        # so that e.args is populated and str(e) works correctly.
        super().__init__(str(self))

    def __str__(self) -> str:
        completed = [s.text for s in self.phase.steps if s.done]
        return (
            f"Phase '{self.phase.name}' failed after {self.attempts} attempts. "
            f"Completed steps: {completed}"
        )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_batch_executor.py::TestPhaseFailedError -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/batch_executor.py tests/test_batch_executor.py
git commit -m "feat: add PhaseFailedError to batch_executor"
```

---

### Task 7: BatchExecutor._run_phase

**Files:**
- Modify: `g3/src/batch_executor.py` — add `BatchExecutor` class with `_run_phase`
- Test: `g3/tests/test_batch_executor.py` — add `TestRunPhase`

- [ ] **Step 1: Write failing tests**

Add to `test_batch_executor.py`:

```python
from src.batch_executor import BatchExecutor
from src.feedback import Approved, Feedback


class TestRunPhase:
    def _make_session(self, player_texts, coach_verdict=None):
        """Build a mock CoachPlayerSession."""
        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_model = ""

        # _run_turn returns TurnResult-like object with .text
        results = []
        for text in player_texts:
            r = MagicMock()
            r.text = text
            r.messages = []
            results.append(r)

        session._run_turn = AsyncMock(side_effect=results)
        session._run_coach_turn_for_phase = AsyncMock(
            return_value=coach_verdict or Approved()
        )
        return session

    def _make_executor(self, session):
        tracker = MagicMock()
        tracker.render_dashboard = MagicMock()
        return BatchExecutor(session=session, tracker=tracker)

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phase = Phase(name="Create", type="create", steps=items)
        # Player returns PHASE_COMPLETE on first try, Coach approves
        session = self._make_session(["PHASE_COMPLETE: Create"])
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is True
        assert phase.attempts == 1

    @pytest.mark.asyncio
    async def test_retry_on_incomplete_then_success(self):
        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phase = Phase(name="Create", type="create", steps=items)
        # Attempt 1: only step 1 done; attempt 2: PHASE_COMPLETE
        session = self._make_session(
            ["Step 1 done: created a.py", "PHASE_COMPLETE: Create"]
        )
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is True
        assert phase.attempts == 2

    @pytest.mark.asyncio
    async def test_retry_on_coach_rejection_then_success(self):
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        session = self._make_session(
            ["PHASE_COMPLETE: Create", "PHASE_COMPLETE: Create"]
        )
        # First coach call rejects, second approves
        session._run_coach_turn_for_phase = AsyncMock(
            side_effect=[Feedback(text="Missing tests"), Approved()]
        )
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is True
        assert phase.attempts == 2

    @pytest.mark.asyncio
    async def test_exhausts_attempts_returns_false(self):
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        # All 3 attempts return partial (no PHASE_COMPLETE)
        session = self._make_session(["no output", "no output", "no output"])
        executor = self._make_executor(session)
        result = await executor._run_phase(phase)
        assert result is False
        assert phase.attempts == 3
```

- [ ] **Step 2: Install pytest-asyncio if needed**

```bash
pip install pytest-asyncio 2>/dev/null || true
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestRunPhase -v
```
Expected: `ImportError` for `BatchExecutor`.

- [ ] **Step 4: Add BatchExecutor class to batch_executor.py**

Add at the end of `batch_executor.py`:

```python
class BatchExecutor:
    """Executes a plan in phases: one Player turn per phase, Coach review once per phase."""

    def __init__(self, session, tracker):
        self.session = session
        self.tracker = tracker

    async def run(self) -> None:
        """Execute all phases. Raises PhaseFailedError on unrecoverable failure."""
        import logging
        from src.plan_tracker import auto_group_phases

        phases = auto_group_phases(self.tracker.items)
        if not phases:
            logging.warning("BatchExecutor.run(): no phases generated, nothing to do")
            return

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
                    self.tracker.render_dashboard()
                    raise PhaseFailedError(phase=phase, attempts=phase.attempts)
        finally:
            self.tracker.stop_dashboard()

    async def _run_phase(self, phase: Phase) -> bool:
        """Execute all steps in one Player turn. Retry on incomplete or Coach rejection."""
        from src.prompts import PLAYER_SYSTEM_PROMPT

        MAX_ATTEMPTS = 3
        completed_steps: list[str] = []
        coach_feedback: str = ""

        for attempt in range(MAX_ATTEMPTS):
            phase.attempts = attempt + 1
            self.tracker.render_dashboard()

            prompt = build_batch_prompt(phase, completed_steps, coach_feedback)

            result = await self.session._run_turn(
                role="player",
                prompt=prompt,
                system_prompt=PLAYER_SYSTEM_PROMPT,
                max_turns=self.session.config.max_turns,
                timeout_s=self.session.config.player_timeout_s,
                model_override=self.session.config.player_model,
            )

            completed_steps = parse_completed_steps(result, phase)

            if len(completed_steps) == len(phase.steps):
                verdict = await self.session._run_coach_turn_for_phase(phase, result)
                from src.feedback import Approved
                if isinstance(verdict, Approved):
                    return True
                from src.feedback import Feedback
                coach_feedback = verdict.text if isinstance(verdict, Feedback) else str(verdict)
                completed_steps = []

        return False
```

- [ ] **Step 5: Add pytest-asyncio to project dependencies and config**

Install:
```bash
pip install pytest-asyncio
```

Add to `pyproject.toml` dependencies list:
```toml
dependencies = [
    "claude-agent-sdk>=0.1.0",
    "pyyaml>=6.0",
    "questionary>=2.0",
    "pytest-asyncio>=0.21",
    "rich>=13.0",
]
```

Add a new section at the end of `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 6: Run tests to verify pass**

```bash
pytest tests/test_batch_executor.py::TestRunPhase -v
```
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add src/batch_executor.py tests/test_batch_executor.py pyproject.toml
git commit -m "feat: add BatchExecutor with _run_phase"
```

---

### Task 8: BatchExecutor.run (integration test)

**Files:**
- Test: `g3/tests/test_batch_executor.py` — add `TestBatchExecutorRun`

- [ ] **Step 1: Write failing tests**

Add to `test_batch_executor.py`:

```python
class TestBatchExecutorRun:
    def _make_full_session(self, player_text="PHASE_COMPLETE: Create (1 steps)"):
        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_model = ""
        r = MagicMock(); r.text = player_text; r.messages = []
        session._run_turn = AsyncMock(return_value=r)
        session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())
        return session

    @pytest.mark.asyncio
    async def test_run_assigns_phases_to_tracker(self):
        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        session = self._make_full_session()
        executor = BatchExecutor(session=session, tracker=tracker)
        await executor.run()
        assert len(tracker.phases) == 1

    @pytest.mark.asyncio
    async def test_run_calls_start_and_stop_dashboard(self):
        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        session = self._make_full_session()
        executor = BatchExecutor(session=session, tracker=tracker)
        await executor.run()
        tracker.start_dashboard.assert_called_once()
        tracker.stop_dashboard.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_raises_phase_failed_error(self):
        items = [PlanItem(text="create a.py")]
        tracker = MagicMock()
        tracker.items = items
        # Player never outputs PHASE_COMPLETE → phase fails
        r = MagicMock(); r.text = "no output"; r.messages = []
        session = MagicMock()
        session.config = MagicMock()
        session.config.max_turns = 10
        session.config.player_timeout_s = 600
        session.config.player_model = ""
        session._run_turn = AsyncMock(return_value=r)
        executor = BatchExecutor(session=session, tracker=tracker)
        with pytest.raises(PhaseFailedError):
            await executor.run()
        tracker.stop_dashboard.assert_called_once()  # finally always runs

    @pytest.mark.asyncio
    async def test_run_empty_items_is_noop(self):
        tracker = MagicMock()
        tracker.items = []
        session = MagicMock()
        executor = BatchExecutor(session=session, tracker=tracker)
        await executor.run()  # should not raise
        tracker.start_dashboard.assert_not_called()
```

Note: `BatchExecutor.run()` was already implemented in Task 7 — no new code needed. These are integration tests for the existing implementation.

- [ ] **Step 2: Run tests to verify pass**

```bash
pytest tests/test_batch_executor.py::TestBatchExecutorRun -v
```
Expected: 4 passed.

- [ ] **Step 3: Run full test_batch_executor.py**

```bash
pytest tests/test_batch_executor.py -v
```
Expected: all tests in this file pass (40+ tests across all task classes from Tasks 1–8).

- [ ] **Step 5: Commit**

```bash
git add tests/test_batch_executor.py
git commit -m "test: add BatchExecutor.run integration tests"
```

---

## Chunk 3: Integration

### Task 9: TurnResult.text field

**Files:**
- Modify: `g3/src/coach_player.py` — add `text: str = ""` to `TurnResult`, populate in `_run_turn`

- [ ] **Step 1: Write failing test**

Add to `g3/tests/test_coach_player.py` (or a new file — check existing):

```python
# Add to test_coach_player.py or test_batch_executor.py:

class TestTurnResultText:
    def test_turn_result_has_text_field(self):
        from src.coach_player import TurnResult
        r = TurnResult(role="player", duration_s=1.0, tools_used=0, messages=[])
        assert hasattr(r, "text")
        assert isinstance(r.text, str)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestTurnResultText -v
```
Expected: `TypeError` — unexpected keyword `text`.

- [ ] **Step 3: Update TurnResult in coach_player.py**

Find the `TurnResult` dataclass (lines 33-39) and add `text` field:

```python
@dataclass
class TurnResult:
    """Result of a single agent turn."""
    role: str
    duration_s: float
    tools_used: int
    messages: list
    text: str = ""  # Concatenated assistant text output
```

- [ ] **Step 4: Populate text in _run_turn**

Add `AdaptedMessage` to the existing imports at the top of `coach_player.py`
(line 27 already imports from `src.providers` — add to that block):
```python
from src.providers.message_adapter import AdaptedMessage
```

Find the return statement in `_run_turn` by searching for this exact line:
```python
return TurnResult(role=role, duration_s=duration, tools_used=tools_used, messages=messages)
```

Replace with:
```python
text_parts = [
    msg.get_text_content()
    for msg in messages
    if isinstance(msg, AdaptedMessage) and msg.role == "assistant"
]
result_text = "\n".join(p for p in text_parts if p)
return TurnResult(
    role=role,
    duration_s=duration,
    tools_used=tools_used,
    messages=messages,
    text=result_text,
)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_batch_executor.py::TestTurnResultText -v
pytest tests/ -q
```
Expected: all pass (new test passes, no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/coach_player.py tests/test_batch_executor.py
git commit -m "feat: add text field to TurnResult, populated from AdaptedMessage output"
```

---

### Task 10: build_phase_coach_prompt

**Files:**
- Modify: `g3/src/prompts.py` — add `build_phase_coach_prompt`
- Test: `g3/tests/test_batch_executor.py` — add `TestBuildPhaseCoachPrompt`

- [ ] **Step 1: Write failing tests**

Add to `test_batch_executor.py`:

```python
from src.prompts import build_phase_coach_prompt


class TestBuildPhaseCoachPrompt:
    def test_contains_phase_name(self):
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create (1 steps)", type="create", steps=items)
        result = MagicMock(); result.text = "I created a.py"
        prompt = build_phase_coach_prompt(phase, result)
        assert "Create (1 steps)" in prompt

    def test_contains_step_texts(self):
        items = [PlanItem(text="create a.py"), PlanItem(text="create b.py")]
        phase = Phase(name="Create", type="create", steps=items)
        result = MagicMock(); result.text = "done"
        prompt = build_phase_coach_prompt(phase, result)
        assert "create a.py" in prompt
        assert "create b.py" in prompt

    def test_contains_player_output_truncated(self):
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        long_text = "x" * 3000
        result = MagicMock(); result.text = long_text
        prompt = build_phase_coach_prompt(phase, result)
        # Truncated to 2000 chars
        assert "x" * 2000 in prompt
        assert "x" * 2001 not in prompt

    def test_asks_for_approved_or_feedback(self):
        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)
        result = MagicMock(); result.text = "done"
        prompt = build_phase_coach_prompt(phase, result)
        # Note: intentionally uses wrong string "APPROVED" here — will fail on ImportError first.
        # Step 4 corrects this to "IMPLEMENTATION_APPROVED" after implementing the function.
        assert "APPROVED" in prompt
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestBuildPhaseCoachPrompt -v
```
Expected: `ImportError` for `build_phase_coach_prompt`.

- [ ] **Step 3: Add to prompts.py**

Add at the end of `prompts.py`:

```python
def build_phase_coach_prompt(phase, last_player_result) -> str:
    """Build Coach review prompt for a completed phase.

    Includes planned steps + truncated Player output (≤2000 chars).
    """
    steps_list = "\n".join(f"  - {s.text}" for s in phase.steps)
    player_summary = last_player_result.text[:2000]
    return (
        f"Phase '{phase.name}' has been completed by the Player.\n\n"
        f"Planned steps:\n{steps_list}\n\n"
        f"Player output summary:\n{player_summary}\n\n"
        f"Review the changes made. Check correctness, quality, and "
        f"that all planned steps were actually implemented. "
        f"Respond with IMPLEMENTATION_APPROVED or specific numbered feedback."
    )
```

Note: uses `IMPLEMENTATION_APPROVED` (not `APPROVED`) to match the existing `parse_coach_output` check in `feedback.py`.

- [ ] **Step 4: Fix test to use IMPLEMENTATION_APPROVED**

Update `test_asks_for_approved_or_feedback`:
```python
assert "IMPLEMENTATION_APPROVED" in prompt
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_batch_executor.py::TestBuildPhaseCoachPrompt -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/prompts.py tests/test_batch_executor.py
git commit -m "feat: add build_phase_coach_prompt to prompts"
```

---

### Task 11: _run_coach_turn_for_phase

**Files:**
- Modify: `g3/src/coach_player.py` — add `_run_coach_turn_for_phase` method

- [ ] **Step 1: Write failing test**

Add to `test_batch_executor.py`:

```python
class TestRunCoachTurnForPhase:
    @pytest.mark.asyncio
    async def test_returns_approved_on_approved_output(self):
        from src.coach_player import CoachPlayerSession, TurnResult
        from src.providers.message_adapter import AdaptedMessage, TextBlock

        items = [PlanItem(text="create a.py")]
        phase = Phase(name="Create", type="create", steps=items)

        # Build a mock session with a real _run_turn override
        session = MagicMock(spec=CoachPlayerSession)
        session.config = MagicMock()
        session.config.max_turns = 5
        session.config.coach_timeout_s = 300
        session.config.coach_model = ""

        # _run_turn returns a TurnResult with IMPLEMENTATION_APPROVED in messages
        approved_msg = AdaptedMessage(
            role="assistant",
            content=[TextBlock(text="IMPLEMENTATION_APPROVED")],
        )
        turn_result = TurnResult(
            role="coach", duration_s=1.0, tools_used=0,
            messages=[approved_msg], text="IMPLEMENTATION_APPROVED"
        )
        session._run_turn = AsyncMock(return_value=turn_result)

        # Call the actual method (unbound call)
        result = await CoachPlayerSession._run_coach_turn_for_phase(
            session, phase, MagicMock(text="player output")
        )
        from src.feedback import Approved
        assert isinstance(result, Approved)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_batch_executor.py::TestRunCoachTurnForPhase -v
```
Expected: `AttributeError` — method doesn't exist yet.

- [ ] **Step 3: Add method to CoachPlayerSession in coach_player.py**

Add after the `_run_turn` method (search for `def _print_session_report` — add the new method just before it):

```python
async def _run_coach_turn_for_phase(self, phase, last_player_result) -> "Approved | Feedback":
    """Run Coach review for a completed phase."""
    from src.prompts import build_phase_coach_prompt, COACH_STRICT_SYSTEM_PROMPT
    from src.feedback import parse_coach_output

    prompt = build_phase_coach_prompt(phase, last_player_result)
    result = await self._run_turn(
        role="coach",
        prompt=prompt,
        system_prompt=COACH_STRICT_SYSTEM_PROMPT,
        max_turns=self.config.max_turns,
        timeout_s=self.config.coach_timeout_s,
        model_override=self.config.coach_model,
    )
    return parse_coach_output(result.messages)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_batch_executor.py::TestRunCoachTurnForPhase -v
pytest tests/ -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/coach_player.py tests/test_batch_executor.py
git commit -m "feat: add _run_coach_turn_for_phase to CoachPlayerSession"
```

---

## Chunk 4: Config & CLI

### Task 12: Config.batch_mode

**Files:**
- Modify: `g3/src/config.py` — add `batch_mode` field + env var

- [ ] **Step 1: Write failing tests**

Add to `g3/tests/test_config.py`:

```python
class TestBatchModeConfig:
    def test_batch_mode_default_false(self):
        from src.config import resolve_config
        config = resolve_config({"working_dir": "."})
        assert config.batch_mode is False

    def test_batch_mode_from_env_true(self, monkeypatch):
        monkeypatch.setenv("G3_BATCH_MODE", "true")
        from src.config import resolve_config
        config = resolve_config({"working_dir": "."})
        assert config.batch_mode is True

    def test_batch_mode_from_env_1(self, monkeypatch):
        monkeypatch.setenv("G3_BATCH_MODE", "1")
        from src.config import resolve_config
        config = resolve_config({"working_dir": "."})
        assert config.batch_mode is True

    def test_batch_mode_from_env_yes(self, monkeypatch):
        monkeypatch.setenv("G3_BATCH_MODE", "yes")
        from src.config import resolve_config
        config = resolve_config({"working_dir": "."})
        assert config.batch_mode is True

    def test_batch_mode_from_env_false_string(self, monkeypatch):
        monkeypatch.setenv("G3_BATCH_MODE", "false")
        from src.config import resolve_config
        config = resolve_config({"working_dir": "."})
        assert config.batch_mode is False
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_config.py::TestBatchModeConfig -v
```
Expected: `TypeError` — `batch_mode` not in `Config`.

- [ ] **Step 3: Add to config.py**

In `Config` dataclass, add after `player_model`:
```python
batch_mode: bool = False    # --batch / G3_BATCH_MODE
```

In `resolve_config`, add to `env_map`:
```python
"G3_BATCH_MODE": ("batch_mode", lambda x: x.lower() in ("true", "1", "yes")),
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_config.py::TestBatchModeConfig -v
pytest tests/ -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add batch_mode to Config with G3_BATCH_MODE env var"
```

---

### Task 13: --batch CLI flag + entry point

**Files:**
- Modify: `g3/g3.py` — add `--batch` arg, update `_resolve_go_config` and `run_go`

- [ ] **Step 1: Add --batch argument to argparse in g3.py**

In `go_parser` arguments (after `--coach-model`), add:

```python
go_parser.add_argument(
    "--batch",
    action="store_true",
    dest="batch_mode",
    default=None,  # IMPORTANT: keep default=None (not False) — follows the same
                   # pattern as --verbose/--autonomous so resolve_config can filter
                   # None values and let yaml/env take precedence.
    help="Group steps into phases (batch execution mode)",
)
```

- [ ] **Step 2: Add batch_mode to _resolve_go_config**

In `_resolve_go_config`, add to the dict:
```python
"batch_mode": getattr(args, "batch_mode", None),
```

- [ ] **Step 3: Update run_go to branch on batch_mode**

Replace the `try` block in `run_go`:

```python
try:
    from src.plan_tracker import parse_requirements, PlanTracker
    from src.batch_executor import BatchExecutor, PhaseFailedError

    items = parse_requirements(requirements)
    session = CoachPlayerSession(config, requirements, str(plan_path))

    if config.batch_mode:
        tracker = PlanTracker(items)
        executor = BatchExecutor(session, tracker)
        try:
            await executor.run()
            sys.exit(0)
        except PhaseFailedError as e:
            print(f"\n❌ {e}")
            sys.exit(1)
    else:
        result = await session.run()
        sys.exit(0 if result.approved else 1)

except RuntimeError as e:
    print(f"\nОшибка: {e}")
    sys.exit(1)
```

- [ ] **Step 4: Run existing tests to check no regressions**

```bash
pytest tests/ -q
```
Expected: all pass.

- [ ] **Step 5: Manual smoke test — verify flag is accepted**

```bash
python3 g3.py go --help | grep batch
```
Expected: shows `--batch` in help output.

- [ ] **Step 6: Commit**

```bash
git add g3.py
git commit -m "feat: add --batch CLI flag and batch execution entry point to g3.py"
```

---

### Task 14: Menu toggle

**Files:**
- Modify: `g3/src/menu.py` — add Batch Mode toggle to `_questionary_menu` and `_edit_setting_questionary`

- [ ] **Step 1: Add batch_mode display to `_questionary_menu`**

In `_questionary_menu`, find the block where `verbose_display` and `autonomous_display` are set and add `batch_display` immediately after `autonomous_display`:

```python
verbose_display = "вкл" if config.verbose else "выкл"
autonomous_display = "вкл" if config.autonomous else "выкл"
batch_display = "вкл" if config.batch_mode else "выкл"   # add this line
```

In the `choices` list, the current tail looks like this (the final separator before Save/Quit has only dashes, no label):

```python
questionary.Choice(f"    Verbose:        {verbose_display}", value="verbose"),
questionary.Choice(f"    Автономный:     {autonomous_display}", value="autonomous"),
questionary.Separator("─────────────────────────────────────────"),
questionary.Choice("💾  Сохранить как default", value="save_default"),
```

Insert the new choice between `autonomous_display` and that final separator:

```python
questionary.Choice(f"    Verbose:        {verbose_display}", value="verbose"),
questionary.Choice(f"    Автономный:     {autonomous_display}", value="autonomous"),
questionary.Choice(f"    Batch Mode:     {batch_display}", value="batch_mode"),
questionary.Separator("─────────────────────────────────────────"),
questionary.Choice("💾  Сохранить как default", value="save_default"),
```

- [ ] **Step 2: Add batch_mode handling to `_edit_setting_questionary`**

In `_edit_setting_questionary`, add a new `elif` for `batch_mode` (follow the same pattern as `verbose` and `autonomous`):

```python
elif setting == "batch_mode":
    config = Config(**{**config.__dict__, "batch_mode": not config.batch_mode})
```

- [ ] **Step 3: Update `_save_global_default` to persist batch_mode**

In `_save_global_default`, find the `data["defaults"]` dict (it includes `coach_model` as the last entry). Add `batch_mode` to the dict:

```python
data = {
    "defaults": {
        "working_dir": config.working_dir,
        "plan_file": config.plan_file,
        "max_turns": config.max_turns,
        "verbose": config.verbose,
        "autonomous": config.autonomous,
        "batch_mode": config.batch_mode,   # add this line
        "player_provider": config.player_provider,
        "coach_provider": config.coach_provider,
        "player_model": config.player_model,
        "coach_model": config.coach_model,
    }
}
```

Without this, toggling Batch Mode on and pressing "Save as default" would silently drop the setting — it would revert to `False` on the next run.

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/menu.py
git commit -m "feat: add Batch Mode toggle to settings menu"
```

---

## Final Verification

- [ ] **Run complete test suite**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
pytest tests/ -v
```
Expected: all tests pass including the 17 new `test_batch_executor.py` tests.

- [ ] **Verify CLI flag works**

```bash
python3 g3.py go --help
```
Expected: `--batch` appears in options.

- [ ] **Verify no import errors**

```bash
python3 -c "from src.batch_executor import BatchExecutor, PhaseFailedError; from src.plan_tracker import PlanTracker, auto_group_phases, Phase; print('OK')"
```
Expected: `OK`

- [ ] **Final commit**

```bash
git add -p  # review anything unstaged
git commit -m "feat: complete batch execution feature — Phase, PlanTracker, BatchExecutor"
```
