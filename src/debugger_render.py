"""Render module for the graph-aware debugger.

Produces markdown-formatted context strings that combine source-code listings
(with numbered lines) and structured contract summaries for dependency files.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.debugger_contracts import FileContract
from src.debugger_graph import DependencyGraph

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LARGE_FILE_LINE_THRESHOLD = 500
LARGE_FILE_HEAD_LINES = 200
LARGE_FILE_TAIL_LINES = 100
MAX_SYMBOLS = 200

_SYMBOL_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kind>async def|def|class)\s+(?P<name>[A-Za-z_]\w*)"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _number_lines(lines: list[str], start: int = 1) -> str:
    """Return *lines* with right-aligned line numbers prepended."""
    if not lines:
        return ""
    width = len(str(start + len(lines) - 1))
    return "\n".join(
        f"{lineno:>{width}}: {line}"
        for lineno, line in enumerate(lines, start=start)
    )


# ---------------------------------------------------------------------------
# Step 1 — render_file_with_lines
# ---------------------------------------------------------------------------

def render_file_with_lines(rel_path: str, source: str) -> str:
    """Render a file as a markdown section with numbered lines.

    For files ≤ 500 lines the entire source is shown inside a fenced code
    block preceded by a ``### File: {rel_path}`` header.

    For files > 500 lines the output contains:
      1. First 200 lines (numbered)
      2. ``... [N lines omitted]`` marker
      3. Last 100 lines (numbered)
      4. A symbol index produced by :func:`build_symbol_index`
    """
    lines = source.splitlines()
    line_count = len(lines)
    fence = "```python" if rel_path.endswith(".py") else "```"
    header = f"### File: {rel_path}"

    if line_count <= LARGE_FILE_LINE_THRESHOLD:
        numbered = _number_lines(lines)
        return f"{header}\n{fence}\n{numbered}\n```\n"

    # --- Large file: head + omitted marker + tail + symbol index ---
    head = _number_lines(lines[:LARGE_FILE_HEAD_LINES])
    tail_lines = lines[-LARGE_FILE_TAIL_LINES:]
    tail_start = line_count - LARGE_FILE_TAIL_LINES + 1
    tail = _number_lines(tail_lines, start=tail_start)
    omitted = line_count - LARGE_FILE_HEAD_LINES - LARGE_FILE_TAIL_LINES

    symbol_idx = build_symbol_index(source)

    parts = [f"{header}\n{fence}"]
    parts.append(head)
    parts.append(f"... [{omitted} lines omitted]")
    parts.append(tail)
    parts.append("```")
    if symbol_idx:
        parts.append(symbol_idx.rstrip())

    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Step 2 — build_symbol_index
# ---------------------------------------------------------------------------

def build_symbol_index(source: str) -> str:
    """Build a regex-based index of top-level ``def``, ``async def``, and ``class`` definitions.

    Scans *source* for lines starting with ``async def``, ``def``, or ``class``
    at indent level 0 and returns a multi-line string with
    ``{lineno}: {signature}`` entries.

    Simplified migration of ``_build_symbol_index`` from
    ``debugger_context.py`` — only top-level (indent 0) definitions.

    Returns an empty string if no symbols are found.
    """
    matches: list[str] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        m = _SYMBOL_RE.match(line)
        if not m:
            continue
        if m.group("indent"):
            continue
        signature = line.strip().rstrip(":")
        matches.append(f"{lineno:>4}: {signature}")
        if len(matches) >= MAX_SYMBOLS:
            matches.append("     ...")
            break
    if not matches:
        return ""
    return "### Symbols\n" + "\n".join(matches) + "\n"


# ---------------------------------------------------------------------------
# Step 3 — render_contracts_section
# ---------------------------------------------------------------------------

def render_contracts_section(dep_contracts: dict[str, FileContract]) -> str:
    """Render a ``## Dependency Contracts`` markdown section.

    For each file in *dep_contracts* a sub-section is emitted listing export
    names, signatures, preconditions, postconditions, side-effects, and
    raised exceptions.

    Returns an empty string when *dep_contracts* is empty.
    """
    if not dep_contracts:
        return ""

    parts: list[str] = ["## Dependency Contracts"]

    for rel_path in sorted(dep_contracts):
        contract = dep_contracts[rel_path]
        parts.append(f"\n### {rel_path}")

        if not contract.exports:
            parts.append("(no exports)")
            continue

        for export in contract.exports:
            parts.append(f"\n#### `{export.name}`")
            if export.signature:
                parts.append(f"- **signature**: `{export.signature}`")
            if export.preconditions:
                parts.append(f"- **pre**: {', '.join(export.preconditions)}")
            if export.postconditions:
                parts.append(f"- **post**: {', '.join(export.postconditions)}")
            if export.side_effects:
                parts.append(f"- **side_effects**: {', '.join(export.side_effects)}")
            if export.raises:
                parts.append(f"- **raises**: {', '.join(export.raises)}")
            if export.return_type:
                parts.append(f"- **returns**: `{export.return_type}`")

    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Step 4 — build_context_from_graph
# ---------------------------------------------------------------------------

def build_context_from_graph(
    graph: DependencyGraph,
    contracts: dict[str, FileContract],
    working_dir: str,
    file_subset: list[str],
) -> str:
    """Build a combined context string from a dependency graph and contracts.

    For each file in *file_subset*:
      1. Read the source from *working_dir*.
      2. Render via :func:`render_file_with_lines`.

    Then collect (deduped) dependency contracts for all files reachable
    through the graph (excluding files already in *file_subset*) and append
    them via :func:`render_contracts_section`.

    Returns the joined context string.
    """
    root = Path(working_dir).expanduser().resolve()
    sections: list[str] = []
    dep_contract_keys: set[str] = set()

    for rel_path in file_subset:
        abs_path = root / rel_path
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        sections.append(render_file_with_lines(rel_path, source))

        # Collect dependency contracts for this file (exclude files already
        # in the subset to avoid duplication).
        deps = graph.dependencies_of(rel_path)
        for dep_path in deps:
            if dep_path in contracts and dep_path not in file_subset:
                dep_contract_keys.add(dep_path)

    # Build deduped contracts dict (sorted for deterministic output)
    dep_contracts: dict[str, FileContract] = {
        k: contracts[k] for k in sorted(dep_contract_keys) if k in contracts
    }

    contracts_section = render_contracts_section(dep_contracts)
    if contracts_section:
        sections.append(contracts_section)

    return "\n".join(sections)
