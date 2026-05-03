"""Tests for src/ldb/inputs.py — _extract_signature, _extract_surrounding_code, _parse_entry_lines."""

from __future__ import annotations

import pytest

from src.ldb.inputs import (
    _extract_signature,
    _extract_surrounding_code,
    _parse_entry_lines,
    _dotted_parts,
)


class TestDottedParts:
    def test_plain_function(self):
        cls, func = _dotted_parts("foo")
        assert cls is None
        assert func == "foo"

    def test_class_method(self):
        cls, func = _dotted_parts("Foo.bar")
        assert cls == "Foo"
        assert func == "bar"


class TestExtractSignature:
    def test_plain_function_excludes_body(self):
        src = "def foo(x, y):\n    z = x + y\n    return z\n"
        sig = _extract_signature(src, "foo")
        assert "z = x + y" not in sig
        assert sig == "def foo(x, y)"

    def test_method_with_docstring_no_duplication(self):
        src = (
            "class C:\n"
            "    def bar(self):\n"
            '        """Docstring here."""\n'
            "        return 42\n"
        )
        sig = _extract_signature(src, "C.bar")
        assert sig.count("Docstring") == 1
        assert "return 42" not in sig

    def test_multiline_signature_excludes_body(self):
        src = "def baz(\n    a,\n    b,\n):\n    pass\n"
        sig = _extract_signature(src, "baz")
        assert "pass" not in sig
        assert "def baz(" in sig

    def test_missing_function_returns_fallback(self):
        sig = _extract_signature("x = 1\n", "nonexistent")
        assert sig == "def nonexistent(...): ..."

    def test_dotted_entry_class_method(self):
        src = "class MyClass:\n    def my_method(self, x):\n        return x * 2\n"
        sig = _extract_signature(src, "MyClass.my_method")
        assert "def my_method(self, x)" in sig
        assert "return x * 2" not in sig

    def test_syntax_error_returns_fallback(self):
        sig = _extract_signature("def (:\n", "broken")
        assert sig == "def broken(...): ..."

    def test_function_with_return_type_annotation(self):
        src = "def greet(name: str) -> str:\n    return f'Hello {name}'\n"
        sig = _extract_signature(src, "greet")
        assert "def greet(name: str) -> str" in sig
        assert "Hello" not in sig

    def test_async_function_signature(self):
        src = "async def fetch(url):\n    return await url\n"
        sig = _extract_signature(src, "fetch")
        assert "async def fetch(url)" in sig
        assert "await" not in sig


class TestExtractSurroundingCode:
    def test_returns_window_around_function(self):
        lines = ["# header"] * 50 + ["def target():", "    pass"] + ["# footer"] * 50
        src = "\n".join(lines)
        result = _extract_surrounding_code(src, "target")
        assert "def target():" in result

    def test_missing_function_returns_prefix(self):
        result = _extract_surrounding_code("x = 1\n", "nonexistent")
        assert "x = 1" in result

    def test_syntax_error_returns_prefix(self):
        result = _extract_surrounding_code("def (:\n", "broken")
        assert len(result) > 0


class TestParseEntryLines:
    def test_extracts_entry_calls(self):
        raw = "entry(1, 2)\nentry(3, 4)\nsome other text\nentry(5, 6)"
        lines = _parse_entry_lines(raw)
        assert len(lines) == 3
        assert lines[0] == "entry(1, 2)"
        assert lines[1] == "entry(3, 4)"
        assert lines[2] == "entry(5, 6)"

    def test_no_entries_returns_empty(self):
        lines = _parse_entry_lines("no entries here\njust text")
        assert lines == []

    def test_entries_with_kwargs(self):
        raw = "entry(x=1, y=2)\nentry(a=3)"
        lines = _parse_entry_lines(raw)
        assert len(lines) == 2
