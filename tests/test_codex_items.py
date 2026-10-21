"""Codex 0.147 real item types: file_change / mcp_tool_call / web_search.

_adapt_item previously only knew fictional file_edit/file_write/file_read
items, so these never showed up as console activity. These lock the new
ToolUseBlock shape and the "only started may arrive" edge case for
file_change.
"""

from src.providers.codex import CodexProvider


def test_file_change_started_yields_edit_tool_use():
    provider = CodexProvider()
    item = {
        "id": "item-1",
        "type": "file_change",
        "status": "in_progress",
        "changes": [{"path": "src/foo.py", "kind": "edit"}, {"path": "src/bar.py", "kind": "add"}],
    }
    msg = provider._adapt_item(item, "item.started")
    assert msg is not None
    block = msg.content[0]
    assert block.type == "tool_use"
    assert block.name == "edit"
    assert block.input == {"path": "src/foo.py, src/bar.py"}
    assert block.id == "item-1"


def test_file_change_completed_after_started_is_not_duplicated():
    provider = CodexProvider()
    item = {
        "id": "item-1",
        "type": "file_change",
        "changes": [{"path": "src/foo.py", "kind": "edit"}],
    }
    first = provider._adapt_item(item, "item.started")
    assert first is not None
    second = provider._adapt_item({**item, "status": "completed"}, "item.completed")
    assert second is None


def test_file_change_completed_only_still_yields_tool_use():
    """Codex may emit only item.completed for a file_change (no started seen)."""
    provider = CodexProvider()
    item = {
        "id": "item-2",
        "type": "file_change",
        "status": "completed",
        "changes": [{"path": "src/only.py", "kind": "edit"}],
    }
    msg = provider._adapt_item(item, "item.completed")
    assert msg is not None
    block = msg.content[0]
    assert block.name == "edit"
    assert block.input == {"path": "src/only.py"}


def test_mcp_tool_call_started_yields_server_dot_tool():
    provider = CodexProvider()
    item = {
        "id": "item-3",
        "type": "mcp_tool_call",
        "server": "figma",
        "tool": "get_design_context",
        "arguments": {"node": "1:2"},
    }
    msg = provider._adapt_item(item, "item.started")
    assert msg is not None
    block = msg.content[0]
    assert block.name == "figma.get_design_context"
    assert block.input == {"node": "1:2"}


def test_mcp_tool_call_string_arguments_do_not_crash():
    """Regression: arguments can arrive as a raw JSON string (or other
    non-dict) instead of an object; dict(item.get("arguments") or {}) used
    to raise ValueError/TypeError here, crashing the role call."""
    provider = CodexProvider()
    item = {
        "id": "item-5",
        "type": "mcp_tool_call",
        "server": "figma",
        "tool": "get_design_context",
        "arguments": '{"q": 1}',
    }
    msg = provider._adapt_item(item, "item.started")
    assert msg is not None
    block = msg.content[0]
    assert block.name == "figma.get_design_context"
    assert block.input == {"arguments": '{"q": 1}'}


def test_mcp_tool_call_non_json_string_arguments_do_not_crash():
    provider = CodexProvider()
    item = {
        "id": "item-6",
        "type": "mcp_tool_call",
        "server": "figma",
        "tool": "get_design_context",
        "arguments": "ab",
    }
    msg = provider._adapt_item(item, "item.started")
    assert msg is not None
    assert msg.content[0].input == {"arguments": "ab"}


def test_mcp_tool_call_int_arguments_do_not_crash():
    provider = CodexProvider()
    item = {
        "id": "item-7",
        "type": "mcp_tool_call",
        "server": "figma",
        "tool": "get_design_context",
        "arguments": 5,
    }
    msg = provider._adapt_item(item, "item.started")
    assert msg is not None
    assert msg.content[0].input == {"arguments": "5"}


def test_mcp_tool_call_none_arguments_yields_empty_input():
    provider = CodexProvider()
    item = {
        "id": "item-8",
        "type": "mcp_tool_call",
        "server": "figma",
        "tool": "get_design_context",
        "arguments": None,
    }
    msg = provider._adapt_item(item, "item.started")
    assert msg is not None
    assert msg.content[0].input == {}


def test_web_search_started_yields_query():
    provider = CodexProvider()
    item = {
        "id": "item-4",
        "type": "web_search",
        "query": "tero v4 live logs",
    }
    msg = provider._adapt_item(item, "item.started")
    assert msg is not None
    block = msg.content[0]
    assert block.name == "web_search"
    assert block.input == {"query": "tero v4 live logs"}


def test_old_fictional_file_edit_branch_still_works():
    """D keeps the old file_edit/file_write/file_read branches untouched."""
    provider = CodexProvider()
    item = {"id": "item-5", "type": "file_edit", "path": "src/old.py"}
    msg = provider._adapt_item(item, "item.started")
    assert msg is not None
    block = msg.content[0]
    assert block.name == "file_edit"
    assert block.input == {"path": "src/old.py"}
