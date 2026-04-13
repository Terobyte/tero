# Refactoring Plan: tero

> AI-assisted code debugging/coaching system (~26K LOC Python, 37 modules).
> Goal: readable, scalable, easier to debug and grow.

---

## Phase 1: Exception Hierarchy + Constants

> Foundation layer. Zero behavior change, every subsequent phase benefits.

- [x] **1A: Create `src/errors.py`** — structured exception hierarchy
  - [x] `TeroError` base class
  - [x] `ProviderError` → `ProviderNotReadyError`, `ProviderTimeoutError`, `RateLimitError`
  - [x] `SessionError` → `StateTransitionError`
  - [x] `PhaseFailedError`, `PlanResetRequested` moved from `batch_executor.py`
  - [x] `ConfigError`
  - [x] `ContextError`
  - [x] `BudgetExceededError`
  - [ ] `DebuggerError` — defer to Phase 5A
  - [x] Replace `raise ValueError(...)` in `providers/__init__.py` → `ProviderError`
  - [x] ~~Replace `raise RuntimeError(...)` in `coach_player.py::_check_role_ready()`~~ — method removed entirely in Phase 2, `RoleRouter.check_roles_ready()` now raises `ProviderNotReadyError`
  - [x] Write `tests/test_errors.py`
- [x] **1B: Create `src/constants.py`** — centralize magic numbers (~60 constants)
  - [x] `debugger.py` → `DEBUG_RETRY_BACKOFF_S`, `DEBUG_STALE_THRESHOLD_S`
  - [x] `debugger_context.py` → `MAX_CONTEXT_CHARS`, `BUDGET_CHARS_PER_FILE`, `LARGE_FILE_LINE_THRESHOLD`, `MAX_SYMBOLS`
  - [x] `config.py` → `DEFAULT_CONTEXT_LIMIT`
  - [x] `codex.py` + `opencode.py` → `MAX_TOOL_OUTPUT_CHARS` (deduplicated)
  - [x] `BATCH_REVIEW_MAX_TURNS = 4` added to `constants.py` — but `coach_player.py:87` still has the hardcoded duplicate
  - [x] `chain.py` → `MAX_BUFFER_MSGS`
  - [x] Update imports in ~10 files
  - [x] `coach_player.py:87` — remove hardcoded `BATCH_REVIEW_MAX_TURNS = 4`, import from `src.constants`
- [x] **Verify:** existing regression tests pass; grep confirms no straggler constants/exceptions

---

## Phase 2: Role Router — implementation ✅, tests/verify pending

> Extract 7 duplicated provider-resolution methods from `CoachPlayerSession`.
> Depends on: Phase 1.

- [x] **Create `src/role_router.py`** (250 lines)
  - [x] `_ROLE_CONFIG_MAP` — module-level dict, 6 role entries: role → (provider_attr, model_attr)
  - [x] `provider_for(role)` → `AgentProvider`
  - [x] `provider_name_for(role)` → `str`
  - [x] `display_label_for(role)` → `str`
  - [x] `check_roles_ready(roles)` — raises `ProviderNotReadyError`
  - [x] `switch_role(role, provider_name, model)` → new label
  - [x] `_resolve_review_provider_name()`, `_resolve_review_provider()`, `_resolve_review_model()` — moved here
  - [x] Standalone helpers: `_provider_model()`, `_provider_account()`, `format_provider_display()`
- [x] **Removed from `coach_player.py`** (~150 lines):
  - [x] `_provider_name_for_role()`
  - [x] `_provider_for_role()`
  - [x] `_resolve_review_provider_name()`
  - [x] `_resolve_review_provider()`
  - [x] `_resolve_review_model()`
  - [x] `_build_role_display()`
  - [x] `_format_provider_display()`
  - [x] `_check_role_ready()` — replaced by `RoleRouter.check_roles_ready()`
