"""Debugger context builder — decides what code context to show the player model.

Adapted from the research file_strategy.py. Removes benchmark-specific code
(metadata.json, historical_bug_density, diff view) and adds discover_py_files()
that walks a working directory.
"""

import ast
import re
from pathlib import Path

# Hard cap — prevents context from exceeding model window
MAX_CONTEXT_CHARS = 200_000  # ~50K tokens
BUDGET_CHARS_PER_FILE = 8_000
LARGE_FILE_LINE_THRESHOLD = 200
MAX_SYMBOLS = 200
SHORT_HELPER_LINE_THRESHOLD = 15
SEMANTIC_HELPER_LINE_THRESHOLD = 40
STRUCTURE_OVERVIEW_RATIO = 2.0

_SKIP_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".tox", ".eggs", "dist", "build",
    ".ruff_cache", ".hypothesis",
    "tests", "test",  # skip test dirs — player targets implementation, not tests
    "docs", ".worktrees",
}

SEMANTIC_HELPER_RE = re.compile(
    r"match|valid|count|total|subtotal|history|copy|list|report|active|"
    r"available|reserved|reorder|discount|points|redeem|earn|fulfill|"
    r"ship|transfer|deliver|cancel|create|price"
)


def discover_py_files(working_dir: str) -> list[str]:
    """Walk working_dir and return relative paths to .py files, skipping noise dirs."""
    root = Path(working_dir).expanduser().resolve()
    rel_paths: list[str] = []
    for path in sorted(root.rglob("*.py")):
        # Skip any path that has a skip-dir component
        parts = set(path.relative_to(root).parts)
        if parts & _SKIP_DIRS:
            continue
        rel_paths.append(str(path.relative_to(root)))
    return rel_paths


# ── Formatting helpers ────────────────────────────────────────────────────────

def _format_numbered_lines(content: str) -> list[str]:
    lines = content.splitlines()
    if not lines:
        return []
    width = len(str(len(lines)))
    return [f"{lineno:>{width}}: {line}" for lineno, line in enumerate(lines, start=1)]


def _format_with_line_numbers(content: str) -> str:
    return "\n".join(_format_numbered_lines(content))


def _format_line_slice(content: str, start: int, end: int) -> str:
    lines = content.splitlines()
    if not lines:
        return ""
    start = max(1, start)
    end = min(len(lines), end)
    if start > end:
        return ""
    width = len(str(len(lines)))
    return "\n".join(
        f"{lineno:>{width}}: {lines[lineno - 1]}"
        for lineno in range(start, end + 1)
    )


# ── Symbol index ──────────────────────────────────────────────────────────────

def _build_symbol_index(rel_path: str, content: str) -> str:
    if not rel_path.endswith(".py"):
        return ""
    matches: list[str] = []
    pattern = re.compile(
        r"^(?P<indent>\s*)(?P<kind>async def|def|class)\s+(?P<name>[A-Za-z_]\w*)"
    )
    for lineno, line in enumerate(content.splitlines(), start=1):
        m = pattern.match(line)
        if not m:
            continue
        indent = len(m.group("indent").expandtabs(4)) // 4
        prefix = "  " * min(indent, 3)
        signature = line.strip().rstrip(":")
        matches.append(f"{lineno:>4}: {prefix}{signature}")
        if len(matches) >= MAX_SYMBOLS:
            matches.append("     ...")
            break
    if not matches:
        return ""
    return "### Symbols\n" + "\n".join(matches) + "\n"


# ── Python AST-based hotspot detection ───────────────────────────────────────

from dataclasses import dataclass


@dataclass(frozen=True)
class _SymbolEntry:
    lineno: int
    end_lineno: int
    indent: int
    signature: str
    docstring_lines: tuple[str, ...]
    hotspot_reasons: tuple[str, ...]


