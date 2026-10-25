"""Phase summaries for the console and the run slug."""

from __future__ import annotations

import re

from src.loop.phase_ctx import PhaseOutcome
from src.loop.plan import Phase

_SLUG = re.compile(r"[^a-zA-Z0-9]+")


def rounds_word(rounds: int) -> str:
    if 11 <= (rounds % 100) <= 14:
        return "раундов"
    last = rounds % 10
    if last == 1:
        return "раунд"
    if 2 <= last <= 4:
        return "раунда"
    return "раундов"


def phase_summary_line(phase: Phase, outcome: PhaseOutcome, *, total: int) -> str:
    rounds = outcome.rounds
    text = f"фаза {phase.index + 1}/{total} «{phase.title}»: "
    if outcome.status == "ship":
        sha = (outcome.commit_sha or "")[:7]
        return f"{text}SHIP за {rounds} {rounds_word(rounds)} → {sha}"
    if outcome.status == "judge_pending":
        return (
            f"{text}JUDGE_PENDING ({outcome.reason}) "
            f"за {rounds} {rounds_word(rounds)}"
        )
    if outcome.status == "waiting_human":
        return (
            f"{text}ожидание решения ({outcome.reason}) "
            f"за {rounds} {rounds_word(rounds)}"
        )
    if outcome.status in ("answer", "skip"):
        return (
            f"{text}{outcome.status.upper()} ({outcome.reason}) "
            f"за {rounds} {rounds_word(rounds)}"
        )
    return f"{text}STOP ({outcome.reason}) за {rounds} {rounds_word(rounds)}"


def _print_phase_summary(phase: Phase, outcome: PhaseOutcome, *, total: int) -> None:
    print(phase_summary_line(phase, outcome, total=total), flush=True)


def slugify(name: str) -> str:
    text = _SLUG.sub("-", name).strip("-").lower()
    return text or "plan"
