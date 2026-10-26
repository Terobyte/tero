"""Role report helpers: schema prompt wrapper and JSON extraction.

Extracted from agents.py (ceiling room for the stop flag); agents.py
re-exports both names so existing imports keep working.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any] | None:
    """Last JSON object in the assistant text: fenced block, else bare `{...}`."""
    fenced: list[tuple[int, dict[str, Any]]] = []
    for match in _FENCE.finditer(text or ""):
        parsed = _loads_object(match.group(1))
        if parsed is not None:
            fenced.append((match.start(), parsed))
    bare = _bare_objects(text or "")
    if fenced and (not bare or fenced[-1][0] > bare[-1][0]):
        return fenced[-1][1]
    return bare[-1][1] if bare else None


def _bare_objects(text: str) -> list[tuple[int, dict[str, Any]]]:
    decoder = json.JSONDecoder()
    found: list[tuple[int, dict[str, Any]]] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            found.append((start, obj))
        index = end
    return found


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def with_schema(prompt: str, schema: dict) -> str:
    """Spell the schema out: opencode/gemini/cursor ignore output_schema."""
    return (
        f"{prompt}\n\n## Формат финального сообщения\n"
        "Один JSON-объект строго по этой JSON Schema, без текста вокруг:\n"
        f"```json\n{json.dumps(schema, ensure_ascii=False)}\n```"
    )
