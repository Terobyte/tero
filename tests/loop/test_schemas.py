"""Schema + semantic validation for the three v4 roles."""

import json

import pytest

from src.loop.ledger import Ledger
from src.loop.schemas import ValidateCtx, validate


def _player(**overrides):
    data = {
        "status": "done",
        "changes": [
            {"file": "src/parse.py", "what": "empty returns []", "why": "step 2"}
        ],
        "start_here": ["src/parse.py:40"],
        "blocked_reason": "",
    }
    data.update(overrides)
    return data


def _finding(**overrides):
    data = {
        "id": None,
        "severity": "blocking",
        "level": "major",
        "route": "player",
        "file": "src/parse.py",
        "line": 42,
        "problem": "parse('') raises",
        "expected": "returns []",
    }
    data.update(overrides)
    return data


def _judge(**overrides):
    data = {
        "verdict": "NO_SHIP",
        "previous": [],
        "findings": [_finding()],
        "user_question": "",
    }
    data.update(overrides)
    return data


def _tester(**overrides):
    data = {
        "tests": [
            {
                "finding_id": "F1",
                "path": "tests/test_parse.py",
                "name": "test_parse_empty_string",
            }
        ],
        "skipped": [],
    }
    data.update(overrides)
    return data


def test_player_valid_done():
    assert validate("player", _player()) == []


def test_player_rejects_passed_tests_field():
    data = _player()
    data["tests_passed"] = True
    errors = validate("player", data)
    assert errors
    assert any("tests_passed" in e or "additional" in e.lower() for e in errors)


def test_player_rejects_unknown_status():
    errors = validate("player", _player(status="shipped"))
    assert errors


def test_judge_valid_no_ship():
    assert validate("judge", _judge()) == []


def test_judge_ship_rejects_blocking_finding():
    errors = validate(
        "judge",
        _judge(verdict="SHIP", findings=[_finding(severity="blocking")]),
    )
    assert errors
    assert any("blocking" in e.lower() or "SHIP" in e for e in errors)


def test_judge_has_no_advisory():
    errors = validate(
        "judge",
        _judge(
            verdict="NO_SHIP",
            findings=[_finding(severity="advisory", route="player")],
        ),
    )
    assert errors


def test_judge_ship_rejects_still_open_previous():
    errors = validate(
        "judge",
        _judge(
            verdict="SHIP",
            findings=[],
            previous=[{"id": "F1", "state": "still_open"}],
        ),
        ValidateCtx(open_ids=["F1"]),
    )
    assert errors


def test_judge_ship_requires_all_previous_resolved():
    assert (
        validate(
            "judge",
            _judge(
                verdict="SHIP",
                findings=[],
                previous=[{"id": "F1", "state": "resolved"}],
            ),
            ValidateCtx(open_ids=["F1"]),
        )
        == []
    )


def test_judge_no_ship_requires_blocking_or_still_open():
    errors = validate(
        "judge",
        _judge(verdict="NO_SHIP", findings=[]),
    )
    assert errors


def test_judge_no_ship_ok_with_still_open_only():
    assert (
        validate(
            "judge",
            _judge(
                verdict="NO_SHIP",
                findings=[],
                previous=[{"id": "F1", "state": "still_open"}],
            ),
            ValidateCtx(open_ids=["F1"]),
        )
        == []
    )


def test_judge_needs_user_requires_question():
    errors = validate(
        "judge",
        _judge(verdict="NEEDS_USER", findings=[], user_question=""),
    )
    assert errors
    assert any("user_question" in e for e in errors)


def test_judge_needs_user_valid():
    assert (
        validate(
            "judge",
            _judge(
                verdict="NEEDS_USER",
                findings=[],
                user_question="which API should parse return?",
            ),
        )
        == []
    )


def test_judge_previous_must_cover_open_ids():
    errors = validate(
        "judge",
        _judge(previous=[]),
        ValidateCtx(open_ids=["F1", "F2"]),
    )
    assert errors
    assert any("F1" in e or "previous" in e.lower() for e in errors)


def test_judge_previous_covers_all_open_ids():
    assert (
        validate(
            "judge",
            _judge(
                previous=[
                    {"id": "F1", "state": "resolved"},
                    {"id": "F2", "state": "still_open"},
                ]
            ),
            ValidateCtx(open_ids=["F1", "F2"]),
        )
        == []
    )


def test_judge_discard_test_requires_protected_open_id():
    errors = validate(
        "judge",
        _judge(
            findings=[_finding(route="discard_test", id="F9")],
        ),
        ValidateCtx(open_ids=["F1"], protected_ids={"F1"}),
    )
    assert errors


