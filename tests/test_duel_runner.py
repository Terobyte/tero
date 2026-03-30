"""Tests for Duel Runner - parallel agent execution."""

import asyncio
import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from src.duel import DuelRunner, RoundResult
from src.providers.base import AgentResult
from src.bug_detector import BugReport
from src.judge import JudgeDecision


class MockProvider:
    """Mock provider for testing."""
    def __init__(self, name: str, result: AgentResult = None, delay: float = 0.0):
        self._name = name
        self._result = result or AgentResult(
            success=True, exit_code=0, stdout="OK", stderr="", duration_s=1.0
        )
        self._delay = delay
        self.call_count = 0
        self.last_prompt = None
        self.last_working_dir = None

    def name(self) -> str:
        return self._name

    def health_check(self) -> bool:
        return True

    def run(self, prompt: str, working_dir: str, autonomous: bool = False, timeout_s: int = 600, **kwargs):
        import time
        _ = timeout_s  # unused
        self.call_count += 1
        self.last_prompt = prompt
        self.last_working_dir = working_dir
        if self._delay:
            time.sleep(self._delay)
        return self._result


class TestDuelRunner:
    """Test suite for DuelRunner."""

    def test_duel_runner_initialization(self):
        """Test DuelRunner can be initialized."""
        registry = Mock()
        worktree = Mock()
        bug_detector = Mock()
        judge = Mock()

        runner = DuelRunner(registry, worktree, bug_detector, judge)

        assert runner.registry is registry
        assert runner.worktree is worktree
        assert runner.bug_detector is bug_detector
        assert runner.judge is judge

    @pytest.mark.asyncio
    async def test_run_round_creates_workspaces(self):
        """Test that run_round creates workspaces for both agents."""
        registry = Mock()
        registry.get = Mock(side_effect=lambda name: MockProvider(name))

        worktree = Mock()
        worktree.create = Mock(side_effect=lambda name: f"/tmp/ws_{name}")
        worktree.get_diff = Mock(return_value="diff content")

        bug_detector = Mock()
        bug_detector.run = Mock(return_value=BugReport())

        judge = Mock()
        judge.compare = Mock(return_value=JudgeDecision(
            action="winner_a", confidence="high"
        ))

        # Use custom workspace names to test backwards compatibility
        runner = DuelRunner(registry, worktree, bug_detector, judge,
                           workspace_a_name="agent_a", workspace_b_name="agent_b")

        result = await runner.run_round("test task", "agent_a", "agent_b")

        assert worktree.create.call_count == 2
        worktree.create.assert_any_call("agent_a")
        worktree.create.assert_any_call("agent_b")

    @pytest.mark.asyncio
    async def test_run_round_runs_both_agents(self):
        """Test that both agents are run during a round."""
        provider_a = MockProvider("agent_a")
        provider_b = MockProvider("agent_b")

        registry = Mock()
        registry.get = Mock(side_effect=lambda name:
            provider_a if name == "agent_a" else provider_b
        )

        worktree = Mock()
        worktree.create = Mock(side_effect=lambda name: f"/tmp/ws_{name}")
        worktree.get_diff = Mock(return_value="diff content")

        bug_detector = Mock()
        bug_detector.run = Mock(return_value=BugReport())

        judge = Mock()
        judge.compare = Mock(return_value=JudgeDecision(
            action="winner_a", confidence="high"
        ))

        runner = DuelRunner(registry, worktree, bug_detector, judge,
                           workspace_a_name="agent_a", workspace_b_name="agent_b")

        result = await runner.run_round("test task", "agent_a", "agent_b")

        assert provider_a.call_count == 1
        assert provider_b.call_count == 1
        assert provider_a.last_prompt == "test task"
        assert provider_b.last_prompt == "test task"

    @pytest.mark.asyncio
    async def test_run_round_runs_bug_detection(self):
        """Test that bug detection runs on both workspaces."""
        registry = Mock()
        registry.get = Mock(side_effect=lambda name: MockProvider(name))

        worktree = Mock()
        worktree.create = Mock(side_effect=lambda name: f"/tmp/ws_{name}")
        worktree.get_diff = Mock(return_value="diff content")

        bug_detector = Mock()
        bug_detector.run = Mock(return_value=BugReport(
            test_bugs=1, total=1
        ))

        judge = Mock()
        judge.compare = Mock(return_value=JudgeDecision(
            action="winner_a", confidence="high"
        ))

        runner = DuelRunner(registry, worktree, bug_detector, judge,
                           workspace_a_name="agent_a", workspace_b_name="agent_b")

        result = await runner.run_round("test task", "agent_a", "agent_b")

        assert bug_detector.run.call_count == 2

    @pytest.mark.asyncio
    async def test_run_round_calls_judge(self):
        """Test that judge is called with correct arguments."""
        registry = Mock()
        registry.get = Mock(side_effect=lambda name: MockProvider(name))

        worktree = Mock()
        worktree.create = Mock(side_effect=lambda name: f"/tmp/ws_{name}")
        worktree.get_diff = Mock(return_value="diff content")

        bug_report_a = BugReport(test_bugs=2, total=2)
        bug_report_b = BugReport(test_bugs=0, total=0)

        bug_detector = Mock()
        bug_detector.run = Mock(side_effect=lambda ws:
            bug_report_a if "agent_a" in ws else bug_report_b
        )

        judge = Mock()
        judge.compare = Mock(return_value=JudgeDecision(
            action="winner_b", confidence="high"
        ))

        runner = DuelRunner(registry, worktree, bug_detector, judge,
                           workspace_a_name="agent_a", workspace_b_name="agent_b")

        result = await runner.run_round("test task", "agent_a", "agent_b")

        assert judge.compare.called
        call_kwargs = judge.compare.call_args[1]
        assert call_kwargs["task"] == "test task"
        assert call_kwargs["bugs_a"] == bug_report_a
        assert call_kwargs["bugs_b"] == bug_report_b

    @pytest.mark.asyncio
    async def test_run_round_returns_round_result(self):
        """Test that run_round returns a proper RoundResult."""
        registry = Mock()
        registry.get = Mock(side_effect=lambda name: MockProvider(name))

        worktree = Mock()
        worktree.create = Mock(side_effect=lambda name: f"/tmp/ws_{name}")
        worktree.get_diff = Mock(return_value="diff content")

        bug_detector = Mock()
        bug_detector.run = Mock(return_value=BugReport())

        judge = Mock()
        judge.compare = Mock(return_value=JudgeDecision(
            action="winner_a", confidence="high", reason="Agent A is better"
        ))

        runner = DuelRunner(registry, worktree, bug_detector, judge,
                           workspace_a_name="agent_a", workspace_b_name="agent_b")

        result = await runner.run_round("test task", "agent_a", "agent_b")

        assert isinstance(result, RoundResult)
        assert isinstance(result.result_a, AgentResult)
        assert isinstance(result.result_b, AgentResult)
        assert isinstance(result.bugs_a, BugReport)
        assert isinstance(result.bugs_b, BugReport)
        assert isinstance(result.decision, JudgeDecision)
        assert result.decision.action == "winner_a"

    @pytest.mark.asyncio
    async def test_run_round_agents_run_in_parallel(self):
        """Test that agents run concurrently, not sequentially."""
        import time

        provider_a = MockProvider("agent_a", delay=0.1)
        provider_b = MockProvider("agent_b", delay=0.1)

        registry = Mock()
        registry.get = Mock(side_effect=lambda name:
            provider_a if name == "agent_a" else provider_b
        )

        worktree = Mock()
        worktree.create = Mock(side_effect=lambda name: f"/tmp/ws_{name}")
        worktree.get_diff = Mock(return_value="diff content")

        bug_detector = Mock()
        bug_detector.run = Mock(return_value=BugReport())

        judge = Mock()
        judge.compare = Mock(return_value=JudgeDecision(
            action="winner_a", confidence="high"
        ))

        runner = DuelRunner(registry, worktree, bug_detector, judge,
                           workspace_a_name="agent_a", workspace_b_name="agent_b")

        start = time.time()
        result = await runner.run_round("test task", "agent_a", "agent_b")
        elapsed = time.time() - start

        # If running in parallel, should take ~0.1s, not ~0.2s
        assert elapsed < 0.25, f"Agents should run in parallel, took {elapsed}s"

    @pytest.mark.asyncio
    async def test_run_round_agent_a_fails(self):
        """Test handling when agent A fails."""
        provider_a = MockProvider("agent_a", AgentResult(
            success=False, exit_code=1, stdout="", stderr="Error", duration_s=1.0
        ))
        provider_b = MockProvider("agent_b", AgentResult(
            success=True, exit_code=0, stdout="OK", stderr="", duration_s=1.0
        ))

        registry = Mock()
        registry.get = Mock(side_effect=lambda name:
            provider_a if name == "agent_a" else provider_b
        )

        worktree = Mock()
        worktree.create = Mock(side_effect=lambda name: f"/tmp/ws_{name}")
        worktree.get_diff = Mock(return_value="diff content")

        bug_detector = Mock()
        bug_detector.run = Mock(return_value=BugReport())

        judge = Mock()
        judge.compare = Mock(return_value=JudgeDecision(
            action="winner_b", confidence="high", reason="Agent A failed"
        ))

        runner = DuelRunner(registry, worktree, bug_detector, judge,
                           workspace_a_name="agent_a", workspace_b_name="agent_b")

        result = await runner.run_round("test task", "agent_a", "agent_b")

        assert result.decision.action == "winner_b"
        assert not result.result_a.success
        assert result.result_b.success


