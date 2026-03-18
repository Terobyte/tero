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

**Phase-Aware BatchExecutor**

Auto-group `PlanItem` steps into phases by keyword type. A `BatchExecutor` executes each phase
in one Player turn, then runs Coach review once per phase. A new `PlanTracker` class wraps
the existing module functions and adds a Rich live progress dashboard.

Result: ~136 turns → ~10–15 turns, Rich progress dashboard, Coach review preserved.

---

## Codebase Anchors

Key facts about existing code that this spec builds on:

| Thing | Reality |
|---|---|
| Step type | `PlanItem` dataclass in `plan_tracker.py` with `.text: str` and `.done: bool` |
| `plan_tracker.py` | Module of free functions — no class exists. We add one. |
| `TurnResult` | `coach_player.py` dataclass with `role, duration_s, tools_used, messages`. No `.text` field — **add it.** |
| `_run_turn` signature | `(role, prompt, system_prompt, max_turns, timeout_s, model_override="")` |
| Coach system prompt | `COACH_STRICT_SYSTEM_PROMPT` from `prompts.py` |
| Coach turns config | `self.config.max_turns`, `self.config.coach_timeout_s`, `self.config.coach_model` |
| `Config.resolve_config` | Uses `env_map` dict + `lambda x: x.lower() == "true"` for bool envvars |
| `Feedback` verdict | Has `.text: str` attribute (checked in `feedback.py`) |

---

## Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Coach runs | After each phase | Preserves quality, reduces turns dramatically |
| Incomplete phase | Retry whole phase with context of what was done | Player gets done/remaining list |
| Coach rejection | Reset completed_steps=[], include feedback in next prompt | Intentional: any step may be wrong; Player fixes all with feedback |
| Phase definition | Auto-group by keyword type | No manual YAML needed |
| Integration point | New `PlanTracker` class + new `BatchExecutor` | Minimal coupling to existing code |
| Phase split boundary | Split when batch reaches `PHASE_SIZE[type]` exactly | Not at PHASE_SIZE+1 |
| Empty phases | No-op + `logging.warning` | If plan has no steps, `BatchExecutor.run()` returns silently |
| Dashboard updates | Only at phase/attempt transitions | Player runs as single turn; no mid-turn signals |

---

## Section 1: Data Model

### Step Type Detection

Added to `plan_tracker.py`:

```python
STEP_TYPE_MAP = {
    "create": ["create", "add", "write", "generate", "implement"],
    "update": ["update", "modify", "change", "extend", "refactor"],
    "test":   ["test", "tests", "verify", "validate", "check"],
    "review": ["review", "analyze", "audit", "fix issue"],
}

# Max PlanItems per phase by type
PHASE_SIZE = {"create": 6, "update": 4, "test": 3, "review": 1}

# Type when no keyword matches
DEFAULT_STEP_TYPE = "update"
```

**Detection rules:**
- Case-insensitive scan of `PlanItem.text`
- Match whole words/phrases only (`\b...\b`), not raw substring containment
- Priority order is `review > test > create > update` so phrases like "write tests" stay in
  `test`, not `create`
- No match → `DEFAULT_STEP_TYPE`

### Phase Dataclass

Added to `plan_tracker.py`:

```python
@dataclass
class Phase:
    name: str            # e.g. "Create (5 steps)"
    type: str            # "create" | "update" | "test" | "review"
    steps: list[PlanItem]  # PlanItem objects from plan_tracker
    status: str = "pending"  # pending | in_progress | done | failed
    attempts: int = 0
```

### Auto-Grouping

Added to `plan_tracker.py`:

