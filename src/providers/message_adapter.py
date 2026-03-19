"""Message adapter for converting between SDK objects and native CLI JSON."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    """Text content block, compatible with SDK TextBlock."""

    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    """Tool use block, compatible with SDK ToolUseBlock."""

    name: str
    input: dict = field(default_factory=dict)
    type: str = "tool_use"
    id: str = ""  # Optional tool use ID


@dataclass
class ToolResultBlock:
    """Tool result block, compatible with SDK ToolResultBlock."""

    tool_use_id: str
    content: str
    is_error: bool = False
    type: str = "tool_result"


@dataclass
class AdaptedMessage:
    """Lightweight wrapper compatible with SDK interface.

    Uses duck-typing so existing code that checks __class_name__ works.
    """

    role: str  # "assistant" or "tool" or "user"
    content: list  # [TextBlock, ToolUseBlock, ...]
    stop_reason: str = ""  # "end_turn", "max_tokens", "tool_use", etc.
    model: str = ""  # Model that generated this message

    @property
    def __class_name__(self) -> str:
        """Duck-typing compatibility with SDK message types."""
        if self.role == "assistant":
            return "AssistantMessage"
        if self.role == "tool":
            return "ToolResultMessage"
        return "UserMessage"

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
    """Convert JSON event from claude CLI to SDK-compatible object.

    Claude CLI with --output-format stream-json emits JSON objects.
    This function adapts them to match SDK message interface.

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
                )

        if text:
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=text)],
                stop_reason=event.get("stop_reason", ""),
                model=event.get("model", ""),
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
        )

    # Final result (end of conversation)
    if etype == "result":
        text = event.get("result", event.get("text", ""))
        if text:
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=text)],
                stop_reason=event.get("stop_reason", "end_turn"),
            )

    # Ignored event types: init, system, permission_request, etc.
    return None


def adapt_sdk_message(msg: Any) -> AdaptedMessage | None:
    """Convert SDK message object to AdaptedMessage if needed.

    This is a pass-through for already-compatible objects.
    Used to normalize both native CLI and SDK sources.

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
        )

    # claude_agent_sdk.ResultMessage — skip here; handled as fallback in _run_turn
    if type_name == "ResultMessage":
        return None

    # Legacy SDK objects that do have a .role attribute
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
        )

    # Dict from native CLI
    if isinstance(msg, dict):
        return adapt_claude_event(msg)

    return None
