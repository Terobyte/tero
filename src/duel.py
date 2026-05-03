"""Parallel execution of two agents + Bug Detection + Judge."""

import asyncio
import time
from dataclasses import dataclass

from src.providers.registry import ProviderRegistry
from src.worktree import WorktreeManager
from src.bug_detector import BugDetector, BugReport
from src.judge import JudgeRunner, JudgeDecision
from src.providers.base import AgentResult
from src.constants import EXIT_AGENT_TIMEOUT, DEFAULT_DUEL_TIMEOUT_S


async def _collect_agent_result(
    agent, task: str, workspace: str, timeout_s: int
) -> AgentResult:
    """Drain an async-generator agent and return a consolidated AgentResult.

    asyncio.to_thread() only works for synchronous callables.  AgentProvider.run()
    is an async generator, so it must be awaited directly inside the event loop.
    """
    start = time.monotonic()
    stdout_parts: list[str] = []
    try:
        async for msg in agent.run(
            prompt=task,
            system_prompt="",
            working_dir=workspace,
            max_turns=max(1, timeout_s // 20),
        ):
            if isinstance(msg, dict):
                text = msg.get("text", "")
            else:
                text = getattr(msg, "text", "") or ""
            if text:
                stdout_parts.append(text)
        return AgentResult(
            success=True,
            exit_code=0,
            stdout="\n".join(stdout_parts),
            stderr="",
            duration_s=time.monotonic() - start,
        )
    except Exception as exc:
        return AgentResult(
            success=False,
            exit_code=1,
            stdout="\n".join(stdout_parts),
            stderr=str(exc),
            duration_s=time.monotonic() - start,
        )


async def _collect_agent_result_with_timeout(
    agent, task: str, workspace: str, timeout_s: int
) -> AgentResult:
    """Treat a single agent timeout as that agent failing, not the whole duel."""
    start = time.monotonic()
    try:
        return await asyncio.wait_for(
            _collect_agent_result(agent, task, workspace, timeout_s),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        return AgentResult(
            success=False,
            exit_code=EXIT_AGENT_TIMEOUT,
            stdout="",
            stderr=f"Agent exceeded timeout of {timeout_s}s",
            duration_s=time.monotonic() - start,
        )


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
        timeout_s: int = DEFAULT_DUEL_TIMEOUT_S,
    ) -> RoundResult:
        # 1. Create isolated workspaces
        ws_a = self.worktree.create(self.workspace_a_name)
        ws_b = self.worktree.create(self.workspace_b_name)

        agent_a = self.registry.get(agent_a_name)
        agent_b = self.registry.get(agent_b_name)

        # 2. Parallel agent launch — agents are async generators, not sync functions,
        #    so they must be awaited directly via _collect_agent_result
        #    (not asyncio.to_thread).
        result_a, result_b = await asyncio.gather(
            _collect_agent_result_with_timeout(agent_a, task, ws_a, timeout_s),
            _collect_agent_result_with_timeout(agent_b, task, ws_b, timeout_s),
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
            result_a=result_a,
            result_b=result_b,
            bugs_a=bugs_a,
            bugs_b=bugs_b,
            diff_a=diff_a,
            diff_b=diff_b,
        )

        return RoundResult(
            result_a=result_a,
            result_b=result_b,
            bugs_a=bugs_a,
            bugs_b=bugs_b,
            diff_a=diff_a,
            diff_b=diff_b,
            decision=decision,
            workspace_a=ws_a,
            workspace_b=ws_b,
        )

    def run_round_sync(
        self,
        task: str,
        agent_a_name: str,
        agent_b_name: str,
        autonomous: bool = False,
        timeout_s: int = DEFAULT_DUEL_TIMEOUT_S,
    ) -> RoundResult:
        """Synchronous wrapper for run_round.

        Works correctly both from sync and async contexts.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # Already inside an event loop — create a new loop in a thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.run_round(
                        task, agent_a_name, agent_b_name, autonomous, timeout_s
                    ),
                )
                return future.result()
        return asyncio.run(
            self.run_round(task, agent_a_name, agent_b_name, autonomous, timeout_s)
        )
