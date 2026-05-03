"""RED proof tests for GEN-B10 through GEN-B17.

Each test asserts correct behavior that the current codebase violates.
All tests must FAIL (red), proving the bugs exist.
Do NOT fix any code.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_compact_codex_context_uses_effective_limit_not_sentinel():
    """GEN-B10: _compact_codex_context passes raw sentinel 110_000 instead of
    effective limit. For glm-5.1 (204_800 window), 110_000 is passed."""
    from src.context_manager import _compact_codex_context
    from src.config import Config, get_effective_context_limit
    from src.constants import DEFAULT_CONTEXT_LIMIT

    config = Config(
        player_model="glm-5.1",
        context_limit=DEFAULT_CONTEXT_LIMIT,
        working_dir="/tmp",
    )

    expected_limit = get_effective_context_limit("glm-5.1", DEFAULT_CONTEXT_LIMIT)
    assert expected_limit == 204_800

    captured: dict = {}

    class CapturingProvider:
        async def run(self, **kwargs):
            captured.update(kwargs)
            yield SimpleNamespace(text="done")

    messages = [SimpleNamespace(role="assistant", content="completed step 1")]
    await _compact_codex_context(CapturingProvider(), messages, config)

    assert "context_limit" in captured, "context_limit was not passed to provider.run()"
    assert captured["context_limit"] == expected_limit, (
        f"GEN-B10: got context_limit={captured['context_limit']}, "
        f"expected effective limit {expected_limit}. "
        f"Sentinel {DEFAULT_CONTEXT_LIMIT} is passed directly."
    )


@pytest.mark.asyncio
async def test_claude_native_raises_on_none_returncode():
    """GEN-B11: `if event.returncode and ...` short-circuits on None.
    Killed process (returncode=None) appears successful instead of raising."""
    from src.providers.claude_native import ClaudeNativeProvider
    from src.providers.subprocess_runner import SubprocessExit

    provider = ClaudeNativeProvider()

    async def _fake_subprocess(*args, **kwargs):
        yield SubprocessExit(returncode=None, stderr=b"killed")

    with patch("src.providers.claude_native.run_subprocess_jsonl", _fake_subprocess):
        raised = False
        try:
            async for _ in provider.run("p", "s", "/tmp", max_turns=1):
                pass
        except (RuntimeError, Exception):
            raised = True

    assert raised, (
        "GEN-B11: No error was raised for returncode=None. "
        "Bug: `if event.returncode and event.returncode != 0` — "
        "bool(None) is False, condition short-circuits."
    )


def test_chain_runaway_error_classified_as_recoverable():
    """GEN-B13: _is_recoverable_error returns False for the runaway response
    error. Chain re-raises immediately, bypassing all fallback providers."""
    from src.providers.chain import _is_recoverable_error

    err = RuntimeError("possible runaway response")
    result = _is_recoverable_error(err)

    assert result is True, (
        f"GEN-B13: _is_recoverable_error returned {result!r} for "
        "'possible runaway response'. Must be True for chain fallback to work. "
        "'runaway' is absent from the keywords list."
    )


def test_registry_unknown_provider_raises_provider_error():
    """GEN-B14: ProviderRegistry raises ValueError instead of ProviderError
    for unknown provider types. Callers only catch ProviderError."""
    from src.providers.registry import ProviderRegistry
    from src.errors import ProviderError

    registry = ProviderRegistry()
    with pytest.raises(ProviderError):
        registry.get("nonexistent_xyz_abc_provider")


@pytest.mark.asyncio
async def test_codex_run_review_cleans_up_temp_instructions_on_failure():
    """GEN-B15: run_review() finally block missing _cleanup_temp_instructions().
    Temp file leaks after a failed review call."""
    from src.providers.codex import CodexProvider
    from src.providers.subprocess_runner import SubprocessExit

    provider = CodexProvider()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="codex_instr_test_", delete=False
    ) as f:
        f.write("# instructions\n")
        tmp_path = f.name

    provider._temp_instructions_path = tmp_path
    assert Path(tmp_path).exists(), "Precondition failed"

    async def _failing_subprocess(*args, **kwargs):
        yield SubprocessExit(returncode=1, stderr=b"review failed")

    with patch("src.providers.codex.run_subprocess_jsonl", _failing_subprocess):
        try:
            async for _ in provider.run_review(working_dir="/tmp"):
                pass
        except Exception:
            pass

    still_exists = Path(tmp_path).exists()
    if still_exists:
        Path(tmp_path).unlink(missing_ok=True)

    assert not still_exists, (
        f"GEN-B15: run_review() did not clean up temp file {tmp_path}. "
        "Missing _cleanup_temp_instructions() in run_review() finally block."
    )


def test_start_debug_back_does_not_call_edit_setting():
    """GEN-B16: After run_debugger_menu returns, missing continue causes
    fall-through to _edit_setting_questionary(config, 'start_debug')."""
    from src.config import Config
    from src.menu import _questionary_menu

    config = Config()
    answers = ["start_debug", None]
    idx = [0]

    def mock_ask():
        i = idx[0]
        idx[0] += 1
        return answers[i] if i < len(answers) else None

    mock_q = MagicMock()
    mock_q.select.return_value.ask.side_effect = mock_ask

    with patch.dict(sys.modules, {"questionary": mock_q}), \
         patch("src.menu.run_debugger_menu", return_value=config), \
         patch("src.menu._edit_setting_questionary", return_value=config) as mock_edit:
        _questionary_menu(config)

    for c in mock_edit.call_args_list:
        if len(c.args) >= 2 and c.args[1] == "start_debug":
            pytest.fail(
                "GEN-B16: _edit_setting_questionary called with 'start_debug'. "
                "Control fell through after run_debugger_menu returned — "
                "missing continue at menu.py ~line 530."
            )


def test_recorder_load_all_skips_corrupt_jsonl_lines():
    """GEN-B17: json.loads() in _read_all_records() has no exception handling.
    A corrupt JSONL line raises json.JSONDecodeError instead of being skipped."""
    from src.learning.recorder import RunRecorder

    with tempfile.TemporaryDirectory() as tmpdir:
        runs_file = Path(tmpdir) / "runs.jsonl"

        valid = {
            "run_id": "run-1234567890-abcd1234",
            "timestamp": "2024-01-01 12:00:00",
            "requirements_file": "task.md",
            "turns_used": 3,
            "max_turns": 10,
            "status": "completed",
            "total_duration_s": 42.5,
            "turn_details": [],
            "feedback": None,
            "metadata": {},
        }
        runs_file.write_text(json.dumps(valid) + "\n" + "NOT JSON {{{\n")

        recorder = RunRecorder(knowledge_dir=tmpdir)
        records = recorder.load_all()

    assert len(records) == 1, (
        f"GEN-B17: Expected 1 valid record, got {len(records)} (or crashed). "
        "Missing try/except around json.loads(line) in _read_all_records()."
    )
