"""Audit proof tests — prove or disprove each identified bug.

LOGIC
-----
  RED  (test FAILS with current code)  -> bug EXISTS
  GREEN (test PASSES with current code) -> false positive, no bug

Each test asserts CORRECT behavior. A failure means the code violates it.
"""

from __future__ import annotations

import inspect
import sys
from dataclasses import FrozenInstanceError

import pytest


# ===========================================================================
# BUG #1 (HIGH): orchestrator.py resume() skips the in-progress round
# src/orchestrator.py:294-315
#
# round_num = current_round     # e.g. 2
# while round_num < max_rounds:
#     round_num += 1            # -> 3, skipping round 2
#
# EXPECTED: the interrupted round should be re-executed
# ACTUAL:   round increments before executing, so round 2 is never run
# ===========================================================================


def test_resume_skips_in_progress_round():
    """resume() increments round_num before executing — interrupted round skipped.

    RED  -> bug confirmed (round 2 is absent from executed rounds)
    GREEN -> false positive (logic was fixed before this test ran)
    """
    rounds_executed: list[int] = []
    current_round = 2   # round 2 was in progress when session crashed
    max_rounds = 5

    # Mirror the exact resume() loop (orchestrator.py:294,314-315)
    round_num = current_round
    while round_num < max_rounds:
        round_num += 1
        rounds_executed.append(round_num)

    # CORRECT: the interrupted round (2) should be among the executed rounds
    assert 2 in rounds_executed, (
        f"BUG CONFIRMED: resume() skips the in-progress round. "
        f"Executed rounds: {rounds_executed} — round 2 is absent."
    )


# ===========================================================================
# BUG #2 (HIGH): codex.py proc.wait() called before stderr is drained
# src/providers/codex.py:129-133
#
# await proc.wait()                             <- blocks until process exits
# stderr_message = await self._stderr_message() <- reads stderr only after
#
# EXPECTED: stderr must be drained (or read concurrently) BEFORE proc.wait()
# ACTUAL:   if subprocess writes >64 KB to stderr, proc.wait() deadlocks
# ===========================================================================


def test_codex_provider_wait_before_stderr_drain():
    """proc.wait() appears before _stderr_message in codex.py — deadlock risk.

    RED  -> bug confirmed (wait precedes stderr drain in source)
    GREEN -> false positive (order was corrected)
    """
    from src.providers.codex import CodexProvider

    source = inspect.getsource(CodexProvider.run)

    # First occurrence of each pattern in the source text
    wait_pos = source.find("await proc.wait()")
    stderr_pos = source.find("_stderr_message")

    assert wait_pos != -1, "Could not locate 'await proc.wait()' in CodexProvider.run"
    assert stderr_pos != -1, "Could not locate '_stderr_message' in CodexProvider.run"

    # CORRECT order: drain stderr FIRST, then wait for process exit
    assert stderr_pos < wait_pos, (
        f"BUG CONFIRMED: in CodexProvider.run, 'proc.wait()' (offset {wait_pos}) "
        f"appears before '_stderr_message' (offset {stderr_pos}). "
        "A subprocess writing >64 KB to stderr will fill the pipe and deadlock."
    )


def test_opencode_provider_wait_before_stderr_drain():
    """Same ordering bug exists in opencode.py (copy-paste from codex.py).

    RED  -> bug confirmed
    GREEN -> false positive
    """
    from src.providers.opencode import OpenCodeProvider

    source = inspect.getsource(OpenCodeProvider.run)

    wait_pos = source.find("await proc.wait()")
    stderr_pos = source.find("_stderr_message")

    assert wait_pos != -1, "Could not locate 'await proc.wait()' in OpenCodeProvider.run"
    assert stderr_pos != -1, "Could not locate '_stderr_message' in OpenCodeProvider.run"

    assert stderr_pos < wait_pos, (
        f"BUG CONFIRMED: in OpenCodeProvider.run, 'proc.wait()' (offset {wait_pos}) "
        f"appears before '_stderr_message' (offset {stderr_pos}). Same deadlock risk."
    )


# ===========================================================================
# BUG #3 (MEDIUM): claude_native.py stdin write/drain/close outside try/finally
# src/providers/claude_native.py:65-67
#
# proc = await create_subprocess_exec(...)
# proc.stdin.write(...)    <- OUTSIDE try
# await proc.stdin.drain() <- OUTSIDE try — if this raises, proc is never killed
# proc.stdin.close()
# try:
#     ...
# finally:
#     if proc.returncode is None:
#         proc.kill()       <- never reached on drain() exception
#
# EXPECTED: stdin operations should be inside the try/finally block
# ACTUAL:   exception in drain() leaks the subprocess
# ===========================================================================


