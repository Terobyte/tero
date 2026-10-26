"""F1 red test: streamed Codex error text must not leak MCP noise."""

from src.providers.codex_events import adapt_codex_event, adapt_item


def _text(msg) -> str:
    parts = []
    for b in msg.content or []:
        t = getattr(b, "text", None) or getattr(b, "content", "")
        parts.append(str(t))
    return "\n".join(parts)


def test_streamed_error_event_strips_rmcp_noise():
    ev = {"type": "error", "message": "rmcp::server boom AuthRequired\nreal failure"}
    adapted = adapt_codex_event(ev, set())
    assert adapted is not None
    txt = _text(adapted)
    assert "rmcp::" not in txt
    assert "AuthRequired" not in txt
    assert "real failure" in txt


def test_streamed_turn_failed_strips_rmcp_noise():
    ev = {"type": "turn.failed", "error": {"message": "rmcp::x AuthRequired\nYou've hit your usage limit"}}
    adapted = adapt_codex_event(ev, set())
    assert adapted is not None
    txt = _text(adapted)
    assert "rmcp::" not in txt
    assert "AuthRequired" not in txt


def test_streamed_error_item_strips_rmcp_noise():
    item = {"type": "error", "message": "rmcp::srv AuthRequired oops"}
    adapted = adapt_item(item, "item.completed", set())
    assert adapted is not None
    txt = _text(adapted)
    assert "rmcp::" not in txt
    assert "AuthRequired" not in txt
