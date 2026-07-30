"""Focused regressions for confirmed bug fixes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.bug_detector import BugReport
from src.config import Config
from src.duel import DuelRunner, RoundResult
from src.feedback import Approved, Feedback, parse_coach_output
from src.judge import JudgeDecision, JudgeRunner
from src.providers.base import AgentResult
from src.providers.codex import CodexConfig, CodexProvider
from src.errors import ProviderError
from src.providers.opencode import OpenCodeConfig, OpenCodeProvider


class _MockStdout:
    def __init__(self, lines: list[bytes]):
        self._lines = lines
        self._index = 0

    async def read(self, _size: int = -1):
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line


class _MockStderr:
    def __init__(self, text: str = ""):
        self._text = text

    async def read(self):
        return self._text.encode()


class _MockStdin:
    def write(self, _data: bytes):
        return None

    async def drain(self):
        return None

    def close(self):
        return None


class _MockProcess:
    def __init__(self, lines: list[bytes], stderr_text: str = "", wait_returncode: int = 0):
        self.stdout = _MockStdout(lines)
        self.stderr = _MockStderr(stderr_text)
        self.stdin = _MockStdin()
        self.returncode = None
        self._wait_returncode = wait_returncode

    async def wait(self):
        self.returncode = self._wait_returncode

    def kill(self):
        self.returncode = -9


def _jsonl_events(*events: dict) -> list[bytes]:
    return [(json.dumps(event) + "\n").encode() for event in events]


@pytest.mark.asyncio
async def test_codex_nonzero_exit_raises_failure(monkeypatch):
    provider = CodexProvider(CodexConfig())
    process = _MockProcess(
        _jsonl_events({"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}),
        stderr_text="fatal failure",
        wait_returncode=23,
    )

    async def _create_subprocess(*_args, **_kwargs):
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", _create_subprocess)

    with pytest.raises((RuntimeError, ProviderError), match="codex exited with code 23"):
        async for _ in provider.run("prompt", "", ".", 10):
            pass


@pytest.mark.asyncio
async def test_opencode_nonzero_exit_raises_failure(monkeypatch):
    provider = OpenCodeProvider(OpenCodeConfig())
    process = _MockProcess(
        _jsonl_events({"type": "text", "part": {"type": "text", "text": "partial"}}),
        stderr_text="fatal failure",
        wait_returncode=17,
    )

    async def _create_subprocess(*_args, **_kwargs):
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", _create_subprocess)

    with pytest.raises((RuntimeError, ProviderError), match="opencode exited with code 17"):
        async for _ in provider.run("prompt", "", ".", 10):
            pass


def test_judge_retries_when_both_agents_fail():
    judge = JudgeRunner(provider=object())
    decision = judge.compare(
        task="fix bug",
        result_a=AgentResult(False, 1, "", "boom", 1.0),
        result_b=AgentResult(False, 1, "", "boom", 1.0),
        bugs_a=BugReport(total=0),
        bugs_b=BugReport(total=0),
        diff_a="",
        diff_b="",
    )

    assert decision.action == "retry"
    assert "Both agents failed" in decision.reason


@pytest.mark.asyncio
async def test_duel_timeout_marks_only_that_agent_failed():
    class _Provider:
        def __init__(self, delay: float = 0.0):
            self.delay = delay

        async def run(self, **_kwargs):
            if self.delay:
                import asyncio

                await asyncio.sleep(self.delay)
            yield {"text": "done"}

    registry = MagicMock()
    registry.get.side_effect = lambda name: _Provider(delay=0.05 if name == "slow" else 0.0)

    worktree = MagicMock()
    worktree.create.side_effect = lambda name: f"/tmp/{name}"
    worktree.get_diff.return_value = ""

    bug_detector = MagicMock()
    bug_detector.run.return_value = BugReport(total=0)

    judge = MagicMock()
    judge.compare.return_value = JudgeDecision(action="winner_b", confidence="high")

    runner = DuelRunner(registry, worktree, bug_detector, judge)
    result = await runner.run_round("task", "slow", "fast", timeout_s=0.01)

    assert result.result_a.success is False
    assert result.result_a.exit_code == 124
    assert result.result_b.success is True
    assert judge.compare.called


def test_parse_coach_output_uses_latest_assistant_chunk():
    messages = [
        MagicMock(role="assistant", content="IMPLEMENTATION_APPROVED"),
        MagicMock(role="tool", content="ignored"),
        MagicMock(role="assistant", content="IMPLEMENTATION_DECLINED\n1. Missing tests"),
    ]

    verdict = parse_coach_output(messages)

    assert isinstance(verdict, Feedback)
    assert not isinstance(verdict, Approved)
    assert verdict.text == "1. Missing tests"


def test_orchestrator_run_persists_run_metadata(monkeypatch, tmp_path):
    from src import orchestrator as orch_mod

    plan_file = tmp_path / "plan.md"
    plan_file.write_text("ship it")
    winner_ws = tmp_path / "winner"
    winner_ws.mkdir()
    (winner_ws / "out.txt").write_text("done")

    class _DummyWorktree:
        def __init__(self, *args, **kwargs):
            return None

        def cleanup_all(self):
            return None

    class _DummyRegistry:
        def __init__(self, *args, **kwargs):
            return None

        def get(self, _name):
            return object()

    class _DummyDuelRunner:
        def __init__(self, *args, **kwargs):
            return None

        def run_round_sync(self, **_kwargs):
            return RoundResult(
                result_a=AgentResult(True, 0, "a", "", 1.0),
                result_b=AgentResult(True, 0, "b", "", 1.0),
                bugs_a=BugReport(total=0),
                bugs_b=BugReport(total=1),
                diff_a="diff",
                diff_b="",
                decision=JudgeDecision(action="winner_a", confidence="high"),
                workspace_a=str(winner_ws),
                workspace_b=str(tmp_path / "loser"),
            )

    class _DummyRecorder:
        def __init__(self, *_args, **_kwargs):
            return None

        def record(self, **_kwargs):
            return "run-123"

        def load_all(self):
            return []

    monkeypatch.setattr(orch_mod, "ProviderRegistry", _DummyRegistry)
    monkeypatch.setattr(orch_mod, "WorktreeManager", _DummyWorktree)
    monkeypatch.setattr(orch_mod, "DuelRunner", _DummyDuelRunner)
    monkeypatch.setattr(orch_mod, "BugDetector", lambda *args, **kwargs: object())
    monkeypatch.setattr(orch_mod, "JudgeRunner", lambda provider: object())
    monkeypatch.setattr(orch_mod, "RunRecorder", _DummyRecorder)
    monkeypatch.setattr(orch_mod, "_LEARNING_AVAILABLE", False)

    config = Config(
        working_dir=str(tmp_path),
        plan_file=str(plan_file),
        max_rounds=1,
        ask_feedback=False,
    )
    orchestrator = orch_mod.Orchestrator(config, providers={})
    orchestrator._promote = lambda _winner_workspace: None

    result = orchestrator.run()
    state = orchestrator.session.load()

    assert result.run_id == "run-123"
    assert state["run_id"] == "run-123"
    assert state["final_winner"] == "winner_a"
    assert state["state"] == "completed"


def test_worktree_git_diff_handles_git_file(monkeypatch, tmp_path):
    from src.worktree import WorktreeManager

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / ".git").mkdir()
    ws = tmp_path / "agent-a"
    ws.mkdir()
    (ws / ".git").write_text("gitdir: /tmp/fake")

    manager = WorktreeManager(
        session_dir=str(tmp_path / "session"),
        source_dir=str(source_dir),
        mode="git",
        workspace_base=str(tmp_path),
    )
    manager._used.add("agent-a")

    monkeypatch.setattr(
        "src.worktree.subprocess.run",
        lambda *args, **kwargs: MagicMock(stdout="git diff output", returncode=0),
    )

    assert manager.get_diff("agent-a") == "git diff output"
