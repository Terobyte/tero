"""Tests for the debugger_edges module.

Covers data structures, prompt builders, parsing, and async orchestration
for cross-file edge analysis and SCC (circular dependency) bug detection.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.debugger_bugs import BugEntry
from src.debugger_contracts import ExportContract, FileContract
from src.debugger_edges import (
    EdgeFinding,
    analyze_edges,
    build_edge_prompt,
    build_scc_prompt,
    deep_dive,
    findings_to_bugs,
    parse_edge_findings,
    run_deep_dives,
)
from src.debugger_graph import DependencyGraph, FileNode
from src.debugger_llm import CollectedTextResult


# ─── parse_edge_findings ────────────────────────────────────────────────────


def test_parse_edge_findings_valid():
    """Valid JSON array with all fields produces correct EdgeFinding objects."""
    raw = json.dumps([
        {
            "caller_file": "src/a.py",
            "callee_file": "src/b.py",
            "caller_line": 42,
            "description": "return value ignored",
            "confidence": "high",
            "check_type": "return_ignored",
        }
    ])
    results = parse_edge_findings(raw)
    assert len(results) == 1
    f = results[0]
    assert f.caller_file == "src/a.py"
    assert f.callee_file == "src/b.py"
    assert f.caller_line == 42
    assert f.description == "return value ignored"
    assert f.confidence == "high"
    assert f.check_type == "return_ignored"


def test_parse_edge_findings_invalid_confidence():
    """Invalid confidence 'very_high' is replaced with 'low'."""
    raw = json.dumps([
        {
            "caller_file": "src/a.py",
            "callee_file": "src/b.py",
            "caller_line": 10,
            "description": "something",
            "confidence": "very_high",
            "check_type": "signature_mismatch",
        }
    ])
    results = parse_edge_findings(raw)
    assert len(results) == 1
    assert results[0].confidence == "low"


def test_parse_edge_findings_missing_fields():
    """Entries missing caller metadata are skipped, but caller_line defaults to 0."""
    raw = json.dumps([
        {"callee_file": "src/b.py", "caller_line": 1, "description": "no caller"},
        {"caller_file": "src/a.py", "caller_line": 1, "description": "no callee"},
        {"caller_file": "src/a.py", "callee_file": "src/b.py", "caller_line": 1},
        {"caller_file": "src/a.py", "callee_file": "src/b.py", "description": "no line"},
    ])
    results = parse_edge_findings(raw)
    assert len(results) == 1
    assert results[0].caller_file == "src/a.py"
    assert results[0].callee_file == "src/b.py"
    assert results[0].caller_line == 0


# ─── build_edge_prompt / build_scc_prompt ────────────────────────────────────


def test_build_edge_prompt_structure():
    """build_edge_prompt contains the caller source and 'Dependency Contracts'."""
    source = "def foo():\n    pass\n"
    node = FileNode(
        rel_path="src/caller.py",
        functions=[], classes=[], imports=[],
        external_calls=[], line_count=2,
    )
    contract = FileContract(
        rel_path="src/dep.py",
        exports=[
            ExportContract(
                name="bar", signature="bar(x: int) -> str",
                return_type="str",
            )
        ],
    )
    prompt = build_edge_prompt(source, node, {"src/dep.py": contract})
    assert "src/caller.py" in prompt
    assert "Dependency Contracts" in prompt
    assert "bar" in prompt


def test_build_scc_prompt_structure():
    """build_scc_prompt includes all SCC file paths under circular-dependency
    headings."""
    files = [
        ("src/a.py", "import b\n"),
        ("src/b.py", "import a\n"),
    ]
    prompt = build_scc_prompt(files)
    assert "src/a.py" in prompt
    assert "src/b.py" in prompt
    assert "Circular" in prompt


# ─── analyze_edges ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_edges_skips_no_deps(tmp_path):
    """Files with no project deps produce no LLM call."""
    nodes = {
        "src/standalone.py": FileNode(
            rel_path="src/standalone.py",
            functions=[], classes=[], imports=[],
            external_calls=[], line_count=1,
        ),
    }
    graph = DependencyGraph(files=nodes, edges=[], sccs=[])
    config = MagicMock()
    config.debug_max_concurrent_llm = 1
    config.max_turns = 1
    config.debug_player_model = ""

    provider = MagicMock()
    provider.run = AsyncMock()

    high, medium = await analyze_edges(
        graph, {}, provider, config, str(tmp_path),
    )
    assert high == []
    assert medium == []
    # No LLM call should have been made
    provider.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_edges_scc_grouping(tmp_path):
    """A↔B circular dependency produces exactly one SCC LLM call."""
    # Create the two files on disk so analyze_edges can read them
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("from src import b\n")
    (src / "b.py").write_text("from src import a\n")

    nodes = {
        "src/a.py": FileNode(
            rel_path="src/a.py",
            functions=[], classes=[], imports=[],
            external_calls=[], line_count=1,
        ),
        "src/b.py": FileNode(
            rel_path="src/b.py",
            functions=[], classes=[], imports=[],
            external_calls=[], line_count=1,
        ),
    }
    graph = DependencyGraph(
        files=nodes, edges=[], sccs=[["src/a.py", "src/b.py"]],
    )
    config = MagicMock()
    config.debug_max_concurrent_llm = 1
    config.max_turns = 1
    config.debug_player_model = ""

    with patch("src.debugger_edges.collect_text", new_callable=AsyncMock) as mock_ct:
        mock_ct.return_value = CollectedTextResult(text="[]", completed=True)
        high, medium = await analyze_edges(
            graph, {}, MagicMock(), config, str(tmp_path),
        )
        # Exactly one collect_text call for the SCC group
        assert mock_ct.call_count == 1


@pytest.mark.asyncio
async def test_analyze_edges_confidence_split(tmp_path):
    """Mixed-confidence findings are correctly split into high and medium."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "caller.py").write_text("from src import dep\n")

    nodes = {
        "src/caller.py": FileNode(
            rel_path="src/caller.py",
            functions=[], classes=[], imports=[],
            external_calls=[], line_count=1,
        ),
    }
    graph = DependencyGraph(files=nodes, edges=[], sccs=[])

    # Add an edge so the file has a project dependency
    from src.debugger_graph import ImportEdge
    graph.edges.append(ImportEdge(
        source_file="src/caller.py",
        target_module="src.dep",
        symbols=["something"],
        lineno=1,
        resolved_path="src/dep.py",
    ))

    contracts = {
        "src/dep.py": FileContract(
            rel_path="src/dep.py",
            exports=[ExportContract(name="something")],
        ),
    }

    config = MagicMock()
    config.debug_max_concurrent_llm = 1
    config.max_turns = 1
    config.debug_player_model = ""

    llm_output = json.dumps([
        {
            "caller_file": "src/caller.py",
            "callee_file": "src/dep.py",
            "caller_line": 1,
            "description": "high bug",
            "confidence": "high",
            "check_type": "return_ignored",
        },
        {
            "caller_file": "src/caller.py",
            "callee_file": "src/dep.py",
            "caller_line": 2,
            "description": "medium bug",
            "confidence": "medium",
            "check_type": "none_not_handled",
        },
        {
            "caller_file": "src/caller.py",
            "callee_file": "src/dep.py",
            "caller_line": 3,
            "description": "low bug (discarded)",
            "confidence": "low",
            "check_type": "type_mismatch",
        },
    ])

    with patch("src.debugger_edges.collect_text", new_callable=AsyncMock) as mock_ct:
        mock_ct.return_value = CollectedTextResult(text=llm_output, completed=True)
        high, medium = await analyze_edges(
            graph, contracts, MagicMock(), config, str(tmp_path),
        )

    assert len(high) == 1
    assert high[0].confidence == "high"
    assert len(medium) == 1
    assert medium[0].confidence == "medium"


