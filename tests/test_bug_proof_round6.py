"""Bug-proof tests for round-6 bugs.

RED (failing) = bug confirmed and real.
GREEN (passing) = bug is a false positive or already fixed.

Run: python3 -m pytest tests/test_bug_proof_round6.py -v
"""

import json
from pathlib import Path


# ===========================================================================
# BUG 1 — debugger_contracts.py uses its own stub CONTRACT_EXTRACTION_PROMPT
#          instead of importing the richer one from debugger_prompts.py
# ===========================================================================

def test_contract_extraction_prompt_uses_rich_version():
    """RED if bug exists: contracts module defines a local 8-line stub and
    ignores the detailed schema-rich version in debugger_prompts.py.

    The rich version adds the "skip private _ names" rule, JSON format
    examples, and a batch format section — all of which are absent from
    the stub.  Every contract extraction call therefore uses the weaker
    prompt, lowering extraction quality.

    Fix: remove the local constant, add
        from src.debugger_prompts import CONTRACT_EXTRACTION_PROMPT
    """
    from src.debugger_contracts import CONTRACT_EXTRACTION_PROMPT as contracts_prompt
    from src.debugger_prompts import CONTRACT_EXTRACTION_PROMPT as prompts_prompt

    assert contracts_prompt == prompts_prompt, (
        "Bug confirmed: debugger_contracts.py defines its own stub "
        "CONTRACT_EXTRACTION_PROMPT instead of importing the rich version "
        "from debugger_prompts.py — all contract extraction uses the weaker prompt.\n"
        f"  contracts version length : {len(contracts_prompt)}\n"
        f"  prompts version length   : {len(prompts_prompt)}"
    )


# ===========================================================================
# BUG 2 — parse_edge_findings silently drops entries missing caller_line
#          because _REQUIRED_FIELDS wrongly includes "caller_line".
#          Spec says only caller_file, callee_file, description are required;
#          missing caller_line should default to 0, not discard the entry.
# ===========================================================================

def test_parse_edge_findings_missing_caller_line_defaults_to_zero():
    """RED if bug exists: a valid LLM finding that omits caller_line is
    silently dropped instead of being kept with caller_line=0.

    The spec (docstring of parse_edge_findings) lists only three required
    fields: caller_file, callee_file, description.  caller_line is listed
    as a validated-but-optional field that defaults to 0 when invalid.

    Fix: remove "caller_line" from _REQUIRED_FIELDS.
    """
    from src.debugger_edges import parse_edge_findings

    # Valid entry — has all three spec-required fields but no caller_line
    raw = json.dumps([{
        "caller_file": "src/foo.py",
        "callee_file": "src/bar.py",
        "description": "return value not checked",
        "confidence": "high",
        "check_type": "return_ignored",
    }])
    findings = parse_edge_findings(raw)

    assert len(findings) == 1, (
        f"Bug confirmed: entry with missing caller_line was silently dropped "
        f"(got {len(findings)} findings instead of 1) — "
        "caller_line is incorrectly listed in _REQUIRED_FIELDS"
    )
    assert findings[0].caller_line == 0, (
        f"Expected caller_line=0 (default), got {findings[0].caller_line}"
    )


# ===========================================================================
# BUG 3 — config.py declares debug_edge_batch_size (and maps it via
#          G3_DEBUG_EDGE_BATCH_SIZE) but no code in src/ ever reads it.
#          Any user who sets this env var gets no effect.
# ===========================================================================

def test_debug_edge_batch_size_is_used_in_src():
    """RED if bug exists: debug_edge_batch_size is a dead config field —
    it is defined and env-mapped but never consumed anywhere in src/.

    Fix: either read config.debug_edge_batch_size inside analyze_edges
    (or wherever batch sizes are controlled), or remove the field and
    its env mapping entirely.
    """
    src_dir = Path(__file__).parent.parent / "src"

    usage_files = []
    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name == "config.py":
            continue  # skip the definition site itself
        if "debug_edge_batch_size" in py_file.read_text(encoding="utf-8"):
            usage_files.append(str(py_file.relative_to(src_dir.parent)))

    assert usage_files, (
        "Bug confirmed: debug_edge_batch_size is defined in Config and mapped "
        "via G3_DEBUG_EDGE_BATCH_SIZE but is never read in any src/ file — "
        "setting this env var has zero effect"
    )
