"""Scope enumeration — iter_targets(working_dir) for --all mode.

Walks the AST of Python files under *working_dir* and yields every public
function/method as a target dict suitable for LdbRunner consumption.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_SKIP_DIRS = {
    ".git",
    ".worktrees",
    "venv",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ldb-trace",
    ".g3",
    "tests",
}
# "tests" is intentional: LDB targets source code, not test files.
# Tests written by the Tester agent (test_ldb_*.py) live in tests/ and should not be re-analyzed.


@dataclass(frozen=True)
class LdbTarget:
    """A single function/method target for the LDB pipeline."""

    file: str
    name: str
    lineno: int
    end_lineno: int


def _collect_class_targets(
    cls_node: ast.ClassDef,
    rel: str,
    targets: list[LdbTarget],
    prefix: str = "",
) -> None:
    """Recursively collect public methods from *cls_node*, appending to *targets*."""
    qualified = f"{prefix}.{cls_node.name}" if prefix else cls_node.name
    if cls_node.name.startswith("_"):
        return
    for child in cls_node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name.startswith("_"):
                continue
            targets.append(
                LdbTarget(
                    file=rel,
                    name=f"{qualified}.{child.name}",
                    lineno=child.lineno,
                    end_lineno=getattr(child, "end_lineno", child.lineno),
                )
            )
        elif isinstance(child, ast.ClassDef):
            _collect_class_targets(child, rel, targets, prefix=qualified)


def iter_targets(working_dir: str | Path) -> Iterator[LdbTarget]:
    """Yield every public function/method found in Python files under *working_dir*.

    Public = name does not start with ``_``.

    Scope:
      - Top-level functions (``def foo``) in each module.
      - Methods on top-level and nested classes (``class Foo: def bar``,
        ``class Outer: class Inner: def baz``).

    Yields:
        LdbTarget instances sorted by (file, lineno).
    """
    root = Path(working_dir).expanduser().resolve()
    targets: list[LdbTarget] = []

    for path in sorted(root.rglob("*.py")):
        if path.is_symlink():
            continue
        # Bug fix (issue #4): operator precedence — split skip-dir from skip-name,
        # and only skip filenames (not all parts) that start with "_".
        # Without this, src/ldb/__init__.py would be skipped because "ldb" path
        # contains "_" components in nested projects, and ANY file in a path
        # with a "_"-prefixed component dropped out.
        parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in parts):
            continue
        if path.name.startswith("_"):  # only the FILE name, e.g. _private.py
            continue
        rel = str(path.relative_to(root))
        try:
            source = path.read_text(errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                targets.append(
                    LdbTarget(
                        file=rel,
                        name=node.name,
                        lineno=node.lineno,
                        end_lineno=getattr(node, "end_lineno", node.lineno),
                    )
                )
            elif isinstance(node, ast.ClassDef):
                _collect_class_targets(node, rel, targets)

    targets.sort(key=lambda t: (t.file, t.lineno))
    yield from targets