# ─── deep_dive ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deep_dive_confirmed(tmp_path):
    """deep_dive returns a confirmed finding with high confidence when the LLM
    confirms the bug."""
    # Create caller and callee files on disk
    (tmp_path / "caller.py").write_text("x = 1\n")
    (tmp_path / "callee.py").write_text("def foo(): pass\n")

    finding = EdgeFinding(
        caller_file="caller.py",
        callee_file="callee.py",
        caller_line=1,
        description="possible bug",
        confidence="medium",
        check_type="return_ignored",
    )

    config = MagicMock()
    config.max_turns = 1
    config.debug_player_model = ""

    llm_response = json.dumps({"confirmed": True, "reason": "real bug"})
    with patch("src.debugger_edges.collect_text", new_callable=AsyncMock) as mock_ct:
        mock_ct.return_value = CollectedTextResult(text=llm_response, completed=True)
        result = await deep_dive(finding, str(tmp_path), MagicMock(), config)

    assert result is not None
    assert result.confidence == "high"
    assert "real bug" in result.description


@pytest.mark.asyncio
async def test_deep_dive_refuted(tmp_path):
    """deep_dive returns None when the LLM refutes the finding."""
    (tmp_path / "caller.py").write_text("x = 1\n")
    (tmp_path / "callee.py").write_text("def foo(): pass\n")

    finding = EdgeFinding(
        caller_file="caller.py",
        callee_file="callee.py",
        caller_line=1,
        description="possible bug",
        confidence="medium",
        check_type="return_ignored",
    )

    config = MagicMock()
    config.max_turns = 1
    config.debug_player_model = ""

    llm_response = json.dumps({"confirmed": False, "reason": "false positive"})
    with patch("src.debugger_edges.collect_text", new_callable=AsyncMock) as mock_ct:
        mock_ct.return_value = CollectedTextResult(text=llm_response, completed=True)
        result = await deep_dive(finding, str(tmp_path), MagicMock(), config)

    assert result is None


