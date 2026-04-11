"""Intra-file analysis module for the graph-aware debugger.

Provides per-file bug detection that uses dependency contracts to give
the LLM cross-module context while analysing each file individually.
Each file is analysed with multiple system prompts (governed by
``config.debug_intensity``) and results are merged and deduplicated.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.debugger_bugs import BugEntry, _normalize_description, merge_bugs, parse_bugs
from src.debugger_contracts import ExportContract, FileContract
from src.debugger_graph import DependencyGraph, FileNode
from src.debugger_llm import collect_text
from src.debugger_prompts import CONTRACT_AWARENESS_PREFIX, INTENSITY_PROMPTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _number_source(source: str) -> str:
    """Return *source* with 1-based line numbers prefixed."""
    lines: list[str] = []
    for i, line in enumerate(source.splitlines(), start=1):
        lines.append(f"{i:>4} | {line}")
    return "\n".join(lines)


def _format_contract(contract: ExportContract) -> str:
    """Format a single ExportContract as a compact bullet list."""
    bullets: list[str] = []
    bullets.append(f"- **name**: `{contract.name}`")
    if contract.signature:
        bullets.append(f"- **signature**: `{contract.signature}`")
    if contract.return_type:
        bullets.append(f"- **return_type**: `{contract.return_type}`")
    if contract.preconditions:
        bullets.append("- **preconditions**:")
        for p in contract.preconditions:
            bullets.append(f"  - {p}")
    if contract.postconditions:
        bullets.append("- **postconditions**:")
        for p in contract.postconditions:
            bullets.append(f"  - {p}")
    if contract.side_effects:
        bullets.append("- **side_effects**:")
        for s in contract.side_effects:
            bullets.append(f"  - {s}")
    if contract.raises:
        bullets.append("- **raises**:")
        for r in contract.raises:
            bullets.append(f"  - {r}")
    return "\n".join(bullets)


# ---------------------------------------------------------------------------
# Step 1 — build_intra_prompt
# ---------------------------------------------------------------------------

def build_intra_prompt(
    file_source: str,
    file_node: FileNode,
    dep_contracts: dict[str, FileContract],
) -> str:
    """Build the user prompt for intra-file bug analysis.

    Parameters
    ----------
    file_source:
        Full source text of the file to analyse.
    file_node:
        The :class:`FileNode` for the file (used for its ``rel_path``).
    dep_contracts:
        Mapping of ``rel_path`` → :class:`FileContract` for every
        dependency the file imports.  Each contract's ``exports`` are
        listed so the LLM can verify cross-module usage.

    Returns
    -------
    A prompt string with:
      * The file's full source (line-numbered).
      * A ``## Dependency Contracts`` section listing each dependency's
        public exports as compact bullet lists, if any project deps exist.
    """
    numbered_source = _number_source(file_source)

    prompt = (
        f"--- File: {file_node.rel_path} ---\n\n"
        f"Source code (with line numbers):\n"
        f"```\n{numbered_source}\n```\n"
    )

    if not dep_contracts:
        return prompt

    # -- Dependency Contracts section --
    dep_sections: list[str] = []
    for rel_path, fc in dep_contracts.items():
        if not fc.exports:
            dep_sections.append(f"### {rel_path}\n*(no public exports)*\n")
            continue
        export_blocks: list[str] = []
        for export in fc.exports:
            export_blocks.append(_format_contract(export))
        dep_sections.append(
            f"### {rel_path}\n" + "\n".join(export_blocks)
        )

    contracts_block = "\n\n".join(dep_sections)

    prompt += (
        f"\n## Dependency Contracts\n\n"
        f"{contracts_block}\n"
    )
    return prompt


# ---------------------------------------------------------------------------
# Step 2 — analyze_file
# ---------------------------------------------------------------------------

async def analyze_file(
    rel_path: str,
    file_source: str,
    file_node: FileNode,
    contracts: dict[str, FileContract],
    graph: DependencyGraph,
    provider,
    config,
    working_dir: str = ".",
) -> list[BugEntry]:
    """Run intra-file bug analysis on a single file.

    Gets dependencies via ``graph.dependencies_of(rel_path)``, gathers
    their contracts, builds the user prompt via :func:`build_intra_prompt`,
    then runs the file through each system prompt from
    ``INTENSITY_PROMPTS[config.debug_intensity]`` (with
    ``CONTRACT_AWARENESS_PREFIX`` prepended).

    For each system prompt the LLM is called with ``max_turns=1`` via
    :func:`collect_text`.  If the LLM response is incomplete that pass
    is skipped (not aborted — remaining prompts still run).

    Returns all bugs found across all passes.
    """
    # Get project dependencies for this file
    deps = graph.dependencies_of(rel_path)

    # Gather contracts for dependencies
    dep_contracts: dict[str, FileContract] = {}
    for dep_path in deps:
        if dep_path in contracts:
            dep_contracts[dep_path] = contracts[dep_path]

    # Build user prompt
    user_prompt = build_intra_prompt(file_source, file_node, dep_contracts)

    # Get system prompts based on intensity
    system_prompts = INTENSITY_PROMPTS.get(
        config.debug_intensity,
        INTENSITY_PROMPTS["medium"],
    )

    model = getattr(config, "debug_player_model", "")

    all_bugs: list[BugEntry] = []
    start_id = 1

    for system_prompt in system_prompts:
        full_system_prompt = CONTRACT_AWARENESS_PREFIX + system_prompt

        result = await collect_text(
            provider,
            prompt=user_prompt,
            system_prompt=full_system_prompt,
            working_dir=working_dir,
            max_turns=1,
            model=model,
        )

        # If incomplete → skip this pass (don't abort)
        if not result.completed or not result.text.strip():
            continue

        found = parse_bugs(result.text, start_id=start_id)
        start_id += len(found)
        all_bugs.extend(found)

    return all_bugs


# ---------------------------------------------------------------------------
# Step 3 — analyze_all_files
# ---------------------------------------------------------------------------

async def analyze_all_files(
    graph: DependencyGraph,
    contracts: dict[str, FileContract],
    provider,
    config,
    working_dir: str,
    existing_bugs: list[BugEntry],
) -> list[BugEntry]:
    """Analyse every file in *graph* for intra-file bugs concurrently.

    For each file: create an async task throttled by
    ``asyncio.Semaphore(config.debug_max_concurrent_llm)``.  All tasks
    are dispatched via ``asyncio.gather``.

    New bugs are deduplicated internally (same ``file + line`` or same
    ``file + normalized_description`` across different prompts) and then
    merged with *existing_bugs* via :func:`merge_bugs`.

    Returns the **complete merged list** (existing + new), not just new
    bugs.
    """
    max_concurrent = getattr(config, "debug_max_concurrent_llm", 5)
    sem = asyncio.Semaphore(max_concurrent)
    root = Path(working_dir).expanduser().resolve()

    async def _analyze_one(rel_path: str, fnode: FileNode) -> list[BugEntry]:
        """Analyse a single file under the semaphore."""
        async with sem:
            abs_path = root / rel_path
            try:
                source = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []
            return await analyze_file(
                rel_path, source, fnode, contracts, graph, provider, config,
                working_dir=working_dir,
            )

    # Build tasks for every file in the graph
    tasks = [
        _analyze_one(rel_path, fnode)
        for rel_path, fnode in graph.files.items()
    ]

    gather_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect all new bugs and deduplicate internally
    all_new: list[BugEntry] = []
    seen_lines: set[tuple[str, int]] = set()
    seen_descs: set[tuple[str, str]] = set()

    for item in gather_results:
        if isinstance(item, Exception):
            print(f"   ⚠ Intra-file analysis failed: {item}")
            continue
        if not isinstance(item, list):
            continue
        for bug in item:
            line_key = (bug.file, bug.line)
            desc_key = (bug.file, _normalize_description(bug.description))
            if line_key not in seen_lines and desc_key not in seen_descs:
                seen_lines.add(line_key)
                seen_descs.add(desc_key)
                all_new.append(bug)

    # Merge with existing bugs and return the complete list
    return merge_bugs(existing_bugs, all_new)
