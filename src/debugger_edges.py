"""Edge-analysis module for the graph-aware debugger.

Provides data structures and prompt builders for detecting cross-file bugs at
dependency boundaries (edges) and within circular-dependency groups (SCCs).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from src.debugger_bugs import BugEntry, extract_json_from_text, strip_trailing_commas
from src.debugger_contracts import ExportContract, FileContract
from src.debugger_graph import DependencyGraph, FileNode
from src.debugger_llm import collect_text
from src.debugger_prompts import (
    DEEP_DIVE_PROMPT,
    EDGE_ANALYSIS_PROMPT,
    SCC_ANALYSIS_PROMPT,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CheckType = Literal[
    "signature_mismatch",
    "return_ignored",
    "none_not_handled",
    "exception_uncaught",
    "side_effect_order",
    "type_mismatch",
    # SCC-specific check types
    "init_ordering",
    "stale_state",
    "contract_violation",
    "reimport_side_effect",
]

Confidence = Literal["high", "medium", "low"]


# ---------------------------------------------------------------------------
# Step 1 — EdgeFinding dataclass
# ---------------------------------------------------------------------------

@dataclass
class EdgeFinding:
    """A single cross-file bug finding at a dependency edge.

    Attributes
    ----------
    caller_file:
        Relative path of the file that makes the call.
    callee_file:
        Relative path of the file that defines the callee.
    caller_line:
        Line number in the caller where the suspicious call appears.
    description:
        One-sentence explanation of the suspected bug.
    confidence:
        How certain the finding is (``high``, ``medium``, ``low``).
    check_type:
        The category of cross-file check that flagged this issue.  One of:
        ``signature_mismatch``, ``return_ignored``, ``none_not_handled``,
        ``exception_uncaught``, ``side_effect_order``, ``type_mismatch``,
        ``init_ordering``, ``stale_state``, ``contract_violation``,
        ``reimport_side_effect``.
    """

    caller_file: str = ""
    callee_file: str = ""
    caller_line: int = 0
    description: str = ""
    confidence: Confidence = "medium"
    check_type: CheckType = "signature_mismatch"


# ---------------------------------------------------------------------------
# Step 2 — parse_edge_findings
# ---------------------------------------------------------------------------

_VALID_CONFIDENCES: frozenset[str] = frozenset({"high", "medium", "low"})
_VALID_CHECK_TYPES: frozenset[str] = frozenset({
    "signature_mismatch",
    "return_ignored",
    "none_not_handled",
    "exception_uncaught",
    "side_effect_order",
    "type_mismatch",
    # SCC-specific check types
    "init_ordering",
    "stale_state",
    "contract_violation",
    "reimport_side_effect",
})
_REQUIRED_FIELDS: frozenset[str] = frozenset({"caller_file", "callee_file", "description"})


def parse_edge_findings(raw: str) -> list[EdgeFinding]:
    """Parse raw LLM output into a list of :class:`EdgeFinding` instances.

    Uses :func:`~src.debugger_bugs.extract_json_from_text` to locate JSON
    arrays inside the LLM response, then validates and normalises each entry.

    Validation rules
    ----------------
    * ``confidence`` must be one of ``high``, ``medium``, ``low`` — invalid
      values are replaced with ``"low"``.
    * ``check_type`` must be one of the ten recognised check types (six
      edge types plus four SCC-specific types) — invalid values are
      replaced with ``"type_mismatch"``.
    * ``caller_line`` must be an integer greater than 0 — invalid values are
      replaced with ``0``.
    * Entries missing any of ``caller_file``, ``callee_file``, or
      ``description`` are silently skipped.
    """
    candidates = extract_json_from_text(raw)

    findings: list[EdgeFinding] = []
    seen: set[tuple[str, str, str, int]] = set()

    for candidate in candidates:
        cleaned = strip_trailing_commas(candidate)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            # Skip entries missing required fields
            if not all(k in entry for k in _REQUIRED_FIELDS):
                continue

            # Validate caller_line
            try:
                caller_line = int(entry.get("caller_line", 0))
                if caller_line <= 0:
                    caller_line = 0
            except (TypeError, ValueError):
                caller_line = 0

            # Validate confidence
            confidence = str(entry.get("confidence", "low")).lower()
            if confidence not in _VALID_CONFIDENCES:
                confidence = "low"

            # Validate check_type
            check_type = str(entry.get("check_type", "type_mismatch")).lower()
            if check_type not in _VALID_CHECK_TYPES:
                check_type = "type_mismatch"

            caller_file = str(entry["caller_file"])
            callee_file = str(entry["callee_file"])
            description = str(entry["description"])

            # Deduplicate by (caller_file, callee_file, check_type, caller_line)
            dedup_key = (caller_file, callee_file, check_type, caller_line)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            findings.append(EdgeFinding(
                caller_file=caller_file,
                callee_file=callee_file,
                caller_line=caller_line,
                description=description,
                confidence=confidence,
                check_type=check_type,
            ))

    return findings


# ---------------------------------------------------------------------------
# Step 3 — build_edge_prompt
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


def build_edge_prompt(
    caller_source: str,
    caller_node: FileNode,
    dep_contracts: dict[str, FileContract],
) -> str:
    """Build the user prompt for cross-file edge analysis.

    Parameters
    ----------
    caller_source:
        Full source text of the caller file.
    caller_node:
        The :class:`FileNode` for the caller (used for its ``rel_path``).
    dep_contracts:
        Mapping of ``rel_path`` → :class:`FileContract` for every dependency
        the caller imports.  Each contract's ``exports`` are listed so the
        LLM can verify cross-module usage.

    Returns
    -------
    A prompt string with:
      * The caller's full source (line-numbered).
      * A ``## Dependency Contracts`` section listing each dependency's
        public exports as compact bullet lists.
    """
    numbered_source = _number_source(caller_source)

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

    contracts_block = "\n\n".join(dep_sections) if dep_sections else "*(none)*"

    prompt = (
        f"--- Caller File: {caller_node.rel_path} ---\n\n"
        f"Source code (with line numbers):\n"
        f"```\n{numbered_source}\n```\n\n"
        f"## Dependency Contracts\n\n"
        f"{contracts_block}\n"
    )
    return prompt


# ---------------------------------------------------------------------------
# Step 3 — build_scc_prompt
# ---------------------------------------------------------------------------

def build_scc_prompt(
    scc_files: list[tuple[str, str]],
) -> str:
    """Build the user prompt for SCC (circular dependency) analysis.

    Parameters
    ----------
    scc_files:
        List of ``(rel_path, source_text)`` tuples for every file in the
        strongly connected component.

    Returns
    -------
    A prompt string with:
      * A header explaining these files form a circular dependency group.
      * Each file's full source (line-numbered) under a ``### File:`` heading.
    """
    header = "## Circular Dependency Group — These files have circular imports.\n"

    file_sections: list[str] = []
    for rel_path, source in scc_files:
        numbered = _number_source(source)
        file_sections.append(
            f"### File: {rel_path}\n\n"
            f"```\n{numbered}\n```"
        )

    return header + "\n\n".join(file_sections) + "\n"


# ---------------------------------------------------------------------------
# Step 4 — analyze_edges
# ---------------------------------------------------------------------------

async def analyze_edges(
    graph: DependencyGraph,
    contracts: dict[str, FileContract],
    provider,
    config,
    working_dir: str,
) -> tuple[list[EdgeFinding], list[EdgeFinding]]:
    """Analyse cross-file edges and SCC groups for bugs.

    Returns ``(high_findings, medium_findings)``.  Low-confidence findings
    are discarded.

    Algorithm
    ---------
    1. Identify the set of files that belong to any SCC.
    2. For each SCC group: one LLM call with ``SCC_ANALYSIS_PROMPT`` +
       :func:`build_scc_prompt`.
    3. For each non-SCC file: one LLM call with ``EDGE_ANALYSIS_PROMPT`` +
       :func:`build_edge_prompt`.  Files with no project dependencies or
       no dependency contracts are skipped.
    4. All LLM calls are throttled via
       ``asyncio.Semaphore(config.debug_max_concurrent_llm)`` and
       dispatched with ``asyncio.gather``.
    5. Results are parsed via :func:`parse_edge_findings` and split by
       confidence level.
    """
    max_concurrent = getattr(config, "debug_max_concurrent_llm", 5)
    raw_edge_batch_size = getattr(config, "debug_edge_batch_size", 5)
    edge_batch_size = (
        raw_edge_batch_size
        if isinstance(raw_edge_batch_size, int) and raw_edge_batch_size > 0
        else 5
    )
    max_turns = getattr(config, "max_turns", 10)
    model = getattr(config, "debug_player_model", "")
    sem = asyncio.Semaphore(max_concurrent)
    root = Path(working_dir).expanduser().resolve()

    # (1) Identify SCC files
    scc_files: set[str] = set()
    for scc_group in graph.sccs:
        for f in scc_group:
            scc_files.add(f)

    async def _analyze_scc(scc_group: list[str]) -> list[EdgeFinding]:
        """Run one LLM call for a single SCC group."""
        async with sem:
            # Read sources for all files in the SCC
            scc_file_data: list[tuple[str, str]] = []
            for rel_path in scc_group:
                abs_path = root / rel_path
                try:
                    source = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    source = ""
                scc_file_data.append((rel_path, source))

            user_prompt = build_scc_prompt(scc_file_data)
            result = await collect_text(
                provider,
                prompt=user_prompt,
                system_prompt=SCC_ANALYSIS_PROMPT,
                working_dir=working_dir,
                max_turns=max_turns,
                model=model,
            )
            if result.completed and result.text.strip():
                return parse_edge_findings(result.text)
            return []

    async def _analyze_edge_file(
        rel_path: str,
        fnode: FileNode,
    ) -> list[EdgeFinding]:
        """Run one LLM call for a single non-SCC file."""
        async with sem:
            # Get project dependencies for this file
            deps = graph.dependencies_of(rel_path)
            if not deps:
                return []

            # Gather contracts for dependencies
            dep_contracts: dict[str, FileContract] = {}
            for dep_path in deps:
                if dep_path in contracts:
                    dep_contracts[dep_path] = contracts[dep_path]
            if not dep_contracts:
                return []

            # Read caller source
            abs_path = root / rel_path
            try:
                caller_source = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

            user_prompt = build_edge_prompt(caller_source, fnode, dep_contracts)
            result = await collect_text(
                provider,
                prompt=user_prompt,
                system_prompt=EDGE_ANALYSIS_PROMPT,
                working_dir=working_dir,
                max_turns=max_turns,
                model=model,
            )
            if result.completed and result.text.strip():
                return parse_edge_findings(result.text)
            return []

    # (2) Run SCC groups first
    scc_tasks = [_analyze_scc(scc_group) for scc_group in graph.sccs]
    gather_results: list[object] = []
    if scc_tasks:
        gather_results.extend(await asyncio.gather(*scc_tasks, return_exceptions=True))

    # (3) Run non-SCC edge files in configurable batches
    edge_tasks = []
    for rel_path, fnode in graph.files.items():
        if rel_path in scc_files:
            continue
        edge_tasks.append(_analyze_edge_file(rel_path, fnode))

    for i in range(0, len(edge_tasks), edge_batch_size):
        batch = edge_tasks[i : i + edge_batch_size]
        gather_results.extend(await asyncio.gather(*batch, return_exceptions=True))

    # (5) Collect and split by confidence
    all_findings: list[EdgeFinding] = []
    for item in gather_results:
        if isinstance(item, Exception):
            print(f"   ⚠ Edge analysis failed: {item}")
            continue
        if isinstance(item, list):
            all_findings.extend(item)

    high_findings = [f for f in all_findings if f.confidence == "high"]
    medium_findings = [f for f in all_findings if f.confidence == "medium"]

    return high_findings, medium_findings


# ---------------------------------------------------------------------------
# Step 5 — deep_dive
# ---------------------------------------------------------------------------

async def deep_dive(
    finding: EdgeFinding,
    working_dir: str,
    provider,
    config,
) -> EdgeFinding | None:
    """Verify a single finding by reading both source files and asking the LLM.

    Reads the full source of both caller and callee, formats the
    ``DEEP_DIVE_PROMPT`` with finding details, and asks the LLM to confirm
    or refute the bug.

    Returns
    -------
    EdgeFinding
        The finding with ``confidence`` set to ``"high"`` and the reason
        appended to the description (if confirmed).
    None
        If the LLM refutes the finding.
    """
    max_turns = getattr(config, "max_turns", 10)
    model = getattr(config, "debug_player_model", "")
    root = Path(working_dir).expanduser().resolve()

    # Read caller source
    caller_path = root / finding.caller_file
    try:
        caller_source = caller_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Read callee source
    callee_path = root / finding.callee_file
    try:
        callee_source = callee_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Build the user prompt by formatting DEEP_DIVE_PROMPT
    user_prompt = DEEP_DIVE_PROMPT.format(
        caller_file=finding.caller_file,
        callee_file=finding.callee_file,
        caller_line=finding.caller_line,
        check_type=finding.check_type,
        description=finding.description,
    )

    # Append both full sources
    user_prompt += (
        f"\n\n--- Caller: {finding.caller_file} ---\n"
        f"```\n{caller_source}\n```\n\n"
        f"--- Callee: {finding.callee_file} ---\n"
        f"```\n{callee_source}\n```\n"
    )

    result = await collect_text(
        provider,
        prompt=user_prompt,
        system_prompt="You are a cross-file bug verification assistant. "
        "Respond ONLY with a JSON object containing 'confirmed' (bool) "
        "and 'reason' (str).",
        working_dir=working_dir,
        max_turns=max_turns,
        model=model,
    )

    if not result.completed or not result.text.strip():
        return None

    # Parse the JSON response {"confirmed": bool, "reason": str}
    candidates = extract_json_from_text(result.text)
    for candidate in candidates:
        cleaned = strip_trailing_commas(candidate)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "confirmed" in data:
            confirmed = bool(data["confirmed"])
            reason = str(data.get("reason", ""))
            if confirmed:
                finding.confidence = "high"
                finding.description = f"{finding.description} — {reason}"
                return finding
            else:
                return None

    # Could not parse → treat as unconfirmed
    return None


# ---------------------------------------------------------------------------
# Step 6 — run_deep_dives
# ---------------------------------------------------------------------------

async def run_deep_dives(
    medium_findings: list[EdgeFinding],
    working_dir: str,
    provider,
    config,
) -> list[EdgeFinding]:
    """Orchestrate deep dives on medium-confidence findings.

    The behaviour depends on ``config.debug_deep_dive_mode``:

    ``"skip"``
        Return an empty list immediately.
    ``"normal"``
        Dive the top 10 medium findings (sorted by ``caller_line`` for
        determinism).
    ``"aggressive"``
        Dive ALL medium findings.
    """
    mode = getattr(config, "debug_deep_dive_mode", "normal")

    if mode == "skip" or not medium_findings:
        return []

    # Select candidates based on mode
    sorted_findings = sorted(medium_findings, key=lambda f: f.caller_line)
    if mode == "normal":
        candidates = sorted_findings[:10]
    elif mode == "aggressive":
        candidates = sorted_findings
    else:
        # Unknown mode → treat as normal
        candidates = sorted_findings[:10]

    max_concurrent = getattr(config, "debug_max_concurrent_llm", 5)
    sem = asyncio.Semaphore(max_concurrent)

    async def _dive_one(finding: EdgeFinding) -> EdgeFinding | None:
        async with sem:
            return await deep_dive(finding, working_dir, provider, config)

    tasks = [_dive_one(f) for f in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    confirmed: list[EdgeFinding] = []
    for item in results:
        if isinstance(item, Exception):
            print(f"   ⚠ Deep dive failed: {item}")
            continue
        if isinstance(item, EdgeFinding):
            confirmed.append(item)

    return confirmed


# ---------------------------------------------------------------------------
# Step 7 — findings_to_bugs
# ---------------------------------------------------------------------------

def findings_to_bugs(
    findings: list[EdgeFinding],
    start_id: int = 1,
) -> list[BugEntry]:
    """Convert :class:`EdgeFinding` instances to :class:`BugEntry` instances.

    Each finding becomes a bug with:
      - ``file`` = caller_file
      - ``line`` = caller_line
      - ``description`` = ``"[{check_type}] {description}"``
      - ``severity`` = ``"high"``
      - ``status`` = ``"open"``
    """
    return [
        BugEntry(
            id=start_id + i,
            file=f.caller_file,
            line=f.caller_line,
            description=f"[{f.check_type}] {f.description}",
            severity="high",
            status="open",
        )
        for i, f in enumerate(findings)
    ]
