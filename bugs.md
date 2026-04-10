# Debugger Report — Iteration 3

| Category | Count |
|----------|-------|
| Open (unverified) | 0 |
| Confirmed | 29 |
| Fixed | 50 |
| False positive / invalid | 0 |
| **Total** | **79** |

## Bug List

| ID | File | Line | Status | Description |
|----|------|------|--------|-------------|
| 1 | `run_slug_collection.py` | 125 | ✔ fixed | - High: The slug collector is extracting the wrong part of the URL for most CDX  |
| 2 | `run_slug_collection.py` | 166 | ✔ fixed | - High: The slug collector is extracting the wrong part of the URL for most CDX  |
| 3 | `run_slug_collection.py` | 207 | ✔ fixed | - High: The slug collector is extracting the wrong part of the URL for most CDX  |
| 4 | `run_slug_collection.py` | 242 | ✔ fixed | - High: The slug collector is extracting the wrong part of the URL for most CDX  |
| 5 | `run_slug_collection.py` | 277 | ✔ fixed | - High: The slug collector is extracting the wrong part of the URL for most CDX  |
| 6 | `debugger.py` | 184 | ✔ fixed | - High: The debugger marks bugs as fixed even when nothing proved they were fixe |
| 7 | `debugger.py` | 289 | ✔ fixed | - High: The debugger marks bugs as fixed even when nothing proved they were fixe |
| 8 | `debugger.py` | 406 | ✔ fixed | - High: The debugger marks bugs as fixed even when nothing proved they were fixe |
| 9 | `batch_executor.py` | 145 | ✔ fixed | - Medium: The batch completion-report gate is easy to bypass. src/batch_executor |
| 10 | `batch_executor.py` | 580 | ✔ fixed | - Medium: The batch completion-report gate is easy to bypass. src/batch_executor |
| 11 | `batch_validator.py` | 97 | ✔ fixed | - Medium: Duplicate detection in the batch validator does not match its stated b |
| 12 | `batch_validator.py` | 118 | ✔ fixed | - Medium: Duplicate detection in the batch validator does not match its stated b |
| 13 | `bug_detector.py` | 117 | ✔ fixed | - Medium: Missing developer tools are counted as code bugs in `BugDetector`. src |
| 14 | `bug_detector.py` | 143 | ✔ fixed | - Medium: Missing developer tools are counted as code bugs in `BugDetector`. src |
| 15 | `bug_detector.py` | 179 | ✔ fixed | - Medium: Missing developer tools are counted as code bugs in `BugDetector`. src |
| 16 | `resume_tailor.py` | 100 | ✔ fixed | - Medium: `ResumeTailor.tailor()` ignores its `strategy` argument, so all strate |
| 17 | `resume_tailor.py` | 57 | ✔ fixed | - Medium: `ResumeTailor.tailor()` ignores its `strategy` argument, so all strate |
| 18 | `g3.py` | 22 | ✔ fixed | - Medium: `g3.py` silently drops the `debug` subcommand. g3.py#L22 reuses the sh |
| 19 | `cli_entry.py` | 254 | ✔ fixed | - Medium: `g3.py` silently drops the `debug` subcommand. g3.py#L22 reuses the sh |
| 25 | `run_slug_collection.py` | 312 | ✔ fixed | 1. High: The subdomain-based slug collectors are extracting the wrong part of th |
| 26 | `batch_executor.py` | 120 | ✔ fixed | 2. High: Batch execution can accept a fake completion and mark the whole phase d |
| 28 | `debugger.py` | 321 | ✔ fixed | 3. High: The debugger can declare victory when the provider is just failing. deb |
| 29 | `debugger.py` | 164 | ✔ fixed | 3. High: The debugger can declare victory when the provider is just failing. deb |
| 30 | `debugger.py` | 502 | ✔ fixed | 4. High: The tester/fixer pipeline upgrades uncertainty into “fixed bugs.” debug |
| 31 | `debugger.py` | 183 | ✔ fixed | 4. High: The tester/fixer pipeline upgrades uncertainty into “fixed bugs.” debug |
| 34 | `config.py` | 302 | ✔ fixed | 7. Medium: Config loading assumes every YAML file has a mapping at the root. con |
| 36 | `resume_tailor.py` | 81 | ✔ fixed | 8. Medium: `ResumeTailor` does not honor its public API. resume_tailor.py ignore |
| 37 | `codex.py` | 138 | ✔ fixed | 1. Non-zero Codex/OpenCode subprocess exits are treated as successful runs. In s |
| 38 | `opencode.py` | 107 | ✔ fixed | 1. Non-zero Codex/OpenCode subprocess exits are treated as successful runs. In s |
| 39 | `duel.py` | 24 | ✔ fixed | 1. Non-zero Codex/OpenCode subprocess exits are treated as successful runs. In s |
| 40 | `judge.py` | 35 | ✔ fixed | 2. The judge can select and promote a loser even when both agents failed. In src |
| 41 | `judge.py` | 64 | ✔ fixed | 2. The judge can select and promote a loser even when both agents failed. In src |
| 42 | `duel.py` | 102 | ✔ fixed | 3. A single agent timeout aborts the whole duel instead of being scored as that  |
| 43 | `feedback.py` | 109 | ✔ fixed | 4. Verdict parsing can incorrectly approve because it concatenates all assistant |
| 44 | `feedback.py` | 75 | ✔ fixed | 4. Verdict parsing can incorrectly approve because it concatenates all assistant |
| 45 | `feedback.py` | 269 | ✔ fixed | 4. Verdict parsing can incorrectly approve because it concatenates all assistant |
| 46 | `orchestrator.py` | 158 | ✔ fixed | 5. `resume()` expects `run_id` and `final_winner` in session state, but `run()`  |
| 47 | `orchestrator.py` | 273 | ✔ fixed | 5. `resume()` expects `run_id` and `final_winner` in session state, but `run()`  |
| 48 | `orchestrator.py` | 366 | ✔ fixed | 5. `resume()` expects `run_id` and `final_winner` in session state, but `run()`  |
| 49 | `recorder.py` | 137 | ✔ fixed | 6. Feedback updates can silently drop concurrently appended history entries. `re |
| 50 | `recorder.py` | 120 | ✔ fixed | 6. Feedback updates can silently drop concurrently appended history entries. `re |
| 51 | `recorder.py` | 160 | ✔ fixed | 6. Feedback updates can silently drop concurrently appended history entries. `re |
| 52 | `debugger_context.py` | 377 | ✔ fixed | 7. `_allocate_section_budgets()` can allocate more than the total budget. In src |
| 53 | `debugger_context.py` | 383 | ✔ fixed | 7. `_allocate_section_budgets()` can allocate more than the total budget. In src |
| 54 | `orchestrator.py` | 461 | ✔ fixed | - High: src/orchestrator.py:461 deletes every non-protected file in `working_dir |
| 55 | `codex.py` | 139 | ✔ fixed | - High: src/providers/codex.py:139 and src/providers/opencode.py:108 wait for th |
| 56 | `opencode.py` | 108 | ✔ fixed | - High: src/providers/codex.py:139 and src/providers/opencode.py:108 wait for th |
| 57 | `chain.py` | 91 | ✔ fixed | - High: src/providers/chain.py:91 only falls back when `_is_rate_limit_error(exc |
| 59 | `orchestrator.py` | 299 | ✔ fixed | - Medium: src/orchestrator.py:299 unconditionally does `current_round - 1`; if a |
| 60 | `worktree.py` | 62 | ✔ fixed | - Medium: src/worktree.py:62 checks `ws/.git` with `isdir()`, but git worktrees  |
| 61 | `structured_logger.py` | 27 | ✓ confirmed | 1. JSON logging is broken in src/utils/structured_logger.py:27 and src/utils/str |
| 62 | `structured_logger.py` | 62 | ✓ confirmed | 1. JSON logging is broken in src/utils/structured_logger.py:27 and src/utils/str |
| 63 | `cost_tracker.py` | 174 | ✓ confirmed | 2. Monthly cost totals are overstated in src/utils/cost_tracker.py:174. `get_mon |
| 64 | `cost_tracker.py` | 184 | ✓ confirmed | 2. Monthly cost totals are overstated in src/utils/cost_tracker.py:174. `get_mon |
| 65 | `cost_tracker.py` | 194 | ✓ confirmed | 2. Monthly cost totals are overstated in src/utils/cost_tracker.py:174. `get_mon |
| 66 | `runtime_controls.py` | 163 | ✓ confirmed | 3. Active warnings are cleared by normal status refreshes in src/runtime_control |
| 67 | `coach_player.py` | 1419 | ✓ confirmed | 3. Active warnings are cleared by normal status refreshes in src/runtime_control |
| 68 | `runtime_controls.py` | 443 | ✓ confirmed | 4. `RuntimeControls` cannot be restarted after `stop()` in src/runtime_controls. |
| 69 | `runtime_controls.py` | 481 | ✓ confirmed | 4. `RuntimeControls` cannot be restarted after `stop()` in src/runtime_controls. |
| 70 | `runtime_controls.py` | 496 | ✓ confirmed | 4. `RuntimeControls` cannot be restarted after `stop()` in src/runtime_controls. |
| 71 | `cost_tracker.py` | 78 | ✓ confirmed | 5. A single malformed history entry can crash cost tracking startup in src/utils |
| 72 | `state.py` | 115 | ✓ confirmed | 6. Invalid persisted session states break transitions, including failure handlin |
| 73 | `config_validator.py` | 112 | ✓ confirmed | 7. The config report mislabels missing AI keys as healthy in src/utils/config_va |
| 74 | `worktree.py` | 29 | ✓ confirmed | 1. `WorktreeManager` breaks multi-round runs and retries because workspace names |
| 75 | `worktree.py` | 31 | ✓ confirmed | 1. `WorktreeManager` breaks multi-round runs and retries because workspace names |
| 76 | `worktree.py` | 101 | ✓ confirmed | 1. `WorktreeManager` breaks multi-round runs and retries because workspace names |
| 77 | `duel.py` | 113 | ✓ confirmed | 1. `WorktreeManager` breaks multi-round runs and retries because workspace names |
| 80 | `structured_logger.py` | 87 | ✓ confirmed | 2. `setup_logging(json_output=True)` is broken at first log emission. Loguru pas |
| 81 | `state.py` | 49 | ✓ confirmed | 3. The session state machine is incompatible with resume logic. `Orchestrator.re |
| 82 | `state.py` | 54 | ✓ confirmed | 3. The session state machine is incompatible with resume logic. `Orchestrator.re |
| 83 | `state.py` | 71 | ✓ confirmed | 3. The session state machine is incompatible with resume logic. `Orchestrator.re |
| 84 | `state.py` | 109 | ✓ confirmed | 3. The session state machine is incompatible with resume logic. `Orchestrator.re |
| 85 | `orchestrator.py` | 321 | ✓ confirmed | 3. The session state machine is incompatible with resume logic. `Orchestrator.re |
| 86 | `cost_tracker.py` | 88 | ✓ confirmed | 4. `CostTracker._load_history()` still crashes on common forms of corrupted hist |
| 87 | `cost_tracker.py` | 93 | ✓ confirmed | 4. `CostTracker._load_history()` still crashes on common forms of corrupted hist |
| 88 | `cost_tracker.py` | 101 | ✓ confirmed | 4. `CostTracker._load_history()` still crashes on common forms of corrupted hist |
| 90 | `cost_tracker.py` | 179 | ✓ confirmed | 5. `get_monthly_total()` does not actually return the total for a specific month |
| 91 | `runtime_controls.py` | 444 | ✓ confirmed | 6. `RuntimeControls` cannot be cleanly restarted after `stop()`. The listener th |
| 92 | `runtime_controls.py` | 488 | ✓ confirmed | 6. `RuntimeControls` cannot be cleanly restarted after `stop()`. The listener th |
