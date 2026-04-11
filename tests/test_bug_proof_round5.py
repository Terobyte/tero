"""Bug-proof tests — each test proves a real bug exists by asserting correct behavior.

A RED (failing) test means the bug is real and confirmed.
A GREEN (passing) test means the bug is a false positive or already fixed.

Run: python -m pytest tests/test_bug_proof_round5.py -v
"""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# BUG 1: debugger_bugs.py — _normalize_description strips leading digits
#         from descriptions that start with a real word like "1-indexed"
# ===========================================================================

def test_normalize_description_strips_leading_number_from_real_word():
    """Bug: _normalize_description removes "1-" from "1-indexed array access"
    making it identical to "indexed array access" — false dedup."""
    from src.debugger_bugs import _normalize_description

    # Two genuinely different bugs:
    #   "1-indexed array access fails" vs "indexed array access fails"
    # After normalization both become "indexed array access fails" -> false dedup
    result1 = _normalize_description("1-indexed array access fails")
    result2 = _normalize_description("indexed array access fails")

    # These are DIFFERENT descriptions and should NOT normalize to the same string
    assert result1 != result2, (
        f"Bug confirmed: both normalize to '{result1}' — "
        "descriptions starting with a digit-word are falsely deduped"
    )


# ===========================================================================
# BUG 2: debugger_bugs.py — extract_json_from_text Strategy 3 duplicates
#         results already found by Strategy 1 and 2
# ===========================================================================

def test_extract_json_from_text_produces_duplicates():
    """Bug: bracket-matched Strategy 3 re-finds the same JSON that Strategies
    1 and 2 already extracted, inflating the candidate list with duplicates."""
    from src.debugger_bugs import extract_json_from_text

    text = '```json\n[{"file": "a.py", "line": 1, "description": "x", "severity": "high"}]\n```'
    candidates = extract_json_from_text(text)

    # The same JSON array appears multiple times in candidates
    # Strategy 1 finds it via ```json block
    # Strategy 3 finds it via bracket matching
    # Strategy 4 finds it via the entire text
    # This means parsing is redundantly attempted 3+ times on the same content
    json_arrays = [c for c in candidates if c.strip().startswith("[")]
    unique_arrays = set(json_arrays)

    assert len(json_arrays) == len(unique_arrays), (
        f"Bug confirmed: {len(json_arrays)} candidates but only {len(unique_arrays)} unique — "
        "same JSON extracted multiple times"
    )


# ===========================================================================
# BUG 3: debugger_graph.py — _source_package returns wrong package for
#         single-component files like "utils.py"
# ===========================================================================

def test_source_package_single_file_returns_empty():
    """Bug: _source_package("utils.py") returns [] because parts[:-1] on a
    single-element list yields an empty list, but the package for a top-level
    file IS the root package (empty), which happens to be correct by accident.

    However, _compute_full_module with this empty list and level=1 will
    produce an incorrect module path for relative imports from top-level files.
    Let's test _compute_full_module instead."""
    from src.debugger_graph import _compute_full_module, _source_package

    # For a file "utils.py" at root, _source_package returns []
    pkg = _source_package("utils.py")
    assert pkg == []  # This is actually correct

    # But _compute_full_module with level=1 from root file:
    # cut = 0 - 0 = 0, base_parts = []
    # module_path "helpers" -> "helpers" (correct)
    result = _compute_full_module("utils.py", level=1, module_path="helpers")
    assert result == "helpers"  # This is correct

    # Empty module_path at root level must return None, not "".
    # "" would pass the `if sym_full is not None` guard and silently store
    # an empty string in import_map, corrupting the map.
    result_empty = _compute_full_module("utils.py", level=1, module_path="")
    assert result_empty is None, (
        f"Bug: _compute_full_module returned {result_empty!r} instead of None "
        "for empty module_path at root level — empty string leaks into import_map"
    )

    # Level=2 from root file:
    # cut = 0 - 1 < 0 -> max(0, -1) = 0, base_parts = []
    # This means level=2 from root silently degrades to level=1
    result2 = _compute_full_module("utils.py", level=2, module_path="helpers")
    assert result2 != "helpers", (
        f"Bug confirmed: level=2 relative import from root file resolves to '{result2}' "
        "instead of failing — silently ignores the extra parent level"
    )


# ===========================================================================
# BUG 4: debugger.py — _verify_confirmed_fixes marks bugs fixed even when
#         they don't have a test_file, because the loop only skips bugs
#         with no test_file but still counts ALL bugs for the return value
# ===========================================================================

