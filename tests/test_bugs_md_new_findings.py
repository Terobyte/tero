"""Coverage for open items in ``bugs.md`` → section «New Findings» and GEN-B16.

These tests encode *expected* behaviour from the bug report. Many are RED until
the implementation is fixed — that is intentional (spec-as-test).

Run:
    python3 -m pytest tests/test_bugs_md_new_findings.py -v
"""

from __future__ import annotations

import inspect

import pytest


# --- NEW-01: get_context_window substring "codex" --------------------------------


def test_new_01_custom_model_with_codex_substring_not_one_million_window() -> None:
    """NEW-01 / BUG-21: arbitrary model names containing ``codex`` must not get 1M window."""
    from src.config import get_context_window

    assert get_context_window("my-custom-codex-model-v2") == 0, (
        "unrelated hyphenated names must not match the codex window rule"
    )
    assert get_context_window("codexifier-3000") == 0


# --- NEW-02: LDB Player should use runtime trace; tracer naming -----------------


def test_new_02_ldb_run_player_calls_trace_function() -> None:
    """NEW-02: Player phase should invoke runtime tracing, not only static blocks."""
    from src.ldb.runner import LdbRunner

    src = inspect.getsource(LdbRunner._run_player)
    assert "trace_function" in src, (
        "NEW-02: LdbRunner._run_player should call trace_function (or equivalent) "
        "so the LLM sees execution-grounded block data"
    )


def test_new_02_tracer_does_not_use_random_randint_for_trace_artifacts() -> None:
    """NEW-02: ``random.randint`` for trace paths risks collisions under parallel runs."""
    from src.ldb import tracer

    src = inspect.getsource(tracer)
    assert "random.randint" not in src, (
        "NEW-02: prefer secrets.token_hex / uuid4 for unique trace file names"
    )


# --- NEW-03: Gemini CLI errors must not look like assistant text ----------------


def test_new_03_gemini_error_stream_event_raises_provider_error() -> None:
    """NEW-03: ``type:error`` from Gemini CLI must surface as ProviderError."""
    from src.errors import ProviderError
    from src.providers.gemini import GeminiProvider

    with pytest.raises(ProviderError):
        GeminiProvider()._adapt_gemini_event({"type": "error", "message": "cli failed"})


# --- NEW-04: Claude native token accounting ------------------------------------


def test_new_04_claude_native_exposes_last_token_counts() -> None:
    """NEW-04: parity with other providers — cost/logging need _last_*_tokens."""
    from src.providers.claude_native import ClaudeNativeProvider

    p = ClaudeNativeProvider()
    assert hasattr(p, "_last_input_tokens"), "missing _last_input_tokens"
    assert hasattr(p, "_last_output_tokens"), "missing _last_output_tokens"


# --- NEW-05: PlanItem roles must be tuple / hashable ----------------------------


def test_new_05_plan_item_list_roles_normalized_and_hashable() -> None:
    """NEW-05: list ``roles`` must not leak as unhashable list on PlanItem."""
    from src.plan_tracker import PlanItem

    item = PlanItem("step", roles=["a", "b"])
    assert isinstance(item.roles, tuple)
    hash(item)


# --- NEW-06: RunRecorder.update_feedback durability -----------------------------


def test_new_06_recorder_update_feedback_uses_atomic_publish() -> None:
    """NEW-06: read–truncate–write without atomic replace risks torn files."""
    from src.learning.recorder import RunRecorder

    src = inspect.getsource(RunRecorder.update_feedback)
    assert (
        "os.replace" in src
        or "rename" in src
        or "NamedTemporaryFile" in src
        or "mkstemp" in src
    ), (
        "NEW-06: rewrite via truncate should be replaced with write-to-temp + "
        "atomic os.replace (or equivalent) under the same lock"
    )


# --- NEW-07: subprocess stderr must not deadlock parent -------------------------


def test_new_07_subprocess_jsonl_concurrent_stderr_drain() -> None:
    """NEW-07: stderr must be drained concurrently with stdout/wait (pipe deadlock)."""
    from src.providers.subprocess_runner import run_subprocess_jsonl

    src = inspect.getsource(run_subprocess_jsonl)
    assert "create_task" in src and "stderr" in src, (
        "NEW-07: drain stderr in a task (or thread) before join/wait on the process"
    )


# --- GEN-B16: menu fallback debugger branch (historical) ------------------------


def test_gen_b16_menu_debugger_dispatch_uses_elif_and_continue() -> None:
    """GEN-B16: bare ``if`` after a branch that handles ``s`` can fall through incorrectly.

    If the debugger menu was removed from ``menu.py``, this check is skipped.
    """
    from src import menu

    full = inspect.getsource(menu)
    if "run_debugger_menu" not in full:
        pytest.skip("run_debugger_menu absent — GEN-B16 N/A after tero simplification")

    # If present, require a safe dispatch pattern near the debugger call.
    assert "run_debugger_menu" in full
    idx = full.index("run_debugger_menu")
    window = full[max(0, idx - 400) : idx + 120]
    assert "elif" in window or "continue" in window, (
        "GEN-B16: after run_debugger_menu use elif/continue so other branches do not run"
    )
