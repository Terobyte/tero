# Tero Simplification — Design Spec (v2)

**Date:** 2026-05-01  
**Approach:** B — full field + code removal; non-batch `run()` stays as infrastructure but is no longer called

---

## Goal

Strip tero to the core that works: **Player + Coach + Batch pipeline**.
Remove dead features (TDD, TestWriter, Pre-Plan, Plan Polisher).
Batch is the only execution path — hardcoded, no toggle, no field, no env var.
Slim the interactive menu to 7 visible items.

---

## Implementation Order

Dependencies run bottom-up — remove consumers before providers.

```
1. Config (src/config.py)               — remove fields first so downstream can ref them
2. Role Router (src/role_router.py)     — remove test_writer/preplanner roles
3. Coach Player (src/coach_player.py)   — remove methods that use removed fields
4. Constants (src/constants.py)         — remove constants freed by step 3
5. Prompts (src/prompts.py)             — remove prompts freed by step 3
6. Streaming (src/streaming.py)         — remove UI helpers freed by step 3
7. Menu (src/menu.py)                   — slim down
8. CLI (src/cli_entry.py)               — remove args, hardcode batch dispatch
9. Tests                                — delete/update
```

---

## 1. Config (`src/config.py`)

### Remove fields from `Config`
```
tdd_mode
test_command
test_timeout_s
preplan_mode
preplan_provider
preplan_model
preplan_timeout_s
test_writer_provider
test_writer_model
batch_mode            ← removed entirely; batch is always on
```

### Remove from `_ENV_MAP`
```
G3_TDD_MODE
G3_TEST_COMMAND
G3_TEST_TIMEOUT_S
G3_PREPLAN_MODE, G3_PREPLAN_PROVIDER, G3_PREPLAN_MODEL
G3_TEST_WRITER_PROVIDER, G3_TEST_WRITER_MODEL
G3_BATCH_MODE         ← removed; batch is hardcoded
```

### Remove from `_UNSAFE_GLOBAL_DEFAULT_KEYS`
```
"tdd_mode"
"preplan_mode"
"batch_mode"          ← field no longer exists
```

### Remove from `resolve_config()` provider normalization block (~lines 483-486)
```python
"test_writer_provider",   # remove from the normalization loop
"preplan_provider",       # remove from the normalization loop
```

---

## 2. Role Router (`src/role_router.py`)

### Remove from `_ROLE_CONFIG_MAP`
```python
"test_writer": ("test_writer_provider", "test_writer_model"),
"preplanner":  ("preplan_provider", "preplan_model"),
```

### Remove all `test_writer` and `preplanner` special-cases
- `provider_name_for()` — remove fallback special-case for `test_writer`
- `provider_for()` — remove special-case branch for `test_writer`
- `switch_role()` / any snapshot dict — remove `test_writer_provider`, `test_writer_model`, `preplan_provider`, `preplan_model` keys
- `check_roles_ready()` — remove any special handling for `test_writer` or `preplanner`

---

## 3. Coach Player (`src/coach_player.py`)

### Remove `_run_phase_zero()` (~lines 371–477, actually ~107 lines)
Imports that become dead after removal:
- Remove `from src.personas import PersonaRegistry` (top-level, line 27)
- Change `self._persona_registry: PersonaRegistry | None = None` → `self._persona_registry = None`
  (`from __future__ import annotations` makes the annotation a lazy string — safe; but cleaner to drop it)
- `_system_prompt_with_overlay()` stays unchanged — it handles `None` registry gracefully
  (`if registry is None or not ordered_roles: return base_prompt`)
- Remove local imports inside `_run_phase_zero()`: `auto_group_phases`, `parse_enriched_plan`, `write_enriched_plan`, `PREPLANNER_SYSTEM_PROMPT`, `build_preplan_prompt`

### Remove from `_verify_providers_ready()`
```python
if self.config.tdd_mode:
    roles.append("test_writer")
if self.config.preplan_mode:
    roles.append("preplanner")
```

### Remove from `run()` method
- preplan branch (~lines 488–493)
- `tests_written = False` dead variable (~line 557)
- TDD test_writer branch (~lines 561–578)
- TDD re-check branch (~line 697)
- Remove top-level import `build_test_writer_prompt`, `TEST_WRITER_SYSTEM_PROMPT`

### Remove methods that become dead
- `_run_tests()` — only called from TDD branch (line 698)
- `_detect_test_command()` — only called from `_run_tests()`

### Remove dead imports in coach_player.py after above removals
- `import shlex` — used only in `_run_tests()` / `_detect_test_command()`
- `import subprocess` — used only in `_run_tests()`

---

## 4. Constants (`src/constants.py`)

### Remove
```python
DEFAULT_PREPLAN_TIMEOUT_S
TEST_WRITER_MAX_TURNS
DEFAULT_TEST_TIMEOUT_S    # only used in test_timeout_s config field (now removed)
```

---

## 5. Prompts (`src/prompts.py`)

Remove functions/constants used only by TDD/preplan:
- `PREPLANNER_SYSTEM_PROMPT`
- `TEST_WRITER_SYSTEM_PROMPT`
- `build_preplan_prompt()`
- `build_test_writer_prompt()`

---

## 6. Streaming UI (`src/streaming.py`)

Remove functions used only by TDD/preplan:
- `print_preplanner_header()`
- `print_preplan_result()`
- `print_test_writer_header()`
- `print_tdd_status()`

---

## 7. Menu (`src/menu.py`)