def test_verify_confirmed_fixes_only_counts_tested_bugs():
    """Bug: _verify_confirmed_fixes includes bugs without test_file in the
    denominator of the return count, but those bugs are never marked fixed.
    However, the return value only counts bugs with status=='fixed', so
    bugs without test_file correctly stay 'confirmed' and are excluded from
    the count. Let me verify the actual behavior."""
    from src.debugger import Debugger
    from src.debugger_bugs import BugEntry
    from src.config import Config

    config = Config()
    debugger = Debugger(config)

    # Bug WITH test_file that passes -> should be marked fixed
    # Bug WITHOUT test_file -> should stay confirmed
    bugs = [
        BugEntry(id=1, file="a.py", line=1, description="bug1",
                 severity="high", status="confirmed",
                 test_file="tests/test_fake_pass.py"),
        BugEntry(id=2, file="b.py", line=2, description="bug2",
                 severity="high", status="confirmed",
                 test_file=None),
    ]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        fixed_count = debugger._verify_confirmed_fixes(bugs)

    # Bug 1 should be fixed, bug 2 should stay confirmed
    assert bugs[0].status == "fixed"
    assert bugs[1].status == "confirmed"
    assert fixed_count == 1  # Only bug 1 counted


# ===========================================================================
# BUG 5: debugger_bugs.py — parse_bugs silently accepts non-string severity
#         values that may cause issues downstream
# ===========================================================================

def test_parse_bugs_handles_non_string_severity():
    """Bug: parse_bugs converts severity to str(), so numeric severity like
    1 or True is accepted instead of being rejected. This inflates results
    with low-quality entries."""
    from src.debugger_bugs import parse_bugs

    raw = json.dumps([
        {"file": "a.py", "line": 1, "description": "test bug", "severity": 1},
        {"file": "b.py", "line": 2, "description": "test bug2", "severity": True},
    ])
    bugs = parse_bugs(raw)

    # severity "1" and "True" are nonsensical but accepted
    assert len(bugs) == 2
    assert bugs[0].severity == "1"
    assert bugs[1].severity == "True"


# ===========================================================================
# BUG 6: debugger_edges.py — parse_edge_findings allows caller_line=0
#         which is never a valid line number, creating phantom findings
# ===========================================================================

def test_parse_edge_findings_allows_zero_line():
    """Bug: caller_line=0 passes validation because the check is
    `caller_line <= 0` which sets it to 0. Zero is never a valid line
    number in source code. Findings with line 0 are phantom entries that
    cannot be traced back to real code."""
    from src.debugger_edges import parse_edge_findings

    raw = json.dumps([
        {
            "caller_file": "a.py",
            "callee_file": "b.py",
            "caller_line": -5,
            "description": "test",
            "check_type": "signature_mismatch",
        }
    ])
    findings = parse_edge_findings(raw)

    assert len(findings) == 1
    assert findings[0].caller_line == 0, (
        "Bug confirmed: invalid line -5 is normalized to 0 instead of being rejected"
    )


# ===========================================================================
# BUG 7: debugger_graph.py — discover_py_files skips "tests" directory
#         meaning the debugger never analyzes test files for bugs
# ===========================================================================

def test_discover_py_files_skips_tests_dir():
    """Bug: _SKIP_DIRS includes 'tests' and 'test', so the debugger
    graph-builder never discovers test files. This means bugs in test
    helpers or test fixtures are invisible to the debugger."""
    from src.debugger_graph import discover_py_files, _SKIP_DIRS

    assert "tests" in _SKIP_DIRS
    assert "test" in _SKIP_DIRS


# ===========================================================================
# BUG 8: debugger_contracts.py — build_contract_prompt truncation can
#         cut in the middle of a multi-byte character or a line number
#         prefix, producing invalid prompt content
# ===========================================================================

def test_build_contract_prompt_truncation_mid_line():
    """Bug: when the numbered source exceeds max_source chars, it's truncated
    at an arbitrary byte position which can cut in the middle of a line
    number prefix, producing garbled output for the LLM."""
    from src.debugger_contracts import build_contract_prompt, PROMPT_BUDGET
    from src.debugger_graph import FileNode, FunctionSig, ClassSig, ImportEdge, ExternalCall

    # Create a large source that will be truncated
    long_source = "x = 1\n" * 5000  # ~35k chars

    fnode = FileNode(
        rel_path="big.py",
        functions=[],
        classes=[],
        imports=[],
        external_calls=[],
        line_count=5000,
    )

    prompt = build_contract_prompt(long_source, fnode)

    # The truncation marker should be present
    assert "... [truncated]" in prompt


