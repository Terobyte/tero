"""Tests for src/ldb/blocks.py — decompose_function, decompose_file, Block."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ldb.blocks import Block, decompose_file, decompose_function


SIMPLE_FUNC = """\
def add(a, b):
    c = a + b
    return c
"""

BRANCH_FUNC = """\
def abs_val(x):
    if x < 0:
        return -x
    return x
"""

LOOP_FUNC = """\
def sum_list(items):
    total = 0
    for item in items:
        total += item
    return total
"""

CLASS_FUNC = """\
class Foo:
    def bar(self, x):
        if x > 0:
            return x
        return -x
"""

MULTI_FUNC = """\
def alpha():
    return 1

def beta():
    return 2
"""


class TestDecomposeFunctionBasic:
    def test_returns_list_of_blocks(self):
        blocks = decompose_function(SIMPLE_FUNC, "add")
        assert isinstance(blocks, list)
        assert len(blocks) > 0
        assert all(isinstance(b, Block) for b in blocks)

    def test_block_has_required_fields(self):
        blocks = decompose_function(SIMPLE_FUNC, "add")
        for b in blocks:
            assert isinstance(b.id, int)
            assert isinstance(b.source, str)
            assert isinstance(b.statements, list)
            assert isinstance(b.successors, list)

    def test_block_ids_are_unique(self):
        blocks = decompose_function(SIMPLE_FUNC, "add")
        ids = [b.id for b in blocks]
        assert len(ids) == len(set(ids))


class TestDecomposeFunctionBranch:
    def test_branch_produces_multiple_blocks(self):
        blocks = decompose_function(BRANCH_FUNC, "abs_val")
        assert len(blocks) >= 2

    def test_branch_blocks_have_successors(self):
        blocks = decompose_function(BRANCH_FUNC, "abs_val")
        all_successors = []
        for b in blocks:
            all_successors.extend(b.successors)
        assert len(all_successors) >= 1

    def test_branch_blocks_contain_statements(self):
        blocks = decompose_function(BRANCH_FUNC, "abs_val")
        all_stmts = []
        for b in blocks:
            all_stmts.extend(b.statements)
        assert len(all_stmts) >= 1


class TestDecomposeFunctionLoop:
    def test_loop_produces_blocks(self):
        blocks = decompose_function(LOOP_FUNC, "sum_list")
        assert len(blocks) >= 1

    def test_loop_body_has_statements(self):
        blocks = decompose_function(LOOP_FUNC, "sum_list")
        all_stmts = []
        for b in blocks:
            all_stmts.extend(b.statements)
        assert any("total" in s for s in all_stmts)


class TestDecomposeFunctionErrors:
    def test_missing_function_raises_value_error(self):
        with pytest.raises(ValueError, match="not found"):
            decompose_function("def other(): pass", "nonexistent")

    def test_empty_source_raises_value_error(self):
        with pytest.raises(ValueError, match="not found"):
            decompose_function("", "foo")


class TestDecomposeFile:
    def test_decompose_file_reads_and_decomposes(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text(SIMPLE_FUNC)
        blocks = decompose_file(str(p), "add")
        assert len(blocks) > 0
        assert all(isinstance(b, Block) for b in blocks)

    def test_decompose_file_accepts_path_object(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text(SIMPLE_FUNC)
        blocks = decompose_file(p, "add")
        assert len(blocks) > 0

    def test_decompose_file_missing_function_raises(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("def other(): pass\n")
        with pytest.raises(ValueError):
            decompose_file(str(p), "nonexistent")


# ── Step 2.2: Failing tests for planned block_id / start / end API ──


class TestDecomposeFunctionPlannedApi:
    """Tests for the planned API: Block(block_id, lines, start, end).

    These tests are expected to FAIL until blocks.py is updated
    to expose block_id, start, and end attributes on Block.
    """

    def test_decompose_simple_function(self):
        src = "def add(a, b):\n    if a > 0:\n        return a + b\n    return b\n"
        blocks = decompose_function(src, entry="add")
        assert blocks is not None
        assert len(blocks) >= 2  # if-branch, return
        # каждый блок — (block_id: int, lines: list[str], start: int, end: int)
        assert all(isinstance(b.block_id, int) for b in blocks)
        assert all(b.start <= b.end for b in blocks)

    def test_decompose_returns_none_on_syntax_error(self):
        blocks = decompose_function("def broken(\n", entry="broken")
        assert blocks is None
