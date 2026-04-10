# Graph-Aware Contract Analysis Debugger — Complete Design Spec

**Date:** 2026-04-10
**Status:** Approved
**Scope:** Replace chunk-based debugger with graph-aware cross-file analysis pipeline

---

## 1. Problem

The current debugger analyzes files in isolated 200K-char chunks. Cross-file bugs are **invisible**:

- Contract violations (caller passes wrong args to callee)
- Signature mismatches (function signature changed, callers not updated)
- Return value mishandling (caller ignores None, doesn't catch exceptions)
- Side-effect ordering (caller assumes state set by callee that may not hold)
- Type mismatches (caller passes wrong type to callee)

These are the bugs that ship to production — they survive all per-file analysis because neither file is wrong in isolation.

### Why chunks fail

The chunk-based approach has a fundamental limitation: it packs 5-10 files into a 200K-char context window. The LLM's focus is diluted across multiple files, and cross-file relationships are only visible when both files happen to land in the same chunk. For a 30-file project, the probability that a specific caller-callee pair shares a chunk is low.

---

## 2. Solution: 6-Phase Pipeline with Full Outer Loop

Replace the flat chunk-based loop with a phased pipeline. The **entire pipeline repeats** until victory or limit reached.

```
┌─────────────────────────────────────────────────────────┐
│                    OUTER LOOP                           │
│   (repeats until victory OR limit reached)              │
│                                                         │
│   Phase 0: Build Dependency Graph          (AST, 0 LLM)│
│       │                                                 │
│   Phase 1: Extract Contracts          (LLM, cached)     │
│       │                                                 │
│   Phase 2: Edge Analysis              (LLM, O(N) calls) │
│       │                                                 │
│   Phase 3: Deep Dive                  (LLM, medium only)│
│       │                                                 │
│   Phase 4: Intra-File Analysis        (LLM, per-file)   │
│       │                                                 │
│   Phase 5: Test & Fix                 (LLM + pytest)    │
│       │                                                 │
│   ← loop back to Phase 0 ──────────────────────────────│
└─────────────────────────────────────────────────────────┘
```

### Why full outer loop

After Phase 5 (fixer), files change. This means:
- Dependency graph may change (new imports added, old ones removed)
- Contracts become stale for modified files (sha256 mismatch)
- Edge analysis needs to re-check modified caller-callee pairs
- Intra-file analysis needs to verify fixes didn't introduce new bugs

Phase 0 is instant (pure AST). Phase 1 is near-instant on re-runs (cache — only modified files re-extracted). So the cost of looping is primarily Phase 2-4, which only re-analyzes what changed.

### Iteration semantics

One pipeline pass = one iteration. `--limit 3` means 3 full pipeline passes. Default `debug_limit_value: int = 3`. Time-based stopping (`--time`) still works as before.

### Victory condition

Victory = Phase 2 found 0 high/medium findings AND Phase 4 found 0 bugs for `debug_victory_threshold` consecutive iterations. Both edge and intra must be clean.

---

## 3. Module Map

### New and changed files

```
src/
├── debugger.py              REWRITE  — pipeline orchestrator, ~300 lines
├── debugger_graph.py        NEW      — AST graph + SCC, ~250 lines
├── debugger_contracts.py    NEW      — contract extraction + cache, ~200 lines
├── debugger_edges.py        NEW      — edge analysis + deep dive, ~250 lines
├── debugger_intra.py        NEW      — per-file analysis with contracts, ~150 lines
├── debugger_render.py       NEW      — context rendering for tester/fixer, ~200 lines
├── debugger_prompts.py      MODIFY   — add 4 new prompts, keep all existing
├── debugger_bugs.py         KEEP     — unchanged
├── debugger_context.py      DELETE   — replaced by graph + render
├── config.py                MODIFY   — add 4 new fields
│
tests/
├── test_debugger_graph.py       NEW
├── test_debugger_contracts.py   NEW
├── test_debugger_edges.py       NEW
├── test_debugger_intra.py       NEW
├── test_debugger_render.py      NEW
```

### Dependency graph between modules

```
debugger.py
├── debugger_graph.py        (build_dependency_graph, DependencyGraph)
├── debugger_contracts.py    (extract_contracts, FileContract)
├── debugger_edges.py        (analyze_edges, run_deep_dives, findings_to_bugs)
├── debugger_intra.py        (analyze_all_files)
├── debugger_render.py       (build_context_from_graph)
├── debugger_prompts.py      (TESTER_PROMPT, FIXER_PROMPT)
├── debugger_bugs.py         (BugEntry, merge_bugs, renumber_bugs, write_bugs_md, write_final_report)
└── config.py                (Config)

debugger_contracts.py
├── debugger_bugs.py         (_extract_json_from_text — reuse)
└── debugger_graph.py        (DependencyGraph, FileNode)

debugger_edges.py
├── debugger_bugs.py         (_extract_json_from_text, BugEntry)
├── debugger_contracts.py    (FileContract)
├── debugger_graph.py        (DependencyGraph)
└── debugger_prompts.py      (EDGE_ANALYSIS_PROMPT, SCC_ANALYSIS_PROMPT, DEEP_DIVE_PROMPT)

debugger_intra.py
├── debugger_bugs.py         (parse_bugs, merge_bugs)
├── debugger_contracts.py    (FileContract)
├── debugger_graph.py        (DependencyGraph)
└── debugger_prompts.py      (INTENSITY_PROMPTS)

debugger_render.py
├── debugger_contracts.py    (FileContract)
└── debugger_graph.py        (DependencyGraph, FileNode)
```

---

## 4. `src/debugger_graph.py` — Dependency Graph Module

Pure Python. **Zero LLM calls.** Uses `ast` module to parse all `.py` files, extract function signatures, class signatures, import edges, and external calls. Computes strongly connected components (SCCs) for circular dependency detection.

### Dataclasses

```python
@dataclass
class FunctionSig:
    name: str
    args: list[str]           # ["self", "x", "y"]
    returns: str | None        # annotation string or None
    lineno: int
    is_async: bool = False

@dataclass
class ClassSig:
    name: str
    methods: list[FunctionSig]
    bases: list[str]           # ["BaseClass", "Protocol"]
    lineno: int

@dataclass
class ImportEdge:
    source_file: str           # importer: "src/debugger.py"
    target_module: str         # dotted path: "src.config"
    symbols: list[str]         # ["Config", "resolve_config"]
    lineno: int
    resolved_path: str | None  # "src/config.py" or None (stdlib/third-party)

@dataclass
class ExternalCall:
    caller_func: str           # "Debugger._run_player"
    callee_module: str         # "src.debugger_context" (resolved from import)
    callee_name: str           # "build_context"
    lineno: int

@dataclass
class FileNode:
    rel_path: str
    functions: list[FunctionSig]
    classes: list[ClassSig]
    imports: list[ImportEdge]
    external_calls: list[ExternalCall]
    line_count: int
    source_hash: str           # sha256 for contract cache

@dataclass
class DependencyGraph:
    files: dict[str, FileNode]       # rel_path → FileNode
    edges: list[ImportEdge]          # all resolved cross-file edges
    sccs: list[list[str]]            # strongly connected components (2+ nodes only)

    def dependents_of(self, rel_path: str) -> list[str]:
        """Files that import this file."""
        return [e.source_file for e in self.edges if e.resolved_path == rel_path]

    def dependencies_of(self, rel_path: str) -> list[str]:
        """Files that this file imports (resolved project files only)."""
        return [
            e.resolved_path for e in self.edges
            if e.source_file == rel_path and e.resolved_path is not None
        ]
```

### Functions

#### `discover_py_files(working_dir: str) -> list[str]`

Migrated from `debugger_context.py`. Returns sorted list of relative paths.

```python
_SKIP_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".tox", ".eggs", "dist", "build",
    ".ruff_cache", ".hypothesis",
    "tests", "test",
    "docs", ".worktrees",
}
```

Walks `working_dir` recursively, returns `.py` files whose path components don't intersect `_SKIP_DIRS`.

#### `parse_file(path: Path, rel_path: str, working_dir: str) -> FileNode | None`

1. Read file, compute sha256
2. `ast.parse(source)` — if `SyntaxError`, return `None` (skip file)
3. Walk AST top-level: extract `FunctionSig`, `ClassSig`, `ImportEdge` (calling `resolve_import`)
4. Build `import_map: dict[str, str]` mapping imported names to their source module:
   - `from src.config import Config` → `{"Config": "src.config"}`
   - `import src.providers as prov` → `{"prov": "src.providers"}`
5. First-level alias tracking: walk `ast.Assign` at module level and inside `__init__` methods. If `value` is `ast.Name(id=X)` where `X` is in `import_map`, add alias: `{"provider": "src.providers"}`
6. Find `ast.Call` where `func` is `ast.Attribute(value=ast.Name(id=X))` and `X` is in `import_map` → `ExternalCall`

#### `resolve_import(module_path: str, working_dir: str, source_file: str | None = None, level: int = 0) -> str | None`

Maps dotted module path to relative file path. Returns `None` for stdlib/third-party.

**Algorithm:**

For relative imports (`level > 0`):
1. `base_dir` = directory of `source_file`
2. Go up `level - 1` directories
3. If `module_path` present: join as subpath
4. Try: `{resolved_dir}/{module_path}.py` then `{resolved_dir}/{module_path}/__init__.py`
5. If found → return relative path to working_dir. Else → `None`

For absolute imports (`level == 0`):
1. Check `sys.stdlib_module_names` (Python 3.10+) — if top-level name is stdlib → `None`
2. Try: `{working_dir}/{module_path.replace(".", "/")}.py`
3. Try: `{working_dir}/{module_path.replace(".", "/")}/__init__.py`
4. If found → return relative path. Else → `None` (third-party)

**Edge cases handled:**
- `from src.config import Config` → level=0, module="src.config" → `"src/config.py"`
- `from . import utils` → level=1, module="utils" → relative to source dir
- `from .. import base` → level=2, module="base" → up 1 level from source dir
- `from .sub.mod import X` → level=1, module="sub.mod" → `source_dir/sub/mod.py`
- `import json` → stdlib → `None`
- `import requests` → not stdlib, file not found → `None`
- `import src.providers` → `"src/providers/__init__.py"` (package)

#### `build_dependency_graph(working_dir: str) -> DependencyGraph`

1. `discover_py_files(working_dir)` → list of relative paths
2. For each file: `parse_file()` → `FileNode` (skip None results)
3. Collect all `ImportEdge` where `resolved_path is not None` → `edges`
4. Build adjacency dict: `{file: [resolved_path for edge in file.imports if resolved_path]}`
5. `find_sccs(adjacency)` → filter to only SCCs with 2+ nodes
6. Return `DependencyGraph(files, edges, sccs)`

#### `find_sccs(adjacency: dict[str, list[str]]) -> list[list[str]]`

Tarjan's algorithm. Single DFS pass, O(V+E). Returns all SCCs, caller filters to 2+ nodes.

### Tests: `tests/test_debugger_graph.py`

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_parse_file_functions` | Synthetic .py with 3 functions → correct FunctionSig (name, args, returns, lineno, is_async) |
| 2 | `test_parse_file_classes` | Class with methods + bases → correct ClassSig |
| 3 | `test_parse_file_imports` | `from src.x import Y`, `import json` → correct ImportEdge list with resolved_path |
| 4 | `test_parse_file_external_calls` | `Y.method()` where Y imported → correct ExternalCall |
| 5 | `test_parse_file_alias_tracking` | `provider = create_provider(...)` then `provider.run()` → alias resolved to ExternalCall |
| 6 | `test_parse_file_syntax_error` | Malformed .py → returns None |
| 7 | `test_resolve_import_absolute` | `"src.config"` with tmp_path → `"src/config.py"` |
| 8 | `test_resolve_import_relative_dot` | level=1, `"utils"` from `"src/foo.py"` → `"src/utils.py"` |
| 9 | `test_resolve_import_relative_dotdot` | level=2, `"base"` from `"src/sub/foo.py"` → `"src/base.py"` |
| 10 | `test_resolve_import_stdlib` | `"json"` → None |
| 11 | `test_resolve_import_third_party` | `"requests"` → None (file not found) |
| 12 | `test_resolve_import_package_init` | `"src.providers"` → `"src/providers/__init__.py"` |
| 13 | `test_find_sccs_cycle` | A→B→C→A → one SCC [A,B,C] |
| 14 | `test_find_sccs_no_cycle` | A→B→C → empty (all single-node) |
| 15 | `test_find_sccs_two_cycles` | A↔B, C↔D → two SCCs |
| 16 | `test_build_dependency_graph` | tmp_path with 4 .py files importing each other → verify files, edges, sccs |
| 17 | `test_dependency_graph_helpers` | Verify `dependents_of()` and `dependencies_of()` |

---

## 5. `src/debugger_contracts.py` — Contract Extraction Module

LLM-powered. Extracts a structured "contract" for each file — a formalized description of its public interface: exports, preconditions, postconditions, side effects, exceptions. Contracts are cached by sha256 hash.

### Dataclasses

```python
@dataclass
class ExportContract:
    name: str                    # "resolve_config"
    signature: str               # "def resolve_config(cli_args: dict) -> Config"
    preconditions: list[str]     # ["cli_args must be a dict"]
    postconditions: list[str]    # ["returns a valid Config instance"]
    side_effects: list[str]      # ["reads env variables", "reads yaml files"]
    raises: list[str]            # ["ValueError if unknown provider"]
    return_type: str             # "Config"

@dataclass
class ImportUsage:
    source_module: str           # "src.config"
    symbol: str                  # "Config"
    usage_description: str       # "used as return type and for field access"

@dataclass
class FileContract:
    rel_path: str                # "src/config.py"
    exports: list[ExportContract]
    imports_usage: list[ImportUsage]
    invariants: list[str]        # module-level constraints
    source_hash: str             # sha256 for cache invalidation
```

### Functions

#### `build_contract_prompt(source: str, file_node: FileNode) -> str`

User prompt for single-file contract extraction. Contains:
1. Full source code with line numbers
2. AST summary: list of function signatures, class names, import list (from FileNode)
3. Constraint: "Maximum 100 words per function contract"

AST summary guides the LLM to not miss any function and provides structure.

#### `build_batch_prompt(files: list[tuple[str, str, FileNode]]) -> str`

For batch of small files. All files in one message, separated by `--- File: {rel_path} ---`. LLM returns JSON keyed by `rel_path`.

#### `parse_contract_response(raw: str) -> dict[str, FileContract] | FileContract`

Reuses `_extract_json_from_text` from `debugger_bugs.py`.

- Dict with keys = rel_paths → batch response → `dict[str, FileContract]`
- Dict with key `"exports"` → single file → `FileContract`
- Missing fields → empty lists (not crash)
- Malformed JSON → retry extraction strategies
- Completely unparseable → empty contract (pipeline continues)

#### `batch_files(file_nodes: dict[str, FileNode], working_dir: str) -> list[list[str]]`

Groups files into batches by cumulative prompt size.

```python
PROMPT_BUDGET = 15_000  # chars (~3.5K tokens)
```

Algorithm: sort by source size ascending. Greedy bin-packing into batches up to `PROMPT_BUDGET`. Small files (conftest.py, __init__.py) group together. Large files (debugger.py) get own batch.

#### `extract_contracts(graph: DependencyGraph, provider, config: Config, working_dir: str) -> dict[str, FileContract]`

Async orchestrator. Main function.

1. Load cache: `load_cached_contracts(cache_path)`
2. For each file in graph: if cache hash matches → skip. Else → add to stale list.
3. Group stale files via `batch_files()`
4. For each batch → asyncio.Task with `asyncio.Semaphore(config.debug_max_concurrent_llm)`
5. Collect results, update cache, save
6. Return complete `dict[str, FileContract]`

Concurrency pattern:
```python
sem = asyncio.Semaphore(config.debug_max_concurrent_llm)

async def process_batch(batch):
    async with sem:
        prompt = build_batch_prompt(batch) if len(batch) > 1 else build_contract_prompt(...)
        result = await collect_text(provider, prompt, CONTRACT_EXTRACTION_PROMPT, ...)
        return parse_contract_response(result.text)

tasks = [process_batch(b) for b in batches]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

#### `load_cached_contracts(cache_path: Path) -> dict[str, FileContract]`

Reads `.g3/contracts_cache.json`. Returns empty dict if missing or invalid.

#### `save_cached_contracts(contracts: dict[str, FileContract], cache_path: Path) -> None`

Atomic write: write to `.tmp` file → `os.replace()` to final path.

#### `is_contract_stale(contract: FileContract, current_hash: str) -> bool`

```python
return contract.source_hash != current_hash
```

### Tests: `tests/test_debugger_contracts.py`

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_parse_contract_response_valid` | Well-formed JSON → FileContract with all fields |
| 2 | `test_parse_contract_response_missing_fields` | JSON without `raises` → FileContract with `raises=[]` |
| 3 | `test_parse_contract_response_malformed` | Broken JSON → empty contract (no crash) |
| 4 | `test_parse_contract_response_batch` | Multi-file JSON → dict[str, FileContract] |
| 5 | `test_batch_files_small` | 10 files × 500 chars → 1 batch |
| 6 | `test_batch_files_mixed` | 8 small + 2 large → small grouped, large separate |
| 7 | `test_batch_files_single_large` | 1 file × 20K chars → own batch |
| 8 | `test_cache_fresh` | Save, load, hash match → skip extraction |
| 9 | `test_cache_stale` | Save, change 1 file → only that file re-extracted |
| 10 | `test_cache_missing` | No cache file → extract all |
| 11 | `test_cache_atomic_write` | Verify temp file + rename pattern |
| 12 | `test_extract_contracts_concurrency` | Mock provider, 10 files, sem=2 → max 2 concurrent |
| 13 | `test_extract_contracts_error_handling` | 1 batch fails → others succeed, failed = empty contracts |

---

## 6. `src/debugger_edges.py` — Cross-File Edge Analysis Module

The core innovation. Checks caller source **against callee contracts** (not full callee source). One LLM call per file with ALL its outgoing dependency contracts — O(N) not O(E).

### Dataclass

```python
@dataclass
class EdgeFinding:
    caller_file: str             # "src/debugger.py"
    callee_file: str             # "src/debugger_context.py"
    caller_line: int             # 232
    description: str             # "build_context() called with..."
    confidence: str              # "high" | "medium" | "low"
    check_type: str              # one of 6 types
```

### 6 Check Types

| Type | Description | Example |
|------|-------------|---------|
| `signature_mismatch` | Wrong number/types of args | `resolve_config(args, extra)` but takes 1 arg |
| `return_ignored` | Discards important return value | `fulfill(order)` ignores bool return |
| `none_not_handled` | Callee returns None, caller doesn't check | `resolve_import()` → None, caller does `.split()` |
| `exception_uncaught` | Callee raises X, no try/except | `json.loads()` raises JSONDecodeError |
| `side_effect_order` | Caller assumes state not yet set | `reserve()` called after `ship()` |
| `type_mismatch` | Wrong type passed/expected | Function expects `list[str]`, caller passes `dict` |

### Functions

#### `build_edge_prompt(caller_source: str, caller_node: FileNode, dep_contracts: dict[str, FileContract]) -> str`

User prompt structure:
```
## Caller File: src/debugger.py
<full source with line numbers>

## Dependency Contracts

### src/debugger_context.py
Exports:
  - build_context(working_dir: str, file_subset: list[str] | None) -> str
    Pre: working_dir must exist
    Post: returns markdown with file contents
    Raises: OSError if files unreadable
  - plan_file_chunks(working_dir: str) -> list[list[str]]
    ...

### src/debugger_bugs.py
Exports:
  - parse_bugs(raw_output: str, start_id: int = 1) -> list[BugEntry]
    ...
```

Caller sees full source (every line may contain a bug). Dependencies are contracts only (compact interface description).

#### `build_scc_prompt(scc_files: list[tuple[str, str]]) -> str`

For files in a circular dependency (SCC). Contracts are insufficient — need full source of all files in the cycle.

```
## Circular Dependency Group
These files have circular imports. Analyze mutual interactions.

### File: src/a.py
<full source>

### File: src/b.py
<full source>
```

System prompt: `SCC_ANALYSIS_PROMPT` — emphasizes mutual state mutation, initialization ordering, import-time side effects.

#### `parse_edge_findings(raw: str) -> list[EdgeFinding]`

Extracts JSON array from LLM output via `_extract_json_from_text`.

Validation:
- `confidence` must be `{"high", "medium", "low"}` — invalid → `"low"`
- `check_type` must be one of 6 types — invalid → `"type_mismatch"`
- `caller_line` must be int > 0 — invalid → `0`
- Missing required fields → skip entry

#### `analyze_edges(graph: DependencyGraph, contracts: dict[str, FileContract], provider, config: Config, working_dir: str) -> tuple[list[EdgeFinding], list[EdgeFinding]]`

Returns `(high_findings, medium_findings)`. Low findings are discarded.

Algorithm:
1. Identify SCC files: `scc_files = {f for scc in graph.sccs for f in scc}`
2. Process SCC groups: one LLM call per SCC with `SCC_ANALYSIS_PROMPT`
3. Process normal files (not in SCC): one LLM call per file with `EDGE_ANALYSIS_PROMPT`
   - Skip files with no project dependencies
   - Skip files whose deps have no contracts
4. All calls through `asyncio.Semaphore(config.debug_max_concurrent_llm)`
5. Split results: high findings + medium findings

#### `deep_dive(finding: EdgeFinding, working_dir: str, provider, config: Config) -> EdgeFinding | None`

Takes medium-confidence finding, loads full source of both files, asks LLM to confirm or refute.

1. Read `caller_source` and `callee_source`
2. Format prompt with finding description + both full sources
3. LLM returns `{"confirmed": bool, "reason": str}`
4. If confirmed: set `confidence = "high"`, append reason → return finding
5. If refuted: return `None`

#### `run_deep_dives(medium_findings: list[EdgeFinding], working_dir: str, provider, config: Config) -> list[EdgeFinding]`

Orchestrates deep dives based on `config.debug_deep_dive_mode`:

- `"skip"` → return [] (no dives)
- `"normal"` → dive all medium findings
- `"aggressive"` → dive all medium findings (low already discarded)

Uses `asyncio.Semaphore` for concurrency control. Returns list of confirmed findings (now high confidence).

#### `findings_to_bugs(findings: list[EdgeFinding], start_id: int = 1) -> list[BugEntry]`

Converts high-confidence EdgeFindings to BugEntry:
```python
BugEntry(
    id=start_id + i,
    file=f.caller_file,
    line=f.caller_line,
    description=f"[{f.check_type}] {f.description}",
    severity="high",
)
```

### Tests: `tests/test_debugger_edges.py`

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_parse_edge_findings_valid` | Well-formed JSON → list[EdgeFinding] |
| 2 | `test_parse_edge_findings_invalid_confidence` | `"very_high"` → defaults to `"low"` |
| 3 | `test_parse_edge_findings_missing_fields` | Entry without caller_line → skipped |
| 4 | `test_build_edge_prompt_structure` | Verify caller source + "Dependency Contracts" section |
| 5 | `test_build_scc_prompt_structure` | All SCC files included with full source |
| 6 | `test_analyze_edges_skips_no_deps` | File with no project deps → no LLM call |
| 7 | `test_analyze_edges_scc_grouping` | A↔B in SCC → one SCC call, not two edge calls |
| 8 | `test_analyze_edges_confidence_split` | Mock mixed → high and medium separated correctly |
| 9 | `test_deep_dive_confirmed` | Mock confirms → EdgeFinding with high confidence |
| 10 | `test_deep_dive_refuted` | Mock refutes → None |
| 11 | `test_run_deep_dives_skip_mode` | Config skip → returns [] |
| 12 | `test_run_deep_dives_concurrency` | 5 findings, sem=2 → max 2 concurrent |
| 13 | `test_findings_to_bugs` | 3 findings → 3 BugEntry with correct ids and [check_type] prefix |

---

## 7. `src/debugger_intra.py` — Enhanced Per-File Analysis Module

Same 5 prompts (MAIN, ANCHOR, RED_TEAM, BOUNDARY, COMPLETENESS), but now each file is analyzed **individually** with dependency contracts as bonus context. Instead of "200K chunk with 10 files" → "1 file (~5K) + dep contracts (~3K) = ~8K focused context".

### Key improvement

| Dimension | Old (chunk-based) | New (graph-aware) |
|-----------|-------------------|-------------------|
| Unit of analysis | Chunk (5-10 files, 200K chars) | 1 file + dep contracts |
| Context size | ~200K chars | ~8-15K chars |
| LLM focus | Diluted across 10 files | Focused on 1 file |
| Cross-file context | Random (same chunk only) | Guaranteed (contracts of all deps) |
| LLM calls per pass | 1-5 (by chunks) | O(N) (by files × intensity) |

### Functions

#### `build_intra_prompt(file_source: str, file_node: FileNode, dep_contracts: dict[str, FileContract]) -> str`

User prompt structure:
```
Analyze the following code for bugs.

## File: src/debugger.py
```python
  1: """Debugger — automated bug-find-test-fix loop."""
  2: ...
```

## Dependency Contracts
(These describe the interfaces of modules imported by this file.)

### src/config.py
- resolve_config(cli_args: dict) -> Config
  Pre: cli_args must be a dict
  Post: returns valid Config
  Raises: ValueError if unknown provider
```

If no project dependencies → no "Dependency Contracts" section.

#### System prompt augmentation

Each existing prompt gets a one-line prefix prepended (not modifying the prompt body):

```python
CONTRACT_AWARENESS_PREFIX = (
    "You are also given contracts for imported modules that describe their interfaces. "
    "Use these contracts to verify cross-module interactions within this file.\n\n"
)
```

All 5 existing prompts remain **verbatim**. The prefix is prepended at call time.

#### `analyze_file(rel_path: str, file_source: str, file_node: FileNode, contracts: dict[str, FileContract], graph: DependencyGraph, provider, config: Config) -> list[BugEntry]`

Runs all intensity passes on one file.

1. Get dependencies: `graph.dependencies_of(rel_path)`
2. Get dep contracts: `{d: contracts[d] for d in deps if d in contracts}`
3. Build user prompt: `build_intra_prompt(file_source, file_node, dep_contracts)`
4. Get system prompts: `INTENSITY_PROMPTS[config.debug_intensity]`
5. For each system prompt:
   - Prepend `CONTRACT_AWARENESS_PREFIX`
   - Call LLM with `max_turns=1`
   - If incomplete → skip this pass (don't abort pipeline)
   - Parse bugs via `parse_bugs()`
6. Return all found bugs

**Resilience:** if one pass fails, we continue with the others. Only if ALL passes for a file fail do we mark it as inconclusive.

#### `analyze_all_files(graph: DependencyGraph, contracts: dict[str, FileContract], provider, config: Config, working_dir: str, existing_bugs: list[BugEntry]) -> list[BugEntry]`

1. For each file in graph: create async task via `asyncio.Semaphore(config.debug_max_concurrent_llm)`
2. `asyncio.gather()` all tasks (exceptions → logged, file skipped)
3. Deduplicate within new findings (same file+line or same file+description from different prompts)
4. Merge with existing bugs via `merge_bugs()` (don't duplicate what edge analysis found)
5. Return merged list

### Tests: `tests/test_debugger_intra.py`

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_build_intra_prompt_contains_source` | File source present with line numbers |
| 2 | `test_build_intra_prompt_contains_contracts` | "Dependency Contracts" section with dep signatures |
| 3 | `test_build_intra_prompt_no_deps` | No project deps → no contracts section |
| 4 | `test_analyze_file_all_prompts` | Mock, intensity=high → 5 LLM calls |
| 5 | `test_analyze_file_partial_failure` | 1 of 5 fails → other 4 bugs collected |
| 6 | `test_analyze_file_contract_augmentation` | System prompt starts with CONTRACT_AWARENESS_PREFIX |
| 7 | `test_analyze_all_files_concurrency` | 10 files, sem=3 → max 3 concurrent |
| 8 | `test_analyze_all_files_merge` | Edge bugs + intra bugs → no duplicates |
| 9 | `test_deduplicate_same_line` | 2 bugs same (file, line) → keep first |
| 10 | `test_deduplicate_same_description` | 2 bugs same normalized desc → keep first |
| 11 | `test_analyze_all_files_error_handling` | 1 file exception → others processed |

---

## 8. `src/debugger_render.py` — Context Rendering Module

Renders code context for tester and fixer agents. Replaces `build_context()` from the deleted `debugger_context.py`. Simpler than the old module — no hotspot detection, no skeleton rendering, no budget allocation. Each file is rendered individually with line numbers + dependency contracts.

### Why simpler

The old rendering solved "how to fit 10 files into 200K chars" with hotspot detection, skeleton views, and budget allocation. In the new architecture:
- Tester/fixer receive **targeted** file subsets (only files with bugs)
- Each file is typically 100-600 lines → full source is fine
- Dependency contracts provide cross-file context compactly
- No need for complex budget management

### Functions

#### `render_file_with_lines(rel_path: str, source: str) -> str`

Render a single file with line numbers:
```
### File: src/config.py
```python
  1: """Configuration: defaults -> .g3/config.yaml -> env -> CLI args."""
  2:
  3: import json
...
449:
```
```

For files > 500 lines: include first 200 lines + symbol index + last 100 lines with `... [N lines omitted]` marker.

#### `render_contracts_section(dep_contracts: dict[str, FileContract]) -> str`

Render dependency contracts as markdown:
```
## Dependency Contracts

### src/config.py
- resolve_config(cli_args: dict) -> Config
  Pre: cli_args must be a dict
  Post: returns valid Config
  Raises: ValueError if unknown provider

### src/debugger_bugs.py
- parse_bugs(raw_output: str, start_id: int = 1) -> list[BugEntry]
  Pre: raw_output is non-empty string
  Post: returns deduplicated list
```

If no contracts → returns empty string.

#### `build_context_from_graph(graph: DependencyGraph, contracts: dict[str, FileContract], working_dir: str, file_subset: list[str]) -> str`

Main function. Replaces `build_context()`.

1. For each file in `file_subset`:
   - Read source
   - `render_file_with_lines(rel_path, source)`
2. Collect dependency contracts for all files in subset:
   - For each file: `graph.dependencies_of(file)` → get contracts
   - Deduplicate (same dep may be shared by multiple files)
3. `render_contracts_section(dep_contracts)`
4. Return: rendered files + contracts section

#### `build_symbol_index(source: str) -> str`

Simple regex-based index of top-level function/class definitions with line numbers. Used for large file truncation. Migrated from `_build_symbol_index` in `debugger_context.py`.

### Tests: `tests/test_debugger_render.py`

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_render_file_with_lines` | Small file → full source with line numbers |
| 2 | `test_render_file_large_truncation` | 600-line file → first 200 + symbol index + last 100 |
| 3 | `test_render_contracts_section` | FileContract → formatted markdown |
| 4 | `test_render_contracts_empty` | No contracts → empty string |
| 5 | `test_build_context_from_graph` | 2 files + deps → rendered output with both files + contracts |
| 6 | `test_build_context_deduplicates_deps` | 2 files share a dep → contract appears once |

---

## 9. New Prompts in `src/debugger_prompts.py`

All existing prompts remain **unchanged**: `PLAYER_PROMPT_MAIN`, `PLAYER_PROMPT_ANCHOR`, `PLAYER_PROMPT_RED_TEAM`, `PLAYER_PROMPT_BOUNDARY`, `PLAYER_PROMPT_COMPLETENESS`, `TESTER_PROMPT`, `FIXER_PROMPT`, `INTENSITY_PROMPTS`.

### 9.1 `CONTRACT_EXTRACTION_PROMPT`

```python
CONTRACT_EXTRACTION_PROMPT = '''You are a code analyst extracting interface contracts from Python source files.

For each file, produce a JSON contract describing its public interface.

## Output format

```json
{
    "rel_path": "src/example.py",
    "exports": [
        {
            "name": "function_name",
            "signature": "def function_name(x: int, y: str = '') -> bool",
            "preconditions": ["x must be positive"],
            "postconditions": ["returns True if x > len(y)"],
            "side_effects": ["writes to self._cache"],
            "raises": ["ValueError if x < 0"],
            "return_type": "bool"
        }
    ],
    "imports_usage": [
        {
            "source_module": "src.config",
            "symbol": "Config",
            "usage_description": "used as return type annotation"
        }
    ],
    "invariants": ["all handlers must be registered in REGISTRY"]
}
```

## Rules

- Include ALL public functions and classes (not private helpers starting with _)
- Include class methods that are part of the public API
- Maximum 100 words per function contract — be precise, not verbose
- Preconditions: what must be true about inputs
- Postconditions: what the caller can expect about the return value
- Side effects: state mutations, I/O, file writes, network calls
- Raises: exceptions that callers should handle
- Invariants: module-level constraints that span multiple functions
- If a file defines no public API (e.g., __init__.py with only imports), return empty exports

When processing multiple files in one request, return a JSON object keyed by rel_path:
```json
{
    "src/a.py": { "exports": [...], ... },
    "src/b.py": { "exports": [...], ... }
}
```
'''
```

### 9.2 `EDGE_ANALYSIS_PROMPT`

```python
EDGE_ANALYSIS_PROMPT = '''You are a cross-file bug detector. You are given:
1. The FULL source code of a caller file
2. CONTRACTS of all modules it imports (not their full source)

Your job: find bugs at the BOUNDARY between the caller and its dependencies.

## 6 Check Types

For EACH imported function/class used in the caller, verify:

1. **signature_mismatch** — Does the caller pass the correct number and types of arguments?
   Look for: extra args, missing args, wrong arg types, wrong keyword names.

2. **return_ignored** — Does the callee return a value that the caller should use but doesn't?
   Look for: bool returns (success/failure) used as bare calls, computed results discarded.

3. **none_not_handled** — Can the callee return None? Does the caller check for it?
   Look for: functions that return Optional[X] but caller does .attr or [index] on result.

4. **exception_uncaught** — Does the callee raise exceptions the caller doesn't catch?
   Look for: documented raises in the contract vs try/except in the caller.

5. **side_effect_order** — Does the caller assume state that the callee hasn't set yet?
   Look for: calling methods in wrong order, using results before side effects complete.

6. **type_mismatch** — Does the caller pass or expect the wrong type?
   Look for: passing dict where list expected, str where Path expected, int where float needed.

## Output

JSON array of findings:

```json
[
    {
        "caller_file": "src/debugger.py",
        "callee_file": "src/config.py",
        "caller_line": 42,
        "description": "resolve_config() called with 2 args but signature takes 1",
        "confidence": "high",
        "check_type": "signature_mismatch"
    }
]
```

Confidence levels:
- **high** — certain bug, clear contract violation
- **medium** — likely bug, but depends on runtime conditions
- **low** — possible issue, speculative

If no cross-file bugs found, return `[]`.
Do NOT report bugs within the caller that don't involve its dependencies.
Do NOT report style issues or suggestions.
'''
```

### 9.3 `SCC_ANALYSIS_PROMPT`

```python
SCC_ANALYSIS_PROMPT = '''You are analyzing files with CIRCULAR DEPENDENCIES. These files import each other, creating a cycle.

You are given the FULL source of ALL files in the cycle. Analyze their mutual interactions for bugs.

## Focus areas for circular dependencies

1. **Initialization ordering** — Module A's top-level code uses module B, but module B's top-level code uses module A. Which runs first? Can this cause AttributeError at import time?

2. **Mutual state mutation** — A.func() modifies state that B.func() reads, and vice versa. Is there a consistent ordering? Can they see stale state?

3. **Contract violations across the cycle** — Same 6 check types as edge analysis (signature_mismatch, return_ignored, none_not_handled, exception_uncaught, side_effect_order, type_mismatch), but check ALL directions in the cycle.

4. **Re-import side effects** — Does importing A trigger code in B that re-imports A? Does this cause incomplete module state?

## Output

Same format as edge analysis:

```json
[
    {
        "caller_file": "src/a.py",
        "callee_file": "src/b.py",
        "caller_line": 15,
        "description": "...",
        "confidence": "high",
        "check_type": "side_effect_order"
    }
]
```

If no bugs found, return `[]`.
'''
```

### 9.4 `DEEP_DIVE_PROMPT`

```python
DEEP_DIVE_PROMPT = '''You are confirming or refuting a suspected cross-file bug.

## Suspected bug

- **Caller file:** {caller_file}
- **Callee file:** {callee_file}
- **Line:** {caller_line}
- **Type:** {check_type}
- **Description:** {description}

## Task

You are given the FULL source of both files. Carefully verify whether this bug is real.

- Read the caller code at line {caller_line} and surrounding context
- Read the callee implementation (not just the contract)
- Determine if the described issue actually causes incorrect behavior

## Output

```json
{{
    "confirmed": true,
    "reason": "One sentence explaining why this is a real bug"
}}
```

Or if the finding is wrong:

```json
{{
    "confirmed": false,
    "reason": "One sentence explaining why this is NOT a bug"
}}
```
'''
```

### 9.5 `CONTRACT_AWARENESS_PREFIX`

Not a standalone prompt. Prepended to existing INTENSITY_PROMPTS at call time:

```python
CONTRACT_AWARENESS_PREFIX = (
    "You are also given contracts for imported modules that describe their "
    "interfaces. Use these contracts to verify cross-module interactions "
    "within this file.\n\n"
)
```

---

## 10. Config Changes (`src/config.py`)

### New fields in Config dataclass

```python
# Graph-aware debugger
debug_deep_dive_mode: str = "normal"       # "aggressive" | "normal" | "skip"
debug_cache_contracts: bool = True          # cache contracts between runs
debug_edge_batch_size: int = 5             # files per concurrent edge analysis group
debug_max_concurrent_llm: int = 5          # max parallel LLM calls
```

### New env mappings in `_ENV_MAP`

```python
"G3_DEBUG_DEEP_DIVE_MODE": ("debug_deep_dive_mode", str),
"G3_DEBUG_CACHE_CONTRACTS": ("debug_cache_contracts", lambda x: x.lower() in ("true", "1", "yes")),
"G3_DEBUG_EDGE_BATCH_SIZE": ("debug_edge_batch_size", int),
"G3_DEBUG_MAX_CONCURRENT_LLM": ("debug_max_concurrent_llm", int),
```

### Reused fields

- `debug_victory_threshold` — now means "consecutive clean pipeline passes" (both edge + intra must find 0 bugs)
- `debug_limit_value` — now means "max pipeline iterations" (default changed from 10 to 3)
- `debug_intensity` — unchanged, controls which prompts run in Phase 4
- `debug_player_provider` / `debug_player_model` — used for Phases 1-4 (contract extraction, edge analysis, intra-file)

### Semantic notes

- `debug_deep_dive_mode`:
  - `"aggressive"` — dive all medium findings (most thorough, most LLM calls)
  - `"normal"` — dive all medium findings (same as aggressive since low is already discarded)
  - `"skip"` — skip deep dives entirely (fastest, may miss real bugs classified as medium)

- `debug_max_concurrent_llm` applies to all phases: contract extraction, edge analysis, deep dives, and intra-file analysis. It's a global semaphore.

---

## 11. Rewritten `src/debugger.py` — Pipeline Orchestrator

### Keep unchanged

- `_Pulse` class (animated status dot)
- `DebuggerResult` dataclass
- `_CollectedTextResult`, `_collect_text()`, `_extract_text()`
- `_should_stop()` (semantic change: iteration = pipeline pass)
- `_git_commit()`, `_parse_tester_results()`
- `_verify_confirmed_fixes()`, `_mark_inconclusive()`
- Color helpers (`_red`, `_yellow`, `_green`, `_grey`), `_compact_status()`, `_display_final()`
- `run_sync()`

### Remove

- `self._chunks`, `self._chunk_cursor`, `_next_chunk()`
- `_display_iteration_header()` (replaced by `_display_phase_header()`)
- `_run_player()` (replaced by Phase 2-4 calls)
- Import of `build_context` / `plan_file_chunks` from `debugger_context`

### New imports

```python
from src.debugger_graph import build_dependency_graph, DependencyGraph
from src.debugger_contracts import extract_contracts, FileContract
from src.debugger_edges import analyze_edges, run_deep_dives, findings_to_bugs
from src.debugger_intra import analyze_all_files
from src.debugger_render import build_context_from_graph
```

### New `__init__`

```python
def __init__(self, config: Config):
    self.config = config
    self.working_dir = config.working_dir

    player_cfg = {}
    tester_cfg = {}
    fixer_cfg = {}

    self._player = create_provider(config.debug_player_provider, player_cfg)
    self._tester = create_provider(config.debug_tester_provider, tester_cfg)
    self._fixer = create_provider(config.debug_fixer_provider, fixer_cfg)

    self._bugs: list[BugEntry] = []
    self._iteration = 0
    self._clean_passes = 0
    self._start_time = 0.0
    self._inconclusive_reason: str | None = None

    # Graph-aware state (built per iteration)
    self._graph: DependencyGraph | None = None
    self._contracts: dict[str, FileContract] = {}
```

### New `run()` — 6-phase pipeline with outer loop

```python
async def run(self) -> DebuggerResult:
    self._start_time = time.time()
    bugs_md_path = f"{self.working_dir}/bugs.md"

    print(f"\n🔍 Debugger started (graph-aware)")
    print(f"   Player:  {self.config.debug_player_provider}")
    print(f"   Tester:  {self.config.debug_tester_provider}")
    print(f"   Fixer:   {self.config.debug_fixer_provider}")
    print(f"   Mode:    {self.config.debug_intensity} intensity")
    print(f"   Limit:   {self.config.debug_limit_mode}")
    print(f"   Dive:    {self.config.debug_deep_dive_mode}")
    print()

    while True:
        self._iteration += 1

        if self._should_stop():
            break

        print(f"══ Pipeline iteration {self._iteration} ══════════════════════")

        # ── Phase 0: Dependency Graph ──────────────────────────
        self._display_phase_header("Phase 0: Building dependency graph")
        self._graph = build_dependency_graph(self.working_dir)
        file_count = len(self._graph.files)
        edge_count = len(self._graph.edges)
        scc_count = len(self._graph.sccs)
        print(f"   {file_count} files, {edge_count} edges, {scc_count} SCCs")

        if file_count == 0:
            self._mark_inconclusive("No Python files found.")
            break

        # ── Phase 1: Contract Extraction ───────────────────────
        self._display_phase_header("Phase 1: Extracting contracts")
        self._contracts = await extract_contracts(
            self._graph, self._player, self.config, self.working_dir
        )
        fresh = sum(1 for f in self._graph.files if f in self._contracts)
        print(f"   {fresh}/{file_count} contracts ready")

        # ── Phase 2: Edge Analysis ─────────────────────────────
        self._display_phase_header("Phase 2: Cross-file edge analysis")
        high_findings, medium_findings = await analyze_edges(
            self._graph, self._contracts, self._player, self.config, self.working_dir
        )
        print(f"   {len(high_findings)} high, {len(medium_findings)} medium findings")

        # ── Phase 3: Deep Dive ─────────────────────────────────
        self._display_phase_header("Phase 3: Deep dive on medium findings")
        confirmed = await run_deep_dives(
            medium_findings, self.working_dir, self._player, self.config
        )
        print(f"   {len(confirmed)}/{len(medium_findings)} confirmed")

        # Convert all high + confirmed to BugEntry
        all_edge_findings = high_findings + confirmed
        edge_bugs = findings_to_bugs(
            all_edge_findings,
            start_id=max((b.id for b in self._bugs), default=0) + 1,
        )

        # ── Phase 4: Intra-File Analysis ───────────────────────
        self._display_phase_header("Phase 4: Per-file analysis with contracts")
        self._bugs = merge_bugs(self._bugs, edge_bugs)
        self._bugs = await analyze_all_files(
            self._graph, self._contracts, self._player, self.config,
            self.working_dir, self._bugs,
        )
        renumber_bugs(self._bugs)

        new_bug_count = len(edge_bugs)  # edge bugs this iteration
        # Count intra bugs added this iteration (approximate)
        intra_new = len(self._bugs) - len(edge_bugs) - (len(self._bugs) - len(edge_bugs))
        # Simpler: check if any new bugs were found this iteration
        iteration_found_bugs = len(edge_bugs) > 0 or len(self._bugs) > len(edge_bugs)

        if not any(b.status == "open" for b in self._bugs) and len(edge_bugs) == 0:
            self._clean_passes += 1
            print(f"   No new bugs. Clean passes: {self._clean_passes}/{self.config.debug_victory_threshold}")
            if self._clean_passes >= self.config.debug_victory_threshold:
                break
            continue
        else:
            self._clean_passes = 0

        # ── Phase 5: Test & Fix ────────────────────────────────
        self._display_phase_header("Phase 5: Test & fix")

        open_bugs = [b for b in self._bugs if b.status == "open"]
        if open_bugs:
            tester_ok = await self._run_tester(open_bugs)
            if not tester_ok:
                break

        confirmed_bugs = [b for b in self._bugs if b.status == "confirmed"]
        if confirmed_bugs:
            fixed_count = await self._run_fixer(confirmed_bugs)
            if fixed_count is None:
                break
            if fixed_count > 0:
                self._git_commit(self._iteration, fixed_count)

        write_bugs_md(self._bugs, bugs_md_path, self._iteration)

    # Final report
    duration = time.time() - self._start_time
    victory = (
        self._clean_passes >= self.config.debug_victory_threshold
        and self._inconclusive_reason is None
    )
    fixed = [b for b in self._bugs if b.status == "fixed"]
    confirmed = [b for b in self._bugs if b.status == "confirmed"]

    write_final_report(self._bugs, bugs_md_path.replace("bugs.md", "bugs_final.md"), duration, victory)
    self._display_final(victory, duration)

    return DebuggerResult(
        victory=victory,
        iterations=self._iteration,
        total_bugs=len(self._bugs),
        fixed_bugs=len(fixed),
        confirmed_bugs=len(confirmed),
        duration_s=duration,
    )
```

### Modified `_run_tester` and `_run_fixer`

Both methods change one thing: replace `build_context(self.working_dir, file_subset=bug_files)` with:

```python
context = build_context_from_graph(
    self._graph, self._contracts, self.working_dir, file_subset=bug_files
)
```

Everything else (prompt construction, LLM call, result parsing, verification) stays identical.

### New `_display_phase_header`

```python
def _display_phase_header(self, name: str) -> None:
    print(f"\n── {name} {'─' * max(1, 50 - len(name))}")
```

---

## 12. Cleanup & Deletions

### Delete `src/debugger_context.py`

Pre-check: verify no module imports from it:
```bash
grep -rn "from src.debugger_context\|import debugger_context" src/ tests/
```

The only consumers are:
- `src/debugger.py` — imports `build_context`, `plan_file_chunks` → replaced by new modules
- Tests referencing chunk-based functionality → removed/rewritten

### `discover_py_files()` migration

Function moves from `debugger_context.py` to `debugger_graph.py`. Same `_SKIP_DIRS`, same logic, same signature.

### Functions NOT migrated (deleted)

These were chunk-specific and are no longer needed:
- `plan_file_chunks()` — no more chunking
- `build_context()` — replaced by `build_context_from_graph()`
- `_render_large_python_section()` — no more hotspot rendering
- `_build_hotspot_sections()` — no more hotspot detection
- `_build_docstring_index()` — no more skeleton rendering
- `_parse_python_symbols()` — hotspot-specific AST analysis
- `_allocate_section_budgets()` — no more budget allocation
- `_context_budget()` — no more budget calculation
- `_sample_numbered_body()` — complex truncation logic
- `_build_excerpt_body()` — excerpt windowing
- `_render_truncated_section()` — budget-based truncation
- `_truncate_rendered_text()` — simple truncation
- `_build_structure_overview()` — structure rendering

### Functions migrated to `debugger_render.py`

- `_format_with_line_numbers()` → `render_file_with_lines()` (simplified)
- `_build_symbol_index()` → `build_symbol_index()` (for large file truncation)
- `_section_header()` → incorporated into `render_file_with_lines()`

### Test updates

Remove or rewrite tests that reference:
- `_chunks`, `plan_file_chunks`, `_next_chunk`
- `build_context` from `debugger_context`
- Chunk-based iteration patterns

---

## 13. Data Flow & Complexity Analysis

### Per-phase LLM call count

| Phase | LLM calls | Context per call | For tero (~30 files) |
|-------|-----------|-----------------|---------------------|
| 0 (graph) | 0 | — (pure AST) | instant |
| 1 (contracts) | O(N/batch) | ~5K per file | ~6 calls (cached: 0-2) |
| 2 (edges) | O(N) | ~15K (file + dep contracts) | ~25 calls |
| 3 (deep dive) | O(medium findings) | ~20K (two full files) | ~5-10 calls |
| 4 (intra-file) | O(N × intensity) | ~8-15K (file + dep contracts) | 30-150 calls |
| 5 (test+fix) | 2 | full context | 2 calls |

**Total per iteration:** ~70-190 LLM calls (intensity dependent).

**Cost comparison:** Old system did 1-5 calls per iteration × 200K context. New system does 70-190 calls × 8-15K context. Total tokens are similar, but each call is more focused and finds more real bugs.

**Caching impact:** After first iteration, Phase 1 is near-zero (only modified files). Phase 0 is always instant. So iterations 2+ are cheaper.

### Memory usage

- `DependencyGraph` — small: ~30 FileNode objects, each with a few lists
- `dict[str, FileContract]` — small: ~30 contracts, each ~1KB JSON
- Peak memory is during `asyncio.gather()` with `debug_max_concurrent_llm` parallel tasks, each holding one file source + prompt string. At 5 concurrent × 20K = 100K — negligible.

---

## 14. Edge Cases & Error Handling

| Scenario | Behavior |
|----------|----------|
| File with SyntaxError | `parse_file()` returns None, file skipped in graph |
| Circular imports (SCC) | Special SCC prompt with all files' full source |
| stdlib/third-party imports | `resolve_import()` returns None, skipped in graph |
| Very large file (>500 lines) | `render_file_with_lines()` truncates with symbol index |
| Contract cache stale | Hash mismatch → re-extract only changed files |
| Contract cache corrupted | JSON parse fails → treat as empty cache, re-extract all |
| No cross-file bugs found | Phase 2-3 produce 0 bugs, Phase 4 still runs |
| No intra-file bugs found | If also no edge bugs → clean pass counted |
| Provider rate limit | `_collect_text()` retry logic (60s → 120s → 240s) unchanged |
| LLM returns invalid JSON | Multiple extraction strategies in `_extract_json_from_text` |
| LLM returns empty response | Mark as incomplete, skip that analysis (don't abort pipeline) |
| Single file project | Graph has 1 node, 0 edges. Phase 2-3 do nothing. Phase 4 runs normally. |
| No Python files found | `_mark_inconclusive("No Python files found.")`, stop |
| `__init__.py` with only imports | Gets empty contract (no exports). Used as edge in graph. |
| Dynamic imports (`importlib`) | Not detected by AST analysis. Acceptable — edge analysis still works for static imports. |
| Star imports (`from X import *`) | ImportEdge with `symbols=["*"]`. Contract of X used but specific names unknown. |

---

## 15. What Doesn't Change

- `bugs.md` / `bugs_final.md` format — unchanged
- `DebuggerResult` dataclass — unchanged
- Git commit messages — same pattern: `fix(debugger): iteration N — M bug(s) fixed`
- Menu/CLI interface — same commands, same args
- Tester prompt and flow — unchanged
- Fixer prompt and flow — unchanged
- Provider system — unchanged
- `_Pulse` animation — unchanged
- `_collect_text()` retry logic — unchanged
- `_verify_confirmed_fixes()` — unchanged
- `_parse_tester_results()` — unchanged
- `BugEntry` dataclass — unchanged
- `parse_bugs()`, `merge_bugs()`, `renumber_bugs()` — unchanged
- `write_bugs_md()`, `write_final_report()` — unchanged