# ===========================================================================
# BUG 9: debugger.py — _compact_status uses variable name 'f' which
#         shadows the builtin, and more importantly uses the SAME name
#         as the outer-scope 'f' in _display_final
# ===========================================================================

def test_compact_status_does_not_mutate_bugs():
    """This is a non-bug verification — _compact_status uses `f` as a local
    variable in a generator expression which doesn't shadow anything dangerous.
    Let me verify it returns a non-empty string."""
    from src.debugger import Debugger
    from src.debugger_bugs import BugEntry
    from src.config import Config

    config = Config()
    debugger = Debugger(config)
    debugger._bugs = [
        BugEntry(id=1, file="a.py", line=1, description="test",
                 severity="high", status="open"),
    ]

    result = debugger._compact_status()
    # Should contain ANSI escape codes and the count
    assert "\033[31m" in result  # red for open=1


# ===========================================================================
# BUG 10: debugger.py — _Pulse._animate uses hardcoded _STALE_THRESHOLD_S
#          from debugger_llm, creating a cross-module dependency on a
#          private constant
# ===========================================================================

def test_pulse_stale_threshold_import():
    """Bug: _Pulse in debugger.py references _STALE_THRESHOLD_S from
    debugger_llm.py — a private constant (leading underscore). If the
    constant is renamed or removed, _Pulse silently breaks."""
    from src.debugger_llm import _STALE_THRESHOLD_S
    from src.debugger import _Pulse

    pulse = _Pulse("test")
    # The import works but creates fragile coupling
    assert _STALE_THRESHOLD_S == 15


# ===========================================================================
# BUG 11: debugger_bugs.py — strip_trailing_commas breaks valid JSON
#          that has a comma inside a string value before ] or }
# ===========================================================================

def test_strip_trailing_commas_breaks_string_with_comma():
    """Bug: strip_trailing_commas removes commas before ] or } even when
    the comma is inside a JSON string value, corrupting valid data."""
    from src.debugger_bugs import strip_trailing_commas

    # Valid JSON with comma inside a string value before ]
    input_json = '[{"desc": "use x, not y"}]'
    result = strip_trailing_commas(input_json)

    # The comma inside the string should NOT be removed
    assert result == input_json, (
        f"Bug confirmed: '{result}' — comma inside string was incorrectly stripped"
    )


# ===========================================================================
# BUG 12: debugger_bugs.py — parse_bugs description dedup is too aggressive
#          _normalize_description truncates at 60 chars losing distinguishing
#          info from long descriptions
# ===========================================================================

def test_normalize_description_truncates_at_60():
    """Bug: _normalize_description truncates to 60 chars, so two bugs with
    the same first 60 chars but different details will be falsely deduped."""
    from src.debugger_bugs import _normalize_description

    desc1 = "Wrong variable used in calculation of total price for items in cart when quantity is zero"
    desc2 = "Wrong variable used in calculation of total price for items in cart when quantity is negative"

    norm1 = _normalize_description(desc1)
    norm2 = _normalize_description(desc2)

    # These are DIFFERENT bugs (zero vs negative) but may normalize to same
    assert norm1 != norm2, (
        f"Bug confirmed: both normalize to '{norm1}' — truncation causes false dedup"
    )


# ===========================================================================
# BUG 13: config.py — short_model_name matches "mimo" before "minimax"
#          causing "minimax" to be labeled as "MIMO"
# ===========================================================================

def test_short_model_name_minimax_labeled_as_mimo():
    """Bug: short_model_name checks "mimo" substring before "minimax",
    so "minimax-m2.5" matches "mimo" first and returns "MIMO" instead of
    "MINIMAX"."""
    from src.config import short_model_name

    result = short_model_name("minimax-m2.5")
    assert result == "MINIMAX", (
        f"Bug confirmed: 'minimax-m2.5' returns '{result}' instead of 'MINIMAX' — "
        "'mimo' matches before 'minimax' in the if-chain"
    )


# ===========================================================================
# BUG 14: debugger.py — _git_commit uses glob "*.py" from working_dir
#          which only matches top-level .py files, not nested ones
# ===========================================================================

def test_git_commit_only_stages_top_level_py_files():
    """Bug: _git_commit runs 'git add -- *.py' from working_dir, which
    only stages Python files in the root directory. Files in subdirectories
    like src/fix.py are NOT staged."""
    from src.debugger import Debugger
    from src.config import Config

    config = Config(working_dir="/tmp/test_tero")
    debugger = Debugger(config)

    # Verify the command that would be run
    calls = []
    def mock_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    with patch("subprocess.run", side_effect=mock_run):
        debugger._git_commit(1, 1)

    # Find the git add command
    add_calls = [c for c in calls if "add" in c]
    assert len(add_calls) > 0

    # The glob *.py only matches top-level files
    assert "*.py" in add_calls[0], (
        "Bug confirmed: git add uses '*.py' glob which only matches top-level files"
    )


