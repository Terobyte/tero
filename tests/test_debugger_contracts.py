"""Tests for src.debugger_contracts — parsing, batching, caching, extraction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.debugger_contracts import (
    PROMPT_BUDGET,
    ExportContract,
    FileContract,
    batch_files,
    build_batch_prompt,
    extract_contracts,
    is_contract_stale,
    load_cached_contracts,
    parse_contract_response,
    save_cached_contracts,
)
from src.debugger_graph import DependencyGraph, FileNode


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


def _write_source(base: Path, rel_path: str, content: str) -> None:
    """Write a source file under *base*, creating parent dirs as needed."""
    fpath = base / rel_path
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. parse_contract_response — valid
# ---------------------------------------------------------------------------


def test_parse_contract_response_valid():
    """Parse a well-formed single-file contract JSON."""
    raw = json.dumps({
        "rel_path": "src/foo.py",
        "exports": [
            {
                "name": "do_stuff",
                "signature": "do_stuff(x: int) -> bool",
                "preconditions": ["x > 0"],
                "postconditions": ["return is bool"],
                "side_effects": ["logs to stdout"],
                "raises": ["ValueError"],
                "return_type": "bool",
            }
        ],
        "imports_usage": [
            {
                "source_module": "os",
                "symbol": "path",
                "usage_description": "file path manipulation",
            }
        ],
        "invariants": ["always returns bool"],
        "source_hash": "abc123",
    })

    result = parse_contract_response(raw)
    assert isinstance(result, FileContract)
    assert result.rel_path == "src/foo.py"
    assert len(result.exports) == 1
    assert result.exports[0].name == "do_stuff"
    assert result.exports[0].raises == ["ValueError"]
    assert result.exports[0].preconditions == ["x > 0"]
    assert len(result.imports_usage) == 1
    assert result.imports_usage[0].symbol == "path"
    assert result.invariants == ["always returns bool"]
    assert result.source_hash == "abc123"


# ---------------------------------------------------------------------------
# 2. parse_contract_response — missing fields
# ---------------------------------------------------------------------------


def test_parse_contract_response_missing_fields():
    """Missing 'raises' key defaults to empty list."""
    raw = json.dumps({
        "rel_path": "src/bar.py",
        "exports": [
            {
                "name": "simple",
                "signature": "simple()",
            }
        ],
    })

    result = parse_contract_response(raw)
    assert isinstance(result, FileContract)
    assert len(result.exports) == 1
    ec = result.exports[0]
    assert ec.name == "simple"
    assert ec.raises == []
    assert ec.preconditions == []
    assert ec.postconditions == []
    assert ec.side_effects == []


# ---------------------------------------------------------------------------
# 3. parse_contract_response — malformed
# ---------------------------------------------------------------------------


def test_parse_contract_response_malformed():
    """Broken JSON returns an empty FileContract."""
    result = parse_contract_response("{ this is not valid json !!!")
    assert isinstance(result, FileContract)
    assert result.rel_path == ""
    assert result.exports == []


# ---------------------------------------------------------------------------
# 4. parse_contract_response — batch
# ---------------------------------------------------------------------------


def test_parse_contract_response_batch():
    """Multi-file batch JSON returns a dict keyed by rel_path."""
    raw = json.dumps({
        "src/a.py": {
            "rel_path": "src/a.py",
            "exports": [{"name": "func_a", "signature": "func_a()"}],
            "imports_usage": [],
            "invariants": [],
        },
        "src/b.py": {
            "rel_path": "src/b.py",
            "exports": [{"name": "func_b", "signature": "func_b(x)"}],
            "imports_usage": [],
            "invariants": [],
        },
    })

    result = parse_contract_response(raw)
    assert isinstance(result, dict)
    assert "src/a.py" in result
    assert "src/b.py" in result
    assert result["src/a.py"].exports[0].name == "func_a"
    assert result["src/b.py"].exports[0].name == "func_b"


# ---------------------------------------------------------------------------
# 5. batch_files — 10 small files
# ---------------------------------------------------------------------------


def test_batch_files_small(tmp_path):
    """10 files × 500 chars each fit in a single batch (5K < 15K budget)."""
    nodes: dict[str, FileNode] = {}
    for i in range(10):
        rel = f"src/mod_{i}.py"
        _write_source(tmp_path, rel, "x" * 500)
        nodes[rel] = _make_file_node(rel)

    batches = batch_files(nodes, str(tmp_path))
    assert len(batches) == 1
    assert len(batches[0]) == 10


# ---------------------------------------------------------------------------
# 6. batch_files — mixed sizes
# ---------------------------------------------------------------------------


def test_batch_files_mixed(tmp_path):
    """8 small + 2 large files produce correctly sized batches."""
    nodes: dict[str, FileNode] = {}

    # 8 small files (500 chars each)
    for i in range(8):
        rel = f"src/small_{i}.py"
        _write_source(tmp_path, rel, "s" * 500)
        nodes[rel] = _make_file_node(rel)

    # 2 large files (10K chars each)
    for i in range(2):
        rel = f"src/large_{i}.py"
        _write_source(tmp_path, rel, "L" * 10_000)
        nodes[rel] = _make_file_node(rel)

    batches = batch_files(nodes, str(tmp_path))

    # All 10 files are covered exactly once
    all_paths = [p for batch in batches for p in batch]
    assert len(all_paths) == 10
    assert set(all_paths) == set(nodes.keys())

    # Every batch respects the budget
    for batch in batches:
        total = sum(len((tmp_path / p).read_text()) for p in batch)
        assert total <= PROMPT_BUDGET

    # All 8 small files are packed together in one batch
    small_batches = [b for b in batches if any("small_" in p for p in b)]
    assert len(small_batches) == 1, (
        f"All 8 small files should be in one batch, got {len(small_batches)} batches"
    )
    assert sum(1 for p in small_batches[0] if "small_" in p) == 8

    # Large files are separated — no batch contains both
    for batch in batches:
        large_in_batch = [p for p in batch if "large_" in p]
        assert len(large_in_batch) <= 1, "Large files must be in separate batches"


# ---------------------------------------------------------------------------
# 7. batch_files — single oversized file
# ---------------------------------------------------------------------------


def test_batch_files_single_large(tmp_path):
    """A single 20K file gets its own batch (exceeds budget)."""
    rel = "src/big.py"
    _write_source(tmp_path, rel, "B" * 20_000)
    nodes = {rel: _make_file_node(rel)}

    batches = batch_files(nodes, str(tmp_path))
    assert len(batches) == 1
    assert batches[0] == [rel]


# ---------------------------------------------------------------------------
# 8. Cache — fresh (hash match)
# ---------------------------------------------------------------------------


def test_cache_fresh():
    """Hash match means the contract is NOT stale."""
    h = hashlib.sha256(b"source").hexdigest()
    contract = FileContract(rel_path="foo.py", source_hash=h)
    assert is_contract_stale(contract, h) is False


# ---------------------------------------------------------------------------
# 9. Cache — stale (one file changed)
# ---------------------------------------------------------------------------


def test_cache_stale():
    """Changing one file causes only that file's contract to be stale."""
    h1 = hashlib.sha256(b"original").hexdigest()
    h2 = hashlib.sha256(b"modified").hexdigest()

    cached = {
        "src/a.py": FileContract(rel_path="src/a.py", source_hash=h1),
        "src/b.py": FileContract(rel_path="src/b.py", source_hash="old_hash"),
    }
    assert is_contract_stale(cached["src/a.py"], h1) is False
    assert is_contract_stale(cached["src/b.py"], h2) is True


