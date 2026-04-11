"""Implementation-audit tests for the graph-aware debugger plan.

Interpretation:
  - Test FAILS (red)  -> the bug is real in the current implementation.
  - Test PASSES (green) -> that suspicion was a false positive or is already fixed.

This file intentionally mixes both outcomes so we can separate confirmed bugs
from already-resolved items.

Bug inventory:
  BUG-1  Config has the new debugger fields (currently green)
  BUG-2  Env overrides for the new debugger fields are wired (currently green)
  BUG-3  debug_limit_value default is 3 (currently green)
  BUG-4  Edge-analysis phase landed with tests (currently green)
  BUG-5  debugger_intra exists (currently green)
  BUG-6  debugger_render exists (currently green)
  BUG-7  debugger.py still has legacy chunk-loop leftovers in run() (currently red)
  BUG-8  empty contract cached forever on LLM failure (currently red)
  BUG-9  batch_files underestimates real prompt size (currently red)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-1: config.py missing 4 new fields
# ═══════════════════════════════════════════════════════════════════════════════


def test_bug1_config_has_debug_cache_contracts():
    """Config must have debug_cache_contracts field (default True)."""
    from src.config import Config

    cfg = Config()
    assert hasattr(cfg, "debug_cache_contracts"), (
        "BUG-1: Config is missing 'debug_cache_contracts' field"
    )
    assert cfg.debug_cache_contracts is True


def test_bug1_config_has_debug_edge_batch_size():
    """Config must have debug_edge_batch_size field (default 5)."""
    from src.config import Config

    cfg = Config()
    assert hasattr(cfg, "debug_edge_batch_size"), (
        "BUG-1: Config is missing 'debug_edge_batch_size' field"
    )
    assert cfg.debug_edge_batch_size == 5


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-2: env overrides for the new debugger fields work end-to-end
# ═══════════════════════════════════════════════════════════════════════════════


def test_bug2_env_override_applies_cache_contracts(monkeypatch, tmp_path):
    """G3_DEBUG_CACHE_CONTRACTS should override the default through resolve_config()."""
    from src.config import resolve_config

    monkeypatch.setenv("G3_DEBUG_CACHE_CONTRACTS", "false")

    with patch("src.config._load_defaults_section", return_value={}), patch(
        "src.config.load_merged_settings", return_value={}
    ):
        cfg = resolve_config({"working_dir": str(tmp_path)})

    assert cfg.debug_cache_contracts is False, (
        "BUG-2: resolve_config() ignored G3_DEBUG_CACHE_CONTRACTS=false"
    )


def test_bug2_env_override_applies_edge_batch_size(monkeypatch, tmp_path):
    """G3_DEBUG_EDGE_BATCH_SIZE should override the default through resolve_config()."""
    from src.config import resolve_config

    monkeypatch.setenv("G3_DEBUG_EDGE_BATCH_SIZE", "11")

    with patch("src.config._load_defaults_section", return_value={}), patch(
        "src.config.load_merged_settings", return_value={}
    ):
        cfg = resolve_config({"working_dir": str(tmp_path)})

    assert cfg.debug_edge_batch_size == 11, (
        "BUG-2: resolve_config() ignored G3_DEBUG_EDGE_BATCH_SIZE=11"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-3: debug_limit_value default should be 3 (was 10)
# ═══════════════════════════════════════════════════════════════════════════════


def test_bug3_debug_limit_value_is_three():
    """Spec says: 'debug_limit_value: int = 3 (was 10 — one pipeline pass is
    now much more thorough than one chunk pass)'."""
    from src.config import Config

    cfg = Config()
    assert cfg.debug_limit_value == 3, (
        f"BUG-3: debug_limit_value is {cfg.debug_limit_value}, expected 3"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-4: debugger_edges.py missing 4 functions (Phase 4 incomplete)
# ═══════════════════════════════════════════════════════════════════════════════


def test_bug4_test_file_exists():
    """tests/test_debugger_edges.py must exist with 13 tests per spec."""
    test_file = Path(__file__).parent / "test_debugger_edges.py"
    assert test_file.exists(), (
        "BUG-4: tests/test_debugger_edges.py does not exist. "
        "Phase 4 requires 13 tests."
    )
    content = test_file.read_text()
    assert content.count("def test_") >= 10, (
        "BUG-4: test_debugger_edges.py has fewer than 10 tests (spec requires 13)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-5: debugger_intra.py does not exist (Phase 5)
# ═══════════════════════════════════════════════════════════════════════════════


def test_bug5_debugger_intra_importable():
    """debugger_intra module must exist and be importable."""
    import src.debugger_intra  # noqa: F401


def test_bug5_analyze_all_files_exists():
    """analyze_all_files must be importable from debugger_intra."""
    from src.debugger_intra import analyze_all_files  # noqa: F401


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-6: debugger_render.py does not exist (Phase 6)
# ═══════════════════════════════════════════════════════════════════════════════


def test_bug6_debugger_render_importable():
    """debugger_render module must exist and be importable."""
    import src.debugger_render  # noqa: F401


def test_bug6_build_context_from_graph_exists():
    """build_context_from_graph must be importable from debugger_render."""
    from src.debugger_render import build_context_from_graph  # noqa: F401


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-7: debugger.py still imports debugger_context (Phase 7)
# ═══════════════════════════════════════════════════════════════════════════════


def test_bug7_debugger_initializes_graph_state(tmp_path):
    """Debugger now initializes graph/contracts state."""
    from src.config import Config
    from src.debugger import Debugger

    with patch("src.debugger.create_provider", return_value=MagicMock()):
        debugger = Debugger(Config(working_dir=str(tmp_path)))

    assert hasattr(debugger, "_graph"), (
        "BUG-7: Debugger does not initialize graph-aware state (_graph missing)"
    )
    assert hasattr(debugger, "_contracts"), (
        "BUG-7: Debugger does not initialize contract state (_contracts missing)"
    )
    assert not hasattr(debugger, "_chunks")
    assert not hasattr(debugger, "_chunk_cursor")


@pytest.mark.asyncio
async def test_bug7_debugger_run_does_not_reference_removed_chunk_state(tmp_path):
    """run() should not touch removed _chunks/_chunk_cursor state anymore."""
    from src.config import Config
    from src.debugger import Debugger

    cfg = Config(
        working_dir=str(tmp_path),
        debug_limit_mode="iterations",
        debug_limit_value=0,
    )
    with patch("src.debugger.create_provider", return_value=MagicMock()):
        debugger = Debugger(cfg)

    result = await debugger.run()

    assert result.iterations == 0


def test_bug7_no_legacy_run_player_method(tmp_path):
    """_run_player() has been removed — only graph-aware phases remain."""
    from src.config import Config
    from src.debugger import Debugger

    with patch("src.debugger.create_provider", return_value=MagicMock()):
        debugger = Debugger(Config(working_dir=str(tmp_path)))

    assert not hasattr(debugger, "_run_player"), (
        "BUG-7: Debugger still has legacy _run_player() method"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-8: empty contract cached forever on LLM failure
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bug8_failed_contract_retried_on_next_run(tmp_path):
    """When LLM returns garbage, the contract should be re-extracted on the
    next run, NOT cached as an empty contract with the correct hash.

    Current behavior: parse_contract_response returns empty FileContract,
    _process_batch sets source_hash on it, cache saves it. Next run sees
    matching hash → skips extraction → empty contract lives forever.
    """
    from src.debugger_contracts import (
        FileContract,
        FileNode,
        extract_contracts,
    )
    from src.debugger_graph import DependencyGraph

    # One file with known hash
    source = "def my_func(): pass\n"
    h = hashlib.sha256(source.encode()).hexdigest()
    rel = "src/target.py"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(source)

    nodes = {rel: FileNode(
        rel_path=rel, functions=[], classes=[],
        imports=[], external_calls=[], line_count=1,
        source_hash=h,
    )}
    graph = DependencyGraph(files=nodes)
    config = MagicMock()
    config.debug_max_concurrent_llm = 1
    config.max_turns = 1
    config.debug_cache_contracts = True
    provider = MagicMock()

    call_count = 0

    async def _garbage_then_good(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # First call: LLM returns garbage
            result.text = "I cannot analyze this file. Sorry."
        else:
            # Second call: LLM returns valid contract
            result.text = json.dumps({
                "rel_path": rel,
                "exports": [{"name": "my_func", "signature": "my_func()"}],
                "imports_usage": [],
                "invariants": [],
            })
        result.completed = True
        return result

    with patch("src.debugger_contracts.collect_text", side_effect=_garbage_then_good):
        # Run 1: LLM returns garbage → empty contract cached
        result1 = await extract_contracts(graph, provider, config, str(tmp_path))
        assert rel in result1

    with patch("src.debugger_contracts.collect_text", side_effect=_garbage_then_good):
        # Run 2: should re-extract because first attempt produced nothing useful
        result2 = await extract_contracts(graph, provider, config, str(tmp_path))

    # BUG: on the second run, the LLM is never called because the hash matches
    # the empty contract. So result2.exports is still empty.
    assert call_count >= 2, (
        "BUG-8: Second extraction run should call the LLM again, "
        f"but it was only called {call_count} time(s). "
        "Empty contract from failed extraction is cached forever."
    )
    assert len(result2[rel].exports) > 0, (
        "BUG-8: Empty contract from failed extraction is reused on next run. "
        "The file will never get a proper contract."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-9: batch_files underestimates prompt size (no line-number overhead)
# ═══════════════════════════════════════════════════════════════════════════════


def test_bug9_batch_files_line_number_overhead(tmp_path):
    """batch_files counts raw source chars but the actual prompt includes
    line numbers (~6 chars/line overhead for '  123 | ').

    A file with many short lines can fit in the raw budget but exceed the
    actual prompt budget when line numbers are added.
    """
    from src.debugger_contracts import PROMPT_BUDGET, FileNode, batch_files, build_contract_prompt

    # Create a file with 1000 short lines (15 chars each = 15,000 raw chars)
    # With line numbers: each line becomes ~21 chars → ~21,000 chars total
    # That's 6,000 chars over the budget
    short_lines = ["x = 1 + " + str(i) for i in range(1000)]
    raw_source = "\n".join(short_lines)
    assert len(raw_source) <= PROMPT_BUDGET, "Sanity: raw source fits in budget"

    rel = "src/many_lines.py"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(raw_source)

    fnode = FileNode(
        rel_path=rel, functions=[], classes=[],
        imports=[], external_calls=[], line_count=1000,
    )
    nodes = {rel: fnode}

    batches = batch_files(nodes, str(tmp_path))

    # The batch was created thinking the source fits in PROMPT_BUDGET
    assert len(batches) == 1, "Sanity: batch_files put it in one batch"

    # batch_files should ensure the ACTUAL prompt (with line numbers) fits.
    # If it only counts raw source chars, it allows oversized batches.
    prompt = build_contract_prompt(raw_source, fnode)
    actual_prompt_size = len(prompt)

    # The correct behavior: actual prompt must fit within budget
    assert actual_prompt_size <= PROMPT_BUDGET, (
        f"BUG-9: batch_files allowed a batch where the actual prompt is "
        f"{actual_prompt_size} chars, exceeding the {PROMPT_BUDGET} budget. "
        f"batch_files only counts raw source chars ({len(raw_source)}) but "
        f"the real prompt has line-number overhead (~6 chars/line)."
    )