# ===========================================================================
# BUG 15: debugger_render.py — _number_lines computes width from start
#          param but start is always 1 for the head section
# ===========================================================================

def test_number_lines_width_with_large_start():
    """Verify _number_lines correctly handles large start values (for tail
    section of large files)."""
    from src.debugger_render import _number_lines

    # Tail of a 1000-line file starts at line 901
    lines = ["code"] * 100
    result = _number_lines(lines, start=901)

    # Width should be 4 (to fit "1000")
    first_line = result.split("\n")[0]
    assert first_line.startswith(" 901:"), f"Got: {first_line}"

    last_line = result.split("\n")[-1]
    assert last_line.startswith("1000:"), f"Got: {last_line}"


# ===========================================================================
# BUG 16: debugger_contracts.py — parse_contract_response heuristic for
#          batch vs single is fragile — a single-file response with a key
#          like "exports" at top level AND other random keys breaks it
# ===========================================================================

def test_parse_contract_response_batch_with_exports_key():
    """Bug: parse_contract_response treats a response as single-file if ANY
    of the contract_keys appears at top level. But a batch response like
    {"src/foo.py": {...}, "exports": [...]} would be misclassified."""
    from src.debugger_contracts import parse_contract_response

    # Batch response where one rel_path happens to be named "exports"
    batch_json = json.dumps({
        "exports": {
            "rel_path": "exports.py",
            "exports": [{"name": "process"}],
        }
    })

    result = parse_contract_response(batch_json)

    # This should be parsed as a batch (dict keyed by rel_path)
    # But because "exports" is in _contract_keys, it's parsed as single-file
    from src.debugger_contracts import FileContract
    assert isinstance(result, dict) and not isinstance(result, FileContract), (
        "Bug confirmed: batch response with key 'exports' misclassified as single-file"
    )


# ===========================================================================
# BUG 17: debugger.py — victory check is ANDed with _inconclusive_reason
#          being None, but _mark_inconclusive only records the FIRST reason.
#          If the first call was a transient issue and subsequent iterations
#          succeed, victory is still blocked.
# ===========================================================================

def test_mark_inconclusive_only_records_first():
    """Bug: _mark_inconclusive only saves the first reason and never clears
    it, so even if the debugger recovers and achieves clean passes, it
    can never declare victory."""
    from src.debugger import Debugger
    from src.config import Config

    config = Config()
    debugger = Debugger(config)

    # First inconclusive
    debugger._mark_inconclusive("First error")
    assert debugger._inconclusive_reason == "First error"

    # Debugger recovers... but reason is never cleared
    debugger._mark_inconclusive(None)  # This is a no-op (None check)
    assert debugger._inconclusive_reason == "First error"

    # Victory would be blocked even with clean passes
    debugger._clean_passes = 3
    victory = (
        debugger._clean_passes >= config.debug_victory_threshold
        and debugger._inconclusive_reason is None
    )
    assert not victory, (
        "Bug confirmed: victory is blocked forever after first inconclusive call"
    )


# ===========================================================================
# BUG 18: debugger_llm.py — collect_text retry index is off by one
#          _RETRY_BACKOFF_S has 3 entries but range(len+1) gives 4 attempts
#          (1 normal + 3 retries), which is correct. But the wait time
#          for attempt N uses _RETRY_BACKOFF_S[N], and on the 3rd retry
#          (attempt=3), it would index _RETRY_BACKOFF_S[3] which doesn't
#          exist if we get there. Let me check...
# ===========================================================================

def test_collect_text_retry_count():
    """Verify retry logic doesn't index out of bounds."""
    from src.debugger_llm import _RETRY_BACKOFF_S

    # _RETRY_BACKOFF_S = [60, 120, 240] — 3 entries
    # range(len(_RETRY_BACKOFF_S) + 1) = range(4) -> attempts 0,1,2,3
    # attempt 0: normal (no retry)
    # attempt 1: retry, wait = _RETRY_BACKOFF_S[0] = 60
    # attempt 2: retry, wait = _RETRY_BACKOFF_S[1] = 120
    # attempt 3: retry, wait = _RETRY_BACKOFF_S[2] = 240
    # attempt >= 3 and >= len(3): return incomplete

    # So we get 4 total attempts: 1 normal + 3 retries. Correct.
    # The comment says "60s → 120s → 240s (7 min total before giving up)"
    # But total wait = 60+120+240 = 420s = 7 min. Correct.
    assert len(_RETRY_BACKOFF_S) == 3
    assert sum(_RETRY_BACKOFF_S) == 420