# ---------------------------------------------------------------------------
# 10. Cache — missing file
# ---------------------------------------------------------------------------


def test_cache_missing(tmp_path):
    """No cache file returns empty dict."""
    cache_path = tmp_path / "missing.json"
    result = load_cached_contracts(cache_path)
    assert result == {}


# ---------------------------------------------------------------------------
# 11. Cache — atomic write
# ---------------------------------------------------------------------------


def test_cache_atomic_write(tmp_path):
    """save_cached_contracts writes .tmp first then renames via os.replace."""
    cache_path = tmp_path / "contracts.json"
    contracts = {
        "foo.py": FileContract(
            rel_path="foo.py",
            exports=[ExportContract(name="bar", signature="bar()")],
        ),
    }

    # Spy on os.replace to verify atomic write pattern (tmp + rename)
    with patch("src.debugger_contracts.os.replace", wraps=os.replace) as mock_replace:
        save_cached_contracts(contracts, cache_path)

        # os.replace was called exactly once
        mock_replace.assert_called_once()
        src_arg = mock_replace.call_args[0][0]
        dst_arg = mock_replace.call_args[0][1]
        assert src_arg.endswith(".tmp"), f"Source should be .tmp, got {src_arg}"
        assert dst_arg == str(cache_path), f"Dest should be {cache_path}, got {dst_arg}"

    # Final file exists
    assert cache_path.exists()
    # No leftover .tmp file
    assert not cache_path.with_suffix(".tmp").exists()
    # Content round-trips correctly
    loaded = load_cached_contracts(cache_path)
    assert "foo.py" in loaded
    assert loaded["foo.py"].exports[0].name == "bar"


