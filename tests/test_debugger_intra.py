"""Tests for src.debugger_intra — prompt building, file analysis, concurrency."""

from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.debugger_bugs import BugEntry, _normalize_description
from src.debugger_contracts import ExportContract, FileContract
from src.debugger_graph import DependencyGraph, FileNode
from src.debugger_intra import (
    analyze_all_files,
    analyze_file,
    build_intra_prompt,
)
from src.debugger_prompts import CONTRACT_AWARENESS_PREFIX, INTENSITY_PROMPTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file_node(rel_path: str, source_hash: str = "") -> FileNode:
    """Create a minimal FileNode for testing."""
    return FileNode(
        rel_path=rel_path,
        functions=[],
        classes=[],
        imports=[],
        external_calls=[],
        line_count=0,
        source_hash=source_hash,
    )


def _write_source(base, rel_path: str, content: str) -> None:
    """Write a source file under *base*, creating parent dirs as needed."""
    from pathlib import Path

    fpath = Path(base) / rel_path
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. test_build_intra_prompt_contains_source
# ---------------------------------------------------------------------------


def test_build_intra_prompt_contains_source():
    """Prompt includes the numbered source code for the target file."""
    source = "def foo():\n    return 42\n"
    node = _make_file_node("src/example.py")

    prompt = build_intra_prompt(source, node, {})

    assert "--- File: src/example.py ---" in prompt
    assert "   1 | def foo():" in prompt
    assert "   2 |     return 42" in prompt
    assert "Source code (with line numbers):" in prompt


# ---------------------------------------------------------------------------
# 2. test_build_intra_prompt_contains_contracts
# ---------------------------------------------------------------------------


def test_build_intra_prompt_contains_contracts():
    """When dep_contracts are provided the prompt includes a contracts section."""
    source = "x = 1\n"
    node = _make_file_node("src/caller.py")

    contracts = {
        "src/dep.py": FileContract(
            rel_path="src/dep.py",
            exports=[
                ExportContract(
                    name="do_thing",
                    signature="do_thing(x: int) -> bool",
                    preconditions=["x > 0"],
                    postconditions=["returns bool"],
                    side_effects=[],
                    raises=["ValueError"],
                    return_type="bool",
                ),
            ],
        ),
    }

    prompt = build_intra_prompt(source, node, contracts)

    assert "## Dependency Contracts" in prompt
    assert "### src/dep.py" in prompt
    assert "`do_thing`" in prompt
    assert "`do_thing(x: int) -> bool`" in prompt
    assert "preconditions" in prompt
    assert "x > 0" in prompt


# ---------------------------------------------------------------------------
# 3. test_build_intra_prompt_no_deps
# ---------------------------------------------------------------------------


def test_build_intra_prompt_no_deps():
    """When dep_contracts is empty there is no Dependency Contracts section."""
    source = "y = 2\n"
    node = _make_file_node("src/solo.py")

    prompt = build_intra_prompt(source, node, {})

    assert "## Dependency Contracts" not in prompt
    assert "--- File: src/solo.py ---" in prompt


