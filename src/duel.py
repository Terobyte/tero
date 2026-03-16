"""Parallel execution of two agents + Bug Detection + Judge."""

import asyncio
from dataclasses import dataclass

from src.providers.registry import ProviderRegistry
from src.worktree import WorktreeManager
from src.bug_detector import BugDetector, BugReport
from src.judge import JudgeRunner, JudgeDecision
from src.providers.base import AgentResult


@dataclass
class RoundResult:
    result_a: AgentResult
    result_b: AgentResult
    bugs_a: BugReport
    bugs_b: BugReport
    diff_a: str
    diff_b: str
    decision: JudgeDecision
    workspace_a: str
    workspace_b: str


class DuelRunner:
    def __init__(
        self,
        registry: ProviderRegistry,
        worktree: WorktreeManager,
        bug_detector: BugDetector,
        judge: JudgeRunner,
        workspace_a_name: str = "g",
        workspace_b_name: str = "g1",
    ):
        self.registry = registry
        self.worktree = worktree
        self.bug_detector = bug_detector
        self.judge = judge
        self.workspace_a_name = workspace_a_name
        self.workspace_b_name = workspace_b_name

    async def run_round(
        self,
        task: str,
        agent_a_name: str,
        agent_b_name: str,
        autonomous: bool = False,
        timeout_s: int = 600,
    ) -> RoundResult:
        # 1. Create isolated workspaces
        ws_a = self.worktree.create(self.workspace_a_name)
        ws_b = self.worktree.create(self.workspace_b_name)

        agent_a = self.registry.get(agent_a_name)
        agent_b = self.registry.get(agent_b_name)

        # 2. Parallel agent launch
        result_a, result_b = await asyncio.gather(
            asyncio.to_thread(agent_a.run, task, ws_a, autonomous, timeout_s),
            asyncio.to_thread(agent_b.run, task, ws_b, autonomous, timeout_s),
        )

        # 3. Parallel Bug Detection
        bugs_a, bugs_b = await asyncio.gather(
            asyncio.to_thread(self.bug_detector.run, ws_a),
            asyncio.to_thread(self.bug_detector.run, ws_b),
        )

        # 4. Diff extraction
        diff_a = self.worktree.get_diff(self.workspace_a_name)
        diff_b = self.worktree.get_diff(self.workspace_b_name)

        # 5. Judge
        decision = self.judge.compare(
            task=task,
            result_a=result_a, result_b=result_b,
            bugs_a=bugs_a, bugs_b=bugs_b,
            diff_a=diff_a, diff_b=diff_b,
        )

        return RoundResult(
            result_a=result_a, result_b=result_b,
            bugs_a=bugs_a, bugs_b=bugs_b,
            diff_a=diff_a, diff_b=diff_b,
            decision=decision,
            workspace_a=ws_a, workspace_b=ws_b,
        )

    def run_round_sync(
        self,
        task: str,
        agent_a_name: str,
        agent_b_name: str,
        autonomous: bool = False,
        timeout_s: int = 600,
    ) -> RoundResult:
        """Synchronous wrapper for run_round."""
        return asyncio.run(self.run_round(
            task, agent_a_name, agent_b_name, autonomous, timeout_s
        ))
