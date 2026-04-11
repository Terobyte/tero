"""Standalone LLM text-collection helpers for the debugger.

Extracted from Debugger so that debugger_contracts, debugger_edges,
debugger_intra (etc.) can call them without needing a Debugger instance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.debugger import _Pulse

# Retry schedule: 60s → 120s → 240s (7 min total before giving up)
_RETRY_BACKOFF_S = [60, 120, 240]
_STALE_THRESHOLD_S = 15  # switch dot to yellow after no data for this long


@dataclass
class CollectedTextResult:
    """Provider output plus whether the run completed successfully."""

    text: str
    completed: bool


def extract_text(message, parts: list[str]) -> None:
    """Extract text from any provider message format into *parts*.

    Handles: SDK objects (.content), AdaptedMessage, raw dicts from
    claude_native CLI events, and bare strings.
    """
    if isinstance(message, str):
        parts.append(message)
        return

    # Objects with .content (SDK messages, AdaptedMessage)
    if hasattr(message, "content") and not isinstance(message, dict):
        content = message.content
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
        return

    # Dicts from claude_native provider (raw Claude CLI JSON events)
    if isinstance(message, dict):
        # {"type": "text", "text": "..."} — non-JSON line fallback
        if message.get("type") == "text" and "text" in message:
            parts.append(message["text"])
        # {"result": "full text"} — result event
        if "result" in message and isinstance(message.get("result"), str):
            parts.append(message["result"])
        # {"message": {"content": [{"type": "text", "text": "..."}]}}
        msg_data = message.get("message")
        if isinstance(msg_data, dict):
            content = msg_data.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))


async def collect_text(
    provider,
    prompt: str,
    system_prompt: str,
    working_dir: str,
    max_turns: int,
    model: str = "",
    pulse: _Pulse | None = None,
) -> CollectedTextResult:
    """Collect text from *provider* with retry and live pulse status."""
    for attempt in range(len(_RETRY_BACKOFF_S) + 1):
        parts: list[str] = []
        try:
            async for message in provider.run(
                prompt=prompt,
                system_prompt=system_prompt,
                working_dir=working_dir,
                max_turns=max_turns,
                model=model,
            ):
                if pulse:
                    pulse.heartbeat()
                extract_text(message, parts)
            return CollectedTextResult(text="\n".join(parts), completed=True)
        except Exception as exc:
            print(f"\n   ⚠ LLM call failed (attempt {attempt + 1}/{len(_RETRY_BACKOFF_S) + 1}): {exc}")
            if attempt >= len(_RETRY_BACKOFF_S):
                print(f"   ✗ Giving up after {len(_RETRY_BACKOFF_S) + 1} attempts: {exc}")
                return CollectedTextResult(text="\n".join(parts), completed=False)

            # Retry with countdown visible on the pulse
            wait = _RETRY_BACKOFF_S[attempt]
            if pulse:
                for remaining in range(wait, 0, -1):
                    pulse.set_retrying(attempt + 1, remaining)
                    await asyncio.sleep(1)
            else:
                await asyncio.sleep(wait)
