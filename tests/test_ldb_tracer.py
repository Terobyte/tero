"""Tests for src/ldb/tracer.py — trace_function."""

from __future__ import annotations

import pytest

from src.ldb.tracer import BlockTrace, trace_function


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


class TestTraceFunctionBasic:
    def test_simple_function_records_blocks(self):
        traces = trace_function(source=SIMPLE_FUNC, test={"a": 1, "b": 2}, entry="add")
        assert len(traces) > 0
        assert all(isinstance(t, BlockTrace) for t in traces)
        assert all(t.hit_count > 0 for t in traces)

    def test_lines_hit_populated(self):
        traces = trace_function(source=SIMPLE_FUNC, test={"a": 1, "b": 2}, entry="add")
        all_lines = []
        for t in traces:
            all_lines.extend(t.lines_hit)
        assert len(all_lines) >= 2

    def test_visitation_order_no_duplicates(self):
        traces = trace_function(source=SIMPLE_FUNC, test={"a": 1, "b": 2}, entry="add")
        block_ids = [t.block_id for t in traces]
        assert block_ids == list(dict.fromkeys(block_ids))


class TestTraceFunctionBranch:
    def test_negative_input_visits_negative_branch(self):
        traces = trace_function(source=BRANCH_FUNC, test={"x": -5}, entry="abs_val")
        assert len(traces) >= 1
        assert all(t.hit_count > 0 for t in traces)

    def test_positive_input_visits_positive_branch(self):
        traces = trace_function(source=BRANCH_FUNC, test={"x": 5}, entry="abs_val")
        assert len(traces) >= 1

    def test_different_inputs_visit_different_blocks(self):
        traces_neg = trace_function(source=BRANCH_FUNC, test={"x": -5}, entry="abs_val")
        traces_pos = trace_function(source=BRANCH_FUNC, test={"x": 5}, entry="abs_val")
        ids_neg = {t.block_id for t in traces_neg}
        ids_pos = {t.block_id for t in traces_pos}
        assert ids_neg != ids_pos


class TestTraceFunctionLoop:
    def test_loop_body_hit_multiple_times(self):
        traces = trace_function(source=LOOP_FUNC, test={"items": [1, 2, 3]}, entry="sum_list")
        assert len(traces) > 0
        loop_traces = [t for t in traces if t.hit_count > 1]
        assert len(loop_traces) >= 1

    def test_empty_list_no_loop_hits(self):
        traces = trace_function(source=LOOP_FUNC, test={"items": []}, entry="sum_list")
        assert len(traces) > 0


class TestTraceFunctionTestInput:
    def test_string_test_code(self):
        traces = trace_function(source=SIMPLE_FUNC, test="result = add(3, 4)", entry="add")
        assert len(traces) > 0

    def test_callable_test(self):
        traces = trace_function(source=SIMPLE_FUNC, test=lambda f: f(10, 20), entry="add")
        assert len(traces) > 0

    def test_none_test_calls_no_args(self):
        no_arg = """\
def greet():
    return "hello"
"""
        traces = trace_function(source=no_arg, test=None, entry="greet")
        assert len(traces) > 0

    def test_empty_dict_test(self):
        no_arg = """\
def greet():
    return "hello"
"""
        traces = trace_function(source=no_arg, test={}, entry="greet")
        assert len(traces) > 0


class TestTraceFunctionErrors:
    def test_function_not_found_raises(self):
        with pytest.raises(ValueError, match="not found"):
            trace_function(source="def other(): pass", test={}, entry="nonexistent")


# ------------------------------------------------------------------ #
# Phase 2.5 — Failing tests for the new TraceResult API (step 2.5)   #
# These define the contract for the upgraded trace_function that      #
# returns TraceResult instead of a plain list.  They are expected to  #
# FAIL until step 2.6 implements the new API.                         #
# ------------------------------------------------------------------ #


class TestTraceFunctionNewAPI:
    """Failing tests for the TraceResult-based trace_function API."""

    def test_trace_function_buggy_returns_blocks_with_values(self, tmp_path, monkeypatch):
        """trace_function writes source to a tmp file internally; test verifies the contract."""
        monkeypatch.chdir(tmp_path)  # tracer creates its tmp files in CWD
        src = (
            "def add(a, b):\n"
            "    s = a - b  # bug: should be +\n"
            "    return s\n"
        )
        test = "assert add(2, 3) == 5"
        result = trace_function(source=src, test=test, entry="add", timeout=5)
        assert result.kind == "ok"
        assert len(result.blocks) >= 1
        flat = "\n".join(line for b in result.blocks for line in b.rendered)
        assert "a=2" in flat or "a = 2" in flat
        assert "b=3" in flat or "b = 3" in flat

    def test_trace_function_timeout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src = "def loop(x):\n    while True:\n        x += 1\n"
        result = trace_function(source=src, test="loop(0)", entry="loop", timeout=2)
        assert result.kind == "timeout"

    def test_trace_function_syntax_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = trace_function(source="def broken(:\n", test="broken()", entry="broken", timeout=5)
        assert result.kind == "fail"
