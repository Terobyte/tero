# Debugger Report — Iteration 12

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
| 1 | `src/debugger_contracts.py` | 309-391 | · open | `batch_files` bin-packs by raw source length (`len(source)`) but `build_batch_prompt` adds unaccounted overhead: line numbers (~6 chars/line), AST summaries (100-500 chars/file), headers/separators (~50 chars/file). A batch packed to 14K raw chars can produce a ~19K actual prompt, 31% over `PROMPT_BUDGET`. `build_contract_prompt` handles this correctly with explicit overhead accounting; `batch_files` / `build_batch_prompt` do not. |
| 2 | `src/debugger_contracts.py` | 196-254 | · open | `build_batch_prompt` has no truncation mechanism at all. If a single file in a multi-file batch is large, the numbered source alone can exceed the budget with no fallback. Compare `build_contract_prompt` (line 183-187) which truncates with a `... [truncated]` marker. |
| 3 | `src/debugger_bugs.py` | 112 | · open | `_extract_prose_fallback` regex `\b(\w+\.py)[#:]` only matches filenames, not relative paths. If the LLM writes `src/debugger.py:42` in prose, the regex captures `debugger.py:42` and loses the `src/` prefix. The resulting `BugEntry.file = "debugger.py"` won't match any real file, making the bug unactionable. |
| 4 | `src/debugger_graph.py` | 460-472 | · open | `parse_file` alias tracking handles `ast.Assign` but not `ast.AnnAssign` (type-annotated assignments). Code like `from src.config import Config; my_config: Config = Config()` won't register `my_config` in `import_map`, so calls like `my_config.method()` are missed from `external_calls`. |
| 5 | `src/debugger_bugs.py` | 223-234 | · open | `merge_bugs` builds dedup sets from all bugs where `status != "fixed"`, including `false_positive` and `invalid_test`. Once a bug is classified as false positive, no future iteration can report a new bug at the same `(file, line)` — even if the classification was wrong or the code changed. |
| 6 | `src/debugger.py` | 149, 173 | · open | `run()` builds the dependency graph twice on the first iteration: once at line 149 for the startup banner, and again at line 173 for Phase 0. No source changes occur between these calls. The initial build should be reused or Phase 0 should skip the rebuild on iteration 1. |
| 7 | `src/debugger_edges.py` + `src/debugger_intra.py` | 185, 25 | · open | `_number_source` and `_format_contract` are duplicated verbatim across both modules (~35 lines each). A fix applied to one copy must be mirrored in the other or they diverge silently. These should be extracted to a shared module (e.g. `debugger_render.py`). |