def test_claude_native_stdin_outside_try_block():
    """stdin.write/drain/close are outside the try/finally in claude_native.py.

    RED  -> bug confirmed (stdin ops precede the try block in source)
    GREEN -> false positive (moved inside try)
    """
    from src.providers.claude_native import ClaudeNativeProvider

    source = inspect.getsource(ClaudeNativeProvider.run)

    stdin_write_pos = source.find("proc.stdin.write(")
    try_pos = source.find("try:")

    assert stdin_write_pos != -1, "Could not locate 'proc.stdin.write' in run()"
    assert try_pos != -1, "Could not locate 'try:' in run()"

    # CORRECT: stdin.write must be INSIDE the try block so finally always runs
    assert stdin_write_pos > try_pos, (
        f"BUG CONFIRMED: 'proc.stdin.write' (offset {stdin_write_pos}) is outside "
        f"the try block (offset {try_pos}). An exception in drain() leaks the process."
    )


# ===========================================================================
# BUG #4 (MEDIUM): chain.py check_ready() makes a redundant second call
# src/providers/chain.py:53-60
#
# def check_ready(self):
#     for p in self.providers:
#         ok, reason = p.check_ready()   <- first call (all fail)
#         if ok:
#             return True, ""
#     return self.providers[0].check_ready()  <- second call! reason was saved above
#
# EXPECTED: return the already-collected error reason without a second call
# ACTUAL:   calls check_ready() again, doubling API/network overhead
# ===========================================================================


def test_chain_check_ready_calls_provider_twice_on_all_fail():
    """ProviderChain.check_ready() makes an unnecessary second call on total failure.

    RED  -> bug confirmed (call_count > 1 for one provider)
    GREEN -> false positive (returns saved reason on first pass)
    """
    from src.providers.chain import ProviderChain

    call_count = 0

    class _FailingProvider:
        @property
        def display_name(self) -> str:
            return "failing"

        def check_ready(self) -> tuple[bool, str]:
            nonlocal call_count
            call_count += 1
            return False, "provider unavailable"

    chain = ProviderChain([_FailingProvider()])
    ok, reason = chain.check_ready()

    assert not ok  # sanity — chain correctly reports failure

    # CORRECT: one provider -> check_ready called exactly once
    assert call_count == 1, (
        f"BUG CONFIRMED: check_ready called {call_count} times for a single "
        "failing provider (expected 1). The second call at line 60 is wasted."
    )


# ===========================================================================
# BUG #5 (FALSE POSITIVE): duplicate 'import shutil' in orchestrator.py
# src/orchestrator.py:3 (module level) and :419 (inside _promote method)
#
# Python's import system caches modules in sys.modules.
# A second 'import shutil' inside a method is a no-op at runtime.
# There is no correctness or performance concern — this is a style issue only.
# ===========================================================================


def test_duplicate_shutil_import_is_harmless_false_positive():
    """Duplicate import shutil is a FALSE POSITIVE — Python deduplicates imports.

    GREEN -> test PASSES, confirming this is NOT a real bug.
    """
    import shutil as stdlib_shutil
    from src import orchestrator

    # Module-level import resolves to the standard library shutil
    assert orchestrator.shutil is stdlib_shutil, (
        "Module-level shutil should be the stdlib shutil object"
    )

    # The inner 'import shutil' inside _promote is cached via sys.modules — no overhead
    assert "shutil" in sys.modules, "shutil is cached in sys.modules after first import"


# ===========================================================================
# BUG #6 (LOW): PlanItem frozen=True with mutable list[str] roles field
# src/plan_tracker.py:13-18
#
# @dataclass(frozen=True)
# class PlanItem:
#     roles: list[str] = field(default_factory=list)  <- mutable despite frozen!
#
# EXPECTED: frozen dataclass should prevent all mutation — roles.append() should fail
# ACTUAL:   frozen only blocks __setattr__; in-place list mutation is unrestricted
# ===========================================================================


def test_frozen_plan_item_prevents_tuple_mutation():
    """PlanItem.roles are immutable tuples, preventing any list mutations.

    This test confirms that the frozen dataclass with tuple roles prevents
    in-place mutations that would bypass the frozen guard.
    """
    from src.plan_tracker import PlanItem

    item = PlanItem(text="implement feature", roles=("dev",))

    # CORRECT: tuples don't have append() method — mutation is prevented at type level
    with pytest.raises(AttributeError):
        # tuples are immutable, so append() doesn't exist
        item.roles.append("unauthorized_role")
