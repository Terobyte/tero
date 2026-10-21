"""Message adapter: normalize CLI provider events to a common message format."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    """Text content block."""

    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    """Tool use block."""

    name: str
    input: dict = field(default_factory=dict)
    type: str = "tool_use"
    id: str = ""  # Optional tool use ID


@dataclass
class ToolResultBlock:
    """Tool result block."""

    tool_use_id: str
    content: str
    is_error: bool = False
    type: str = "tool_result"


@dataclass
class AdaptedMessage:
    """Common message format for CLI provider events."""

    role: str  # "assistant" or "tool" or "user"
    content: list  # [TextBlock, ToolUseBlock, ...]
    stop_reason: str = ""  # "end_turn", "max_tokens", "tool_use", etc.
    model: str = ""  # Model that generated this message
    type: str = ""  # Event type: "text", "assistant", "tool_use", "tool_result", "result"

    def __getitem__(self, key: str) -> Any:
        """Dict-like access to message fields."""
        if key == "text":
            return self.get_text_content()
        return getattr(self, key)

    @property
    def text(self) -> str:
        """Convenience property for text content."""
        return self.get_text_content()

    def get_text_content(self) -> str:
        """Extract all text from content blocks."""
        texts = []
        for block in self.content:
            if isinstance(block, TextBlock):
                texts.append(block.text)
            elif hasattr(block, "text"):
                texts.append(block.text)
        return "\n".join(texts)


def adapt_claude_event(event: dict) -> AdaptedMessage | None:
    """Convert JSON event from claude CLI to the common message format.

    Claude CLI with --output-format stream-json emits JSON objects.
    This function adapts them to the common message format.

    Args:
        event: JSON event dict from claude CLI

    Returns:
        AdaptedMessage if event is relevant, None if it should be ignored.

    Event types handled:
        - "assistant" / "text": text response
        - "tool_use": tool invocation
        - "tool_result": tool result
        - "result": final result
    """
    etype = event.get("type", "")

    # Text response from assistant
    # Claude CLI stream-json nests content: {"type":"assistant","message":{"content":[...]}}
    if etype in ("assistant", "text"):
        text = event.get("text", "")
        message = event.get("message", {})

        if not text and isinstance(message, dict):
            blocks = []
            for block in message.get("content", []):
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        blocks.append(TextBlock(text=block.get("text", "")))
                    elif btype == "tool_use":
                        blocks.append(ToolUseBlock(
                            name=block.get("name", "unknown"),
                            input=block.get("input", {}),
                            id=block.get("id", ""),
                        ))
            if blocks:
                return AdaptedMessage(
                    role="assistant",
                    content=blocks,
                    stop_reason=message.get("stop_reason", ""),
                    model=message.get("model", event.get("model", "")),
                    type=etype,
                )

        if text:
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=text)],
                stop_reason=event.get("stop_reason", ""),
                model=event.get("model", ""),
                type=etype,
            )

        # Role-only delta (message start indicator)
        role = event.get("role", "")
        if role:
            return AdaptedMessage(
                role=role,
                content=[],
                stop_reason="",
                model=event.get("model", ""),
                type=etype,
            )
        return None

    # Tool use request (flat format)
    if etype == "tool_use":
        return AdaptedMessage(
            role="assistant",
            content=[ToolUseBlock(
                name=event.get("name", "unknown"),
                input=event.get("input", {}),
                id=event.get("id", ""),
            )],
            stop_reason="tool_use",
            type=etype,
        )

    # Tool result
    if etype == "tool_result":
        return AdaptedMessage(
            role="tool",
            content=[ToolResultBlock(
                tool_use_id=event.get("tool_use_id", ""),
                content=event.get("content", ""),
                is_error=event.get("is_error", False),
            )],
            type=etype,
        )

    # Final result (end of conversation)
    if etype == "result":
        text = event.get("result", event.get("text", ""))
        if text:
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=text)],
                stop_reason=event.get("stop_reason", "end_turn"),
                type=etype,
            )
        # Result without content signals end of stream
        return AdaptedMessage(
            role="assistant",
            content=[],
            stop_reason=event.get("stop_reason", "end_turn"),
            type=etype,
        )

    # Ignored event types: init, system, permission_request, etc.
    return None


def adapt_sdk_message(msg: Any) -> AdaptedMessage | None:
    """Convert a message object to AdaptedMessage if needed.

    Pass-through for already-adapted objects. Also handles message
    objects with .content/.role attributes and raw CLI event dicts.

    Args:
        msg: SDK message object or dict

    Returns:
        AdaptedMessage or None if input is None
    """
    if msg is None:
        return None

    # Already an AdaptedMessage
    if isinstance(msg, AdaptedMessage):
        return msg

    type_name = type(msg).__name__

    # claude_agent_sdk.AssistantMessage — no .role, has .content list
    if type_name == "AssistantMessage":
        content = []
        if hasattr(msg, "content"):
            raw_content = msg.content
            if isinstance(raw_content, str):
                content = [TextBlock(text=raw_content)]
            elif isinstance(raw_content, list):
                content = raw_content
            else:
                content = [TextBlock(text=str(raw_content))]
        return AdaptedMessage(
            role="assistant",
            content=content,
            stop_reason="",
            model=getattr(msg, "model", ""),
            type="assistant",
        )

    # claude_agent_sdk.ResultMessage — skip here; handled as fallback in _run_turn
    if type_name == "ResultMessage":
        return None

    # Message objects that do have a .role attribute
    if hasattr(msg, "role"):
        content = []
        if hasattr(msg, "content"):
            raw_content = msg.content
            if isinstance(raw_content, str):
                content = [TextBlock(text=raw_content)]
            elif isinstance(raw_content, list):
                content = raw_content
            else:
                content = [TextBlock(text=str(raw_content))]

        return AdaptedMessage(
            role=getattr(msg, "role", "assistant"),
            content=content,
            stop_reason=getattr(msg, "stop_reason", ""),
            model=getattr(msg, "model", ""),
            type=getattr(msg, "type", "assistant"),
        )

    # Dict from native CLI
    if isinstance(msg, dict):
        return adapt_claude_event(msg)

    # Fallback: any object with a .content attribute (handles legacy/test objects
    # that have content blocks but no recognised type name or .role attribute).
    if hasattr(msg, "content"):
        raw_content = msg.content
        if isinstance(raw_content, str):
            content: list = [TextBlock(text=raw_content)]
        elif isinstance(raw_content, list):
            content = raw_content
        else:
            content = [TextBlock(text=str(raw_content))]
        return AdaptedMessage(
            role=getattr(msg, "role", "assistant"),
            content=content,
            stop_reason=getattr(msg, "stop_reason", ""),
            model=getattr(msg, "model", ""),
            type=getattr(msg, "type", "assistant"),
        )

    return None


def normalize_message(msg: Any) -> "AdaptedMessage | None":
    """Normalize any provider message format to AdaptedMessage.

    Single entry point for all message format conversion. Handles:
    - AdaptedMessage — passthrough (already adapted)
    - Message objects (assistant/result/role-bearing objects)
    - Raw dicts from claude CLI (stream-json events)
    - Raw dicts from codex JSONL
    - Bare strings — wrapped in a text-only AdaptedMessage

    Returns None for unknown or empty/terminal event types.
    """
    if msg is None:
        return None

    if isinstance(msg, str):
        if not msg:
            return None
        return AdaptedMessage(
            role="assistant",
            content=[TextBlock(text=msg)],
            type="text",
        )

    return adapt_sdk_message(msg)
