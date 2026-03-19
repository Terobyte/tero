"""Minimal judge primitives used by duel/orchestrator tests."""

from dataclasses import dataclass


@dataclass
class JudgeDecision:
    """Decision returned after comparing two agent results."""

    action: str
    confidence: str
    reason: str = ""


class JudgeRunner:
    """Small compatibility judge wrapper."""

    def __init__(self, provider):
        self.provider = provider

    def compare(
        self,
        task: str,
        result_a,
        result_b,
        bugs_a,
        bugs_b,
        diff_a: str,
        diff_b: str,
    ) -> JudgeDecision:
        _ = (task, diff_a, diff_b, self.provider)

        if getattr(result_a, "success", False) and not getattr(result_b, "success", False):
            return JudgeDecision(action="winner_a", confidence="high", reason="Agent B failed")
        if getattr(result_b, "success", False) and not getattr(result_a, "success", False):
            return JudgeDecision(action="winner_b", confidence="high", reason="Agent A failed")

        bugs_total_a = getattr(bugs_a, "total", 0)
        bugs_total_b = getattr(bugs_b, "total", 0)
        if bugs_total_a < bugs_total_b:
            return JudgeDecision(action="winner_a", confidence="medium", reason="Fewer bugs")
        if bugs_total_b < bugs_total_a:
            return JudgeDecision(action="winner_b", confidence="medium", reason="Fewer bugs")

        return JudgeDecision(action="retry", confidence="low", reason="No clear winner")