- [x] **Update `batch_executor.py`** — imports `RoleRouter` directly, constructor accepts `router: RoleRouter`
- [x] Write `tests/test_role_router.py`
- [x] **Verify:** existing `test_coach_player.py` passes + new unit tests

---

## Phase 3: CoachPlayerSession Decomposition ✅

> Break god object into focused components. 1,623 → ~600 LOC (currently 1,177 after Phase 3).
> Depends on: Phase 2.

- [x] **3A: Extract `AgentTurnRunner` → `src/turn_runner.py`**
  - [x] Move `_run_turn()` (~156 lines) — provider call, streaming, timeout
  - [x] Move `_run_with_continuation()` (~57 lines) — continuation retry
  - [x] Move `_build_continuation_retry_prompt()` (~41 lines)
  - [x] `coach_player.py::_run_turn()` and `_run_with_continuation()` now delegate to `AgentTurnRunner`
  - [x] Write `tests/test_turn_runner.py`
- [x] **3B: Extract `ProcessGuard` → `src/process_guard.py`**
  - [x] Move `_snapshot_pids()` + `_kill_new_processes()` — now `ProcessGuard.snapshot_pids()` / `kill_new_processes()`
  - [x] `coach_player.py::_snapshot_pids()` and `_kill_new_processes()` delegate to `self._process_guard`
- [x] **3C: Slim `CoachPlayerSession`**
  - [x] Keeps: `__init__`, `run()`, `_run_phase_zero()`, recording/reporting
  - [x] Delegates to: `RoleRouter`, `AgentTurnRunner`, `ProcessGuard`
- [x] **Verify:** all tests pass (test_bugs_found.py updated for delegation pattern)

> **Rollback:** if a phase causes test regression, revert the extracted file and restore the original methods before debugging. Never debug a half-extracted state.

---

## Phase 4: Message Normalization ✅

> Single entry point for all message format conversion (replaces 5+ scattered paths).
> Depends on: Phase 3.

- [x] **Extend `src/providers/message_adapter.py`**
  - [x] Add `normalize_message(msg) -> AdaptedMessage | None`
  - [x] Handles: SDK objects, raw dicts (claude CLI), raw dicts (codex JSONL), bare strings, `AdaptedMessage` passthrough
- [x] **Simplify callers:**
  - [x] `debugger.py::_extract_text()` → uses `normalize_message()`
  - [x] `feedback.py::_extract_text_from_message()` → uses `normalize_message()`
- [x] Write `tests/test_message_normalization.py`

---

## Phase 5: Debugger Bug State Machine + Context Caching

> Explicit bug transitions + performance boost (~200 LLM calls/iteration, many redundant file reads).
> Depends on: Phase 1.

- [x] **5A: Bug status state machine**
  - [x] Add `DebuggerError` to `src/errors.py` (deferred from Phase 1)
  - [x] Add `BugStatus(str, Enum)` to `debugger_bugs.py`
  - [x] Add `_VALID_TRANSITIONS` dict
  - [x] Add `transition_bug(bug, new_status)` — raises `DebuggerError` on invalid
  - [x] Replace 3 mutation sites in `debugger.py` with `transition_bug()` calls
  - [x] Write `tests/test_bug_state_machine.py`
- [ ] **5B: Context caching**
  - [ ] Add `ContextCache` to `debugger_context.py` (currently 578 lines, purely functional) — keyed by `(path, mtime)`
    - ⚠️ `debugger_context.py` is already large and purely functional; consider `src/debugger_cache.py` if the class grows beyond ~80 lines
  - [ ] Cache file content + AST symbol index between iterations
  - [ ] Invalidate on fixer runs (files change)
  - [ ] Pass cache to `build_context()` and `plan_file_chunks()`
- [ ] **Verify:** all tests pass, time debugger run before/after
  - ⚠️ "30-50% fewer file reads" is an unverified estimate — benchmark first, then set a target

---

## Phase 6: Silent Error Audit + Subprocess Hardening

