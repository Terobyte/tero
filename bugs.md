# Debugger Report — Iteration 2

| Category | Count |
|----------|-------|
| Open (unverified) | 0 |
| Confirmed | 23 |
| Fixed | 27 |
| False positive / invalid | 0 |
| **Total** | **50** |

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
| 37 | `codex.py` | 138 | ✓ confirmed | 1. Non-zero Codex/OpenCode subprocess exits are treated as successful runs. In s |
| 38 | `opencode.py` | 107 | ✓ confirmed | 1. Non-zero Codex/OpenCode subprocess exits are treated as successful runs. In s |
| 39 | `duel.py` | 24 | ✓ confirmed | 1. Non-zero Codex/OpenCode subprocess exits are treated as successful runs. In s |
| 40 | `judge.py` | 35 | ✓ confirmed | 2. The judge can select and promote a loser even when both agents failed. In src |
| 41 | `judge.py` | 64 | ✓ confirmed | 2. The judge can select and promote a loser even when both agents failed. In src |
| 42 | `duel.py` | 102 | ✓ confirmed | 3. A single agent timeout aborts the whole duel instead of being scored as that  |
| 43 | `feedback.py` | 109 | ✓ confirmed | 4. Verdict parsing can incorrectly approve because it concatenates all assistant |
| 44 | `feedback.py` | 75 | ✓ confirmed | 4. Verdict parsing can incorrectly approve because it concatenates all assistant |
| 45 | `feedback.py` | 269 | ✓ confirmed | 4. Verdict parsing can incorrectly approve because it concatenates all assistant |
| 46 | `orchestrator.py` | 158 | ✓ confirmed | 5. `resume()` expects `run_id` and `final_winner` in session state, but `run()`  |
| 47 | `orchestrator.py` | 273 | ✓ confirmed | 5. `resume()` expects `run_id` and `final_winner` in session state, but `run()`  |
| 48 | `orchestrator.py` | 366 | ✓ confirmed | 5. `resume()` expects `run_id` and `final_winner` in session state, but `run()`  |
| 49 | `recorder.py` | 137 | ✓ confirmed | 6. Feedback updates can silently drop concurrently appended history entries. `re |
| 50 | `recorder.py` | 120 | ✓ confirmed | 6. Feedback updates can silently drop concurrently appended history entries. `re |
| 51 | `recorder.py` | 160 | ✓ confirmed | 6. Feedback updates can silently drop concurrently appended history entries. `re |
| 52 | `debugger_context.py` | 377 | ✓ confirmed | 7. `_allocate_section_budgets()` can allocate more than the total budget. In src |
| 53 | `debugger_context.py` | 383 | ✓ confirmed | 7. `_allocate_section_budgets()` can allocate more than the total budget. In src |
| 54 | `orchestrator.py` | 461 | ✓ confirmed | - High: src/orchestrator.py:461 deletes every non-protected file in `working_dir |
| 55 | `codex.py` | 139 | ✓ confirmed | - High: src/providers/codex.py:139 and src/providers/opencode.py:108 wait for th |
| 56 | `opencode.py` | 108 | ✓ confirmed | - High: src/providers/codex.py:139 and src/providers/opencode.py:108 wait for th |
| 57 | `chain.py` | 91 | ✓ confirmed | - High: src/providers/chain.py:91 only falls back when `_is_rate_limit_error(exc |
| 59 | `orchestrator.py` | 299 | ✓ confirmed | - Medium: src/orchestrator.py:299 unconditionally does `current_round - 1`; if a |
| 60 | `worktree.py` | 62 | ✓ confirmed | - Medium: src/worktree.py:62 checks `ws/.git` with `isdir()`, but git worktrees  |
