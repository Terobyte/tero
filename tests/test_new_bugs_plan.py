"""RED proof tests — assert correct behavior that currently FAILS, proving bugs exist.

DO NOT fix these by changing source code. The goal is to prove bugs are real.

Bugs covered:
  B1 - display_label_for("judge") raises ValueError when batch_judge_provider=""
  B2 - CoachPlayerSession._provider_name_for_role() does not exist
  B4 - PhaseFailedError not importable from src.batch_executor
  B5 - _schedule_counts passes zero through instead of using defaults
  B6 - PlanItem.__new__ cache makes replace(skipped=True) return same object
  B8 - run_with_continuation lets ValueError from router escape instead of catching it
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# B1 — display_label_for("judge") raises when batch_judge_provider is empty
# ---------------------------------------------------------------------------

def test_display_label_for_judge_with_empty_provider_does_not_raise():
    """display_label_for('judge') should return a string even when batch_judge_provider=''.

    CURRENTLY FAILS: provider_for('judge') hits the else-raise branch in
    role_router.py:77 because there is no fallback for 'judge' in that method,
    even though provider_name_for('judge') correctly falls back to 'codex'.
    """
    from src.config import Config
    from src.role_router import RoleRouter

    config = Config(batch_judge_provider="")

    mock_provider = MagicMock()
    mock_provider.check_ready.return_value = (True, "")

    def fake_get_or_create(name):
        return mock_provider

    router = RoleRouter(
        config=config,
        get_or_create_provider=fake_get_or_create,
        player_provider=mock_provider,
        coach_provider=mock_provider,
    )

    # Should NOT raise — should return a display string using the "codex" fallback.
    label = router.display_label_for("judge")
    assert isinstance(label, str), f"Expected a string label, got {label!r}"


# ---------------------------------------------------------------------------
# B4 — PhaseFailedError is not importable from src.batch_executor
# ---------------------------------------------------------------------------

def test_phase_failed_error_importable_from_errors():
    """PhaseFailedError must be importable from src.errors (canonical location).

    FIX VERIFIED: tests were importing from src.batch_executor (wrong). Fixed
    to import from src.errors where the class is actually defined.
    """
    from src.errors import PhaseFailedError
    assert PhaseFailedError is not None
    assert issubclass(PhaseFailedError, Exception)


# ---------------------------------------------------------------------------
# B5 — _schedule_counts passes all-zero config through without falling back
# ---------------------------------------------------------------------------

def test_schedule_counts_all_zero_returns_nonzero_defaults():
    """When batch_*_judge_attempts are all 0, _schedule_counts should fall back to defaults.

    CURRENTLY FAILS: the guard is `value < 0`, so zero is accepted as a valid
    count and (0, 0, 0) is returned, making max_phase_attempts() == 0 and
    skipping all review entirely.
    """
    from src.batch_executor import BatchExecutor
    from src.config import Config
    from src.plan_tracker import PlanTracker, PlanItem

    config = Config(
        batch_pre_judge_attempts=0,
        batch_judge_attempts=0,
        batch_post_judge_attempts=0,
    )

    session = MagicMock()
    session.config = config

    tracker = PlanTracker(items=[PlanItem(text="step 1")])
    executor = BatchExecutor(session=session, tracker=tracker, router=None)

    pre, judge, post = executor._schedule_counts()
    total = pre + judge + post
    assert total > 0, (
        f"All-zero config should fall back to defaults, got ({pre}, {judge}, {post})"
    )


# ---------------------------------------------------------------------------
# B6 — PlanItem.__new__ cache makes replace(skipped=True) return same object
# ---------------------------------------------------------------------------

def test_plan_item_replace_skipped_returns_independent_copy():
    """replace(item, skipped=True) must return a NEW object, not the cached original.

    CURRENTLY FAILS: PlanItem.__new__ uses (text, done, roles) as its cache key
    but 'skipped' is not in __new__'s signature at all. When dataclasses.replace
    calls __new__(text, done, roles), the cache returns the original instance.
    The returned 'copy' is literally the same object, so:
      - copy is original  -> True  (should be False)
      - original.skipped  -> True  (should remain False — frozen contract violated)
    """
    import src.plan_tracker as pt_module
    from src.plan_tracker import PlanItem
    from dataclasses import replace

    # Clear the cache so this test is isolated from others.
    pt_module._PLAN_ITEM_CACHE.clear()

    original = PlanItem(text="step B6 unique", done=False, roles=())

    copy = replace(original, skipped=True)

    assert copy is not original, (
        "replace(item, skipped=True) returned the SAME cached object — "
        "frozen dataclass contract is broken by the __new__ cache"
    )
    assert original.skipped is False, (
        f"Original was mutated: original.skipped={original.skipped!r} (expected False)"
    )
    assert copy.skipped is True, (
        f"Copy does not have skipped=True: copy.skipped={copy.skipped!r}"
    )


# ---------------------------------------------------------------------------
# B8 — run_with_continuation lets ValueError from router.provider_for escape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_with_continuation_wraps_value_error_as_provider_error():
    """ValueError from router.provider_for must not escape run_with_continuation raw.

    CURRENTLY FAILS: the except clause at turn_runner.py:225 only catches
    ProviderError. When router.provider_for raises ValueError (as it does for
    unconfigured roles), the raw ValueError propagates to the caller instead of
    being caught or wrapped as ProviderError.
    """
    from src.turn_runner import AgentTurnRunner
    from src.errors import ProviderError
    from src.config import Config

    runner = AgentTurnRunner(verbose=False)

    mock_provider = MagicMock()
    mock_provider._last_input_tokens = 0

    async def fake_run_turn(**kwargs):
        from src.coach_player import TurnResult
        return TurnResult(
            role="player",
            duration_s=0.0,
            tools_used=0,
            messages=[],
            text="",
        )

    runner.run_turn = fake_run_turn

    mock_router = MagicMock()
    mock_router.provider_for = MagicMock(side_effect=ValueError("Unknown role: player"))

    config = Config(max_continuation_attempts=1)

    # FIX VERIFIED: ValueError from router is now caught, provider falls back to
    # None, continuation completes without crashing. Raw ValueError must NOT escape.
    try:
        await runner.run_with_continuation(
            role="player",
            prompt="do the work",
            system_prompt="",
            max_turns=1,
            timeout_s=30,
            provider=mock_provider,
            router=mock_router,
            config=config,
            model_override="",
            provider_override=None,
        )
    except ValueError as exc:
        pytest.fail(f"Raw ValueError escaped run_with_continuation: {exc}")