> No more swallowed exceptions; shared subprocess management.
> Depends on: Phase 1 (for typed exceptions). Phase 4 is not a hard prerequisite — 6A can run any time after Phase 1; 6B is independent of Phase 4.

- [x] **6A: Narrow all bare `except Exception:` instances**
  - [x] `debugger.py::_collect_turn_text()` retry loop → catches `ProviderError`
  - [x] `coach_player.py::_check_provider_ready_without_cache()` → catches `ProviderError`
  - [x] `turn_runner.py::run_with_continuation()` catches `router.provider_for()` error → `ProviderError`
  - [x] `orchestrator.py` — 4 bare `except Exception: pass` guards → `StateTransitionError`
  - [x] `context_manager.py::_compact_codex_context()` → added warning log before returning `""`
- [ ] **6B: Create `src/providers/subprocess_runner.py`**
  - [ ] `run_subprocess_jsonl(cmd, working_dir, env, timeout_s)` → `AsyncIterator[dict]`
  - [ ] Consolidates: process creation, cleanup, buffer limits, JSON parse errors, timeout
  - [ ] Migrate `claude_native.py` → use `subprocess_runner`
  - [ ] Migrate `codex.py` → use `subprocess_runner`
  - [ ] Migrate `opencode.py` → use `subprocess_runner`
  - *(zai.py excluded — uses Claude Agent SDK, not subprocess JSONL)*
  - [ ] Write `tests/test_subprocess_runner.py`
- [ ] **Verify:** all tests pass, manual smoke with each provider type

---

## Dependency Graph

```
Phase 1 (errors + constants) ─── no deps
   |
   |---> Phase 2 (role router) ✅ ---> Phase 3 (session decompose) ✅
   |                                            |
   |                                            +---> Phase 4 (message normalization)
   |
   |---> Phase 5 (debugger state + cache)
   |
   |---> Phase 6A (exception narrowing)   ← requires Ph1 only; run after Ph3 for accurate file refs
   |                                        Ph4 is NOT a prerequisite for 6A
   +---> Phase 6B (subprocess runner) ─── independent of Ph4
```

---

## New Files

| File | Phase | Status | Purpose |
|------|-------|--------|---------|
| `src/errors.py` | 1 | ✅ Done | Exception hierarchy (11 classes) |
| `src/constants.py` | 1 | ✅ Done | Centralized magic numbers |
| `src/role_router.py` | 2 | ✅ Done | Role → provider mapping (250 lines) |
| `src/turn_runner.py` | 3 | ✅ Done | Single agent turn execution |
| `src/process_guard.py` | 3 | ✅ Done | Subprocess lifecycle management |
| `src/providers/subprocess_runner.py` | 6 | ❌ Not started | Shared JSONL subprocess helper |

## Test Coverage

| Test File | Phase | Status |
|-----------|-------|--------|
| `tests/test_errors.py` | 1 | ✅ Written |
| `tests/test_role_router.py` | 2 | ✅ Written |
| `tests/test_turn_runner.py` | 3 | ✅ Written |
| `tests/test_message_normalization.py` | 4 | ✅ Written |
| `tests/test_bug_state_machine.py` | 5 | ✅ Written |
| `tests/test_subprocess_runner.py` | 6 | ❌ Not written (Phase 6B not started) |

> Test strategy: each test file should cover happy path + invalid input + edge cases for its module. No mocking of the database-level session state — use real `Config` objects where possible.

---

## Scope

- No feature changes — only structural improvements
- No new pip dependencies
- No `menu.py` refactor (self-contained, working)
- No structural refactor of `orchestrator.py`/`duel.py`/`state.py` (separate subsystem) — Phase 6A exception narrowing in `orchestrator.py` is the only permitted touch, and only at the 4 bare `except Exception: pass` sites
- No debugger rewrite (separate project)
- Z.AI (GLM-5.1) provider already added: `src/providers/zai.py` — uses Claude Agent SDK, not subprocess JSONL; excluded from Phase 6B subprocess_runner migration
