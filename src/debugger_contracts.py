"""Contract extraction module for the graph-aware debugger.

LLM-powered extraction of structured "contracts" for each source file —
a formalized description of its public interface: exports, preconditions,
postconditions, side effects, exceptions.  Contracts are cached by SHA-256
hash so unchanged files are skipped on subsequent runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Union

from src.debugger_bugs import extract_json_from_text, strip_trailing_commas
from src.debugger_graph import DependencyGraph, FileNode
from src.debugger_llm import collect_text
from src.debugger_prompts import CONTRACT_EXTRACTION_PROMPT

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROMPT_BUDGET = 15_000  # max chars per batch prompt (~3.5 K tokens)

_SMALL_FILE_NAMES = {"conftest.py", "__init__.py"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ExportContract:
    """Contract for a single exported symbol (function / class / constant)."""

    name: str = ""
    signature: str = ""
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    raises: list[str] = field(default_factory=list)
    return_type: str = ""


@dataclass
class ImportUsage:
    """Describes how a specific imported symbol is used in the file."""

    source_module: str = ""
    symbol: str = ""
    usage_description: str = ""


@dataclass
class FileContract:
    """Complete contract for a single source file."""

    rel_path: str = ""
    exports: list[ExportContract] = field(default_factory=list)
    imports_usage: list[ImportUsage] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)
    source_hash: str = ""


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _file_contract_from_dict(d: dict) -> FileContract:
    """Construct a FileContract from a raw dict (e.g. parsed JSON / cache)."""
    exports: list[ExportContract] = []
    for raw in d.get("exports") or []:
        if isinstance(raw, dict):
            exports.append(ExportContract(
                name=str(raw.get("name", "")),
                signature=str(raw.get("signature", "")),
                preconditions=list(raw.get("preconditions") or []),
                postconditions=list(raw.get("postconditions") or []),
                side_effects=list(raw.get("side_effects") or []),
                raises=list(raw.get("raises") or []),
                return_type=str(raw.get("return_type", "")),
            ))

    imports_usage: list[ImportUsage] = []
    for raw in d.get("imports_usage") or []:
        if isinstance(raw, dict):
            imports_usage.append(ImportUsage(
                source_module=str(raw.get("source_module", "")),
                symbol=str(raw.get("symbol", "")),
                usage_description=str(raw.get("usage_description", "")),
            ))

    return FileContract(
        rel_path=str(d.get("rel_path", "")),
        exports=exports,
        imports_usage=imports_usage,
        invariants=list(d.get("invariants") or []),
        source_hash=str(d.get("source_hash", "")),
    )


def _looks_like_single_contract(data: dict) -> bool:
    """Heuristic for distinguishing a single contract from a batch map."""
    return (
        isinstance(data.get("rel_path"), str)
        or isinstance(data.get("exports"), list)
        or isinstance(data.get("imports_usage"), list)
        or isinstance(data.get("invariants"), list)
    )


# ---------------------------------------------------------------------------
# Step 2 — build_contract_prompt
# ---------------------------------------------------------------------------

def build_contract_prompt(source: str, file_node: FileNode) -> str:
    """Build the user prompt for single-file contract extraction.

    Contains the full source with line numbers plus an AST-derived summary
    (function signatures, class names, import list) so the LLM has structural
    context without guessing.
    """
    # -- source with line numbers --
    numbered_lines: list[str] = []
    for i, line in enumerate(source.splitlines(), start=1):
        numbered_lines.append(f"{i:>4} | {line}")
    numbered_source = "\n".join(numbered_lines)

    # -- AST summary --
    summary_parts: list[str] = []

    if file_node.functions:
        summary_parts.append("Functions:")
        for fn in file_node.functions:
            prefix = "async " if fn.is_async else ""
            ret = f" -> {fn.returns}" if fn.returns else ""
            args = ", ".join(fn.args)
            summary_parts.append(f"  {prefix}{fn.name}({args}){ret}")

    if file_node.classes:
        summary_parts.append("Classes:")
        for cls in file_node.classes:
            bases_str = f"({', '.join(cls.bases)})" if cls.bases else ""
            summary_parts.append(f"  class {cls.name}{bases_str}")
            for m in cls.methods:
                prefix = "async " if m.is_async else ""
                ret = f" -> {m.returns}" if m.returns else ""
                args = ", ".join(m.args)
                summary_parts.append(f"    {prefix}{m.name}({args}){ret}")

    if file_node.imports:
        summary_parts.append("Imports:")
        seen_mods: set[str] = set()
        for imp in file_node.imports:
            key = imp.target_module
            if key not in seen_mods:
                seen_mods.add(key)
                syms = ", ".join(imp.symbols) if imp.symbols else ""
                summary_parts.append(
                    f"  from {key} import {syms}" if syms else f"  import {key}"
                )

    ast_summary = "\n".join(summary_parts)

    # Build the non-source scaffolding first so we know how much budget remains
    # for the numbered source (raw source + ~6 chars/line for line numbers).
    footer = (
        f"AST summary:\n{ast_summary}\n\n"
        f"Maximum 100 words per function contract.\n"
        f"Return JSON with keys: rel_path, exports, imports_usage, invariants.\n"
        f"Each export: name, signature, preconditions, postconditions, "
        f"side_effects, raises, return_type.\n"
        f"Each import_usage: source_module, symbol, usage_description.\n"
    )
    header = f"--- File: {file_node.rel_path} ---\n\nSource code (with line numbers):\n```\n"
    closing = "\n```\n\n"
    _TRUNCATION_MARKER = "\n... [truncated]"
    overhead = len(header) + len(closing) + len(footer) + len(_TRUNCATION_MARKER)
    max_source = PROMPT_BUDGET - overhead
    if len(numbered_source) > max_source > 0:
        numbered_source = numbered_source[:max_source] + _TRUNCATION_MARKER

    return header + numbered_source + closing + footer


# ---------------------------------------------------------------------------
# Step 3 — build_batch_prompt
# ---------------------------------------------------------------------------

def build_batch_prompt(files: list[tuple[str, str, FileNode]]) -> str:
    """Build a single prompt covering multiple small files.

    Parameters
    ----------
    files:
        List of ``(rel_path, source_text, FileNode)`` tuples.

    Returns
    -------
    A prompt string with each file separated by ``--- File: {rel_path} ---``.
    The LLM is instructed to return a JSON dict keyed by *rel_path*.
    """
    sections: list[str] = []
    for rel_path, source, fnode in files:
        # -- source with line numbers --
        numbered_lines: list[str] = []
        for i, line in enumerate(source.splitlines(), start=1):
            numbered_lines.append(f"{i:>4} | {line}")

        # -- lightweight AST summary --
        summary_parts: list[str] = []
        for fn in fnode.functions:
            prefix = "async " if fn.is_async else ""
            ret = f" -> {fn.returns}" if fn.returns else ""
            args = ", ".join(fn.args)
            summary_parts.append(f"  {prefix}{fn.name}({args}){ret}")
        for cls in fnode.classes:
            bases_str = f"({', '.join(cls.bases)})" if cls.bases else ""
            summary_parts.append(f"  class {cls.name}{bases_str}")
            for m in cls.methods:
                prefix = "async " if m.is_async else ""
                ret = f" -> {m.returns}" if m.returns else ""
                args = ", ".join(m.args)
                summary_parts.append(f"    {prefix}{m.name}({args}){ret}")
        seen_mods: set[str] = set()
        for imp in fnode.imports:
            key = imp.target_module
            if key not in seen_mods:
                seen_mods.add(key)
                syms = ", ".join(imp.symbols) if imp.symbols else ""
                summary_parts.append(
                    f"  from {key} import {syms}" if syms else f"  import {key}"
                )

        ast_summary = "\n".join(summary_parts)
        numbered_source = "\n".join(numbered_lines)
        sections.append(
            f"--- File: {rel_path} ---\n\n"
            f"Source:\n```\n{numbered_source}\n```\n\n"
            f"AST summary:\n{ast_summary}\n"
        )

    header = (
        "Analyse the following files and return a JSON object keyed by file "
        "relative path. Each value has: rel_path, exports, imports_usage, "
        "invariants. Maximum 100 words per function contract.\n\n"
    )
    return header + "\n".join(sections)


# ---------------------------------------------------------------------------
# Step 4 — parse_contract_response
# ---------------------------------------------------------------------------

def parse_contract_response(
    raw: str,
) -> Union[dict[str, FileContract], FileContract]:
    """Parse LLM output into structured contract(s).

    Returns
    -------
    dict[str, FileContract]
        When the response is a batch (keys = rel_paths).
    FileContract
        When the response describes a single file (has ``"exports"`` key).
    """
    if not raw or not raw.strip():
        return FileContract()

    candidates = extract_json_from_text(raw)

    for candidate in candidates:
        cleaned = strip_trailing_commas(candidate)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue

        if not isinstance(data, dict):
            continue

        if _looks_like_single_contract(data):
            return _file_contract_from_dict(data)

        result: dict[str, FileContract] = {}
        for key, val in data.items():
            if not isinstance(val, dict) or not _looks_like_single_contract(val):
                continue
            fc = _file_contract_from_dict(val)
            fc.rel_path = fc.rel_path or key
            result[key] = fc
        if result:
            return result

    # Completely unparseable → empty contract (pipeline continues)
    return FileContract()


# ---------------------------------------------------------------------------
# Step 5 — batch_files
# ---------------------------------------------------------------------------

def batch_files(
    file_nodes: dict[str, FileNode],
    working_dir: str,
) -> list[list[str]]:
    """Group files into batches by cumulative prompt size.

    Algorithm
    ---------
    1. Read source for each file.
    2. Sort by source size ascending.
    3. Greedy bin-pack into batches up to :data:`PROMPT_BUDGET` chars.
    4. Small files (``conftest.py``, ``__init__.py``) are grouped together.
    5. Large files that exceed the budget get their own batch.

    Returns
    -------
    list of batches, where each batch is a list of ``rel_path`` strings.
    """
    root = Path(working_dir).expanduser().resolve()

    # Read sources and compute sizes
    file_sizes: list[tuple[str, int]] = []
    for rel_path, fnode in file_nodes.items():
        abs_path = root / rel_path
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_sizes.append((rel_path, len(source)))

    # Sort by size ascending
    file_sizes.sort(key=lambda t: t[1])

    # Separate small files (conftest.py, __init__.py) from regular files
    small: list[tuple[str, int]] = []
    regular: list[tuple[str, int]] = []
    for rel_path, size in file_sizes:
        fname = Path(rel_path).name
        if fname in _SMALL_FILE_NAMES:
            small.append((rel_path, size))
        else:
            regular.append((rel_path, size))

    batches: list[list[str]] = []

    # --- bin-pack small files together first ---
    if small:
        small_batch: list[str] = []
        small_total = 0
        for rel_path, size in small:
            if small_total + size > PROMPT_BUDGET and small_batch:
                batches.append(small_batch)
                small_batch = []
                small_total = 0
            small_batch.append(rel_path)
            small_total += size
        if small_batch:
            batches.append(small_batch)

    # --- bin-pack regular files ---
    current_batch: list[str] = []
    current_size = 0
    for rel_path, size in regular:
        # Large files get their own batch
        if size > PROMPT_BUDGET:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_size = 0
            batches.append([rel_path])
            continue

        if current_size + size > PROMPT_BUDGET:
            batches.append(current_batch)
            current_batch = []
            current_size = 0

        current_batch.append(rel_path)
        current_size += size

    if current_batch:
        batches.append(current_batch)

    return batches


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_cached_contracts(cache_path: Path) -> dict[str, FileContract]:
    """Load previously cached contracts from disk.

    Returns an empty dict if the cache file is missing or invalid.
    """
    if not cache_path.exists():
        return {}
    try:
        raw = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, FileContract] = {}
    for key, val in data.items():
        if isinstance(val, dict):
            result[key] = _file_contract_from_dict(val)
    return result


def save_cached_contracts(
    contracts: dict[str, FileContract],
    cache_path: Path,
) -> None:
    """Atomically persist contracts to *cache_path*.

    Writes to a ``.tmp`` file first, then :func:`os.replace` to final path.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {k: asdict(v) for k, v in contracts.items()}
    payload = json.dumps(serialisable, indent=2, ensure_ascii=False)
    tmp_path = cache_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(str(tmp_path), str(cache_path))
    except OSError:
        # Best-effort: clean up tmp if rename failed
        try:
            tmp_path.unlink()
        except OSError:
            pass


