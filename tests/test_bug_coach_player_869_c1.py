"""Test for bug at src/coach_player.py:869 — ignored return value and
missing TimeoutError handling in code-review fix loop.

Check 1 (ignored return value): The TurnResult from the player fix turn
is discarded, so there is no way to know if the fix succeeded.

Check 3 (side effect before failable validation without rollback): The
player edits files (side effect) during the fix turn. If the call raises
TimeoutError the edits persist with no rollback, and the exception
propagates out of the review loop instead of being caught and handled
gracefully like every other _run_turn / _run_with_continuation call in
the same run() method.

LOGIC:
  - Test FAILS (red)  -> bug EXISTS (code behaves incorrectly)
  - Test PASSES (green) -> bug is FIXED or false positive
"""

from __future__ import annotations

import inspect
import textwrap

import pytest


def test_code_review_fix_turn_missing_timeout_handling():
    """The player fix turn at line 869 should be wrapped in
    try/except TimeoutError, just like every other _run_turn /
    _run_with_continuation call in the run() method.
    """
    import src.coach_player as mod

    source = textwrap.dedent(inspect.getsource(mod.CoachPlayerSession.run))
    lines = source.split("\n")

    in_review_loop = False
    current_try_depth = 0
    timeout_try_depths = set()

    for i, line in enumerate(lines):
        if "build_player_fix_prompt" in line:
            in_review_loop = True

        stripped = line.strip()

        if stripped.startswith("try:"):
            current_try_depth += 1

        if in_review_loop:
            if "except" in stripped and "TimeoutError" in stripped:
                if current_try_depth > 0:
                    timeout_try_depths.add(current_try_depth)

            if "except" in stripped and current_try_depth > 0:
                current_try_depth -= 1
                timeout_try_depths.discard(current_try_depth + 1)

            if ("run_fn(" in line or "await run_fn" in line) and "await" in line:
                if current_try_depth not in timeout_try_depths:
                    pytest.fail(
                        "Bug confirmed: The player fix turn (await run_fn(...)) "
                        "in the code review loop is NOT wrapped in "
                        "try/except TimeoutError. Every other _run_turn / "
                        "_run_with_continuation call in run() handles TimeoutError "
                        "gracefully, but this one does not. If the fix turn "
                        "times out, the exception propagates out and crashes "
                        "the session."
                    )


def test_code_review_fix_turn_return_value_ignored():
    """The return value of the player fix turn at line 869 is silently
    discarded. We verify this by checking that the await expression's
    result is not assigned to any variable.
    """
    import src.coach_player as mod

    source = inspect.getsource(mod.CoachPlayerSession.run)
    lines = source.split("\n")

    in_fix_section = False
    for i, line in enumerate(lines):
        if "build_player_fix_prompt" in line:
            in_fix_section = True
            continue

        if in_fix_section:
            stripped = line.strip()
            if stripped.startswith("await run_fn(") or stripped.startswith(
                "await self._run_turn("
            ):
                if not stripped.startswith("result") and not stripped.startswith(
                    "fix_result"
                ):
                    pytest.fail(
                        "Bug confirmed: The return value of the player fix turn "
                        f"at line ~{i} is silently discarded. The code does: "
                        f"`{stripped}` instead of `fix_result = {stripped}`. "
                        "Every other _run_turn call in run() captures the "
                        "TurnResult for inspection."
                    )
                break
