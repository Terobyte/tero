"""Proof tests for current bugs.md entries.

Convention:
  - FAIL (red)  -> the bug is real in the current implementation
  - PASS (green) -> the bug is fixed or the suspicion was a false positive

This file intentionally mixes both outcomes so bugs.md can track the current
red list while also recording recently retired findings.
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_orchestrator_run_handles_errors_before_round_counter_initializes():
    """run() should report early setup errors instead of crashing on round_num."""
    from src.orchestrator import Orchestrator

    orchestrator = object.__new__(Orchestrator)
    orchestrator.session_id = "sess_test"
    orchestrator.config = SimpleNamespace(plan_file="missing.md")
    orchestrator.session = MagicMock()
    orchestrator.session.create.side_effect = RuntimeError("setup exploded")
    orchestrator.session._state = {"state": "created"}
    orchestrator.worktree = MagicMock()

    result = Orchestrator.run(orchestrator)

    assert result.success is False
    assert result.error == "setup exploded"
    assert result.rounds_used == 0
    orchestrator.worktree.cleanup_all.assert_called_once()


@pytest.mark.asyncio
async def test_claude_native_run_drains_stderr_while_streaming_stdout():
    """run() should not block forever waiting for stdout before draining stderr."""
    from src.providers.claude_native import ClaudeNativeProvider

    stderr_drained = asyncio.Event()

    class FakeStdin:
        def write(self, _data: bytes) -> None:
            pass

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            pass

    class FakeStdout:
        def __init__(self) -> None:
            self._limit = 0
            self._yielded = False

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            if not self._yielded:
                self._yielded = True
                return b'{"type":"text","text":"hello"}\n'
            await stderr_drained.wait()
            raise StopAsyncIteration

    class FakeStderr:
        def __init__(self) -> None:
            self._drained = False

        async def read(self, _n: int = -1) -> bytes:
            if self._drained:
                return b""
            self._drained = True
            stderr_drained.set()
            return b"warning"

    class FakeProc:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()
            self.returncode = 0

        async def wait(self) -> int:
            return 0

        def kill(self) -> None:
            self.returncode = -9
            stderr_drained.set()

    async def _consume() -> list[dict]:
        provider = ClaudeNativeProvider()
        events = []
        async for event in provider.run(
            prompt="prompt",
            system_prompt="system",
            working_dir=".",
        ):
            events.append(event)
        return events

    with patch(
        "src.providers.claude_native.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=FakeProc()),
    ):
        events = await asyncio.wait_for(_consume(), timeout=0.2)

    assert events == [{"type": "text", "text": "hello"}]


@pytest.mark.asyncio
async def test_batch_executor_treats_noverdict_subclasses_as_retry():
    """NoVerdict subclasses should follow the NoVerdict retry path."""
    from src.batch_executor import BatchExecutor
    from src.feedback import NoVerdict
    import src.streaming as streaming_ui

    class DelayedNoVerdict(NoVerdict):
        pass

    rejected_messages: list[str] = []

    executor = object.__new__(BatchExecutor)
    executor.session = SimpleNamespace(
        config=SimpleNamespace(max_turns=1, player_timeout_s=1, player_model=""),
        _run_coach_turn_for_phase=AsyncMock(return_value=DelayedNoVerdict()),
        _runtime=None,
        _snapshot_pids=lambda: set(),
        _kill_new_processes=lambda _pids: None,
    )
    executor.tracker = SimpleNamespace(render_dashboard=lambda: None)
    executor._max_phase_attempts = lambda: 1
    executor._role_label = lambda _role: "player"
    executor._review_strategy = lambda _attempt: {
        "header_role": "coach",
        "label": "coach",
        "provider_name_override": "",
        "model_override": "",
        "review_role": "coach",
    }
    executor._run_player_turn = AsyncMock(return_value=SimpleNamespace(text="done", tools_used=0))

    phase = SimpleNamespace(
        name="Phase 1",
        steps=[SimpleNamespace(text="Implement feature", roles=[])],
        attempts=0,
    )

    with patch("src.batch_executor.build_batch_prompt", return_value="prompt"), patch(
        "src.batch_executor.parse_completed_steps",
        return_value=["Implement feature"],
    ), patch(
        "src.batch_executor.has_required_completion_report",
        return_value=True,
    ), patch(
        "src.batch_executor.player_claimed_tools_unavailable",
        return_value=False,
    ), patch(
        "src.batch_executor.build_incomplete_phase_feedback",
        return_value="RETRY_NO_VERDICT",
    ), patch.object(
        streaming_ui,
        "print_batch_turn_header",
        lambda **_kwargs: None,
    ), patch.object(
        streaming_ui,
        "print_step_rejected",
        rejected_messages.append,
    ):
        success = await BatchExecutor._run_phase(executor, phase)

    assert success is False
    assert rejected_messages[-1] == "RETRY_NO_VERDICT"


def test_runtime_controls_restores_sigwinch_handler_on_stop(monkeypatch):
    """stop() should restore the previous SIGWINCH handler."""
    from src.runtime_controls import RuntimeControls

    if not hasattr(signal, "SIGWINCH"):
        pytest.skip("SIGWINCH not available on this platform")

    controls = RuntimeControls()
    monkeypatch.setattr(controls._listener, "start", lambda: None)
    monkeypatch.setattr(controls._listener, "stop", lambda: None)
    monkeypatch.setattr(controls._listener, "join", lambda timeout=0.2: None)
    monkeypatch.setattr(controls._status_bar, "update", lambda *args, **kwargs: None)
    monkeypatch.setattr(controls._status_bar, "clear", lambda: None)

    previous = signal.getsignal(signal.SIGWINCH)
    try:
        controls.start()
        controls.stop()
        assert signal.getsignal(signal.SIGWINCH) is previous
    finally:
        signal.signal(signal.SIGWINCH, previous)


def test_git_commit_does_not_stage_unrelated_worktree_changes(tmp_path):
    """_git_commit() should not commit unrelated dirty files from the whole repo."""
    from src.config import Config
    from src.debugger import Debugger

    repo = Path(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True, capture_output=True)

    fix_file = repo / "fix.py"
    unrelated_file = repo / "notes.txt"
    fix_file.write_text("print('before')\n")
    unrelated_file.write_text("baseline\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    fix_file.write_text("print('after')\n")
    unrelated_file.write_text("local notes changed\n")

    with patch("src.debugger.create_provider", return_value=MagicMock()):
        debugger = Debugger(Config(working_dir=str(repo)))

    debugger._git_commit(iteration=1, count=1)

    changed_files = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert "notes.txt" not in changed_files


def test_resolve_config_ignores_top_level_unsafe_global_keys(monkeypatch, tmp_path):
    """Top-level unsafe keys from merged config should not silently alter runtime mode."""
    from src.config import resolve_config

    monkeypatch.delenv("G3_BATCH_MODE", raising=False)

    with patch("src.config._load_defaults_section", return_value={}), patch(
        "src.config.load_merged_settings",
        return_value={"batch_mode": True},
    ):
        cfg = resolve_config({"working_dir": str(tmp_path)})

    assert cfg.batch_mode is False