```python
def detect_step_type(item: PlanItem | str) -> str:
    """Return step type by keyword match using word boundaries."""
    text = item.text if isinstance(item, PlanItem) else item
    text = text.lower()

    def has_keyword(keyword: str) -> bool:
        pattern = r"\b" + re.escape(keyword) + r"\b"
        return bool(re.search(pattern, text))

    for step_type in ("review", "test", "create", "update"):
        keywords = STEP_TYPE_MAP[step_type]
        if any(has_keyword(kw) for kw in keywords):
            return step_type
    return DEFAULT_STEP_TYPE


def auto_group_phases(items: list[PlanItem]) -> list[Phase]:
    """Group PlanItems into phases by detected type, respecting PHASE_SIZE limits."""
    if not items:
        return []

    phases: list[Phase] = []
    current_type = detect_step_type(items[0])
    current_batch: list[PlanItem] = []

    for item in items:
        itype = detect_step_type(item)
        # Start new phase if type changes OR batch is full (at PHASE_SIZE, not PHASE_SIZE+1)
        if itype != current_type or len(current_batch) >= PHASE_SIZE[current_type]:
            if current_batch:
                phases.append(_make_phase(current_type, current_batch))
            current_type = itype
            current_batch = []
        current_batch.append(item)

    if current_batch:
        phases.append(_make_phase(current_type, current_batch))

    return phases


def _make_phase(ptype: str, items: list[PlanItem]) -> Phase:
    name = f"{ptype.capitalize()} ({len(items)} steps)"
    return Phase(name=name, type=ptype, steps=items)
```

### PlanTracker Class

New class added to `plan_tracker.py`. Wraps existing free functions and owns dashboard state:

```python
from rich.live import Live
from rich.table import Table


class PlanTracker:
    """Tracks phase/step progress and owns the Rich live dashboard."""

    def __init__(self, items: list[PlanItem]):
        self.items = items           # all PlanItems (for existing code compatibility)
        self.phases: list[Phase] = []  # populated by BatchExecutor before run
        self._live: Live | None = None

    # --- Phase progress ---

    def phase_done(self, phase: Phase) -> None:
        """Mark all PlanItems in phase as done and re-render dashboard.

        Persistence is handled by the caller (`write_checklist_back`) so that batch mode
        remains resumable after every approved phase.
        """
        for item in phase.steps:
            item.done = True
        self.render_dashboard()

    # --- Dashboard ---

    def start_dashboard(self) -> None:
        """Start Rich Live display. Call once before execution loop."""
        self._live = Live(self._build_table(), refresh_per_second=4)
        self._live.__enter__()

    def render_dashboard(self) -> None:
        """Update live display. Idempotent — safe to call extra times."""
        if self._live:
            self._live.update(self._build_table())

    def stop_dashboard(self) -> None:
        """Stop Rich Live display. Always called in finally block."""
        if self._live:
            self._live.__exit__(None, None, None)
            self._live = None

    def _build_table(self) -> Table:
        table = Table(title="G3 Execution", show_header=False, box=None)
        for i, phase in enumerate(self.phases):
            done = sum(1 for s in phase.steps if s.done)
            total = len(phase.steps)
            pct = done * 100 // total if total else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            icon = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}[phase.status]
            attempts_str = f" (attempt {phase.attempts})" if phase.attempts > 1 else ""
            table.add_row(
                f"{icon} Phase {i+1}: {phase.name}{attempts_str}",
                f"{bar} {pct}%",
            )
        total_steps = sum(len(p.steps) for p in self.phases)
        done_steps = sum(s.done for p in self.phases for s in p.steps)
        table.add_row("", f"Steps: {done_steps}/{total_steps}")
        return table
```

---

## Section 2: BatchExecutor

**File:** `g3/src/batch_executor.py`

### TurnResult Extension

`TurnResult` in `coach_player.py` currently has `role, duration_s, tools_used, messages`.
Add a `.text` field populated in `_run_turn`:

```python
# coach_player.py — update TurnResult
@dataclass
class TurnResult:
    role: str
    duration_s: float
    tools_used: int
    messages: list
    text: str = ""  # NEW: concatenated assistant text from messages
```

