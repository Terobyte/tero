"""JSON schemas and semantic validation for the three roles."""

from __future__ import annotations

from dataclasses import dataclass, field

from jsonschema import Draft202012Validator


PLAYER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "changes", "start_here", "blocked_reason"],
    "properties": {
        "status": {"type": "string", "enum": ["done", "blocked"]},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file", "what", "why"],
                "properties": {
                    "file": {"type": "string"},
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
        "start_here": {"type": "array", "items": {"type": "string"}},
        "blocked_reason": {"type": "string"},
    },
}

JUDGE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "previous", "findings", "user_question"],
    "properties": {
        "verdict": {"type": "string", "enum": ["SHIP", "NO_SHIP", "NEEDS_USER"]},
        "previous": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "state"],
                "properties": {
                    "id": {"type": "string"},
                    "state": {"type": "string", "enum": ["resolved", "still_open"]},
                },
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                # OpenAI strict mode: every property must be required; optional
                # ones are nullable. id is null for new findings and names an
                # open finding only for route=discard_test.
                "required": ["id", "severity", "route", "file", "line", "problem", "expected"],
                "properties": {
                    "id": {"type": ["string", "null"]},
                    "severity": {"type": "string", "enum": ["blocking"]},
                    "route": {
                        "type": "string",
                        "enum": ["tester", "player", "discard_test"],
                    },
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "problem": {"type": "string"},
                    "expected": {"type": "string"},
                },
            },
        },
        "user_question": {"type": "string"},
    },
}

TESTER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tests", "skipped"],
    "properties": {
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "path", "name"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "path": {"type": "string"},
                    "name": {"type": "string"},
                },
            },
        },
        "skipped": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "reason"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

SCHEMAS = {"player": PLAYER, "judge": JUDGE, "tester": TESTER}
_VALIDATORS = {
    role: Draft202012Validator(schema) for role, schema in SCHEMAS.items()
}


@dataclass
class ValidateCtx:
    """Loop context the JSON schema cannot see."""

    open_ids: list[str] = field(default_factory=list)
    protected_ids: set[str] = field(default_factory=set)


def validate(role: str, data: dict, ctx: ValidateCtx | None = None) -> list[str]:
    """Return schema + semantic errors. Empty list means the payload is usable."""
    schema = SCHEMAS.get(role)
    if schema is None:
        return [f"unknown role: {role}"]

    errors = [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in _VALIDATORS[role].iter_errors(data)
    ]
    if errors:
        return errors
    if role == "judge":
        errors.extend(_semantic_judge(data, ctx or ValidateCtx()))
    if role == "player" and data["status"] == "done" and not data["changes"]:
        errors.append(
            "changes: done без правок не принимается — запиши код и перечисли файлы; "
            "если контракт уже выполнен, перечисли файлы, где он выполнен"
        )
    return errors


def _semantic_judge(data: dict, ctx: ValidateCtx) -> list[str]:
    errors: list[str] = []
    verdict = data["verdict"]
    findings = data["findings"]
    previous = data["previous"]
    question = data["user_question"].strip()

    blocking = [f for f in findings if f["severity"] == "blocking"]
    still_open = [p for p in previous if p["state"] == "still_open"]
    prev_ids = {p["id"] for p in previous}

    missing = [fid for fid in ctx.open_ids if fid not in prev_ids]
    if missing:
        errors.append(f"previous must cover open findings: {', '.join(missing)}")

    if verdict == "SHIP":
        if findings:
            errors.append("SHIP cannot include findings: every issue is blocking")
        if still_open:
            errors.append("SHIP requires every previous finding resolved")
    elif verdict == "NO_SHIP":
        if not blocking and not still_open:
            errors.append("NO_SHIP needs a blocking finding or a still_open previous")
    elif verdict == "NEEDS_USER":
        if not question:
            errors.append("NEEDS_USER requires a non-empty user_question")

    for finding in findings:
        if finding["route"] != "discard_test":
            continue
        fid = finding.get("id") or ""
        if fid not in ctx.open_ids or fid not in ctx.protected_ids:
            errors.append(
                "discard_test is only valid for an open finding with a protected test"
            )
    return errors