def test_judge_discard_test_ok_for_protected_open_id():
    assert (
        validate(
            "judge",
            _judge(
                previous=[{"id": "F1", "state": "still_open"}],
                findings=[_finding(route="discard_test", id="F1")],
            ),
            ValidateCtx(open_ids=["F1"], protected_ids={"F1"}),
        )
        == []
    )


def test_judge_discard_test_rejects_unprotected_open_id():
    errors = validate(
        "judge",
        _judge(
            previous=[{"id": "F1", "state": "still_open"}],
            findings=[_finding(route="discard_test", id="F1")],
        ),
        ValidateCtx(open_ids=["F1"], protected_ids=set()),
    )
    assert errors


def test_judge_unknown_route_rejected():
    errors = validate("judge", _judge(findings=[_finding(route="coach")]))
    assert errors


def test_tester_valid():
    assert validate("tester", _tester()) == []


def test_tester_skipped_valid():
    assert (
        validate(
            "tester",
            _tester(
                tests=[],
                skipped=[{"finding_id": "F3", "reason": "UI only"}],
            ),
        )
        == []
    )


def test_tester_rejects_extra_fields():
    data = _tester()
    data["ok"] = True
    assert validate("tester", data)


def test_unknown_role_is_error():
    errors = validate("coach", {})
    assert errors


def _strict_violations(schema, path):
    """OpenAI strict structured output: every object lists all properties as
    required and forbids extra keys. codex --output-schema rejects anything else
    with 400 invalid_json_schema before the model runs."""
    out = []
    if isinstance(schema, dict):
        if "properties" in schema:
            missing = sorted(set(schema["properties"]) - set(schema.get("required", [])))
            if missing:
                out.append(f"{path}: not in required: {missing}")
            if schema.get("additionalProperties") is not False:
                out.append(f"{path}: additionalProperties must be false")
        for key, value in schema.items():
            out.extend(_strict_violations(value, f"{path}/{key}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            out.extend(_strict_violations(value, f"{path}[{index}]"))
    return out


def test_schemas_are_openai_strict():
    from src.loop.schemas import JUDGE, PLAYER, TESTER

    violations = []
    for name, schema in (("PLAYER", PLAYER), ("JUDGE", JUDGE), ("TESTER", TESTER)):
        violations.extend(_strict_violations(schema, name))
    assert violations == []


def test_judge_new_finding_with_null_id_valid():
    assert validate("judge", _judge(findings=[_finding(id=None)])) == []


def test_level_optional_never_retries(tmp_path):
    ledger = Ledger(tmp_path / "run")
    missing = _finding()
    del missing["level"]
    data = _judge(findings=[missing, _finding(level=None), _finding(level="huge")])
    assert validate("judge", data, ValidateCtx(ledger=ledger, round=2, phase="P")) == []
    assert [item["level"] for item in data["findings"]] == [None, None, None]
    events = [
        json.loads(line)
        for line in (tmp_path / "run" / "ledger.jsonl").read_text().splitlines()
    ]
    assert len(events) == 3
    assert all(
        item["kind"] == "judge_level_missing"
        and item["round"] == 2
        and item["phase"] == "P"
        and item["role"] == "judge"
        for item in events
    )


def test_judge_level_missing_event_failure_nonfatal():
    class _BoomLedger:
        def event(self, *args, **kwargs):
            raise OSError("disk full")

    missing = _finding()
    del missing["level"]
    data = _judge(findings=[missing, _finding(level=None), _finding(level="huge")])
    assert validate("judge", data, ValidateCtx(ledger=_BoomLedger(), round=1, phase="P")) == []
    assert [item["level"] for item in data["findings"]] == [None, None, None]


@pytest.mark.asyncio
async def test_level_missing_null_unknown_single_judge_call(tmp_path):
    import json as _json

    from src.loop.agents import LiveAgents

    missing = _finding()
    del missing["level"]
    payload = _judge(findings=[missing, _finding(level=None), _finding(level="huge")])

    class _OnceProvider:
        def __init__(self):
            self.calls = 0

        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            self.calls += 1
            yield _json.dumps(payload)

    provider = _OnceProvider()
    agents = LiveAgents(str(tmp_path), factory=lambda _name: provider, retry_wait_s=0)
    ledger = Ledger(tmp_path / "run")
    data = await agents.run_role(
        "judge",
        provider="codex",
        model="m",
        prompt="P",
        timeout=5,
        ctx=ValidateCtx(ledger=ledger, round=2, phase="P"),
    )
    assert provider.calls == 1
    assert "__invalid__" not in data
    assert [item["level"] for item in data["findings"]] == [None, None, None]


def test_level_does_not_change_verdict_rules():
    assert validate("judge", _judge(findings=[_finding(level="nit")])) == []
    errors = validate("judge", _judge(verdict="SHIP", findings=[_finding(level="nit")]))
    assert errors