Populate in `_run_turn` after collecting messages. Messages are `AdaptedMessage` objects
(from `message_adapter.py`) which have `.role: str` and `.get_text_content() -> str`:

```python
# At end of _run_turn, before return statement:
from src.providers.message_adapter import AdaptedMessage
text_parts = [
    msg.get_text_content()
    for msg in messages
    if isinstance(msg, AdaptedMessage) and msg.role == "assistant"
]
result_text = "\n".join(p for p in text_parts if p)
return TurnResult(role=role, duration_s=elapsed, tools_used=tools_used,
                  messages=messages, text=result_text)
```

Note: `parse_coach_output(result.messages)` already works with `AdaptedMessage` objects —
`_is_assistant_message` passes on them via `hasattr(msg, "content")`, and
`_extract_text_from_message` handles `TextBlock.text` correctly.

### Exception

```python
# batch_executor.py

@dataclass
class PhaseFailedError(Exception):
    """Raised when a phase exhausts all retry attempts."""
    phase: Phase
    attempts: int
    completed_steps: list[str]

    def __post_init__(self):
        # Needed: @dataclass does not call Exception.__init__, so args is empty
        # without this. Ensures str(e) and e.args work correctly.
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"Phase '{self.phase.name}' failed after {self.attempts} attempts. "
            f"Completed steps: {self.completed_steps}"
        )
```

### BatchExecutor

```python
# batch_executor.py

import logging
import re
from dataclasses import dataclass

from src.plan_tracker import Phase, PlanTracker, auto_group_phases, write_checklist_back
from src.feedback import Approved, Feedback


@dataclass
class PhaseFailedError(Exception):
    ...  # see above


class BatchExecutor:
    def __init__(self, session: "CoachPlayerSession", tracker: PlanTracker):
        self.session = session
        self.tracker = tracker

    async def run(self) -> None:
        """Execute all pending phases. Raises PhaseFailedError on unrecoverable failure."""
        pending_items = [item for item in self.tracker.items if not item.done]
        phases = auto_group_phases(pending_items)
        if not phases:
            logging.warning("BatchExecutor.run(): no phases generated, nothing to do")
            return

        self.tracker.phases = phases
        self.tracker.start_dashboard()

        try:
            for phase in phases:
                phase.status = "in_progress"
                self.tracker.render_dashboard()

                completed_steps = await self._run_phase(phase)

                phase.status = "done"
                self.tracker.phase_done(phase)
                if self.session.plan_file_path:
                    write_checklist_back(self.session.plan_file_path, self.tracker.items)
        finally:
            self.tracker.stop_dashboard()

    async def _run_phase(self, phase: Phase) -> list[str]:
        """
        Execute all steps in one Player turn. Retry if incomplete or Coach rejects.

        Returns the final confirmed step texts when the phase is approved.
        Raises `PhaseFailedError` with the last confirmed partial progress when all attempts
        are exhausted.
        """
        MAX_ATTEMPTS = 3
        completed_steps: list[str] = []
        coach_feedback: str = ""

        for attempt in range(MAX_ATTEMPTS):
            phase.attempts = attempt + 1
            self.tracker.render_dashboard()

            prompt = build_batch_prompt(phase, completed_steps, coach_feedback)

            from src.prompts import PLAYER_BATCH_SYSTEM_PROMPT
            result = await self.session._run_turn(
                role="player",
                prompt=prompt,
                system_prompt=PLAYER_BATCH_SYSTEM_PROMPT,
                max_turns=self.session.config.max_turns,
                timeout_s=self.session.config.player_timeout_s,
                model_override=self.session.config.player_model,
            )

            completed_steps = parse_completed_steps(result, phase)

            if len(completed_steps) == len(phase.steps):
                verdict = await self.session._run_coach_turn_for_phase(phase, result)
                if isinstance(verdict, Approved):
                    return completed_steps
                # Coach rejected — include feedback in next attempt
                coach_feedback = verdict.text if isinstance(verdict, Feedback) else str(verdict)
                completed_steps = []

        raise PhaseFailedError(
            phase=phase,
            attempts=phase.attempts,
            completed_steps=completed_steps,
        )
```

