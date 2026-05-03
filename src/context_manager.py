"""Context management utilities: compaction, continuation, review logging."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.providers.message_adapter import AdaptedMessage


def _build_compact_summary(messages: list) -> str:
    """Extract assistant text + compact tool interaction records.

    Preserves which files were read/edited/written and what commands were run
    so that continuation subprocesses don't redo the same exploration.
    """
    from src.providers.message_adapter import ToolUseBlock, ToolResultBlock

    parts: list[str] = []
    tool_names: dict[str, str] = {}  # tool_use_id -> tool name

    for msg in messages:
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", None)

        if role == "assistant":
            if isinstance(content, str):
                text = content.strip()
                if text:
                    parts.append(text)
                continue
            for block in content or []:
                if isinstance(block, str):
                    text = block.strip()
                    if text:
                        parts.append(text)
                elif isinstance(block, ToolUseBlock):
                    tool_names[block.id] = block.name
                    inp = block.input or {}
                    if block.name in ("Read", "read_file", "file_read"):
                        path = inp.get("file_path") or inp.get("path", "")
                        parts.append(f"[Tool: {block.name} -> {path}]")
                    elif block.name in ("Edit", "file_edit", "edit_file",
                                        "Write", "file_write", "write_file"):
                        path = inp.get("file_path") or inp.get("path", "")
                        parts.append(f"[Tool: {block.name} -> {path}]")
                    elif block.name in ("Bash", "bash", "execute_command"):
                        cmd = inp.get("command", "")
                        if len(cmd) > 200:
                            cmd = cmd[:200] + "..."
                        parts.append(f"[Tool: {block.name} -> {cmd}]")
                    else:
                        parts.append(f"[Tool: {block.name}]")
                elif hasattr(block, "text"):
                    text = getattr(block, "text", "")
                    if text:
                        parts.append(text.strip())

        elif role == "tool":
            for block in content or []:
                if isinstance(block, ToolResultBlock):
                    tool_name = tool_names.get(block.tool_use_id, "unknown")
                    result_text = block.content or ""
                    if block.is_error:
                        parts.append(
                            f"[Tool result ({tool_name}): ERROR: {result_text[:300]}]"
                        )
                    elif tool_name in ("Bash", "bash", "execute_command"):
                        truncated = result_text[:500]
                        if len(result_text) > 500:
                            truncated += "... (truncated)"
                        parts.append(f"[Tool result ({tool_name}): {truncated}]")

    return "\n".join(p for p in parts if p)


def _build_continuation_prompt(
    summary: str,
    role: str,
    require_phase_complete: bool = False,
) -> str:
    """Build continuation prompt for a given role."""
    if role == "player":
        completion_lines = [
            "  What changed:\n  - ...",
            "  Evidence:\n  - ...",
            "  Verification:\n  - ...",
        ]
        if require_phase_complete:
            completion_lines.insert(0, "  PHASE_COMPLETE: <phase name>")
        return (
            f"You are continuing previous work. Here is what was already done:\n\n"
            f"{summary}\n\n"
            "You still have access to filesystem inspection, command execution, and edit tools.\n"
            "Do not claim tools are unavailable in this session unless an actual tool call failed.\n"
            "Continue using tools if more verification or edits are needed, then send the required completion report.\n"
            "Now output the required completion report:\n"
            f"{chr(10).join(completion_lines)}"
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
    import inspect as _inspect
    summary = _build_compact_summary(messages)
    if not summary:
        return ""
    compact_prompt = (
        "Summarize the following work log compactly. Preserve: completed steps, "
        "files changed, current state, pending work. Be brief.\n\n"
        f"{summary}"
    )
    model = getattr(config, "player_model", "") or getattr(config, "coach_model", "") or ""
    run_kwargs: dict = {
        "prompt": compact_prompt,
        "system_prompt": "You are a concise summarizer.",
        "working_dir": config.working_dir,
        "max_turns": 3,
        "model": model,
    }
    params = _inspect.signature(provider.run).parameters
    accepts_kwargs = any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values())
    context_limit = getattr(config, "context_limit", 0)
    if context_limit and ("context_limit" in params or accepts_kwargs):
        run_kwargs["context_limit"] = context_limit
    result_parts = []
    try:
        async for chunk in provider.run(**run_kwargs):
            text = getattr(chunk, "text", None) or ""
            if text:
                result_parts.append(text)
    except Exception as exc:
        import sys
        print(f"  [compact] Warning: context compaction failed: {exc}", file=sys.stderr)
        return ""
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

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    if not path.exists():
        path.write_text(f"# Code Review — Step {step_num} — {ts}\n\n"
                        f"- Provider: {provider_display}\n\n")

    with path.open("a") as f:
        if isinstance(verdict, ReviewPassed):
            f.write(f"## Iteration {iteration} — PASSED\nNo critical issues found.\n\n")
        else:
            issues = getattr(verdict, "text", str(verdict))
            f.write(f"## Iteration {iteration} — Issues Found\n{issues}\n\n")
