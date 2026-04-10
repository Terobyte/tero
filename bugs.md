# Debugger Report — Iteration 1

| Category | Count |
|----------|-------|
| Open (unverified) | 28 |
| Confirmed | 0 |
| Fixed | 0 |
| False positive / invalid | 0 |
| **Total** | **28** |

## Bug List

| ID | File | Line | Status | Description |
|----|------|------|--------|-------------|
| 1 | `run_slug_collection.py` | 125 | · open | - High: the slug extractor is parsing the path after the domain instead of the s |
| 2 | `run_slug_collection.py` | 166 | · open | - High: the slug extractor is parsing the path after the domain instead of the s |
| 3 | `run_slug_collection.py` | 207 | · open | - High: the slug extractor is parsing the path after the domain instead of the s |
| 4 | `run_slug_collection.py` | 242 | · open | - High: the slug extractor is parsing the path after the domain instead of the s |
| 5 | `run_slug_collection.py` | 277 | · open | - High: the slug extractor is parsing the path after the domain instead of the s |
| 6 | `run_slug_collection.py` | 312 | · open | - High: the slug extractor is parsing the path after the domain instead of the s |
| 7 | `debugger.py` | 183 | · open | - High: the debugger marks every confirmed bug as fixed immediately after `_run_ |
| 8 | `debugger.py` | 289 | · open | - High: the debugger marks every confirmed bug as fixed immediately after `_run_ |
| 9 | `debugger.py` | 321 | · open | - High: tester failures silently become confirmations. `_collect_text()` swallow |
| 10 | `debugger.py` | 456 | · open | - High: tester failures silently become confirmations. `_collect_text()` swallow |
| 11 | `g3.py` | 22 | · open | - Medium: `g3.py` advertises the shared parser, but its `main()` only dispatches |
| 12 | `cli_entry.py` | 389 | · open | - Medium: `g3.py` advertises the shared parser, but its `main()` only dispatches |
| 13 | `a.py` | 10 | · open | - Medium: the debugger collapses distinct bugs that share a file and line number |
| 14 | `debugger_bugs.py` | 151 | · open | - Medium: the debugger collapses distinct bugs that share a file and line number |
| 15 | `debugger_bugs.py` | 176 | · open | - Medium: the debugger collapses distinct bugs that share a file and line number |
| 17 | `debugger.py` | 502 | · open | 1. High: src/debugger.py:183 marks every `confirmed` bug as `fixed` immediately  |
| 18 | `batch_executor.py` | 120 | · open | 2. High: src/batch_executor.py:120 and src/batch_executor.py:145 accept a pasted |
| 19 | `batch_executor.py` | 145 | · open | 2. High: src/batch_executor.py:120 and src/batch_executor.py:145 accept a pasted |
| 21 | `run_slug_collection.py` | 170 | · open | 3. High: scripts/run_slug_collection.py:125 always extracts the first path segme |
| 22 | `run_slug_collection.py` | 283 | · open | 3. High: scripts/run_slug_collection.py:125 always extracts the first path segme |
| 23 | `debugger.py` | 437 | · open | 4. Medium: src/debugger.py:437 stages the entire repository with `git add -A` on |
| 24 | `bug_detector.py` | 121 | · open | 5. Medium: src/bug_detector.py:121 says it will use “flake8 or fallback to pyfla |
| 25 | `bug_detector.py` | 143 | · open | 5. Medium: src/bug_detector.py:121 says it will use “flake8 or fallback to pyfla |
| 26 | `config.py` | 302 | · open | 6. Medium: src/config.py:302 can return any YAML root object, but callers immedi |
| 27 | `config.py` | 344 | · open | 6. Medium: src/config.py:302 can return any YAML root object, but callers immedi |
| 28 | `config.py` | 351 | · open | 6. Medium: src/config.py:302 can return any YAML root object, but callers immedi |
| 29 | `batch_validator.py` | 97 | · open | 7. Medium: src/applier/universal_screening/batch_validator.py:97 stores duplicat |
| 30 | `batch_validator.py` | 118 | · open | 7. Medium: src/applier/universal_screening/batch_validator.py:97 stores duplicat |
