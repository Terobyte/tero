"""Tests for src/debugger_bugs.py."""

import textwrap
from pathlib import Path

import pytest

from src.debugger_bugs import (
    BugEntry,
    parse_bugs,
    merge_bugs,
    write_bugs_md,
    write_final_report,
)


# ── parse_bugs ────────────────────────────────────────────────────────────────

class TestParseBugs:
    def test_json_array_in_code_block(self):
        raw = textwrap.dedent("""\
            Found two bugs:
            ```json
            [
                {"file": "foo.py", "line": 10, "description": "Wrong operator", "severity": "high"},
                {"file": "bar.py", "line": 25, "description": "Missing return", "severity": "high"}
            ]
            ```
        """)
        bugs = parse_bugs(raw, start_id=1)
        assert len(bugs) == 2
        assert bugs[0].file == "foo.py"
        assert bugs[0].line == 10
        assert bugs[0].id == 1
        assert bugs[1].id == 2
        assert bugs[1].file == "bar.py"

    def test_empty_json_array(self):
        raw = "No bugs found.\n```json\n[]\n```"
        bugs = parse_bugs(raw, start_id=1)
        assert bugs == []

    def test_start_id_offset(self):
        raw = '```json\n[{"file": "x.py", "line": 5, "description": "Bug", "severity": "high"}]\n```'
        bugs = parse_bugs(raw, start_id=10)
        assert bugs[0].id == 10

    def test_per_line_dedup(self):
        """Two entries at the same (file, line) are deduplicated."""
        raw = textwrap.dedent("""\
            ```json
            [
                {"file": "a.py", "line": 1, "description": "Bug A", "severity": "high"},
                {"file": "a.py", "line": 1, "description": "Bug B (same line)", "severity": "high"}
            ]
            ```
        """)
        bugs = parse_bugs(raw)
        assert len(bugs) == 1
        assert bugs[0].description == "Bug A"

    def test_prose_fallback(self):
        """When no JSON found, extracts file:line references from prose."""
        raw = "I found a bug in models.py:42 — missing return value"
        bugs = parse_bugs(raw)
        assert len(bugs) == 1
        assert bugs[0].file == "models.py"
        assert bugs[0].line == 42

    def test_prose_fallback_hash_L_format(self):
        raw = "Issue at pricing.py#L75 in the discount calculation"
        bugs = parse_bugs(raw)
        assert len(bugs) == 1
        assert bugs[0].file == "pricing.py"
        assert bugs[0].line == 75

    def test_no_output(self):
        bugs = parse_bugs("")
        assert bugs == []

    def test_trailing_commas_handled(self):
        raw = '```json\n[{"file": "x.py", "line": 3, "description": "D", "severity": "high",}]\n```'
        bugs = parse_bugs(raw)
        assert len(bugs) == 1

    def test_missing_required_fields_skipped(self):
        raw = '```json\n[{"file": "x.py", "line": 3}]\n```'
        bugs = parse_bugs(raw)
        assert bugs == []

    def test_default_status_is_open(self):
        raw = '```json\n[{"file": "x.py", "line": 1, "description": "D", "severity": "high"}]\n```'
        bugs = parse_bugs(raw)
        assert bugs[0].status == "open"


# ── merge_bugs ────────────────────────────────────────────────────────────────

class TestMergeBugs:
    def test_merges_new_bugs(self):
        existing = [BugEntry(id=1, file="a.py", line=10, description="D1", severity="high")]
        new = [BugEntry(id=2, file="b.py", line=20, description="D2", severity="high")]
        merged = merge_bugs(existing, new)
        assert len(merged) == 2
        assert merged[0].file == "a.py"
        assert merged[1].file == "b.py"

    def test_dedup_by_file_line(self):
        existing = [BugEntry(id=1, file="a.py", line=10, description="D1", severity="high")]
        new = [
            BugEntry(id=2, file="a.py", line=10, description="Same location", severity="high"),
            BugEntry(id=3, file="b.py", line=20, description="New", severity="high"),
        ]
        merged = merge_bugs(existing, new)
        assert len(merged) == 2
        assert merged[0].id == 1
        assert merged[1].id == 3

    def test_empty_existing(self):
        new = [BugEntry(id=1, file="a.py", line=5, description="D", severity="high")]
        merged = merge_bugs([], new)
        assert len(merged) == 1

    def test_empty_new(self):
        existing = [BugEntry(id=1, file="a.py", line=5, description="D", severity="high")]
        merged = merge_bugs(existing, [])
        assert len(merged) == 1

    def test_does_not_mutate_existing(self):
        existing = [BugEntry(id=1, file="a.py", line=5, description="D", severity="high")]
        original_len = len(existing)
        merge_bugs(existing, [BugEntry(id=2, file="b.py", line=9, description="D2", severity="high")])
        assert len(existing) == original_len


# ── write_bugs_md ─────────────────────────────────────────────────────────────

class TestWriteBugsMd:
    def test_creates_file(self, tmp_path):
        bugs = [
            BugEntry(id=1, file="x.py", line=5, description="Bug 1", severity="high", status="open"),
            BugEntry(id=2, file="y.py", line=10, description="Bug 2", severity="high", status="confirmed"),
        ]
        out = tmp_path / "bugs.md"
        write_bugs_md(bugs, str(out), iteration=3)
        content = out.read_text()
        assert "Iteration 3" in content
        assert "Bug 1" in content
        assert "Bug 2" in content

    def test_counts_by_status(self, tmp_path):
        bugs = [
            BugEntry(id=1, file="x.py", line=1, description="A", severity="high", status="open"),
            BugEntry(id=2, file="x.py", line=2, description="B", severity="high", status="confirmed"),
            BugEntry(id=3, file="x.py", line=3, description="C", severity="high", status="fixed"),
            BugEntry(id=4, file="x.py", line=4, description="D", severity="high", status="false_positive"),
        ]
        out = tmp_path / "bugs.md"
        write_bugs_md(bugs, str(out), iteration=1)
        content = out.read_text()
        # Each count should appear in the table
        assert "| 1 |" in content  # open=1, confirmed=1, fixed=1, fp=1

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "sub" / "dir" / "bugs.md"
        write_bugs_md([], str(out), iteration=1)
        assert out.exists()


# ── write_final_report ────────────────────────────────────────────────────────

class TestWriteFinalReport:
    def test_victory_report(self, tmp_path):
        bugs = [
            BugEntry(id=1, file="x.py", line=1, description="Fixed bug", severity="high", status="fixed"),
        ]
        out = tmp_path / "report.md"
        write_final_report(bugs, str(out), duration_s=125.0, victory=True)
        content = out.read_text()
        assert "VICTORY" in content
        assert "2m 5s" in content
        assert "Fixed:** 1" in content

    def test_stopped_report(self, tmp_path):
        out = tmp_path / "report.md"
        write_final_report([], str(out), duration_s=60.0, victory=False)
        content = out.read_text()
        assert "STOPPED" in content
