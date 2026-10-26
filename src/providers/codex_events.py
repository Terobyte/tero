"""Codex stream-event adaption, extracted from codex.py (ceiling relief)."""

from __future__ import annotations

from typing import Any

from .message_adapter import AdaptedMessage, TextBlock, ToolUseBlock, ToolResultBlock
from src.constants import MAX_TOOL_OUTPUT_CHARS
from src.errors import strip_mcp_noise


def codex_error_text(event: dict) -> str:
    """Pull the human-readable message out of error/turn.failed events."""
    etype = event.get("type", "")
    if etype == "error":
        return strip_mcp_noise(str(event.get("message", "Unknown error"))) or "Unknown error"
    err = event.get("error")
    if isinstance(err, dict):
        return strip_mcp_noise(str(err.get("message", "Turn failed"))) or "Turn failed"
    if isinstance(err, str):
        return strip_mcp_noise(err) or "Turn failed"
    return "Turn failed"


def stream_error_text(event: Any) -> str | None:
    """Return the error message if this stream event carries one, else None."""
    if not isinstance(event, dict):
        return None
    etype = event.get("type", "")
    if etype in ("error", "turn.failed"):
        return codex_error_text(event)
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") == "error":
        return str(item.get("message", "Unknown item error"))
    return None


def adapt_codex_event(event: dict, started: set[str]) -> AdaptedMessage | None:
    """Convert Codex JSONL event to AdaptedMessage."""
    if not isinstance(event, dict):
        return None
    etype = event.get("type", "")

    if etype == "thread.started":
        return None  # metadata only, ignore

    if etype == "turn.started":
        return AdaptedMessage(
            role="assistant",
            content=[],
            type="assistant",
        )

    if etype in ("item.completed", "item.started"):
        return None  # callers route item events through adapt_item

    # Turn completion carries token usage only (no text payload).
    if etype == "turn.completed":
        return AdaptedMessage(
            role="assistant",
            content=[],
            stop_reason="end_turn",
            type="result",
        )

    # Errors
    if etype == "error":
        msg = codex_error_text(event)
        return AdaptedMessage(
            role="assistant",
            content=[TextBlock(text=f"⚠ Codex error: {msg}")],
            type="text",
        )

    if etype == "turn.failed":
        msg = codex_error_text(event)
        return AdaptedMessage(
            role="assistant",
            content=[TextBlock(text=f"⚠ Codex turn failed: {msg}")],
            type="text",
        )

    return None


def adapt_item(item: dict, event_type: str, started: set[str]) -> AdaptedMessage | None:
    """Convert a single Codex item to AdaptedMessage."""
    if not isinstance(item, dict):
        return None
    item_type = item.get("type", "")

    if item_type == "agent_message":
        return _adapt_agent_message_item(item)

    if item_type == "command_execution":
        return _adapt_command_item(item, event_type)

    # Codex 0.147 real item types: file_change / mcp_tool_call / web_search.
    # Emit a ToolUseBlock on item.started; file_change may arrive with
    # only item.completed (no started seen), so emit there too in that
    # case, but never emit twice for the same item id.
    if item_type in ("file_change", "mcp_tool_call", "web_search"):
        return _adapt_tool_item(item, item_type, event_type, started)

    if item_type in ("file_edit", "file_write", "file_read"):
        return _adapt_file_op_item(item, item_type, event_type)

    # Error item
    if item_type == "error":
        msg = strip_mcp_noise(str(item.get("message", "Unknown item error"))) or "Unknown item error"
        return AdaptedMessage(
            role="assistant",
            content=[TextBlock(text=f"⚠ {msg}")],
            type="text",
        )

    return None


def _adapt_agent_message_item(item: dict) -> AdaptedMessage | None:
    text = item.get("text", "")
    if not text:
        return None
    return AdaptedMessage(
        role="assistant",
        content=[TextBlock(text=text)],
        type="text",
    )


def _adapt_command_item(item: dict, event_type: str) -> AdaptedMessage | None:
    command = item.get("command", "")
    exit_code = item.get("exit_code")
    output = item.get("aggregated_output", "")

    if event_type == "item.started":
        return AdaptedMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    name="shell",
                    input={"command": command},
                    id=item.get("id", ""),
                )
            ],
            stop_reason="tool_use",
            type="tool_use",
        )

    if event_type == "item.completed":
        is_error = exit_code is not None and exit_code != 0
        text = output or ""
        if exit_code is not None:
            text = f"[exit code: {exit_code}]\n{text}"
        if len(text) > MAX_TOOL_OUTPUT_CHARS:
            text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n... (truncated)"
        return AdaptedMessage(
            role="tool",
            content=[
                ToolResultBlock(
                    tool_use_id=item.get("id", ""),
                    content=text,
                    is_error=is_error,
                )
            ],
            type="tool_result",
        )

    return None


def _adapt_tool_item(
    item: dict, item_type: str, event_type: str, started: set[str]
) -> AdaptedMessage | None:
    item_id = item.get("id", "")
    if event_type == "item.started":
        started.add(item_id)
    elif item_id in started:
        return None
    if item_type == "file_change":
        paths = [c.get("path", "") for c in item.get("changes") or [] if c.get("path")]
        name = "edit"
        tool_input: dict = {"path": ", ".join(paths)}
    elif item_type == "mcp_tool_call":
        server = item.get("server", "")
        tool = item.get("tool", "")
        name = f"{server}.{tool}"
        args = item.get("arguments")
        if isinstance(args, dict):
            tool_input = dict(args)
        elif args is None:
            tool_input = {}
        else:
            tool_input = {"arguments": str(args)}
    else:  # web_search
        name = "web_search"
        tool_input = {"query": item.get("query", "")}

    return AdaptedMessage(
        role="assistant",
        content=[ToolUseBlock(name=name, input=tool_input, id=item_id)],
        stop_reason="tool_use",
        type="tool_use",
    )


def _adapt_file_op_item(item: dict, item_type: str, event_type: str) -> AdaptedMessage | None:
    path = item.get("path", item.get("file", ""))
    content = item.get("content", item.get("text", ""))

    if event_type == "item.started":
        return AdaptedMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    name=item_type,
                    input={"path": path},
                    id=item.get("id", ""),
                )
            ],
            stop_reason="tool_use",
            type="tool_use",
        )

    if event_type == "item.completed":
        return AdaptedMessage(
            role="assistant",
            content=[
                ToolResultBlock(
                    tool_use_id=item.get("id", ""),
                    content=content or f"[{item_type}: {path}]",
                    is_error=False,
                )
            ],
            type="tool_result",
        )

    return None
