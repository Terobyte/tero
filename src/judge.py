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
        # Use diffs to pick winner when both succeed with same bug count
        len_a = len(diff_a) if diff_a else 0
        len_b = len(diff_b) if diff_b else 0

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

        # Both succeed with same bugs — pick based on diff quality
        if len_a > len_b:
            return JudgeDecision(action="winner_a", confidence="medium", reason="More substantial changes")
        if len_b > len_a:
            return JudgeDecision(action="winner_b", confidence="medium", reason="More substantial changes")

        return JudgeDecision(action="retry", confidence="low", reason="No clear winner")
