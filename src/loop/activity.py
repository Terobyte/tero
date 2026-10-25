"""One console/stream line per tool call of a role."""

from __future__ import annotations

from pathlib import Path
from typing import Any


_ACTIVITY_KEYS = (
    "file_path",
    "path",
    "filePath",
    "file",
    "command",
    "pattern",
    "url",
    "query",
    "prompt",
    "description",
)
_ACTIVITY_PATH_KEYS = {"file_path", "path", "filePath", "file"}


def _activity(block: Any, working_dir: str = "", emit=None, role: str = "") -> str:
    """One console line per tool call, so a long role call is not silent."""
    name = getattr(block, "name", "")
    if not name or getattr(block, "type", "tool_use") != "tool_use" or name == "StructuredOutput":
        return ""
    params = getattr(block, "input", None) or {}
    key = next((k for k in _ACTIVITY_KEYS if params.get(k)), None)
    if key is None:
        if emit is not None:
            emit("tool", role=role, text=name)
        return name
    arg = str(params[key])
    if key in _ACTIVITY_PATH_KEYS and working_dir:
        arg = ", ".join(_relative_path(p, working_dir) for p in arg.split(", "))
    arg = " ".join(arg.split())
    arg = _truncate_tail(arg, 100)
    line = f"{name} {arg}".strip()
    if emit is not None:
        emit("tool", role=role, text=line)
    return line


def _relative_path(value: str, working_dir: str) -> str:
    """Absolute paths inside working_dir read as `Read src/foo.py`, not a
    100-char-truncated tmpdir prefix that hides the file name."""
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            return value
        return str(candidate.resolve().relative_to(Path(working_dir).resolve()))
    except (OSError, ValueError):
        return value


def _truncate_tail(text: str, limit: int) -> str:
    """Truncate from the LEFT (keep the tail) so a long path keeps its file name."""
    if len(text) <= limit:
        return text
    return "..." + text[-(limit - 3):]
