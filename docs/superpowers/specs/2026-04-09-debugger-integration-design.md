# Debugger Integration — Design Spec

**Date:** 2026-04-09
**Status:** Approved by user
**Scope:** Integrate auto-researched bug-finding system into TerraGo + build autonomous debug cycle

---

## 1. Goal

Add a **Debugger** mode to TerraGo that autonomously finds, confirms, and fixes bugs in any Python project. The user starts it, walks away, and returns to clean(er) code with a `bugs.md` report.

---

## 2. Source Material

From `~/Desktop/Projects/Active/auto/debugger-research/debugger/`:

| File | What we take | Adaptation needed |
|------|-------------|-------------------|
| `prompt.md` | Main bug-hunting prompt (3 passes + blind-spot checklist) | None — use as-is |
| `anchor_prompt.md` | "Search Engine" cross-function audit (6 checks) | None — use as-is |
| `file_strategy.py` | Context builder (skeleton + hotspots, budget allocation) | Replace `task_dir/buggy_files/` with `working_dir` |
| `run_experiment.py` → `FOCUSED_PROMPTS` | 3 of 6 persona prompts for High mode: `red_team`, `boundary`, `completeness` | None — use as-is. The other 3 (`structural`, `docstring`, `integration`) are intentionally excluded — their coverage is already provided by `PLAYER_PROMPT_MAIN` (3 explicit passes matching those categories) and `PLAYER_PROMPT_ANCHOR`. |
| `debugger.py` → `parse_bugs()` | JSON bug parser + prose fallback | Minor: remove `_history_anchor` / `_is_shadow` benchmark metadata |

**Not taken:** autoresearch loop, scorer, benchmark infrastructure, researcher agent.

---

## 3. Architecture

### 3.1 Pipeline Cycle

```
Player ──▶ Tester ──▶ Fixer ──▶ git commit ──▶ next iteration
  │                                                    │
  └────────────────────────────────────────────────────┘
```

Three sequential agents, each a separate LLM call via the existing provider system.

### 3.2 Cycle Logic

```python
clean_passes = 0
bug_counter = 0  # session-level monotonic ID counter

while not should_stop(iteration, start_time):
    bugs = run_player(working_dir)       # find bugs

    if not bugs:
        clean_passes += 1
        if clean_passes >= victory_threshold:  # default 3
            return victory()
        continue                          # skip Tester/Fixer

    clean_passes = 0
    bug_counter = assign_ids(bugs, bug_counter)

    confirmed, false_pos = run_tester(bugs)   # write tests, confirm
    update_bugs_md(confirmed, false_pos)

    if confirmed:
        run_fixer(confirmed)              # fix confirmed bugs
        update_bugs_md_fixed(confirmed)   # mark as fixed
        git_commit(iteration)

    iteration += 1
```

### 3.3 Victory Condition

**3 consecutive Player passes finding 0 bugs = VICTORY.**

When Player finds 0, skip Tester and Fixer — go straight to next Player pass.

### 3.4 Stop Conditions

| Mode | Stops when |
|------|-----------|
| Iterations | `iteration >= limit_value` |
| Time | `elapsed >= limit_minutes` |
| Infinite | Victory (3x clean) or user Ctrl+C |

---

## 4. New Files

### 4.1 `src/debugger.py` — Main Module (~300 lines)

Core class: `Debugger`

No separate `DebuggerConfig` dataclass — the debugger reads `debug_*` fields directly from the existing `Config` dataclass. This ensures settings persist across sessions via the existing `_save_global_default` mechanism.

```python
# Debugger uses Config.debug_* fields directly. No separate config class.
# Config.working_dir is reused (no separate debug_working_dir).

@dataclass
class BugEntry:
    id: int                     # session-level monotonic counter, starting at 1
    description: str
    file: str
    line: int
    severity: str
    status: str                 # "found", "confirmed", "false_positive", "fixed"

@dataclass
class DebuggerResult:
    success: bool               # True if victory
    iterations: int
    bugs_found: int
    bugs_fixed: int
    false_positives: int
    duration_s: float
```

**Methods:**