# ---------------------------------------------------------------------------
# 4. test_analyze_file_all_prompts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_file_all_prompts():
    """With intensity='high', analyze_file makes 5 LLM calls (one per prompt)."""
    source = "def bug():\n    return 1 / 0\n"
    node = _make_file_node("src/buggy.py")
    graph = DependencyGraph(files={"src/buggy.py": node})
    contracts: dict[str, FileContract] = {}

    provider = MagicMock()
    config = MagicMock()
    config.debug_intensity = "high"
    config.debug_player_model = "test-model"

    call_count = 0

    async def _mock_collect_text(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        result.completed = True
        result.text = json.dumps([
            {
                "file": "src/buggy.py",
                "line": 2,
                "description": f"Bug from call {call_count}",
                "severity": "high",
            }
        ])
        return result

    with patch("src.debugger_intra.collect_text", side_effect=_mock_collect_text):
        bugs = await analyze_file(
            "src/buggy.py", source, node, contracts, graph,
            provider, config, working_dir=".",
        )

    assert call_count == 5, f"Expected 5 LLM calls for high intensity, got {call_count}"
    assert len(bugs) == 5


# ---------------------------------------------------------------------------
# 5. test_analyze_file_partial_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_file_partial_failure():
    """If 1 of 5 LLM calls fails (incomplete), the other 4 results are still collected."""
    source = "def partial(): pass\n"
    node = _make_file_node("src/partial.py")
    graph = DependencyGraph(files={"src/partial.py": node})
    contracts: dict[str, FileContract] = {}

    provider = MagicMock()
    config = MagicMock()
    config.debug_intensity = "high"
    config.debug_player_model = ""

    call_idx = 0

    async def _mock_collect_text(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        result = MagicMock()
        if call_idx == 3:
            # This call "fails"
            result.completed = False
            result.text = ""
            return result
        result.completed = True
        result.text = json.dumps([
            {
                "file": "src/partial.py",
                "line": call_idx,
                "description": f"Bug {call_idx}",
                "severity": "high",
            }
        ])
        return result

    with patch("src.debugger_intra.collect_text", side_effect=_mock_collect_text):
        bugs = await analyze_file(
            "src/partial.py", source, node, contracts, graph,
            provider, config, working_dir=".",
        )

    # 4 out of 5 calls succeeded
    assert len(bugs) == 4, f"Expected 4 bugs (1 failed), got {len(bugs)}"


# ---------------------------------------------------------------------------
# 6. test_analyze_file_contract_augmentation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_file_contract_augmentation():
    """Each system prompt passed to collect_text starts with CONTRACT_AWARENESS_PREFIX."""
    source = "x = 1\n"
    node = _make_file_node("src/aug.py")
    graph = DependencyGraph(files={"src/aug.py": node})
    contracts: dict[str, FileContract] = {}

    provider = MagicMock()
    config = MagicMock()
    config.debug_intensity = "low"  # Only 1 prompt
    config.debug_player_model = ""

    captured_system_prompts: list[str] = []

    async def _mock_collect_text(*args, **kwargs):
        captured_system_prompts.append(kwargs.get("system_prompt", ""))
        result = MagicMock()
        result.completed = True
        result.text = "[]"
        return result

    with patch("src.debugger_intra.collect_text", side_effect=_mock_collect_text):
        await analyze_file(
            "src/aug.py", source, node, contracts, graph,
            provider, config, working_dir=".",
        )

    assert len(captured_system_prompts) == 1
    assert captured_system_prompts[0].startswith(CONTRACT_AWARENESS_PREFIX)


# ---------------------------------------------------------------------------
# 7. test_analyze_all_files_concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_all_files_concurrency(tmp_path):
    """Semaphore=3 limits concurrent file analyses to at most 3."""
    nodes: dict[str, FileNode] = {}
    for i in range(10):
        rel = f"src/file_{i}.py"
        source = f"def f{i}(): pass\n"
        h = hashlib.sha256(source.encode()).hexdigest()
        _write_source(tmp_path, rel, source)
        nodes[rel] = _make_file_node(rel, source_hash=h)

    graph = DependencyGraph(files=nodes)
    contracts: dict[str, FileContract] = {}
    provider = MagicMock()
    config = MagicMock()
    config.debug_max_concurrent_llm = 3
    config.debug_intensity = "low"
    config.debug_player_model = ""

    concurrent_calls = 0
    peak_concurrency = 0

    async def _mock_collect_text(*args, **kwargs):
        nonlocal concurrent_calls, peak_concurrency
        concurrent_calls += 1
        peak_concurrency = max(peak_concurrency, concurrent_calls)
        await asyncio.sleep(0.01)
        concurrent_calls -= 1
        result = MagicMock()
        result.completed = True
        result.text = "[]"
        return result

    with patch("src.debugger_intra.collect_text", side_effect=_mock_collect_text):
        result = await analyze_all_files(
            graph, contracts, provider, config,
            working_dir=str(tmp_path), existing_bugs=[],
        )

    assert peak_concurrency <= 3, (
        f"Expected max 3 concurrent calls, saw {peak_concurrency}"
    )


# ---------------------------------------------------------------------------
# 8. test_analyze_all_files_merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_all_files_merge(tmp_path):
    """Intra bugs that duplicate existing edge bugs are deduplicated."""
    # Set up a single file
    rel = "src/mod.py"
    source = "def f(): pass\n"
    h = hashlib.sha256(source.encode()).hexdigest()
    _write_source(tmp_path, rel, source)
    node = _make_file_node(rel, source_hash=h)

    graph = DependencyGraph(files={rel: node})
    contracts: dict[str, FileContract] = {}
    provider = MagicMock()
    config = MagicMock()
    config.debug_max_concurrent_llm = 5
    config.debug_intensity = "low"
    config.debug_player_model = ""

    async def _mock_collect_text(*args, **kwargs):
        result = MagicMock()
        result.completed = True
        result.text = json.dumps([
            # Intra analysis finds the SAME (file, line) as the existing edge bug
            {
                "file": "src/mod.py",
                "line": 5,
                "description": "Rediscovered edge bug",
                "severity": "high",
            },
            # Plus a genuinely new intra-only bug at a different location
            {
                "file": "src/mod.py",
                "line": 1,
                "description": "Intra-only bug",
                "severity": "medium",
            },
        ])
        return result

    # Existing edge bug at the same (file, line) as one intra finding
    existing = [
        BugEntry(id=1, file="src/mod.py", line=5, description="Edge bug", severity="high"),
    ]

    with patch("src.debugger_intra.collect_text", side_effect=_mock_collect_text):
        merged = await analyze_all_files(
            graph, contracts, provider, config,
            working_dir=str(tmp_path), existing_bugs=existing,
        )

    # The duplicate at line 5 is dropped; the new bug at line 1 is kept.
    # Total = 1 existing + 1 new = 2 (not 3).
    assert len(merged) == 2
    files_lines = {(b.file, b.line) for b in merged}
    assert ("src/mod.py", 5) in files_lines  # the original edge bug
    assert ("src/mod.py", 1) in files_lines  # the new intra-only bug


# ---------------------------------------------------------------------------
# 9. test_deduplicate_same_line
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deduplicate_same_line(tmp_path):
    """Two files finding a bug at the same (file, line) are deduplicated."""
    # We need two separate "files" in the graph, both of which discover a bug
    # in the same target file+line. Since analyze_all_files deduplicates across
    # gather results, we simulate two files that each return the same bug.
    rel_a = "src/a.py"
    rel_b = "src/b.py"
    source = "x = 1\n"

    _write_source(tmp_path, rel_a, source)
    _write_source(tmp_path, rel_b, source)

    nodes = {
        rel_a: _make_file_node(rel_a, source_hash=hashlib.sha256(source.encode()).hexdigest()),
        rel_b: _make_file_node(rel_b, source_hash=hashlib.sha256(source.encode()).hexdigest()),
    }

    graph = DependencyGraph(files=nodes)
    contracts: dict[str, FileContract] = {}
    provider = MagicMock()
    config = MagicMock()
    config.debug_max_concurrent_llm = 5
    config.debug_intensity = "low"
    config.debug_player_model = ""

    call_idx = 0

    async def _mock_collect_text(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        result = MagicMock()
        result.completed = True
        # Both files report the same bug at shared.py:10
        result.text = json.dumps([
            {
                "file": "shared.py",
                "line": 10,
                "description": "Same-line bug",
                "severity": "high",
            }
        ])
        return result

    with patch("src.debugger_intra.collect_text", side_effect=_mock_collect_text):
        merged = await analyze_all_files(
            graph, contracts, provider, config,
            working_dir=str(tmp_path), existing_bugs=[],
        )

    # Deduplicated: only one bug at (shared.py, 10)
    assert len(merged) == 1
    assert merged[0].file == "shared.py"
    assert merged[0].line == 10


# ---------------------------------------------------------------------------
# 10. test_deduplicate_same_description
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deduplicate_same_description(tmp_path):
    """Same file + normalized description → deduplicated even at different lines."""
    rel_a = "src/a.py"
    rel_b = "src/b.py"
    source = "x = 1\n"

    _write_source(tmp_path, rel_a, source)
    _write_source(tmp_path, rel_b, source)

    nodes = {
        rel_a: _make_file_node(rel_a, source_hash=hashlib.sha256(source.encode()).hexdigest()),
        rel_b: _make_file_node(rel_b, source_hash=hashlib.sha256(source.encode()).hexdigest()),
    }

    graph = DependencyGraph(files=nodes)
    contracts: dict[str, FileContract] = {}
    provider = MagicMock()
    config = MagicMock()
    config.debug_max_concurrent_llm = 5
    config.debug_intensity = "low"
    config.debug_player_model = ""

    call_idx = 0

    async def _mock_collect_text(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        result = MagicMock()
        result.completed = True
        # Different raw strings that normalize to the same value.
        # "High: Off-by-one error in loop" → strips severity prefix →
        # "Off-by-one error in loop" → lowered → "off-by-one error in loop"
        # "Off-by-one error in loop"  → already clean → lowered → "off-by-one error in loop"
        if call_idx == 1:
            result.text = json.dumps([
                {
                    "file": "target.py",
                    "line": 10,
                    "description": "High: Off-by-one error in loop",
                    "severity": "high",
                }
            ])
        else:
            result.text = json.dumps([
                {
                    "file": "target.py",
                    "line": 20,
                    "description": "Off-by-one error in loop",
                    "severity": "high",
                }
            ])
        return result

    with patch("src.debugger_intra.collect_text", side_effect=_mock_collect_text):
        merged = await analyze_all_files(
            graph, contracts, provider, config,
            working_dir=str(tmp_path), existing_bugs=[],
        )

    # Same normalized description on same file → only first kept
    assert len(merged) == 1
    assert merged[0].line == 10


# ---------------------------------------------------------------------------
# 11. test_analyze_all_files_error_handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_all_files_error_handling(tmp_path):
    """One file raising an exception does not prevent other files from being processed."""
    nodes: dict[str, FileNode] = {}
    for i in range(3):
        rel = f"src/file_{i}.py"
        source = f"def f{i}(): pass\n"
        h = hashlib.sha256(source.encode()).hexdigest()
        _write_source(tmp_path, rel, source)
        nodes[rel] = _make_file_node(rel, source_hash=h)

    graph = DependencyGraph(files=nodes)
    contracts: dict[str, FileContract] = {}
    provider = MagicMock()
    config = MagicMock()
    config.debug_max_concurrent_llm = 5
    config.debug_intensity = "low"
    config.debug_player_model = ""

    call_idx = 0

    async def _mock_collect_text(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 2:
            raise RuntimeError("LLM provider crashed")
        result = MagicMock()
        result.completed = True
        result.text = json.dumps([
            {
                "file": f"src/file_{call_idx - 1}.py",
                "line": 1,
                "description": f"Bug {call_idx}",
                "severity": "high",
            }
        ])
        return result

    with patch("src.debugger_intra.collect_text", side_effect=_mock_collect_text):
        merged = await analyze_all_files(
            graph, contracts, provider, config,
            working_dir=str(tmp_path), existing_bugs=[],
        )

    # 2 of 3 files produced bugs (the exception-bearing file is skipped)
    assert len(merged) == 2, f"Expected 2 bugs from surviving files, got {len(merged)}"
