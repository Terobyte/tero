# Tero Simplification — Design Spec

**Date:** 2026-05-01  
**Approach:** B — full field + code removal, non-batch `run()` stays as infrastructure

---

## Goal

Strip tero down to the core that works: **Player + Coach + Batch pipeline**.
Remove dead features (TDD, TestWriter, Pre-Plan, Plan Polisher).
Make batch mode the only execution mode — always on, no toggle.
Slim the interactive menu to 7 items.

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
```

### Change default
```python
batch_mode: bool = True   # was False
```

### Remove from `_ENV_MAP`
```
G3_TDD_MODE, G3_TEST_COMMAND, G3_TEST_TIMEOUT_S
G3_PREPLAN_MODE, G3_PREPLAN_PROVIDER, G3_PREPLAN_MODEL
G3_TEST_WRITER_PROVIDER, G3_TEST_WRITER_MODEL
```

### Remove from `_UNSAFE_GLOBAL_DEFAULT_KEYS`
```
"tdd_mode"
"preplan_mode"
```

---

## 2. Menu (`src/menu.py`)

### Remove menu items entirely
- TDD Mode toggle + handler
- Pre-Plan toggle + handler
- Plan Polisher provider picker + handler
- TestWriter provider picker + handler
- Batch Mode toggle + handler
- Batch Review schedule + handler
- Review Agent + handler
- Pre-Coach + handler
- Post-Coach + handler
- Code Review toggle + handler
- Verbose toggle + handler
- Autonomous toggle + handler
- Working dir picker + handler
- Debugger entry point + handler
- "Save default" menu choice (becomes automatic)

### Remove from `BATCH_ROLE_LABELS`
```python
"test_writer": "TestWriter"   # remove this entry
```

### Remove helper functions that become dead
- `_format_batch_retry_counts()`
- `_parse_batch_retry_counts()`
- `_launch_debugger()` (from menu module — debugger runs via `tero debug`)

### "Запустить" auto-saves settings
When user selects Start (`▶ Запустить`), call `_save_global_default(config)` before returning config.

### Final menu structure
```
▶   Запустить                    ← auto-saves to ~/.g3/config.yaml

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

---

## 3. CLI (`src/cli_entry.py`)

### Remove from `resolve_go_config()`
```python
"tdd_mode": ..., "test_command": ..., "test_timeout_s": ...
"preplan_mode": ..., "preplan_provider": ..., "preplan_model": ...
```

### Remove from `build_parser()` / `go_parser`
```
--tdd
--test-command
--test-timeout-s
--preplan / --no-preplan
--preplan-provider
--preplan-model
```

### Remove from `run_go()`
```python
# Remove this block:
if config.preplan_mode and hasattr(session, "_run_phase_zero"):
    enriched_items, phases = await session._run_phase_zero(requirements)
    if enriched_items:
        items = enriched_items
```

---

## 4. Coach Player (`src/coach_player.py`)

### Remove `_run_phase_zero()` (~80 lines)
Includes its imports: `PREPLANNER_SYSTEM_PROMPT`, `build_preplan_prompt`, `PersonaRegistry`, `auto_group_phases`, `parse_enriched_plan`, `write_enriched_plan`.

### Remove from `_verify_providers_ready()`
```python
if self.config.tdd_mode:
    roles.append("test_writer")
if self.config.preplan_mode:
    roles.append("preplanner")
```

### Remove from `run()` method
- preplan branch (lines ~488–493)
- TDD test_writer branch (lines ~561–576)
- TDD re-check branch (line ~697)

### Remove methods that become dead
- `_run_tests()` — only called from TDD branch
- `_detect_test_command()` — only called from `_run_tests()`

### Remove unused imports
`build_test_writer_prompt`, `PersonaRegistry`, `TEST_WRITER_SYSTEM_PROMPT`

---

## 5. Constants (`src/constants.py`)

### Remove
```python
DEFAULT_PREPLAN_TIMEOUT_S
TEST_WRITER_MAX_TURNS
DEFAULT_TEST_TIMEOUT_S  # only if unused after removal
```

---

## 6. Prompts (`src/prompts.py`)

Remove (if only used by TDD/preplan):
- `PREPLANNER_SYSTEM_PROMPT`
- `TEST_WRITER_SYSTEM_PROMPT`
- `build_preplan_prompt()`
- `build_test_writer_prompt()`

---

## 7. Streaming UI (`src/streaming.py`)

Remove functions only used by TDD/preplan:
- `print_preplanner_header()`
- `print_preplan_result()`
- `print_test_writer_header()`
- `print_tdd_status()`

---

## 8. Role Router (`src/role_router.py`)

Remove from `_ROLE_CONFIG_MAP`:
```python
"test_writer": ("test_writer_provider", "test_writer_model"),
"preplanner":  ("preplan_provider", "preplan_model"),
```
Remove related `check_roles_ready` special-casing for `test_writer` and `preplanner`.

---

## 8. Tests

- Delete or skip test files that exclusively test TDD/preplan features
- Update any test that creates `Config(tdd_mode=True, ...)` or `Config(preplan_mode=True, ...)`
- Update `test_menu_*.py` files for the new slim menu
- Run full test suite after changes

---

## What does NOT change

| What | Why |
|------|-----|
| Pre-Coach (`batch_pre`) | Works, runs with defaults |
| Post-Coach (`batch_post`) | Works, runs with defaults |
| Judge (`batch_judge`) | Core of batch pipeline |
| Code Review | Available via `--code-review` CLI |
| Verbose / Autonomous | Available via `-v` / `--autonomous` CLI |
| Working dir | Available via `--working-dir` CLI |
| Debugger | Available via `tero debug` command |
| non-batch `run()` method | Stays as infrastructure for edge cases |

---

## Out of scope

- Removing the non-batch `run()` method entirely (Phase C — separate refactor)
- Changing the batch pipeline flow (Pre-Coach → Player → Judge → Post-Coach)
- Any provider changes