### Remove menu items + handlers from `_questionary_menu()` AND `_fallback_menu()`
Both functions must be updated:
- TDD Mode toggle + `elif setting == "tdd_mode"` in `_edit_setting_questionary()`
- Pre-Plan toggle + `elif setting == "preplan_mode"`
- Plan Polisher provider picker + `elif setting == "preplan_provider"`
- TestWriter provider picker + `elif setting == "test_writer"`
- Batch Mode toggle + `elif setting == "batch_mode"`
- Batch Review schedule + `elif setting == "batch_review_schedule"`
- Review Agent picker + `elif setting == "review_provider"`
- Pre-Coach picker + `elif setting == "batch_pre"`
- Post-Coach picker + `elif setting == "batch_post"`
- Code Review toggle + `elif setting == "code_review"`
- Verbose toggle + `elif setting == "verbose"`
- Autonomous toggle + `elif setting == "autonomous"`
- Working dir picker + `elif setting == "working_dir"`
- Debugger entry + handler

### Remove "Save default" as separate menu item
Instead, call `_save_global_default(config)` from within the "Запустить" handler:
```python
if answer == "start":
    try:
        _save_global_default(config)
    except OSError:
        pass        # write failure must not block execution
    return config
```

### Remove dead helper functions
- `_format_batch_retry_counts()`
- `_parse_batch_retry_counts()`
- `_launch_debugger()` (debugger runs via `tero debug` command)
- `BATCH_ROLE_LABELS["test_writer"]` entry
- `REVIEW_PROVIDER_PRESETS` dict
- `_review_effective_label()` function

### Remove from `BATCH_ROLE_LABELS`
```python
"test_writer": "TestWriter"
```

### Final menu structure

**questionary version:**
```
▶   Запустить                               ← auto-saves to ~/.g3/config.yaml

─── провайдеры ──────────────────────────
    Player:     <provider> (<model>)
    Coach:      <provider> (<model>)
    Escalation: <provider> (<model>)
    Judge:      <provider> (<model>)

─── настройки ───────────────────────────
    Файл плана:    requirements.md
    Макс. попыток: 10
    Context Limit: авто

─────────────────────────────────────────
✗   Выход
```

**Fallback (plain text) version:** same 7 items, same structure.

---

## 8. CLI (`src/cli_entry.py`)

### Remove from `resolve_go_config()`
```python
"tdd_mode": ..., "test_command": ..., "test_timeout_s": ...,
"preplan_mode": ..., "preplan_provider": ..., "preplan_model": ...,
"batch_mode": ...,    ← no longer a Config field
```

### Remove from `build_parser()` / `go_parser`
```
--tdd
--test-command
--test-timeout-s
--preplan / --no-preplan
--preplan-provider
--preplan-model
--batch               ← batch is always on; flag removed
```

### Hardcode batch dispatch in `run_go()`
Remove `if config.batch_mode:` branch entirely. `run_go()` always uses BatchExecutor:
```python
async def run_go(args, config=None, *, session_cls=CoachPlayerSession):
    if config is None:
        config = resolve_go_config(args)
    plan_path = Path(config.working_dir) / config.plan_file
    ...
    session = session_cls(config, requirements, str(plan_path))
    items = parse_requirements(requirements)
    tracker = PlanTracker(items)
    executor = BatchExecutor(session, tracker, session.router)
    await executor.run()
    sys.exit(0)
```

---

## 9. Tests

### Files to audit for removal/update
Run `grep -rl "tdd_mode\|preplan_mode\|test_writer\|batch_mode" tests/` to get full list.

Key categories:
- **Delete entirely**: any test file that exclusively tests TDD/preplan behavior
- **Partial update**: remove individual test cases that set `Config(tdd_mode=True)` or `Config(preplan_mode=True)` or `Config(batch_mode=...)`
- **Menu tests**: update `test_menu_*.py` for new slim menu structure
- **Role router tests**: remove test cases for `test_writer` and `preplanner` roles

Run full test suite after each step in the implementation order.

---

## Code Review vs Review Agent — Clarification (C4)

`code_review` feature stays enabled via `--code-review` CLI flag.  
`review_provider` defaults to `""` = follows coach provider automatically.  
Users who need a different review provider set `G3_REVIEW_PROVIDER` env var or add to `.g3/config.yaml`.  
No UI needed in the menu — the default (follow coach) covers the common case.

---

## Batch Retry Counts — No UI (M6)

`batch_pre_judge_attempts`, `batch_judge_attempts`, `batch_post_judge_attempts` remain in Config with their defaults.  
Changed via env vars (`G3_BATCH_PRE_JUDGE_ATTEMPTS` etc.) or `.g3/config.yaml`.  
No menu entry required.

---

## What Does NOT Change

| What | Why |
|------|-----|
| Pre-Coach (`batch_pre`) | Works, runs with defaults, configurable via yaml/env |
| Post-Coach (`batch_post`) | Works, runs with defaults, configurable via yaml/env |
| Judge (`batch_judge`) | Core of batch pipeline |
| Code Review | Available via `--code-review` CLI; review provider via env/yaml |
| Verbose / Autonomous | Available via `-v` / `--autonomous` CLI |
| Working dir | Available via `--working-dir` CLI |
| Debugger | Available via `tero debug` command |
| non-batch `run()` method | Stays as infrastructure; not called from CLI |
| `_system_prompt_with_overlay()` | Handles `None` registry safely; stays |

---

## Out of Scope

- Removing the non-batch `run()` method entirely (future Phase C)
- Changing the batch pipeline flow (Pre-Coach → Player → Judge → Post-Coach)
- Any provider changes
