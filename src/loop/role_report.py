"""Role report helpers: schema prompt wrapper and JSON extraction.

Extracted from agents.py (ceiling room for the stop flag); agents.py
re-exports both names so existing imports keep working.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.loop.schemas import ValidateCtx, validate

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Providers that enforce output_schema natively: the JSON Schema stays
# out-of-band and the prompt needs no example. The rest (opencode, cursor,
# muse, ...) ignore output_schema, so the schema goes into the prompt as a
# filled example instead of raw Schema text (B2–B4: weak models copy the
# Schema itself instead of writing the report).
NATIVE_SCHEMA_PROVIDERS = frozenset({"codex", "claude", "agy"})


def extract_json(text: str) -> dict[str, Any] | None:
    """Last JSON object in the assistant text: fenced block, else bare `{...}`."""
    fenced: list[tuple[int, int, dict[str, Any]]] = []
    for match in _FENCE.finditer(text or ""):
        parsed = _loads_object(match.group(1))
        if parsed is not None:
            fenced.append((match.start(), match.end(), parsed))
    bare = _bare_objects(text or "")
    if fenced and (not bare or fenced[-1][0] > bare[-1][0]):
        return fenced[-1][2]
    return bare[-1][2] if bare else None


def extract_json_end(text: str) -> int | None:
    """End offset of the last JSON object in the text, or None."""
    span = extract_json_span(text or "")
    return span[1] if span else None


def extract_json_span(text: str) -> tuple[int, int] | None:
    """(start, end) offsets of the last JSON object in the text, or None."""
    fenced: list[tuple[int, int]] = []
    for match in _FENCE.finditer(text or ""):
        if _loads_object(match.group(1)) is not None:
            fenced.append((match.start(), match.end()))
    bare = [(start, end) for start, end, _ in _bare_objects(text or "")]
    if fenced and (not bare or fenced[-1][0] > bare[-1][0]):
        return fenced[-1]
    return bare[-1] if bare else None


def chatter_text(text: str) -> str:
    """Joined text with the last parseable JSON object excised, stripped."""
    span = extract_json_span(text or "")
    if span is None:
        return (text or "").strip()
    start, end = span
    return ((text or "")[:start] + (text or "")[end:]).strip()


def verbose_snippet(joined: str, role: str) -> str:
    """Chatter for the ✎ line: report/echo excluded, everything else kept.

    The finding (F1): the ✎ decision must see the assembled message, not
    each stream fragment — a valid report split across chunks printed its
    fragments as ✎ before the message parsed as a report.
    """
    tail = chatter_text(joined) if is_final_report_text(joined, role) else joined
    return " ".join(tail.split())[:200]


def tail_after_report(text: str) -> str:
    """Text after the last parseable JSON object (tail chatter), stripped."""
    end = extract_json_end(text or "")
    if end is None:
        return (text or "").strip()
    return (text or "")[end:].strip()


def _bare_objects(text: str) -> list[tuple[int, int, dict[str, Any]]]:
    decoder = json.JSONDecoder()
    found: list[tuple[int, int, dict[str, Any]]] = []
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
            found.append((start, end, obj))
        index = end
    return found


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def example_report(role: str, schema: dict | None = None) -> dict[str, Any]:
    """Filled example report valid against the role schema.

    May include placeholder strings but never Schema vocabulary: the
    validator must accept it, and the prompt renderer asserts that.
    """
    if role == "player":
        return {
            "status": "done",
            "changes": [{"file": "src/loop/run.py", "what": "<что изменено>", "why": "<пункт контракта>"}],
            "start_here": [],
            "blocked_reason": "",
        }
    if role == "judge":
        return {"verdict": "SHIP", "previous": [], "findings": [], "user_question": ""}
    if role == "tester":
        return {"tests": [], "skipped": []}
    if role == "decider":
        return {"decision": "ask_owner", "why": "нужен вердикт decider"}
    if schema is not None:
        required = list(schema.get("required") or [])
        return {name: [] for name in required}
    return {}


def is_schema_echo(data: dict[str, Any] | None) -> bool:
    """True when the reply looks like the Schema itself, not a report.

    A weak model after a long run copies the JSON Schema (an object with
    "type": "object" plus "properties"/"required") instead of the report.
    """
    if not isinstance(data, dict):
        return False
    if data.get("type") != "object":
        return False
    return "properties" in data or "required" in data


def is_final_report_text(text: str, role: str) -> bool:
    """True when the final message parsed as a report (or a schema echo)."""
    data = extract_json(text or "")
    if data is None:
        return False
    if is_schema_echo(data):
        return True
    return validate(role, data) == []


def validate_role(
    role: str,
    data: dict[str, Any] | None,
    ctx: ValidateCtx | None = None,
) -> list[str]:
    if is_schema_echo(data):
        return ["схему, а не отчёт: прислана JSON Schema вместо заполненного отчёта"]
    if data is None:
        return ["no JSON object in the final message"]
    return validate(role, data, ctx)


async def repair_once(
    call: Any,
    role: str,
    prompt: str,
    text: str,
    data: dict[str, Any] | None,
    ctx: ValidateCtx | None,
    emit: Any = None,
) -> tuple[str | dict[str, Any], dict[str, Any] | None, list[str]]:
    """Second attempt after an invalid report: repair event + repair prompt.

    Returns (text, data, errors) of the second attempt. The `repair`
    stream event carries phase/round/role/text, like `kick` does; the
    repair prompt names a schema echo directly.
    """
    from src.loop.prompts import repair_prompt

    errors = validate_role(role, data, ctx)
    echo = is_schema_echo(data)
    if emit is not None:
        emit("repair", role=role, text="; ".join(errors[:3]))
    text = await call(repair_prompt(role, prompt, text, errors, schema_echo=echo))
    if isinstance(text, dict):
        return text, None, []
    data = extract_json(text)
    return text, data, validate_role(role, data, ctx)


def schema_echo_repair_block(role: str) -> str:
    """Filled example block appended to a repair prompt on a schema echo."""
    example = example_report(role)
    if not example:
        return ""
    rendered = json.dumps(example, ensure_ascii=False)
    return (
        "Заполненный пример отчёта для этого провайдера (значения заполни сам, "
        "схему в ответ не копируй):\n"
        f"```json\n{rendered}\n```"
    )


def with_schema(prompt: str, schema: dict, provider: str = "", role: str = "") -> str:
    """Spell out the report format for the final message.

    Providers with native schema support get the raw JSON Schema, as
    before. Providers without it get a filled example report (valid per
    the validator) plus the one-line field list instead — the Schema
    itself never reaches the prompt.
    """
    if not provider or provider in NATIVE_SCHEMA_PROVIDERS:
        return (
            f"{prompt}\n\n## Формат финального сообщения\n"
            "Один JSON-объект строго по этой JSON Schema, без текста вокруг:\n"
            f"```json\n{json.dumps(schema, ensure_ascii=False)}\n```"
        )
    if not role:
        from src.loop.schemas import SCHEMAS

        role = next(
            (name for name, known in SCHEMAS.items() if known is schema or known == schema),
            "",
        )
    example = example_report(role) if role else {}
    if not example:
        example = {name: [] for name in (schema.get("required") or [])}
    rendered = json.dumps(example, ensure_ascii=False)
    fields = ", ".join(str(name) for name in (schema.get("required") or []))
    return (
        f"{prompt}\n\n## Формат финального сообщения\n"
        "Один JSON-объект, без текста вокруг. Пример (значения заполни сам):\n"
        f"```json\n{rendered}\n```\n"
        f"Поля: {fields}."
    )