### Batch Prompt Builder

```python
def build_batch_prompt(
    phase: Phase,
    completed_steps: list[str],
    coach_feedback: str = "",
) -> str:
    """
    Build Player prompt for executing a phase.

    completed_steps: PlanItem.text values completed in a previous attempt (empty on first try)
    coach_feedback:  Coach rejection message (empty if no rejection yet)

    Player must keep the original per-phase numbering stable across retries and emit:
    - `Step X done: [desc]` for each completed step
    - `PHASE_COMPLETE: <phase_name>` only after all steps are complete
    """
    remaining = [
        (idx + 1, step)
        for idx, step in enumerate(phase.steps)
        if step.text not in completed_steps
    ]
    sections: list[str] = []

    if coach_feedback:
        sections.append(
            f"PREVIOUS ATTEMPT REJECTED BY COACH:\n{coach_feedback}\n\n"
            f"Fix the issues and redo all steps."
        )

    if completed_steps:
        done_list = "\n".join(f"  ✅ {s}" for s in completed_steps)
        sections.append(f"Already completed in this attempt:\n{done_list}")

    steps_list = "\n".join(f"  {step_num}. {step.text}" for step_num, step in remaining)
    sections.append(
        f"Execute ALL of the following steps in sequence.\n"
        f"Complete ALL steps before returning.\n\n"
        f"Phase: {phase.name}\n"
        f"Steps:\n{steps_list}"
    )

    confirmations = "\n".join(
        f"  Step {step_num} done: [one-line description]"
        for step_num, _step in remaining
    )
    sections.append(
        "After completing each step, output exactly:\n"
        f"{confirmations}\n\n"
        f"When ALL steps are complete, output exactly:\n"
        f"  PHASE_COMPLETE: {phase.name}"
    )

    return "\n\n".join(sections)
```

### Completed Steps Parser

```python
_STEP_DONE_RE = re.compile(r"step\s+(\d+)\s+done\s*:", re.IGNORECASE)
_PHASE_COMPLETE_RE = re.compile(r"^\s*PHASE_COMPLETE\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_completed_steps(result: "TurnResult", phase: Phase) -> list[str]:
    """
    Extract confirmed-done step names (PlanItem.text) from Player output (result.text).

    Scan for explicit `Step X done:` markers using the original phase numbering.
    `PHASE_COMPLETE: <phase.name>` only upgrades the result to "all steps done" when:
    - the phase name matches exactly, and
    - every step index for this phase was already explicitly confirmed
    """
    text = result.text

    confirmed: set[int] = set()
    for match in _STEP_DONE_RE.finditer(text):
        idx = int(match.group(1)) - 1  # 1-based → 0-based
        if 0 <= idx < len(phase.steps):
            confirmed.add(idx)

    phase_complete_match = _PHASE_COMPLETE_RE.search(text)
    if (
        phase_complete_match
        and phase_complete_match.group(1).strip() == phase.name
        and len(confirmed) == len(phase.steps)
    ):
        return [s.text for s in phase.steps]

    return [phase.steps[i].text for i in sorted(confirmed)]
```

### Coach Turn Extension

New method on `CoachPlayerSession` in `coach_player.py`:

```python
async def _run_coach_turn_for_phase(
    self,
    phase: Phase,
    last_player_result: TurnResult,
) -> Approved | Feedback:
    """Run Coach review for a completed phase."""
    from src.prompts import build_phase_coach_prompt, COACH_STRICT_SYSTEM_PROMPT

    prompt = build_phase_coach_prompt(phase, last_player_result)
    result = await self._run_turn(
        role="coach",
        prompt=prompt,
        system_prompt=COACH_STRICT_SYSTEM_PROMPT,
        max_turns=self.config.max_turns,
        timeout_s=self.config.coach_timeout_s,
        model_override=self.config.coach_model,
    )
    return parse_coach_output(result.messages)  # existing function
```

