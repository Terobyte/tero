"""RED-only coverage for `bugs.md` audits 1–2: delegations and inline proofs that fail until fixed.

GREEN / false-positive tests are not run here.

Run: python3 -m pytest tests/test_bugs_md_negative_registry.py -v
"""

from __future__ import annotations

import asyncio
import importlib
import pytest


def _import_delegated(module: str):
    """Import sibling test modules (pytest loads them without the ``tests.`` package)."""
    candidates = [module]
    if module.startswith("tests."):
        candidates.append(module[len("tests.") :])
    last: ModuleNotFoundError | None = None
    for name in candidates:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            last = exc
    assert last is not None
    raise last


def _run_class_test(module: str, cls: str, method: str) -> None:
    mod = _import_delegated(module)
    getattr(getattr(mod, cls)(), method)()


def _run_async_module_func(module: str, func: str) -> None:
    mod = _import_delegated(module)
    asyncio.run(getattr(mod, func)())


def _run_sync_module_func(module: str, func: str) -> None:
    mod = _import_delegated(module)
    getattr(mod, func)()


# --- Audit 1 RED (bugs.md) — delegate to test_audit_bugs_* -----------------

# Только делегаты, чьи RED-тесты сейчас падают (остальные BUG-* из bugs.md закрыты или
# доказываются напрямую в test_audit_bugs_* без повторного прогона здесь).
_AUDIT1_CLASS: list[tuple[str, str, str, str]] = [
    ("BUG-02", "tests.test_audit_bugs_critical", "TestScheduleCountsRespectsZeros", "test_zero_schedule_is_respected"),
    ("BUG-20", "tests.test_audit_bugs_serious", "TestDebugLimitZeroPropagated", "test_zero_limit_propagated_to_mode"),
    ("BUG-27", "tests.test_audit_bugs_medium", "TestScheduleCountsOverridesZeros", "test_all_zeros_are_respected"),
]


@pytest.mark.parametrize(
    "bug_id,module,cls,method",
    _AUDIT1_CLASS,
    ids=[row[0] for row in _AUDIT1_CLASS],
)
def test_audit1_red_negative(bug_id: str, module: str, cls: str, method: str) -> None:
    _run_class_test(module, cls, method)


# --- Audit 2 — only delegations that still fail (RED) -----------------------

_AUDIT2_ASYNC: list[tuple[str, str, str]] = [
    ("GEN-B11", "tests.test_new_bugs_general", "test_claude_native_raises_on_none_returncode"),
]


_AUDIT2_SYNC_DELEGATED: list[tuple[str, str, str]] = [
    ("PLAN-B1", "tests.test_new_bugs_plan", "test_display_label_for_judge_with_empty_provider_does_not_raise"),
    ("PLAN-B5", "tests.test_new_bugs_plan", "test_schedule_counts_all_zero_returns_nonzero_defaults"),
    ("PLAN-B6", "tests.test_new_bugs_plan", "test_plan_item_replace_skipped_returns_independent_copy"),
]


@pytest.mark.parametrize(
    "bug_id,module,func",
    _AUDIT2_ASYNC,
    ids=[row[0] for row in _AUDIT2_ASYNC],
)
def test_audit2_async_negative(bug_id: str, module: str, func: str) -> None:
    _run_async_module_func(module, func)


_AUDIT2_SYNC: list[tuple[str, str, str]] = []


@pytest.mark.parametrize(
    "bug_id,module,func",
    _AUDIT2_SYNC_DELEGATED,
    ids=[row[0] for row in _AUDIT2_SYNC_DELEGATED],
)
def test_audit2_sync_delegated(bug_id: str, module: str, func: str) -> None:
    _run_sync_module_func(module, func)


@pytest.mark.parametrize(
    "bug_id,module,func",
    _AUDIT2_SYNC,
    ids=[row[0] for row in _AUDIT2_SYNC],
)
def test_audit2_sync_negative(bug_id: str, module: str, func: str) -> None:
    _run_sync_module_func(module, func)


def test_gen_b9_continuation_preserves_initial_explicit_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """GEN-B9 / PLAN-B3: explicit `provider` from first turn must survive continuation retries.

    Bug: each iteration sets ``provider = provider_override``; when override is None,
    the second attempt re-resolves from the router and drops the caller's provider.
    """
    from unittest.mock import MagicMock

    from src.config import Config
    from src.turn_runner import AgentTurnRunner
    from src.coach_player import TurnResult

    calls: list[str] = []

    async def fake_run_turn(*, provider, **kwargs):
        calls.append(getattr(provider, "_tag", "?"))
        return TurnResult(
            role="player",
            duration_s=0.0,
            tools_used=0,
            messages=[],
            text="incomplete",
        )

    runner = AgentTurnRunner(verbose=False)
    monkeypatch.setattr(runner, "run_turn", fake_run_turn)
    monkeypatch.setattr(runner, "_player_output_complete", lambda text, p: False)

    p_explicit = MagicMock()
    p_explicit._last_input_tokens = 0
    p_explicit._tag = "explicit"

    p_router = MagicMock()
    p_router._last_input_tokens = 0
    p_router._tag = "router"

    mock_router = MagicMock()
    mock_router.provider_for = MagicMock(return_value=p_router)

    config = Config(max_continuation_attempts=2)

    asyncio.run(
        runner.run_with_continuation(
            role="player",
            prompt="task without PHASE_COMPLETE marker",
            system_prompt="",
            max_turns=1,
            timeout_s=30,
            provider=p_explicit,
            router=mock_router,
            config=config,
            model_override="",
            provider_override=None,
        )
    )

    assert calls == ["explicit", "explicit", "explicit"], (
        f"GEN-B9: continuation should keep initial provider; got {calls} "
        "(second call wrongly used router.provider_for)"
    )


def test_plan_b7_skip_branch_syncs_tracker_items() -> None:
    """PLAN-B7: skip branch must update tracker.items (or call phase_done) before checklist write."""
    import inspect

    from src.batch_executor import BatchExecutor

    src = inspect.getsource(BatchExecutor.run)
    assert "phase.status = \"skipped\"" in src
    idx = src.index("phase.status = \"skipped\"")
    snippet = src[idx : idx + 550]
    assert (
        "self.tracker.phase_done" in snippet
        or "tracker.items =" in snippet
        or "tracker.items[:]" in snippet
    ), (
        "PLAN-B7: expected explicit tracker.items sync or phase_done in skip branch"
    )