| Method | Role | LLM calls |
|--------|------|-----------|
| `run()` | Main loop | — |
| `_run_player()` | Call Player LLM, parse bugs | 1/2/5 depending on intensity |
| `_run_tester(bugs)` | Call Tester LLM, run pytest | 1 |
| `_run_fixer(confirmed)` | Call Fixer LLM | 1 |
| `_build_context()` | Adapted from `file_strategy.build_context()` | — |
| `_parse_bugs(raw)` | Adapted from `debugger.parse_bugs()` | — |
| `_update_bugs_md()` | Write/update `{working_dir}/bugs.md` | — |
| `_update_bugs_md_fixed(confirmed)` | Mark fixed bugs in bugs.md | — |
| `_git_commit(iteration)` | `git add -A && git commit` | — |
| `_should_stop(iteration, start)` | Check limit | — |
| `_display_counters()` | Update red/grey/green UI | — |

### 4.2 `src/debugger_prompts.py` — All Prompts (~200 lines)

Contains:

```python
# Copied from debugger-research (auto-researched, do not modify without autoresearch)
PLAYER_PROMPT_MAIN: str       # from prompt.md
PLAYER_PROMPT_ANCHOR: str     # from anchor_prompt.md
PLAYER_PROMPT_RED_TEAM: str   # from FOCUSED_PROMPTS["red_team"]
PLAYER_PROMPT_BOUNDARY: str   # from FOCUSED_PROMPTS["boundary"]
PLAYER_PROMPT_COMPLETENESS: str  # from FOCUSED_PROMPTS["completeness"]

# New prompts (simple, effective, improvable via future autoresearch)
TESTER_PROMPT: str
FIXER_PROMPT: str
```

### 4.3 `src/debugger_context.py` — Context Builder (~150 lines)

Adapted from `file_strategy.py`:
- `build_context(working_dir)` — scans `.py` files in working dir
- Reuses: skeleton+hotspots for large files, budget allocation, line numbering
- Removes: benchmark-specific logic (`task_dir`, `buggy_files/`, `clean_module.py` diff, `metadata.json` historical density)
- Adds: `.gitignore`-aware file discovery, skip `venv/`, `node_modules/`, `__pycache__/`, `.git/`

---

## 5. Modified Files

### 5.1 `src/config.py` — Add Debug Fields

```python
# Debugger settings
debug_player_provider: str = "zai"
debug_tester_provider: str = "claude"
debug_fixer_provider: str = "codex"
debug_player_model: str = ""
debug_tester_model: str = ""
debug_fixer_model: str = ""
debug_intensity: str = "medium"        # low / medium / high
debug_limit_mode: str = "infinite"     # iterations / time / infinite
debug_limit_value: int = 10
debug_victory_threshold: int = 3
```

Env mappings: `G3_DEBUG_PLAYER_PROVIDER`, etc.

The debugger reuses `Config.working_dir` — no separate `debug_working_dir` field needed.

Also: change `Config.player_provider` default from `"black"` to `"zai"` as part of provider cleanup (Section 6).

### 5.2 `src/menu.py` — Add Debugger Entry

**Main menu** — add "Debugger" option (always available, not gated by Coach-Player):

```
1. Start (Coach-Player)
2. Debugger
3. Settings
4. Exit
```

**Debugger submenu:**

```
Debugger Setup:
  Player provider:  [Zai ▼]       + model selector
  Tester provider:  [Claude ▼]    + model selector
  Fixer provider:   [Codex ▼]     + model selector
  Intensity:        [Medium ▼]    (Low=1 / Medium=2 / High=5 Player calls)
  Limit:            [Infinite ▼]  (N iterations / N min / Infinite)

  [Start Debugger]
```

### 5.3 `src/cli_entry.py` — Add `debug` Subcommand

The existing CLI uses `subparsers` with `required=True`. Add a `debug` subcommand (not top-level flags):

```
tero debug                          # enter debugger with defaults
tero debug --intensity medium       # low|medium|high
tero debug --limit 10               # iterations
tero debug --time 30                # minutes
tero debug --infinite               # run until victory
```

---

## 6. Provider Cleanup (Prerequisites)

### 6.1 Remove Provider Black (CCG)

| Action | File |
|--------|------|
| Delete file | `src/providers/ccg.py` |
| Remove `"black"`, `"turbo"` from factory | `src/providers/__init__.py` |
| Remove `CCG_MODEL_PRESETS` | `src/menu.py` |
| Remove CcgEnv / Black account mappings | `src/config.py` |
| Remove BLACKBOX env vars | `src/config.py` |
| Update default provider if "black" | `src/config.py` (change defaults to "zai" or "claude") |
| Remove from fallback chains | `src/providers/chain.py` references |
| Clean tests | Any test referencing "black"/"ccg" |

### 6.2 Cleanup OpenCode Models

