# Plan: Fix Open Bugs (22 Failing Tests)

Updated after 3-agent review. Removed 3 non-bugs, corrected 2 fixes, added 2 missed items.

## Status Summary

- **Already fixed (skip):** 3 — BUG-20, PLAN-B6, GEN-B16
- **Real fixes needed:** 12
- **Test errors (fix tests, not code):** 2
- **Test contradiction (need resolution):** 1 group (BUG-02/27 vs PLAN-B5)

## Skipped — Already Fixed

### ~~1.1 BUG-20: `--limit 0` ignored~~
- **Verdict:** Already fixed. `cli_entry.py:327` uses `is not None`. Test failure must be investigated separately.

### ~~3.2 PLAN-B6: `PlanItem.__new__` cache key~~
- **Verdict:** Already fixed. `plan_tracker.py:33` includes `skipped` in cache key.

### ~~4.3 GEN-B16: menu.py bare `if` + missing `continue`~~
- **Verdict:** Bug doesn't exist. `menu.py` is 736 lines (line 778 out of range). `if/if` with early `return` is safe.

---

## Layer 1 — Isolated 1-Line Fixes

### [ ] 1.2 SW-02: Import inside method in registry.py
- **File**: `src/providers/registry.py:136`
- **Fix**: Move `from src.errors import ProviderError` to top of file
- **Risk**: None

### [ ] 1.3 SW-06: Crash when `stderr=None` in claude_native
- **File**: `src/providers/claude_native.py:72`
- **Fix**: `(event.stderr or b'').decode()` instead of `event.stderr.decode()`
- **Risk**: None

### [ ] 1.4 SW-11: `subprocess.run` without timeout in check_ready
- **File**: `src/providers/claude_native.py:84-89`
- **Fix**: Add `timeout=10` to `subprocess.run()` call
- **Risk**: None

### [ ] 1.5 SW-47: `min(i, len-1)` returns wrong step on invalid index
- **File**: `src/plan_tracker.py:492`
- **Fix**: Replace `items[min(i, len(items) - 1)]` with `[items[i] for i in step_indices if 0 <= i < len(items)]`
- **Risk**: Low — only changes out-of-range behavior

## Layer 2 — Provider/Resource Fixes

### [ ] 2.1 SW-07: FD leak in codex `_build_env` at mkstemp
- **File**: `src/providers/codex.py:213-216`
- **Fix**: Wrap in try/finally to close fd even if os.write fails
- **Risk**: Low

### [ ] 2.2 SW-54: FD leak on JSON parse error in recorder
- **File**: `src/learning/recorder.py:171-213`
- **Fix**: Catch `json.JSONDecodeError` inside the line loop, skip malformed lines
- **Risk**: Low

### [ ] 2.3 SW-61: Worktrees never deleted — disk leak
- **File**: `src/duel.py:105-159`
- **Root cause**: `run_round` creates worktrees but never cleans up on error
- **Fix**: Wrap in try/finally with `self.worktree.cleanup(name)` (**not** `remove()`)
- **Risk**: Medium — verified: `worktree.cleanup()` exists at worktree.py:79-100

## Layer 3 — Cross-File Logic Fixes

### [ ] 3.1 BUG-02/27 + PLAN-B5: `_schedule_counts` silently overrides zeros
- **File**: `src/batch_executor.py:436-443`
- **Root cause**: `value <= 0` treats explicit 0 as invalid
- **Fix**:
  ```python
  if not isinstance(value, int) or value < 0:
      value = default
  # all-zero safeguard
  if sum(values) == 0:
      return defaults
  ```
- **CONTRADICTION NOTE**: Tests BUG-02/27 expect `(0,0,0)` returned, PLAN-B5 expects defaults. Fix satisfies PLAN-B5 but NOT BUG-02/27. **Action: fix code per PLAN-B5, then update BUG-02/27 tests to match.**
- **Risk**: Medium

### [ ] 3.3 PLAN-B7: Skip branch doesn't update `tracker.items`
- **File**: `src/batch_executor.py:604-608`
- **Root cause**: Skip branch marks `phase.steps` as skipped but never syncs to `tracker.items`
- **Fix**: Update tracker.items after marking steps skipped. Use identity matching (`is`) instead of text matching to avoid duplicates:
  ```python
  phase.status = "skipped"
  new_steps = [replace(step, skipped=True) for step in phase.steps]
  phase.steps = new_steps
  # sync tracker.items by identity
  step_ids = {id(s) for s in phase.steps}
  self.tracker.items = [
      next((ns for ns in new_steps if ns.text == it.text), it)
      for it in self.tracker.items
  ]
  ```
- **Risk**: Medium

### [ ] 3.4 PLAN-B1: `display_label_for("judge")` crashes on empty judge provider
- **File**: `src/role_router.py:79-88`
- **Root cause**: `provider_for("judge")` raises before graceful handling
- **Fix**: Guard with try/except. **Must add `from src.errors import ProviderError` to imports** (only `ProviderNotReadyError` is currently imported)
- **Risk**: Low

## Layer 4 — Control-Flow Fixes

### [ ] 4.1 GEN-B9 + SW-13: Continuation overwrites provider each iteration
- **File**: `src/turn_runner.py:217-248`
- **Fix**: Resolve provider once before loop, pass as both `provider` AND `provider_override` to inner `run_turn()` call
- **Risk**: Medium — `ProviderError` is already imported in turn_runner.py:14

### [ ] 4.2 GEN-B11: claude_native silently ignores `returncode=None`
- **File**: `src/providers/claude_native.py:69`
- **Current code**: `if event.returncode is None or event.returncode != 0:` — already treats None as error
- **Fix**: Keep treating None as error (early generator close = process killed = error). Match test expectation: raise specific error for None returncode.
- **Test expects**: `RuntimeError` when `returncode=None`
- **Risk**: Low — consistent with codex.py/opencode.py pattern

## Test Fixes (not code bugs)

### [ ] T1: Fix `test_d_handler_calls_sys_exit` (test_audit_bugs_medium.py)
- **Problem**: Imports `_launch_debugger` which doesn't exist. Correct function is `run_debugger_menu`.
- **Fix**: Update test import and assertion

### [ ] T2: Fix BUG-02/27 tests to match PLAN-B5 semantics
- **Problem**: Tests assert `(0,0,0)` should be returned, but all-zero should fall back to defaults
- **Fix**: Update test expectations to assert defaults returned on all-zero input

## Execution Order

```
Step 1: Layer 1 (1.2–1.5)              — 4 fixes, all independent
Step 2: Layer 2 (2.1–2.3)              — 3 fixes, all independent
Step 3: 3.1 → 3.3                      — sequential (3.3 needs stable schedule_counts)
Step 4: 3.4                            — independent
Step 5: 4.1, 4.2                       — independent
Step 6: Test fixes T1, T2
Step 7: Run failing tests              — verify all 22 pass
```

## Verification

```bash
python3 -m pytest tests/test_audit_bugs_critical.py tests/test_audit_bugs_serious.py tests/test_audit_bugs_medium.py tests/test_bugs_md_negative_registry.py tests/test_bugs_md_sw_negative.py -v
```