def is_contract_stale(contract: FileContract, current_hash: str) -> bool:
    """Return ``True`` if the cached contract is out of date."""
    return contract.source_hash != current_hash


# ---------------------------------------------------------------------------
# Step 6 — extract_contracts (async orchestrator)
# ---------------------------------------------------------------------------

async def extract_contracts(
    graph: DependencyGraph,
    provider,
    config,
    working_dir: str,
) -> dict[str, FileContract]:
    """Orchestrate contract extraction for all files in *graph*.

    1. Load cache → identify stale files (hash mismatch).
    2. Batch stale files via :func:`batch_files`.
    3. Process batches concurrently with a semaphore.
    4. Update cache and save.
    5. Return the complete contracts dict (cached + freshly extracted).
    """
    max_concurrent = getattr(config, "debug_max_concurrent_llm", 5)
    max_turns = getattr(config, "max_turns", 10)

    root = Path(working_dir).expanduser().resolve()
    cache_path = root / ".g3" / "contracts_cache.json"

    # 1. Load existing cache
    cached = load_cached_contracts(cache_path)

    # 2. Identify stale files
    stale_nodes: dict[str, FileNode] = {}
    for rel_path, fnode in graph.files.items():
        existing = cached.get(rel_path)
        if existing is None or is_contract_stale(existing, fnode.source_hash):
            stale_nodes[rel_path] = fnode

    # Nothing to do — all cached
    if not stale_nodes:
        return cached

    # 3. Batch stale files
    batch_keys = batch_files(stale_nodes, working_dir)

    # 4. Process batches concurrently
    sem = asyncio.Semaphore(max_concurrent)
    results: dict[str, FileContract] = {}

    async def _process_batch(batch: list[str]) -> dict[str, FileContract]:
        """Process one batch through the LLM."""
        async with sem:
            if len(batch) == 1:
                rel_path = batch[0]
                fnode = stale_nodes[rel_path]
                abs_path = root / rel_path
                try:
                    source = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return {rel_path: FileContract(rel_path=rel_path)}

                prompt = build_contract_prompt(source, fnode)
                result = await collect_text(
                    provider,
                    prompt=prompt,
                    system_prompt=CONTRACT_EXTRACTION_PROMPT,
                    working_dir=working_dir,
                    max_turns=max_turns,
                )
                parsed = parse_contract_response(result.text)
                if isinstance(parsed, FileContract):
                    parsed.rel_path = parsed.rel_path or rel_path
                    # Only stamp the hash when the contract has real content.
                    # An empty result means the LLM returned garbage — leave
                    # source_hash="" so is_contract_stale() returns True next run.
                    if parsed.exports or parsed.imports_usage or parsed.invariants:
                        parsed.source_hash = fnode.source_hash
                    return {rel_path: parsed}
                elif isinstance(parsed, dict):
                    # Shouldn't normally happen for single-file, but handle it
                    for k, v in parsed.items():
                        if v.exports or v.imports_usage or v.invariants:
                            v.source_hash = v.source_hash or fnode.source_hash
                    return parsed
                return {rel_path: FileContract(rel_path=rel_path)}

            else:
                # Multi-file batch
                file_tuples: list[tuple[str, str, FileNode]] = []
                for rel_path in batch:
                    fnode = stale_nodes[rel_path]
                    abs_path = root / rel_path
                    try:
                        source = abs_path.read_text(
                            encoding="utf-8", errors="replace",
                        )
                    except OSError:
                        source = ""
                    file_tuples.append((rel_path, source, fnode))

                prompt = build_batch_prompt(file_tuples)
                result = await collect_text(
                    provider,
                    prompt=prompt,
                    system_prompt=CONTRACT_EXTRACTION_PROMPT,
                    working_dir=working_dir,
                    max_turns=max_turns,
                )
                parsed = parse_contract_response(result.text)
                batch_result: dict[str, FileContract] = {}
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        if k in stale_nodes:
                            # Only stamp hash when contract has real content
                            if v.exports or v.imports_usage or v.invariants:
                                v.source_hash = v.source_hash or stale_nodes[k].source_hash
                            batch_result[k] = v
                # Fill missing files with empty contracts (no hash — retry next run)
                for rel_path in batch:
                    if rel_path not in batch_result:
                        batch_result[rel_path] = FileContract(rel_path=rel_path)
                return batch_result

    tasks = [_process_batch(b) for b in batch_keys]
    gather_results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, item in enumerate(gather_results):
        if isinstance(item, Exception):
            batch_desc = ", ".join(batch_keys[i]) if i < len(batch_keys) else f"batch {i}"
            print(f"   ⚠ Contract batch failed [{batch_desc}]: {item}")
            continue
        if isinstance(item, dict):
            results.update(item)

    # Fill any stale files that weren't covered (e.g. read errors); no hash so they retry
    for rel_path in stale_nodes:
        if rel_path not in results:
            results[rel_path] = FileContract(rel_path=rel_path)

    # 5. Merge with cached and save
    cached.update(results)

    # Respect config flag for caching
    if getattr(config, "debug_cache_contracts", True):
        save_cached_contracts(cached, cache_path)

    return cached