New function in `prompts.py`:

```python
def build_phase_coach_prompt(phase: Phase, last_player_result: TurnResult) -> str:
    """Build Coach review prompt for a completed phase.

    Includes planned steps + truncated Player output (≤2000 chars) for context.
    Coach is expected to verify against the workspace, not trust the summary blindly.
    """
    steps_list = "\n".join(f"  - {s.text}" for s in phase.steps)
    player_summary = last_player_result.text[:2000]
    return (
        f"Phase '{phase.name}' has been completed by the Player.\n\n"
        f"Planned steps:\n{steps_list}\n\n"
        f"Player output summary:\n{player_summary}\n\n"
        f"Review the changes made. Check correctness, quality, and "
        f"that all planned steps were actually implemented. "
        f"Use tools to inspect the workspace; do not rely only on the summary above. "
        f"Respond with APPROVED or specific numbered feedback."
    )
```

---

## Section 3: Progress Dashboard

Shown above in `PlanTracker._build_table()`. Example output:

```
          G3 Execution
✅ Phase 1: Create (5 steps)          ████████████████████ 100%
🔄 Phase 2: Update (4 steps) (attempt 2) ░░░░░░░░░░░░░░░░░░░░   0%
⏳ Phase 3: Test (3 steps)            ░░░░░░░░░░░░░░░░░░░░   0%
⏳ Phase 4: Review (1 steps)          ░░░░░░░░░░░░░░░░░░░░   0%
                                      Steps: 5/13
```

---

## Section 4: CLI & Config

### Config

```python
# config.py — add to Config dataclass
batch_mode: bool = False
```

**Env-var parsing in `resolve_config`** — follow existing `env_map` pattern:

```python
env_map = {
    # ... existing entries ...
    "G3_BATCH_MODE": ("batch_mode", lambda x: x.lower() in ("true", "1", "yes")),
}
```

**CLI** — add to `g3.py` argparse:

```python
parser.add_argument("--batch", action="store_true", dest="batch_mode", default=None,
                    help="Enable batch execution (group steps into phases)")
```

**Yaml** — `defaults.batch_mode: true` in `.g3/config.yaml` (handled by existing `project.get("defaults", {})` merge).

**Precedence** (highest → lowest): `--batch` CLI > `G3_BATCH_MODE` env > yaml defaults > `False`

### Entry Point (`g3.py`)

```python
if config.batch_mode:
    result = await session.run_batch(items)  # wraps BatchExecutor but preserves SessionResult/history
    raise SystemExit(0 if result.approved else 1)
else:
    await session.run()  # existing path, unchanged
```

`run_batch()` should reuse the existing reporting/recording pipeline (`RunRecord`,
final summary, exit semantics) instead of bypassing it.

### Menu Toggle

```
─── режимы ──────────────────────────────
    TDD Mode:       выкл
    Code Review:    выкл
    Batch Mode:     выкл
```

---

## Section 5: Tests (`test_batch_executor.py`)

Most tests use `PlanItem(text=..., done=False)` as input; resume/persistence tests also
cover pre-existing `done=True` items. Mock `CoachPlayerSession._run_turn` to return a
`TurnResult` with a specific `.text` value.

