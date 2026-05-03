"""Block decomposition via staticfg — decompose_function(prog, entry) -> List[Block]."""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

if not hasattr(ast, "NameConstant"):
    ast.NameConstant = type("NameConstant", (ast.Constant,), {})
if not hasattr(ast, "Name"):
    ast.Name = type("Name", (ast.expr,), {"_fields": ("id", "ctx")})
if not hasattr(ast, "Str"):
    ast.Str = type("Str", (ast.Constant,), {})
if not hasattr(ast, "Num"):
    ast.Num = type("Num", (ast.Constant,), {})

from src.ldb.staticfg import CFGBuilder


@dataclass
class Block:
    """Simplified basic-block descriptor."""

    id: int
    block_id: int
    source: str
    start: int
    end: int
    statements: list[str] = field(default_factory=list)
    successors: list[int] = field(default_factory=list)


def decompose_function(prog: str, entry: str) -> List[Block] | None:
    """Parse *prog* and return the basic blocks of function *entry*.

    Args:
        prog: Python source code (file content or snippet).
        entry: Name of the top-level function to decompose.

    Returns:
        Ordered list of Block descriptors with id, source text, statement
        summaries and successor block ids.  Returns ``None`` when the source
        cannot be parsed (syntax error).

    Raises:
        ValueError: If the named function is not found in the source.
    """
    try:
        builder = CFGBuilder()
        cfg = builder.build_from_src(entry, prog)
    except SyntaxError:
        return None

    if entry not in cfg.functioncfgs:
        raise ValueError(f"function '{entry}' not found in source")

    fcfg = cfg.functioncfgs[entry]
    blocks: list[Block] = []

    for raw in fcfg:
        src = raw.get_source() or ""
        stmts = []
        for s in raw.statements:
            if isinstance(s, ast.stmt):
                stmts.append(ast.unparse(s))

        succs = [e.target.id for e in raw.exits]
        start = raw.at() or 0
        end = raw.end() or 0
        blocks.append(
            Block(
                id=raw.id,
                block_id=raw.id,
                source=src,
                start=start,
                end=end,
                statements=stmts,
                successors=succs,
            )
        )

    return blocks


def decompose_file(path: str | Path, entry: str) -> List[Block]:
    """Convenience: decompose from a file path instead of source string."""
    src = Path(path).read_text()
    return decompose_function(src, entry)