# ─── run_deep_dives ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_deep_dives_skip_mode():
    """In 'skip' mode, run_deep_dives returns an empty list immediately."""
    config = MagicMock()
    config.debug_deep_dive_mode = "skip"
    config.debug_max_concurrent_llm = 1
    findings = [EdgeFinding(caller_file="a.py", callee_file="b.py",
                             caller_line=1, description="x")]
    result = await run_deep_dives(findings, "/tmp", MagicMock(), config)
    assert result == []


@pytest.mark.asyncio
async def test_run_deep_dives_concurrency(tmp_path):
    """5 medium findings with semaphore=2 are all processed and results
    collected."""
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")

    config = MagicMock()
    config.debug_deep_dive_mode = "aggressive"
    config.debug_max_concurrent_llm = 2
    config.max_turns = 1
    config.debug_player_model = ""

    findings = [
        EdgeFinding(
            caller_file="a.py", callee_file="b.py",
            caller_line=i, description=f"bug {i}",
            confidence="medium",
            check_type="return_ignored",
        )
        for i in range(5)
    ]

    call_count = 0
    active = 0
    max_active = 0

    async def _mock_deep_dive(finding, working_dir, provider, cfg):
        nonlocal call_count, active, max_active
        call_count += 1
        active += 1
        max_active = max(max_active, active)
        # Small sleep so concurrent tasks overlap
        await asyncio.sleep(0.01)
        active -= 1
        # Confirm every other one
        if finding.caller_line % 2 == 0:
            finding.confidence = "high"
            return finding
        return None

    with patch("src.debugger_edges.deep_dive", side_effect=_mock_deep_dive):
        result = await run_deep_dives(findings, str(tmp_path), MagicMock(), config)

    # Concurrency must be capped at the semaphore limit of 2
    assert max_active <= 2
    # All 5 should have been sent for deep dive
    assert call_count == 5
    # Only the confirmed ones (caller_line 0, 2, 4) are returned
    assert len(result) == 3
    assert all(f.confidence == "high" for f in result)


# ─── findings_to_bugs ────────────────────────────────────────────────────────


def test_findings_to_bugs():
    """3 findings produce 3 BugEntry instances with [check_type] prefix."""
    findings = [
        EdgeFinding(
            caller_file="src/a.py",
            callee_file="src/b.py",
            caller_line=10,
            description="return value ignored",
            confidence="high",
            check_type="return_ignored",
        ),
        EdgeFinding(
            caller_file="src/c.py",
            callee_file="src/d.py",
            caller_line=20,
            description="None not handled",
            confidence="high",
            check_type="none_not_handled",
        ),
        EdgeFinding(
            caller_file="src/e.py",
            callee_file="src/f.py",
            caller_line=30,
            description="type mismatch",
            confidence="high",
            check_type="type_mismatch",
        ),
    ]
    bugs = findings_to_bugs(findings, start_id=1)
    assert len(bugs) == 3
    for i, b in enumerate(bugs):
        assert isinstance(b, BugEntry)
        assert b.id == i + 1
        assert b.severity == "high"
        assert b.status == "open"
    assert bugs[0].description == "[return_ignored] return value ignored"
    assert bugs[1].description == "[none_not_handled] None not handled"
    assert bugs[2].description == "[type_mismatch] type mismatch"
