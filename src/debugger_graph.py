"""Data model for the debugger dependency graph.

Defines the core dataclasses used to represent code structure, imports,
external calls, and inter-file dependencies within a project.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_SKIP_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".tox", ".eggs", "dist", "build",
    ".ruff_cache", ".hypothesis",
    "tests", "test",
    "docs", ".worktrees",
}


def discover_py_files(working_dir: str) -> list[str]:
    """Walk working_dir and return sorted relative paths to .py files, skipping noise dirs."""
    root = Path(working_dir).expanduser().resolve()
    rel_paths: list[str] = []
    for path in sorted(root.rglob("*.py")):
        # Skip any path that has a skip-dir component
        parts = set(path.relative_to(root).parts)
        if parts & _SKIP_DIRS:
            continue
        rel_paths.append(str(path.relative_to(root)))
    return rel_paths


@dataclass
class FunctionSig:
    """Signature of a function extracted from source code."""

    name: str
    args: List[str]
    returns: Optional[str]
    lineno: int
    is_async: bool = False


@dataclass
class ClassSig:
    """Signature of a class extracted from source code."""

    name: str
    methods: List[FunctionSig]
    bases: List[str]
    lineno: int


@dataclass
class ImportEdge:
    """An import statement linking one file to another module."""

    source_file: str
    target_module: str
    symbols: List[str]
    lineno: int
    resolved_path: Optional[str] = None


@dataclass
class ExternalCall:
    """A call from within the project to an external / stdlib module."""

    caller_func: str
    callee_module: str
    callee_name: str
    lineno: int


@dataclass
class FileNode:
    """Per-file summary of extracted code structure."""

    rel_path: str
    functions: List[FunctionSig]
    classes: List[ClassSig]
    imports: List[ImportEdge]
    external_calls: List[ExternalCall]
    line_count: int
    source_hash: str = ""

    def compute_hash(self, source_text: str) -> str:
        """Compute and store a SHA-256 hash of the raw source text."""
        self.source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        return self.source_hash


@dataclass
class DependencyGraph:
    """Complete dependency graph across all project files."""

    files: Dict[str, FileNode] = field(default_factory=dict)
    edges: List[ImportEdge] = field(default_factory=list)
    sccs: List[List[str]] = field(default_factory=list)

    # --- helper lookups ---------------------------------------------------

    def dependents_of(self, rel_path: str) -> Set[str]:
        """Return the set of file paths that *depend on* the given file.

        In other words, files whose ``ImportEdge`` resolves to *rel_path*.
        """
        dependents: Set[str] = set()
        for edge in self.edges:
            if edge.resolved_path == rel_path and edge.source_file != rel_path:
                dependents.add(edge.source_file)
        return dependents

    def dependencies_of(self, rel_path: str) -> Set[str]:
        """Return the set of file paths that the given file *depends on*.

        In other words, files whose path matches a resolved ``ImportEdge``
        originating from *rel_path*.
        """
        deps: Set[str] = set()
        for edge in self.edges:
            if edge.source_file == rel_path and edge.resolved_path is not None:
                deps.add(edge.resolved_path)
        return deps


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------

def resolve_import(
    module_path: str,
    working_dir: str,
    source_file: str | None = None,
    level: int = 0,
) -> str | None:
    """Resolve an import to a relative file path within the project.

    Returns ``None`` if the import targets the stdlib or an unresolvable
    third-party package.

    Parameters
    ----------
    module_path:
        Dotted module name from the import statement (e.g. ``"src.config"``).
        May be empty for bare relative imports (``from . import X``).
    working_dir:
        Project root directory.
    source_file:
        Relative path of the file containing the import (needed for relative
        imports).
    level:
        Number of dots in a relative import (0 = absolute).
    """
    root = Path(working_dir).expanduser().resolve()

    # --- relative imports ---------------------------------------------------
    if level > 0:
        if source_file is None:
            return None

        source = root / source_file
        base = source.parent
        # Go up (level - 1) directories from the source's directory
        for _ in range(level - 1):
            base = base.parent
        # Guard against escaping the project root
        try:
            base.relative_to(root)
        except ValueError:
            return None

        if module_path:
            candidate = base / module_path.replace(".", "/")
        else:
            candidate = base

        # Try <candidate>.py
        py_file = candidate.with_suffix(".py")
        if py_file.exists():
            try:
                return str(py_file.relative_to(root))
            except ValueError:
                return None

        # Try <candidate>/__init__.py
        init_file = candidate / "__init__.py"
        if init_file.exists():
            try:
                return str(init_file.relative_to(root))
            except ValueError:
                return None

        return None

    # --- absolute imports ---------------------------------------------------
    # Check against stdlib module names (Python 3.10+)
    if hasattr(sys, "stdlib_module_names"):
        top_level = module_path.split(".")[0]
        if top_level in sys.stdlib_module_names:
            return None

    # Try within the project tree
    candidate_path = module_path.replace(".", "/")
    py_file = root / (candidate_path + ".py")
    if py_file.exists():
        try:
            return str(py_file.relative_to(root))
        except ValueError:
            return None

    init_file = root / candidate_path / "__init__.py"
    if init_file.exists():
        try:
            return str(init_file.relative_to(root))
        except ValueError:
            return None

    # Not found — likely third-party
    return None


# ---------------------------------------------------------------------------
# AST helpers (private)
# ---------------------------------------------------------------------------

def _extract_annotation(node: ast.expr | None) -> str | None:
    """Convert a return-type annotation AST node to a string."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_extract_annotation(node.value)}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return f"{_extract_annotation(node.value)}[{_extract_annotation(node.slice)}]"
    if isinstance(node, ast.Index):  # Python 3.8 compat
        return _extract_annotation(node.value)
    if isinstance(node, ast.Tuple):
        return ", ".join(_extract_annotation(e) or "" for e in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _extract_annotation(node.left) or ""
        right = _extract_annotation(node.right) or ""
        return f"{left} | {right}"
    # Fallback: ast.unparse (Python 3.9+)
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _func_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extract argument names from a function definition."""
    all_args = (
        list(getattr(node.args, "posonlyargs", []))
        + list(node.args.args)
        + list(node.args.kwonlyargs)
    )
    result = [a.arg for a in all_args]
    if node.args.vararg:
        result.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        result.append(f"**{node.args.kwarg.arg}")
    return result


def _func_sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionSig:
    """Build a :class:`FunctionSig` from an AST function node."""
    return FunctionSig(
        name=node.name,
        args=_func_args(node),
        returns=_extract_annotation(node.returns),
        lineno=node.lineno,
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )


def _class_bases(node: ast.ClassDef) -> list[str]:
    """Extract base-class name strings from a class definition."""
    bases: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            bases.append(base.id)
        else:
            try:
                bases.append(ast.unparse(base))
            except Exception:
                pass
    return bases


def _source_package(rel_path: str) -> list[str]:
    """Return the package parts of *rel_path*.

    ``src/providers/__init__.py`` → ``["src", "providers"]``
    ``src/providers/foo.py``      → ``["src", "providers"]``
    ``foo.py``                    → ``[]``
    """
    p = Path(rel_path)
    if p.name == "__init__.py":
        parts = list(p.parent.parts)
    else:
        parts = list(p.with_suffix("").parts[:-1])
    return parts


def _compute_full_module(rel_path: str, level: int, module_path: str) -> str | None:
    """Compute the full dotted module path for a relative import."""
    pkg_parts = _source_package(rel_path)
    parent_hops = max(level - 1, 0)
    if parent_hops > len(pkg_parts):
        return None
    base_parts = pkg_parts[: len(pkg_parts) - parent_hops]
    if module_path:
        return ".".join(base_parts + module_path.split("."))
    return ".".join(base_parts) or None


# ---------------------------------------------------------------------------
# File parser
# ---------------------------------------------------------------------------

def parse_file(
    path: str,
    rel_path: str,
    working_dir: str,
) -> FileNode | None:
    """Parse a single Python file and extract code structure.

    Uses :func:`ast.parse` to extract function signatures, class signatures,
    import edges, and external calls.  Returns ``None`` on :class:`SyntaxError`.
    """
    try:
        source = Path(path).read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return None

    functions: list[FunctionSig] = []
    classes: list[ClassSig] = []
    imports: list[ImportEdge] = []
    external_calls: list[ExternalCall] = []

    # import_map: local_name → dotted module path
    import_map: dict[str, str] = {}

    # -- single pass over top-level nodes ------------------------------------
    for node in ast.iter_child_nodes(tree):
        # --- module-level functions -----------------------------------------
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_func_sig(node))
            continue

        # --- classes --------------------------------------------------------
        if isinstance(node, ast.ClassDef):
            methods: list[FunctionSig] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(_func_sig(item))
                    # Alias tracking inside __init__
                    if item.name == "__init__":
                        for stmt in ast.walk(item):
                            if (
                                isinstance(stmt, ast.Assign)
                                and isinstance(stmt.value, ast.Name)
                                and stmt.value.id in import_map
                            ):
                                for target in stmt.targets:
                                    if isinstance(target, ast.Name):
                                        import_map[target.id] = import_map[stmt.value.id]
            classes.append(ClassSig(
                name=node.name,
                methods=methods,
                bases=_class_bases(node),
                lineno=node.lineno,
            ))
            continue

        # --- ``import X`` ---------------------------------------------------
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_path = alias.name
                local_name = alias.asname or alias.name
                import_map[local_name] = module_path
                resolved = resolve_import(
                    module_path, working_dir, source_file=rel_path,
                )
                imports.append(ImportEdge(
                    source_file=rel_path,
                    target_module=module_path,
                    symbols=[local_name],
                    lineno=node.lineno,
                    resolved_path=resolved,
                ))
            continue

        # --- ``from X import Y`` -------------------------------------------
        if isinstance(node, ast.ImportFrom):
            raw_module: str = node.module or ""
            symbols = [alias.name for alias in node.names]
            level = node.level or 0

            # Bare relative import: ``from . import b, c``
            # Each symbol names a sibling module, so resolve individually.
            if raw_module == "" and level > 0:
                for alias in node.names:
                    sym_name = alias.name
                    local_name = alias.asname or alias.name
                    # Per-symbol fully-qualified module for import_map
                    sym_full = _compute_full_module(rel_path, level, sym_name)
                    if sym_full is not None:
                        import_map[local_name] = sym_full
                    resolved = resolve_import(
                        sym_name, working_dir,
                        source_file=rel_path, level=level,
                    )
                    imports.append(ImportEdge(
                        source_file=rel_path,
                        target_module=sym_full or sym_name,
                        symbols=[local_name],
                        lineno=node.lineno,
                        resolved_path=resolved,
                    ))
            else:
                # Qualified relative or absolute: ``from .x import Y``
                # or ``from pkg.mod import Y``
                if level > 0:
                    full_module = _compute_full_module(rel_path, level, raw_module)
                else:
                    full_module = raw_module
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if full_module is not None:
                        import_map[local_name] = full_module
                resolved = resolve_import(
                    raw_module, working_dir,
                    source_file=rel_path, level=level,
                )
                imports.append(ImportEdge(
                    source_file=rel_path,
                    target_module=full_module or raw_module,
                    symbols=symbols,
                    lineno=node.lineno,
                    resolved_path=resolved,
                ))
            continue

        # --- module-level alias tracking ------------------------------------
        if isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Name) and node.value.id in import_map:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        import_map[target.id] = import_map[node.value.id]
            elif (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in import_map
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        import_map[target.id] = import_map[node.value.func.id]

    # -- second pass: find external calls ------------------------------------
    def _walk_for_calls(n: ast.AST, func_name: str) -> None:
        """Recursively discover ``ExternalCall`` nodes."""
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk_for_calls(child, child.name)
            elif isinstance(child, ast.ClassDef):
                for item in child.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        _walk_for_calls(item, f"{child.name}.{item.name}")
                    else:
                        _walk_for_calls(item, func_name)
            elif isinstance(child, ast.Call):
                func = child.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id in import_map
                ):
                    external_calls.append(ExternalCall(
                        caller_func=func_name,
                        callee_module=import_map[func.value.id],
                        callee_name=func.attr,
                        lineno=child.lineno,
                    ))
                # Recurse into the call's arguments for nested calls
                _walk_for_calls(child, func_name)
            else:
                _walk_for_calls(child, func_name)

    _walk_for_calls(tree, "<module>")

    lines = source.splitlines()
    file_node = FileNode(
        rel_path=rel_path,
        functions=functions,
        classes=classes,
        imports=imports,
        external_calls=external_calls,
        line_count=len(lines),
    )
    file_node.compute_hash(source)
    return file_node


# ---------------------------------------------------------------------------
# Strongly-connected components (Tarjan's algorithm)
# ---------------------------------------------------------------------------

def find_sccs(adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Compute all strongly-connected components using Tarjan's algorithm.

    Performs a single DFS pass in O(V+E) time.  Returns *all* SCCs, including
    single-node components — the caller is responsible for filtering to SCCs
    with 2+ nodes if only cycles are desired.

    Parameters
    ----------
    adjacency:
        Mapping from each node to the list of nodes it has edges to.
        Nodes with no outgoing edges should still appear as keys (with an
        empty list) so they are visited by the DFS.
    """
    index_counter = [0]          # mutable counter shared across recursive calls
    stack: list[str] = []
    on_stack: set[str] = set()
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    sccs: list[list[str]] = []

    def _strongconnect(v: str) -> None:
        index[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in adjacency.get(v, []):
            if w not in index:
                _strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])

        # If v is a root node, pop the SCC
        if lowlink[v] == index[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            sccs.append(component)

    # Ensure every node in the adjacency dict is visited
    for node in adjacency:
        if node not in index:
            _strongconnect(node)

    return sccs


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_dependency_graph(working_dir: str) -> DependencyGraph:
    """Build a :class:`DependencyGraph` for the project rooted at *working_dir*.

    The process:
      1. Discover all relevant Python files.
      2. Parse each file to extract structure and import edges.
      3. Collect resolved :class:`ImportEdge` objects.
      4. Build an adjacency dictionary from the edges.
      5. Run :func:`find_sccs` and filter to SCCs with 2+ nodes.
      6. Return the completed :class:`DependencyGraph`.
    """
    rel_paths = discover_py_files(working_dir)
    root = Path(working_dir).expanduser().resolve()

    files: dict[str, FileNode] = {}
    all_edges: list[ImportEdge] = []

    # Parse every file
    for rel_path in rel_paths:
        abs_path = str(root / rel_path)
        file_node = parse_file(abs_path, rel_path, working_dir)
        if file_node is not None:
            files[rel_path] = file_node
            # Collect only import edges that resolve to a project file
            for edge in file_node.imports:
                if edge.resolved_path is not None:
                    all_edges.append(edge)

    # Build adjacency dict: source_file -> [resolved targets]
    adjacency: dict[str, list[str]] = {rp: [] for rp in files}
    for edge in all_edges:
        if edge.source_file in adjacency and edge.resolved_path in files:
            adjacency[edge.source_file].append(edge.resolved_path)

    # Find SCCs and keep only those with 2+ nodes (actual cycles)
    all_sccs = find_sccs(adjacency)
    cycles = [scc for scc in all_sccs if len(scc) >= 2]

    return DependencyGraph(files=files, edges=all_edges, sccs=cycles)
