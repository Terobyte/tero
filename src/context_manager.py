"""Context management utilities: compaction, continuation, review logging."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.providers.message_adapter import AdaptedMessage


def _build_compact_summary(messages: list) -> str:
    """Extract assistant text blocks only — drops tool results (heaviest parts)."""
    parts = []
    for msg in messages:
        if not (hasattr(msg, "role") and msg.role == "assistant"):
            continue
        content = getattr(msg, "content", None) or []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text.strip())
    return "\n".join(p for p in parts if p)


def _build_continuation_prompt(summary: str, role: str) -> str:
    """Build continuation prompt for a given role."""
    if role == "player":
        return (
            f"You are continuing previous work. Here is what was already done:\n\n"
            f"{summary}\n\n"
            "Now output the required completion report:\n"
            "  PHASE_COMPLETE: <phase name>\n"
            "  What changed:\n  - ...\n"
            "  Evidence:\n  - ...\n"
            "  Verification:\n  - ..."
        )
    # coach / reviewer
    return (
        f"You are continuing a review. Here is what was assessed so far:\n\n"
        f"{summary}\n\n"
        "Now output your final verdict: either IMPLEMENTATION_APPROVED "
        "or a numbered list of concrete issues to fix."
    )


async def _compact_codex_context(provider, messages: list, config) -> str:
    """Generate a compact summary of previous Codex turn for use in next prompt."""
    summary = _build_compact_summary(messages)
    if not summary:
        return ""
    compact_prompt = (
        "Summarize the following work log compactly. Preserve: completed steps, "
        "files changed, current state, pending work. Be brief.\n\n"
        f"{summary}"
    )
    result_parts = []
    async for chunk in provider.run(
        prompt=compact_prompt,
        system_prompt="You are a concise summarizer.",
        working_dir=".",
        max_turns=3,
        model=config.coach_model or "",
    ):
        text = getattr(chunk, "text", None) or ""
        if text:
            result_parts.append(text)
    return "\n".join(result_parts)


def _log_review_result(
    step_num: int,
    iteration: int,
    verdict,
    provider_display: str,
    working_dir: str,
) -> None:
    """Append a code review iteration result to .g3/bugs/step-N-DATE.md."""
    from src.feedback import ReviewPassed, ReviewIssues  # local import to avoid cycles

    bugs_dir = Path(working_dir) / ".g3" / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.date.today().isoformat()
    path = bugs_dir / f"step-{step_num}-{date_str}.md"

    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    if not path.exists():
        path.write_text(f"# Code Review — Step {step_num} — {ts}\n\n"
                        f"- Provider: {provider_display}\n\n")

    with path.open("a") as f:
        if isinstance(verdict, ReviewPassed):
            f.write(f"## Iteration {iteration} — PASSED\nNo critical issues found.\n\n")
        else:
            issues = getattr(verdict, "text", str(verdict))
            f.write(f"## Iteration {iteration} — Issues Found\n{issues}\n\n")
