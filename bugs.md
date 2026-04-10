# Debugger Report — Manual Audit

| Category | Count |
|----------|-------|
| Open (unverified) | 7 |
| Confirmed | 0 |
| Fixed | 0 |
| False positive / invalid | 0 |
| **Total** | **7** |

## Bug List

| ID | File | Line | Status | Description |
|----|------|------|--------|-------------|
| 1 | `src/orchestrator.py` | 251 | · open | `round_num` unbound in exception handler — if `session.create()`, `classify_task()`, or `Path(plan_file).read_text()` raises before `round_num = 0` (line 116), the `except` block crashes with `UnboundLocalError` instead of returning a proper `OrchestratorResult` |
| 2 | `src/providers/claude_native.py` | 77–94 | · open | **Pipe deadlock** — stdout read line-by-line while stderr is not drained until after `proc.wait()`. If Claude CLI writes >64 KB to stderr, the pipe buffer fills, the process blocks on stderr write, and can no longer write to stdout → deadlock. The codex provider fixed this exact issue with a background `stderr_task`. |
| 3 | `src/batch_executor.py` | 680 | · open | Fragile `NoVerdict` detection: `getattr(type(verdict), "__name__", "") == "NoVerdict"` instead of `isinstance(verdict, NoVerdict)`. Breaks on subclasses, mocks, or wrapped objects. `NoVerdict` is importable from `src.feedback` but not imported here. |
| 4 | `src/runtime_controls.py` | 498 | · open | **SIGWINCH handler memory leak** — `signal.signal(SIGWINCH, lambda *_: self._status_bar._render())` captures `self` in a closure. The handler is never restored in `stop()`, so the `RuntimeControls` instance is never garbage-collected. Repeated start/stop cycles leak instances. |
| 5 | `src/debugger.py` | 489 | · open | `git add -A` stages ALL working directory changes — including `bugs.md`, `bugs_final.md`, and any other modified/untracked files. A targeted `git add` on only the fix-related files would be safer. |
| 6 | `src/config.py` | 437 | · open | **Global unsafe-key bypass** — `_filter_global_defaults()` only filters keys in the `defaults:` section of `~/.g3/config.yaml`. Top-level keys like `batch_mode: true` bypass the filter via the unknown-key rescue loop at line 435–437 and silently override per-project settings. |
| 7 | `src/debugger.py` | 408 | · open | **Dead code** — `return _CollectedTextResult(text="", completed=False)` is unreachable: the for-loop always returns either on success (line 394) or after exhausting retries (line 397). Harmless but misleading. |