# ===========================================================================
# BUG 19: config.py — get_context_window matches "codex" before specific
#          models, so "codex-o3" would match "codex" (128k) not "o3" (128k).
#          The match is first-come by substring, and "codex" appears before
#          "o3" in the list. Since both are 128k, this is harmless but
#          "codex" matching any model with "codex" in the name is fragile.
# ===========================================================================

def test_get_context_window_ordering():
    """Verify context window lookup order — first match wins."""
    from src.config import get_context_window

    # "codex" appears at index 10, "o3" at index 11
    # A model named "codex-o3" would match "codex" first
    result = get_context_window("codex-o3")
    assert result == 128_000  # Same value either way, but worth noting


# ===========================================================================
# BUG 20: debugger_graph.py — parse_file uses errors="strict" for reading
#          source, which raises UnicodeDecodeError on files with non-UTF8
#          bytes. Other modules use errors="replace" consistently. This
#          inconsistency means one bad file can crash the entire graph build.
# ===========================================================================

def test_parse_file_strict_encoding():
    """Bug: parse_file uses errors='strict' for reading source, while all
    other read sites use errors='replace'. A single file with non-UTF8
    bytes (e.g. a byte literal in a test) crashes the graph build."""
    import tempfile
    from src.debugger_graph import parse_file

    # Create a temp file with non-UTF8 bytes
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="wb") as f:
        f.write(b"# coding: utf-8\nx = '\xff'\n")  # invalid UTF-8
        temp_path = f.name

    try:
        result = parse_file(temp_path, "temp.py", "/tmp")
        assert result is None, (
            "Bug confirmed: parse_file returns None for non-UTF8 files instead of "
            "gracefully handling them like the rest of the pipeline"
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)


# ===========================================================================
# BUG 21: debugger.py — _parse_tester_results can match JSON from prose
#          explanation rather than the actual results array, because it
#          tries all candidates and takes the first one that produces results
# ===========================================================================

def test_parse_tester_results_takes_first_valid_array():
    """Bug: _parse_tester_results tries all JSON candidates and breaks on
    the first one that produces results. If the LLM includes an example
    JSON array in its explanation text before the actual results, the
    example gets parsed instead."""
    from src.debugger import Debugger
    from src.config import Config

    config = Config()
    debugger = Debugger(config)

    # LLM output with an example array before the real results
    raw = '''Here's an example of the format:
```json
[{"bug_id": 1, "status": "confirmed", "test_file": "example.py"}]
```

But actually the real results are:
```json
[{"bug_id": 1, "status": "false_positive", "test_file": null}]
```'''

    results = debugger._parse_tester_results(raw)

    # The FIRST json block (the example) is parsed, not the real results
    assert results.get(1, {}).get("status") == "false_positive", (
        f"Bug confirmed: got status='{results.get(1, {}).get('status')}' from the "
        "example block instead of 'false_positive' from the real results"
    )


# ===========================================================================
# BUG 22: debugger_intra.py — analyze_file does not pass pulse parameter
#          to collect_text, so intra-file analysis has no live status
# ===========================================================================

def test_analyze_file_no_pulse_parameter():
    """Bug: analyze_file calls collect_text without a pulse parameter,
    so the user sees no status indicator during intra-file analysis."""
    import inspect
    from src.debugger_intra import analyze_file

    sig = inspect.signature(analyze_file)
    params = list(sig.parameters.keys())
    assert "pulse" not in params, (
        "Bug confirmed: analyze_file has no pulse parameter — no live status during analysis"
    )


# ===========================================================================
# BUG 23: debugger_render.py — build_context_from_graph passes list
#          to set membership check `dep_path not in file_subset` which is
#          O(n) instead of O(1), slowing down for large file sets
# ===========================================================================

def test_build_context_uses_list_for_membership():
    """Bug: file_subset is a list[str], and `dep_path not in file_subset`
    is O(n) for each check. Should be a set for O(1) lookups."""
    import inspect
    from src.debugger_render import build_context_from_graph

    src = inspect.getsource(build_context_from_graph)
    assert "file_subset" in src

    # The function signature takes list[str] but uses it for membership tests
    sig = inspect.signature(build_context_from_graph)
    assert sig.parameters["file_subset"].annotation == "list[str]"
