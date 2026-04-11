# Debugger Final Report

**Outcome:** VICTORY
**Date:** 2026-04-10
**Total bugs found:** 8
**Fixed:** 8
**Confirmed (unfixed):** 0
**False positives:** 0
**Open (unverified):** 0

---

| ID | File | Line | Description | Fix |
|----|------|------|-------------|-----|
| 1 | `src/batch_executor.py` | 680 | `_run_phase()` uses `getattr(type(verdict), "__name__", "") == "NoVerdict"` instead of `isinstance(verdict, NoVerdict)` | Replaced with `isinstance` check |
| 2 | `src/runtime_controls.py` | 498–514 | `start()` installs SIGWINCH handler but `stop()` never restores the previous one | Save previous handler before install; restore in `stop()` |
| 3 | `src/debugger.py` | 403 | `_git_commit()` stages entire worktree with `git add -A` | Use `git add -- '*.py'` + explicit output files only |
| 4 | `src/config.py` | 443–445 | Unsafe global keys (e.g. `batch_mode`) in merged config bypass `_filter_global_defaults()` | Added `_UNSAFE_GLOBAL_DEFAULT_KEYS` check in `project.items()` loop |
| 5 | `src/debugger.py` | 140–160 | `run()` referenced removed chunk state (`self._chunks`, `_next_chunk()`) | Rewrote `run()` as 6-phase graph-aware pipeline |
| 6 | `src/debugger.py` | 222, 276, 338 | `_run_player/tester/fixer()` called undefined `build_context()` | Replaced with `build_context_from_graph()` |
| 7 | `src/debugger_contracts.py` | 510–560 | Failed LLM extraction cached as empty `FileContract` with fresh hash; never retried | Only stamp `source_hash` when contract has real content |
| 8 | `src/debugger_contracts.py` | 326–385 | `batch_files()` budgets raw source length, not actual prompt length after line numbering | Account for `_TRUNCATION_MARKER` in overhead; truncate in `build_contract_prompt()` |