# ---------------------------------------------------------------------------
# 12. extract_contracts — concurrency (10 files, sem=2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_contracts_concurrency(tmp_path):
    """Semaphore=2 limits concurrent LLM calls to at most 2."""
    nodes: dict[str, FileNode] = {}
    for i in range(10):
        rel = f"src/mod_{i}.py"
        source = f"def func_{i}(): pass\n"
        h = hashlib.sha256(source.encode()).hexdigest()
        _write_source(tmp_path, rel, source)
        nodes[rel] = _make_file_node(rel, source_hash=h)

    graph = DependencyGraph(files=nodes)

    provider = MagicMock()
    config = MagicMock()
    config.debug_max_concurrent_llm = 2
    config.max_turns = 1
    config.debug_cache_contracts = False

    concurrent_calls = 0
    peak_concurrency = 0

    async def _mock_collect_text(*args, **kwargs):
        nonlocal concurrent_calls, peak_concurrency
        concurrent_calls += 1
        peak_concurrency = max(peak_concurrency, concurrent_calls)
        await asyncio.sleep(0.01)  # Let other coroutines start
        concurrent_calls -= 1
        mock_result = MagicMock()
        mock_result.text = json.dumps({
            "exports": [{"name": "func", "signature": "func()"}],
            "imports_usage": [],
            "invariants": [],
        })
        return mock_result

    # Force 10 single-file batches so the semaphore is exercised
    with patch("src.debugger_contracts.batch_files", return_value=[[rel] for rel in nodes]), \
         patch("src.debugger_contracts.collect_text", side_effect=_mock_collect_text):
        result = await extract_contracts(graph, provider, config, str(tmp_path))

    assert len(result) == 10
    for rel in nodes:
        assert rel in result
    # Key assertion: semaphore limited concurrency to at most 2
    assert peak_concurrency <= 2, (
        f"Expected max 2 concurrent calls, saw {peak_concurrency}"
    )


# ---------------------------------------------------------------------------
# 13. extract_contracts — error handling (1 batch fails → others succeed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_contracts_error_handling(tmp_path):
    """One batch fails but others still produce results (empty contract for failed)."""
    # 2 files of 10K chars each → 2 separate batches (20K > 15K budget)
    nodes: dict[str, FileNode] = {}
    for i in range(2):
        rel = f"src/file_{i}.py"
        source = f"def func_{i}(): pass\n" + "x" * 10_000
        h = hashlib.sha256(source.encode()).hexdigest()
        _write_source(tmp_path, rel, source)
        nodes[rel] = _make_file_node(rel, source_hash=h)

    graph = DependencyGraph(files=nodes)
    provider = MagicMock()
    config = MagicMock()
    config.debug_max_concurrent_llm = 5
    config.max_turns = 1
    config.debug_cache_contracts = False

    call_count = 0

    async def _side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("LLM failed")
        mock_result = MagicMock()
        mock_result.text = json.dumps({
            "exports": [{"name": "func_1", "signature": "func_1()"}],
            "imports_usage": [],
            "invariants": [],
        })
        return mock_result

    with patch("src.debugger_contracts.collect_text", side_effect=_side_effect):
        result = await extract_contracts(graph, provider, config, str(tmp_path))

    # Both files present
    assert len(result) == 2
    for rel in nodes:
        assert rel in result

    # Failed batch → empty fallback contract; successful batch → parsed export
    empty = [r for r in result.values() if not r.exports]
    non_empty = [r for r in result.values() if r.exports]
    assert len(empty) == 1, "Failed batch should get an empty fallback contract"
    assert len(non_empty) == 1, "Successful batch should preserve parsed exports"
    assert non_empty[0].exports[0].name == "func_1"


# ---------------------------------------------------------------------------
# 14. build_batch_prompt — newlines between source lines (regression)
# ---------------------------------------------------------------------------


def test_build_batch_prompt_newlines():
    """Source lines must be separated by real newlines, not literal '\\n'."""
    fnode = _make_file_node("src/example.py")
    source = "def hello():\n    return 42\n"
    result = build_batch_prompt([("src/example.py", source, fnode)])

    # The output must NOT contain literal backslash-n between line numbers
    assert "\\n" not in result, (
        "build_batch_prompt should join lines with real newlines, "
        "not literal backslash-n"
    )
    # Each numbered line should appear on its own line
    assert "   1 |" in result
    assert "   2 |" in result