def _parse_python_symbols(content: str) -> list[_SymbolEntry]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    lines = content.splitlines()
    entries: list[_SymbolEntry] = []

    def _names_in(node: ast.AST) -> set[str]:
        return {c.id for c in ast.walk(node) if isinstance(c, ast.Name)}

    def _parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        args = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        return {a.arg for a in args if a.arg not in {"self", "cls"}}

    def _assignment_names(node: ast.AST) -> set[str]:
        names: set[str] = set()
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                names.update(_assignment_names(child))
        return names

    def _hotspot_reasons(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
        reasons: list[str] = []
        params = _parameter_names(node)
        docstring = ast.get_docstring(node, clean=True) or ""
        helper_line_count = getattr(node, "end_lineno", node.lineno) - node.lineno + 1

        # Aliasing check
        if params:
            for child in ast.walk(node):
                if isinstance(child, ast.Assign) and isinstance(child.value, ast.Name):
                    if child.value.id in params and any(
                        _assignment_names(t) for t in child.targets
                    ):
                        reasons.append("aliasing")
                        break

        # Validation check
        validation = node.name.startswith(("validate", "check", "ensure"))
        if not validation:
            for child in ast.walk(node):
                if isinstance(child, (ast.Raise, ast.Assert)):
                    validation = True
                    break
                if isinstance(child, ast.If) and isinstance(child.test, ast.Compare):
                    if not params or (_names_in(child.test) & params):
                        validation = True
                        break
        if validation:
            reasons.append("validation")

        if any(isinstance(child, ast.Try) for child in ast.walk(node)):
            reasons.append("exceptions")

        if not node.name.startswith("__"):
            semantic_helper = bool(
                SEMANTIC_HELPER_RE.search(node.name.lower())
                or SEMANTIC_HELPER_RE.search(docstring.lower())
            )
            if (
                helper_line_count <= SHORT_HELPER_LINE_THRESHOLD
                or (semantic_helper and helper_line_count <= SEMANTIC_HELPER_LINE_THRESHOLD)
            ):
                reasons.append("business-semantics")

        return tuple(reasons)

    def _visit(nodes: list[ast.stmt], indent: int) -> None:
        for node in nodes:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            signature = ""
            if 1 <= node.lineno <= len(lines):
                signature = lines[node.lineno - 1].strip().rstrip(":")
            docstring = ast.get_docstring(node, clean=True) or ""
            docstring_lines = tuple(
                line.strip() for line in docstring.splitlines() if line.strip()
            )[:3]
            hotspot_reasons: tuple[str, ...] = ()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                hotspot_reasons = _hotspot_reasons(node)
            entries.append(
                _SymbolEntry(
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", node.lineno),
                    indent=indent,
                    signature=signature,
                    docstring_lines=docstring_lines,
                    hotspot_reasons=hotspot_reasons,
                )
            )
            if isinstance(node, ast.ClassDef):
                _visit(node.body, indent + 1)

    _visit(tree.body, indent=0)
    return entries


def _build_docstring_index(content: str) -> str:
    entries = _parse_python_symbols(content)
    if not entries:
        return ""
    lines = ["### Skeleton"]
    for entry in entries[:MAX_SYMBOLS]:
        prefix = "  " * min(entry.indent, 3)
        summary = " | ".join(entry.docstring_lines) if entry.docstring_lines else "(no docstring)"
        lines.append(f"{entry.lineno:>4}: {prefix}`{entry.signature}` — {summary}")
    if len(entries) > MAX_SYMBOLS:
        lines.append("     ...")
    return "\n".join(lines) + "\n"


def _build_hotspot_sections(rel_path: str, content: str) -> str:
    entries = [e for e in _parse_python_symbols(content) if e.hotspot_reasons]
    if not entries:
        return ""
    fence = "```python" if rel_path.endswith(".py") else "```"
    parts = ["### Hotspots"]
    for entry in entries[:MAX_SYMBOLS]:
        reasons = ", ".join(entry.hotspot_reasons)
        body = _format_line_slice(content, entry.lineno, entry.end_lineno)
        parts.append(
            f"#### Lines {entry.lineno}-{entry.end_lineno} "
            f"`{entry.signature}` ({reasons})\n"
            f"{fence}\n{body}\n```"
        )
    if len(entries) > MAX_SYMBOLS:
        parts.append("... [additional hotspots omitted]")
    return "\n".join(parts) + "\n"


# ── Section rendering ─────────────────────────────────────────────────────────

def _code_fence(rel_path: str) -> str:
    return "```python" if rel_path.endswith(".py") else "```"


def _section_header(rel_path: str, content: str, *, truncated: bool = False) -> str:
    title = f"### File: {rel_path}"
    if truncated:
        title += " (truncated)"
    return f"{title}\n{_build_symbol_index(rel_path, content)}"


def _render_large_python_section(rel_path: str, content: str) -> str:
    header = _section_header(rel_path, content)
    skeleton = _build_docstring_index(content)
    hotspots = _build_hotspot_sections(rel_path, content)
    return f"{header}{hotspots}{skeleton}".rstrip() + "\n"


def _render_section(rel_path: str, content: str, *, truncated: bool = False) -> str:
    header = _section_header(rel_path, content, truncated=truncated)
    fence = _code_fence(rel_path)
    numbered = _format_with_line_numbers(content)
    return f"{header}{fence}\n{numbered}\n```\n"


def _build_excerpt_body(numbered_lines: list[str], window_count: int, lines_per_window: int) -> str:
    total_lines = len(numbered_lines)
    if total_lines == 0 or lines_per_window <= 0:
        return ""
    window_count = max(1, min(window_count, total_lines))
    max_start = max(0, total_lines - lines_per_window)
    if window_count == 1 or max_start == 0:
        starts = [0]
    else:
        starts = sorted({
            round(i * max_start / (window_count - 1))
            for i in range(window_count)
        })
    windows: list[list[int]] = []
    for start in starts:
        end = min(total_lines, start + lines_per_window)
        if windows and start <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    parts: list[str] = []
    previous_end = 0
    for start, end in windows:
        if start > previous_end:
            omitted_count = start - previous_end
            parts.append(f"... [{omitted_count} omitted lines: {previous_end + 1}-{start}]")
        parts.extend(numbered_lines[start:end])
        previous_end = end
    if previous_end < total_lines:
        omitted_count = total_lines - previous_end
        parts.append(f"... [{omitted_count} omitted lines: {previous_end + 1}-{total_lines}]")
    return "\n".join(parts)


def _sample_numbered_body(content: str, max_chars: int) -> str:
    numbered_lines = _format_numbered_lines(content)
    if not numbered_lines or max_chars <= 0:
        return ""
    full_body = "\n".join(numbered_lines)
    if len(full_body) <= max_chars:
        return full_body
    average_line_len = max(
        1,
        sum(len(line) + 1 for line in numbered_lines) // len(numbered_lines),
    )
    max_visible_lines = max(1, max_chars // average_line_len)
    max_window_count = min(5, len(numbered_lines))
    for window_count in range(max_window_count, 0, -1):
        lines_per_window = max(1, max_visible_lines // window_count)
        while lines_per_window >= 1:
            body = _build_excerpt_body(numbered_lines, window_count, lines_per_window)
            if len(body) <= max_chars:
                return body
            lines_per_window -= 1
    return full_body[:max_chars]


def _render_truncated_section(rel_path: str, content: str, max_chars: int) -> str:
    header = _section_header(rel_path, content, truncated=True)
    fence = _code_fence(rel_path)
    footer = "\n```\n"
    header = f"{header}{fence}\n"
    if max_chars <= len(header) + len(footer):
        return header[:max_chars]
    body_budget = max_chars - len(header) - len(footer)
    body = _sample_numbered_body(content, body_budget)
    return f"{header}{body}{footer}"


def _truncate_rendered_text(section: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(section) <= max_chars:
        return section
    if max_chars <= 20:
        return section[:max_chars]
    return section[: max_chars - len("\n... [truncated]\n")].rstrip() + "\n... [truncated]\n"


def _context_budget(file_count: int) -> int:
    scaled_budget = BUDGET_CHARS_PER_FILE * max(1, file_count)
    return min(MAX_CONTEXT_CHARS, scaled_budget)


def _allocate_section_budgets(full_sizes: list[int], total_budget: int) -> list[int]:
    if not full_sizes:
        return []
    if total_budget <= 0:
        return [0] * len(full_sizes)
    if sum(full_sizes) <= total_budget:
        return full_sizes[:]
    budgets = [0] * len(full_sizes)
    remaining = set(range(len(full_sizes)))
    remaining_budget = total_budget
    while remaining and remaining_budget > 0:
        share = remaining_budget // len(remaining)
        if share == 0:
            for i in sorted(remaining)[:remaining_budget]:
                budgets[i] += 1
            return budgets
        satisfied = [i for i in remaining if full_sizes[i] <= share]
        if not satisfied:
            ordered = sorted(remaining)
            for i in ordered:
                budgets[i] = share
            remainder = remaining_budget - share * len(ordered)
            for i in ordered[:remainder]:
                budgets[i] += 1
            return budgets
        for i in satisfied:
            budgets[i] = full_sizes[i]
            remaining_budget -= full_sizes[i]
            remaining.remove(i)
    return budgets


def _build_structure_overview(rel_paths: list[str], working_dir: str) -> str:
    root = Path(working_dir)
    lines = [
        "## Repository Structure",
        "(Inspect specific files with your Read tool if needed)",
        "",
    ]
    for rel_path in rel_paths:
        file_path = root / rel_path
        if not file_path.exists():
            continue
        text = file_path.read_text(errors="replace")
        line_count = len(text.splitlines())
        top_symbols: list[str] = []
        if rel_path.endswith(".py"):
            pattern = re.compile(r"^(?:async def|def|class)\s+([A-Za-z_]\w*)")
            for line in text.splitlines():
                m = pattern.match(line.strip())
                if m:
                    top_symbols.append(m.group(1))
                    if len(top_symbols) >= 6:
                        break
        symbol_str = f" [{', '.join(top_symbols)}]" if top_symbols else ""
        lines.append(f"  {rel_path} ({line_count} lines){symbol_str}")
    return "\n".join(lines) + "\n"


def plan_file_chunks(working_dir: str) -> list[list[str]]:
    """Split discovered files into context-budget-sized chunks.

    Each chunk fits within MAX_CONTEXT_CHARS when fully rendered, so the player
    sees complete function bodies instead of truncated fragments.  The debugger
    rotates through chunks across iterations for full codebase coverage.
    """
    rel_paths = discover_py_files(working_dir)
    if not rel_paths:
        return []

    root = Path(working_dir)
    measured: list[tuple[str, int]] = []

    for rel_path in rel_paths:
        file_path = root / rel_path
        try:
            content = file_path.read_text(errors="replace")
        except OSError:
            continue
        line_count = len(content.splitlines())
        use_large = (
            rel_path.endswith(".py")
            and line_count > LARGE_FILE_LINE_THRESHOLD
        )
        if use_large:
            rendered = _render_large_python_section(rel_path, content)
        else:
            rendered = _render_section(rel_path, content)
        measured.append((rel_path, len(rendered)))

    if not measured:
        return []

    # Greedy bin-packing: fill each chunk up to MAX_CONTEXT_CHARS
    chunks: list[list[str]] = []
    current: list[str] = []
    current_size = 0

    for rel_path, size in measured:
        if current_size + size > MAX_CONTEXT_CHARS and current:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(rel_path)
        current_size += size

    if current:
        chunks.append(current)

    return chunks


def build_context(working_dir: str, file_subset: list[str] | None = None) -> str:
    """Build the code context string to send to the player (debugger) model.

    When file_subset is provided, renders only those files (used by chunked
    scanning and targeted tester/fixer context).  Otherwise discovers all
    Python files in working_dir.
    """
    rel_paths = file_subset if file_subset is not None else discover_py_files(working_dir)
    if not rel_paths:
        return ""

    root = Path(working_dir)
    files: list[tuple[str, str, str, str]] = []

    for rel_path in rel_paths:
        file_path = root / rel_path
        try:
            content = file_path.read_text(errors="replace")
        except OSError:
            continue

        line_count = len(content.splitlines())
        full_rendered = _render_section(rel_path, content)
        use_large = (
            rel_path.endswith(".py")
            and (
                line_count > LARGE_FILE_LINE_THRESHOLD
                or (
                    line_count >= LARGE_FILE_LINE_THRESHOLD - 40
                    and len(full_rendered) > BUDGET_CHARS_PER_FILE
                )
            )
        )
        rendered = _render_large_python_section(rel_path, content) if use_large else full_rendered
        render_mode = "large" if use_large else "full"
        files.append((rel_path, content, rendered, render_mode))

    if not files:
        return ""

    join_overhead = max(0, len(files) - 1)
    section_budget = max(0, _context_budget(len(files)) - join_overhead)
    full_sizes = [len(s) for _, _, s, _ in files]
    budgets = _allocate_section_budgets(full_sizes, section_budget)

    sections: list[str] = []
    for budget, (rel_path, content, rendered, render_mode) in zip(budgets, files):
        if budget >= len(rendered):
            sections.append(rendered)
        elif budget > 0:
            if render_mode == "large":
                sections.append(_truncate_rendered_text(rendered, budget))
            else:
                sections.append(_render_truncated_section(rel_path, content, budget))

    return "\n".join(sections)
