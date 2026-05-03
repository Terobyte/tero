"""RED-only tests for `bugs.md` audit 3 (SW-*): only cases that still fail until fixed.

Остальные SW-01…SW-61 описаны в ``bugs.md`` без отдельного автопруфа здесь.

Run: python3 -m pytest tests/test_bugs_md_sw_negative.py -v
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _read_src(rel: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "src" / rel).read_text(encoding="utf-8")


def _src(obj) -> str:
    return inspect.getsource(obj)


async def _consume_async_gen(gen):
    async for _ in gen:
        pass


def test_sw_02_provider_error_import_at_module_level() -> None:
    """SW-02: ProviderError import should live at module level in registry."""
    text = _read_src("providers/registry.py")
    first_create = text.find("def _create_provider")
    header = text[:first_create] if first_create != -1 else text
    assert "ProviderError" in header, "SW-02: move ProviderError import to module level in registry.py"


def test_sw_06_claude_native_stderr_none_safe() -> None:
    """SW-06: stderr None must still raise ProviderError, not AttributeError."""
    from src.errors import ProviderError
    from src.providers.claude_native import ClaudeNativeProvider
    from src.providers.subprocess_runner import SubprocessExit

    async def fake_gen(*a, **k):
        yield SubprocessExit(returncode=1, stderr=None)

    prov = ClaudeNativeProvider()

    with patch("src.providers.claude_native.run_subprocess_jsonl", fake_gen):
        with pytest.raises(ProviderError):
            asyncio.run(_consume_async_gen(prov.run("p", "s", "/tmp", max_turns=1)))


@pytest.mark.asyncio
async def test_sw_07_codex_mkstep_handles_write_errors() -> None:
    """SW-07: mkstemp + write must use try/finally so fd closes if os.write fails."""
    src = _read_src("providers/codex.py")
    assert "mkstemp" in src
    i = src.index("mkstemp")
    block = src[i : i + 500]
    assert "try:" in block and "finally:" in block, (
        "SW-07: temp instructions path needs try/finally around fd from mkstemp"
    )


def test_sw_11_claude_native_check_ready_has_timeout() -> None:
    """SW-11: auth status subprocess should use a timeout."""
    src = _src(__import__("src.providers.claude_native", fromlist=["x"]).ClaudeNativeProvider.check_ready)
    assert "timeout=" in src, "SW-11: subprocess.run should pass timeout"


def test_sw_13_continuation_does_not_call_run_turn_with_none_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """SW-13: continuation retries must not invoke run_turn with provider=None."""
    from src.config import Config
    from src.coach_player import TurnResult
    from src.turn_runner import AgentTurnRunner

    calls: list = []

    async def fake_run_turn(*, provider, **kwargs):
        calls.append(provider)
        return TurnResult("player", 0.0, 0, [], "incomplete")

    runner = AgentTurnRunner(verbose=False)
    monkeypatch.setattr(runner, "run_turn", fake_run_turn)
    monkeypatch.setattr(runner, "_player_output_complete", lambda t, p: False)

    r = MagicMock()
    r.provider_for = MagicMock(side_effect=ValueError("boom"))
    cfg = Config(max_continuation_attempts=2)
    p0 = MagicMock()
    asyncio.run(
        runner.run_with_continuation(
            "player", "p", "", 1, 5, p0, r, cfg, "", None
        )
    )
    assert None not in calls, f"SW-13: run_turn called with None provider in {calls!r}"


def test_sw_47_parse_enriched_indices_strict() -> None:
    """SW-47: out-of-range indices must not clamp to the last item.
    When all indices are invalid, phase should be dropped entirely."""
    from src.plan_tracker import parse_enriched_plan

    content = (
        '## Steps\n1. Only step\n\n## Phases\n- Phase 1: "Big" → steps 9-12\n'
    )
    items, phases = parse_enriched_plan(content)
    assert len(items) == 1 and not phases, (
        "SW-47: phase with all-invalid indices should be dropped, not clamped"
    )


def test_sw_54_recorder_json_error_closes_file() -> None:
    """SW-54: update_feedback must tolerate corrupt JSONL lines (try/except)."""
    src = _read_src("learning/recorder.py")
    start = src.index("def update_feedback")
    block = src[start : start + 2200]
    assert "json.loads(line)" in block
    assert "JSONDecodeError" in block, (
        "SW-54: json.loads in update_feedback loop must catch JSONDecodeError"
    )


def test_sw_61_duel_run_round_cleans_worktrees() -> None:
    """SW-61: run_round must remove temporary worktrees in finally."""
    src = _read_src("duel.py")
    assert "cleanup" in src.lower() or "finally" in src, (
        "SW-61: duel.run_round should call worktree.cleanup / finally cleanup"
    )