class TestRoundResult:
    """Test suite for RoundResult dataclass."""

    def test_duel_runner_uses_configured_workspace_names(self):
        """DuelRunner.run_round uses workspace_a_name and workspace_b_name."""
        mock_worktree = MagicMock()
        mock_worktree.create.side_effect = lambda name: f"/tmp/{name}"
        mock_worktree.get_diff.return_value = ""

        mock_registry = MagicMock()
        mock_agent = MagicMock()
        mock_agent.run.return_value = AgentResult(success=True, exit_code=0, stdout="done", stderr="", duration_s=1.0)
        mock_registry.get.return_value = mock_agent

        mock_bug_detector = MagicMock()
        mock_bug_detector.run.return_value = BugReport(total=0, test_bugs=0, lint_bugs=0, type_bugs=0, compile_bugs=0)

        mock_judge = MagicMock()
        mock_judge.compare.return_value = JudgeDecision(action="winner_a", confidence="high", reason="a is better")

        runner = DuelRunner(
            registry=mock_registry,
            worktree=mock_worktree,
            bug_detector=mock_bug_detector,
            judge=mock_judge,
            workspace_a_name="g",
            workspace_b_name="g1",
        )

        asyncio.run(runner.run_round("do the task", "black", "claude"))

        # Check that create was called with "g" and "g1", not "agent_a"/"agent_b"
        calls = [c.args[0] for c in mock_worktree.create.call_args_list]
        assert "g" in calls
        assert "g1" in calls
        assert "agent_a" not in calls
        assert "agent_b" not in calls

    def test_round_result_creation(self):
        """Test RoundResult can be created."""
        result_a = AgentResult(success=True, exit_code=0, stdout="", stderr="", duration_s=1.0)
        result_b = AgentResult(success=True, exit_code=0, stdout="", stderr="", duration_s=2.0)
        bugs_a = BugReport()
        bugs_b = BugReport(test_bugs=1, total=1)
        decision = JudgeDecision(action="winner_a", confidence="high")

        round_result = RoundResult(
            result_a=result_a,
            result_b=result_b,
            bugs_a=bugs_a,
            bugs_b=bugs_b,
            diff_a="diff_a",
            diff_b="diff_b",
            decision=decision,
            workspace_a="/tmp/ws_a",
            workspace_b="/tmp/ws_b",
        )

        assert round_result.result_a == result_a
        assert round_result.result_b == result_b
        assert round_result.bugs_a == bugs_a
        assert round_result.bugs_b == bugs_b
        assert round_result.diff_a == "diff_a"
        assert round_result.diff_b == "diff_b"
        assert round_result.decision == decision
        assert round_result.workspace_a == "/tmp/ws_a"
        assert round_result.workspace_b == "/tmp/ws_b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