| Test | Scenario | Key assertion |
|---|---|---|
| `test_auto_group_phases_by_type` | Items with create/update/test/review keywords | Phase types match |
| `test_auto_group_phases_size_limit` | 8 create-items → 2 phases | phase sizes = [6, 2] |
| `test_auto_group_phases_unknown_type` | Item "do something" (no keyword) | type == DEFAULT_STEP_TYPE |
| `test_auto_group_phases_type_change` | [create, create, update, update] | 2 phases |
| `test_parse_completed_steps_by_index` | result.text = "Step 1 done: created file" | returns [items[0].text] |
| `test_parse_completed_steps_phase_complete` | all `Step N done` markers + exact `PHASE_COMPLETE: <phase.name>` | returns all item texts |
| `test_parse_completed_steps_ignores_stray_phase_complete` | output mentions `PHASE_COMPLETE:` but missing step markers or wrong phase name | does **not** return all item texts |
| `test_parse_completed_steps_partial` | "Step 1 done" + "Step 3 done" (no step 2) | returns 2 texts |
| `test_parse_completed_steps_empty` | result.text = "I tried but failed" | returns [] |
| `test_build_batch_prompt_first_attempt` | No completed_steps, no feedback | no "Already completed" section, no "REJECTED" section |
| `test_build_batch_prompt_with_done_steps` | completed_steps=["create x.py"] | "Already completed" section present |
| `test_build_batch_prompt_keeps_original_step_numbers` | completed_steps=[items[0].text] | remaining steps keep original indices (e.g. 2, 3, 4) |
| `test_build_batch_prompt_with_coach_feedback` | coach_feedback="step 2 missing" | "REJECTED BY COACH" section present |
| `test_run_phase_success_first_attempt` | Mock: all markers + Approved | returns all phase step texts, phase.attempts==1 |
| `test_run_phase_retry_on_incomplete` | Mock: attempt 1 partial, attempt 2 full markers + Approved | returns all phase step texts, phase.attempts==2 |
| `test_run_phase_retry_on_coach_rejection` | Mock: attempt 1 full markers + Rejected, attempt 2 full markers + Approved | returns all phase step texts |
| `test_run_phase_exhausts_attempts` | Mock: 3 attempts all partial | `_run_phase()` raises `PhaseFailedError` with partial `completed_steps` |
| `test_run_raises_on_failed_phase` | `BatchExecutor.run()` hits exhausted phase | `PhaseFailedError` raised |
| `test_run_only_groups_pending_items` | Some `PlanItem.done=True` before batch start | done items are skipped |
| `test_run_writes_checklist_after_each_approved_phase` | 2 phases, first approved | `write_checklist_back()` called before phase 2 starts |
| `test_phase_failed_error_message` | `PhaseFailedError(phase, 3, ["step a"]).__str__()` | contains phase.name, "3 attempts", and partial progress |

---

## File Changes

### New files
```
g3/src/batch_executor.py         — PhaseFailedError, BatchExecutor,
                                   build_batch_prompt, parse_completed_steps
g3/tests/test_batch_executor.py  — batch execution tests (see table above)
```

### Modified files
```
g3/src/plan_tracker.py           — STEP_TYPE_MAP, PHASE_SIZE, Phase dataclass,
                                   detect_step_type, auto_group_phases, PlanTracker class
g3/src/coach_player.py           — TurnResult.text field, _run_coach_turn_for_phase method,
                                   `run_batch()` wrapper preserving SessionResult/history
g3/src/prompts.py                — build_phase_coach_prompt
g3/src/config.py                 — Config.batch_mode field, G3_BATCH_MODE in env_map
g3/src/menu.py                   — Batch Mode toggle
g3/g3.py                         — --batch CLI flag, route to `run_batch()`
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

---

## Implementation Order

1. `STEP_TYPE_MAP/PHASE_SIZE/Phase` + `detect_step_type` + `auto_group_phases` + `PlanTracker` class in `plan_tracker.py`
2. `TurnResult.text` field + text population in `_run_turn` in `coach_player.py`
3. `PhaseFailedError` + `BatchExecutor` + `build_batch_prompt` + `parse_completed_steps` in `batch_executor.py`
4. `_run_coach_turn_for_phase` in `coach_player.py` + `build_phase_coach_prompt` in `prompts.py`
5. `Config.batch_mode` + `G3_BATCH_MODE` in `env_map` in `config.py`
6. `--batch` flag + entry point routing in `g3.py`
7. Batch Mode toggle in `menu.py`
8. Tests in `test_batch_executor.py`