In `src/menu.py` `OPENCODE_MODEL_PRESETS`:
- **Keep:** `"MiniMax M2.5 (free)": "opencode/minimax-m2.5-free"`
- **Remove:** MIMO Pro, MIMO Omni, Kimi K2, Kimi K2.5, Z.AI, Nemotron 3 Super, custom entry

### 6.3 Cleanup Codex Models

In `src/menu.py` `CODEX_MODEL_PRESETS`:
- **Keep:** `"Medium (default)": ""` and `"High": "gpt-5.4"`
- **Remove:** o3, o4-mini, custom entry

---

## 7. Player — Intensity Levels

| Level | Calls | What runs |
|-------|-------|-----------|
| **Low** | 1 | `PLAYER_PROMPT_MAIN` only |
| **Medium** | 2 | `PLAYER_PROMPT_MAIN` + `PLAYER_PROMPT_ANCHOR` |
| **High** | 5 | Medium + `red_team` + `boundary` + `completeness` |

All calls: provider receives code context as user message, prompt as system message.

**Player is text-only analysis** — no tool use. Implementation per provider:
- **Claude Native:** pass `--no-tools` flag or equivalent
- **Codex:** use `--approval-mode full-auto` with no tool permissions, or pipe prompt via `codex exec --json` with `max_turns=1`
- **Zai / OpenCode:** `max_turns=1` (these providers don't enable tools by default)

If a provider doesn't support disabling tools, `max_turns=1` is acceptable — Player's output is read after the first response regardless.

Bug reports from all calls are merged by `(file, line)` dedup. This is a pragmatic simplification — the research system uses a 4-tuple `(file, line, description, severity)` but for production, collapsing multi-bug-same-line to first-seen is acceptable.

---

## 8. Tester Prompt

```
You are a test engineer verifying bug reports. You will receive:
1. Source code with line numbers
2. A list of suspected bugs (file, line, description)

For EACH bug, write a pytest test that PROVES the bug exists.

RULES:
- The test must FAIL on the current (buggy) code
- The test must assert the CORRECT behavior (what the code SHOULD do)
- Do NOT write tests that pass — a passing test means the bug is fake

SELF-CHECK before submitting each test:
1. Does the test import and call the ACTUAL function from the codebase?
2. Does the test check a SPECIFIC wrong output/behavior described in the bug?
3. Would the test PASS if the bug were fixed? If not, rewrite it.
4. Is the test independent (no mocking of the function under test)?

After writing tests, RUN them with pytest. Report results:

For each bug:
- CONFIRMED: test fails as expected — bug is real
- FALSE_POSITIVE: test passes — bug does not exist, remove it
- INVALID_TEST: test errors (import/syntax) — rewrite and retry once

Output a JSON summary after running tests:
[{"bug_id": 1, "status": "confirmed|false_positive|invalid_test", "test_file": "path"}]
```

**Status handling:**
- `confirmed` → bug is real, pass to Fixer
- `false_positive` → bug doesn't exist, mark grey
- `invalid_test` → test couldn't be written/run, treat as unconfirmed (not passed to Fixer, not counted as false_positive — silently dropped for this iteration, Player may re-find it next iteration)

Tester is called as a **code-agent** (has tool access — can write files, run pytest).

---

## 9. Fixer Prompt

```
You are a senior engineer fixing confirmed bugs. You will receive:
1. Source code
2. Confirmed bugs with descriptions
3. Failing tests that prove each bug

For EACH confirmed bug:
1. READ the failing test to understand the expected behavior
2. READ the buggy code at the specified line
3. PLAN the minimal fix (change as few lines as possible)
4. IMPLEMENT the fix
5. RUN the failing test — it must now PASS

RULES:
- Fix only the confirmed bugs. Do not refactor surrounding code.
- Minimal changes — one bug = typically 1-3 lines changed.
- After fixing ALL bugs, run the full test suite to ensure no regressions.
- If a fix breaks other tests, adjust the fix, not the tests.
```

Fixer is called as a **code-agent** (has tool access — can edit files, run tests).

---

## 10. bugs.md Format

### During session:

```markdown
# Bug Report — Debugger Session

## Iteration 1
- [CONFIRMED] #1: Off-by-one in calculate_total() at billing.py:42
- [FALSE_POSITIVE] #2: Missing null check in parse_input() at parser.py:15
- [FIXED] #3: Wrong operator in validate_age() at user.py:88

## Iteration 2
- [CONFIRMED] #4: Mutable default in process_items() at engine.py:23
- [FIXED] #5: Swapped key/value in restore_state() at state.py:67

## Summary
Found: 5 | False Positive: 1 | Fixed: 4 | Remaining: 0
```

### After completion (cleaned):

```markdown
# Bug Report — Debugger Session (Complete)

- #1: Off-by-one in calculate_total — Fixed
- #2: Missing null check in parse_input — False Positive
- #3: Wrong operator in validate_age — Fixed
- #4: Mutable default in process_items — Fixed
- #5: Swapped key/value in restore_state — Fixed

Total: 5 found | 1 false positive | 4 fixed
Victory: 3 consecutive clean passes
Duration: 23m 15s
```

---

## 11. UI — Console Output

While running:

```
[Debugger] Iteration 3/∞ | Intensity: Medium
  Player searching...  ████████░░
  Found: 12 (red) | False Positive: 3 (grey) | Fixed: 8 (green) | Remaining: 1
```

Victory:

```
[Debugger] VICTORY — 3 consecutive clean passes
  Total: 12 found | 3 false positive | 9 fixed
  Duration: 18m 42s
  Report: bugs.md
```

Colors: red=found/confirmed, grey=false_positive, green=fixed.

---

## 12. Data Flow per Iteration

```
working_dir/*.py
    │
    ▼
build_context(working_dir)
    │  (skeleton + hotspots for large files, full for small)
    ▼
Player LLM (1/2/5 calls based on intensity)
    │  system: prompt.md / anchor_prompt.md / persona prompts
    │  user: code context
    │  output: JSON bug array
    ▼
parse_bugs(raw_output)
    │  JSON extraction + dedup by (file, line)
    ▼
bugs.md ← write found bugs (status: found)
    │
    ▼
Tester LLM (1 call, code-agent)
    │  system: tester prompt
    │  user: code + bug list
    │  action: writes pytest tests, runs them
    │  output: confirmed / false_positive per bug
    ▼
bugs.md ← update statuses (confirmed / false_positive)
    │
    ▼
Fixer LLM (1 call, code-agent)  [only if confirmed bugs exist]
    │  system: fixer prompt
    │  user: code + confirmed bugs + failing tests
    │  action: edits source, runs tests to verify
    ▼
bugs.md ← update fixed bugs (status: fixed)
    │
    ▼
git commit -m "debugger: fix N bugs (iteration K)"
    │
    ▼
next iteration (Player sees updated code)
```

---

## 13. Integration with Existing Code

| Existing module | How debugger uses it |
|----------------|---------------------|
| `src/providers/__init__.py` | `create_provider(name)` for each role |
| `src/providers/chain.py` | Not used — debugger does not support fallback chains. Rate limit errors propagate as iteration failures (logged, next iteration retries). |
| `src/config.py` | `Config` dataclass with `debug_*` fields |
| `src/menu.py` | New menu entry + submenu |
| `src/cli_entry.py` | New `--debug` CLI args |
| `src/streaming.py` | Reuse for colored output / spinners |

**Not touched:** `orchestrator.py`, `coach_player.py`, `duel.py`, `judge.py`, `batch_executor.py`, `state.py`, `plan_tracker.py`.

---

## 14. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Player finds 0 bugs | Skip Tester/Fixer, increment clean_passes |
| Tester marks all as false_positive | No Fixer call, next iteration |
| Fixer introduces new bugs | Next iteration Player catches them (snowball) |
| Provider rate limit | Log error, skip this iteration, retry next iteration |
| User Ctrl+C | Graceful stop, write final bugs.md report |
| No .py files in working_dir | Error message, exit |
| Very large codebase | file_strategy budget allocation + skeleton mode |

---

## 15. Implementation Order

1. Provider cleanup (remove Black, clean OpenCode/Codex models, change `Config` defaults from "black" to "zai")
2. `src/config.py` — add `debug_*` fields (needed before debugger.py)
3. `src/debugger_prompts.py` — copy prompts from research
4. `src/debugger_context.py` — adapt file_strategy.py
5. `src/debugger.py` — main loop + bug parsing
6. `src/menu.py` — add Debugger entry + submenu
7. `src/cli_entry.py` — add `debug` subcommand
8. Integration test — run debugger on tero itself

## 16. `bugs.md` Location

`bugs.md` is written to `{Config.working_dir}/bugs.md`. Overwritten each update within a session. Previous session's bugs.md is backed up as `bugs.md.bak` before starting a new session.
