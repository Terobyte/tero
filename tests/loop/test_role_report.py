"""B2–B4: контракт отчёта — пример вместо схемы, эхо схемы, repair-событие, без ✎."""

import json

import pytest

from src.loop.agents import LiveAgents
from src.loop.role_report import (
    example_report,
    extract_json,
    is_final_report_text,
    is_schema_echo,
    with_schema,
)
from src.loop.schemas import PLAYER, TESTER, ValidateCtx, validate


class _Scripted:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield reply


GOOD_PLAYER = json.dumps(
    {
        "status": "done",
        "changes": [{"file": "src/loop/run.py", "what": "правка", "why": "контракт"}],
        "start_here": [],
        "blocked_reason": "",
    }
)

ECHO = json.dumps(
    {"type": "object", "properties": {"status": {}}, "required": ["status"]}
)


def _player_agents(tmp_path, provider):
    return LiveAgents(str(tmp_path), factory=lambda _n: provider, retry_wait_s=0)


async def _player(tmp_path, provider, **kwargs):
    agents = _player_agents(tmp_path, provider)
    return await agents.run_role(
        "player",
        provider="opencode",
        model="m",
        prompt="контракт фазы",
        timeout=10,
        ctx=ValidateCtx(),
        **kwargs,
    )


def test_with_schema_shows_example_not_schema():
    prompt = with_schema("сделай контракт", PLAYER, "opencode")
    assert '"additionalProperties"' not in prompt
    data = extract_json(prompt)
    assert data is not None
    assert validate("player", data) == []


def test_with_schema_native_keeps_schema():
    prompt = with_schema("сделай контракт", PLAYER, "codex")
    assert '"additionalProperties"' in prompt


def test_example_report_is_valid():
    assert validate("player", example_report("player")) == []
    assert validate("judge", json.loads(json.dumps(example_report("judge")))) == []
    assert validate("tester", example_report("tester")) == []


def test_is_schema_echo():
    assert is_schema_echo(json.loads(ECHO)) is True
    assert is_schema_echo(json.loads(GOOD_PLAYER)) is False
    assert is_schema_echo(None) is False
    assert is_schema_echo({"type": "object"}) is False


@pytest.mark.asyncio
async def test_schema_echo_gets_specific_repair(tmp_path):
    provider = _Scripted([ECHO, GOOD_PLAYER])
    data = await _player(tmp_path, provider)
    assert data["changes"]
    assert len(provider.prompts) == 2
    assert "схему, а не отчёт" in provider.prompts[1]


@pytest.mark.asyncio
async def test_repair_emits_stream_event(tmp_path):
    from src.loop.stream import Stream

    stream = Stream(tmp_path / "stream.jsonl")

    def emit(type, **fields):
        fields.setdefault("phase", "T")
        fields.setdefault("round", 3)
        stream.emit(type, **fields)

    provider = _Scripted(["болтовня без json", GOOD_PLAYER])
    data = await _player(tmp_path, provider, emit=emit)
    assert data["changes"]
    lines = [
        json.loads(line)
        for line in (tmp_path / "stream.jsonl").read_text().splitlines()
    ]
    repairs = [e for e in lines if e["type"] == "repair"]
    assert len(repairs) == 1
    assert repairs[0]["role"] == "player"
    assert repairs[0]["round"] == 3
    assert repairs[0]["text"]


@pytest.mark.asyncio
async def test_final_report_not_printed_as_text(tmp_path, capsys):
    provider = _Scripted([GOOD_PLAYER])
    data = await _player(tmp_path, provider, verbose=True)
    assert data["status"] == "done"
    assert '✎ {"status"' not in capsys.readouterr().out


def test_final_report_text_detection():
    assert is_final_report_text(GOOD_PLAYER, "player") is True
    assert is_final_report_text(ECHO, "player") is True
    assert is_final_report_text("просто болтовня", "player") is False


def test_tester_example_lists_fields_line():
    prompt = with_schema("сделай контракт", TESTER, "cursor")
    assert '"additionalProperties"' not in prompt
    assert "tests" in prompt and "skipped" in prompt


@pytest.mark.asyncio
async def test_split_report_fragments_not_printed(tmp_path, capsys):
    cut = GOOD_PLAYER.index('"changes"')

    class _SplitProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield GOOD_PLAYER[:cut]
            yield GOOD_PLAYER[cut:]

    data = await _player(tmp_path, _SplitProvider(), verbose=True)
    assert data["status"] == "done"
    assert "✎" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_echo_repair_adds_example_for_native_provider(tmp_path):
    provider = _Scripted([ECHO, GOOD_PLAYER])
    agents = _player_agents(tmp_path, provider)
    data = await agents.run_role(
        "player",
        provider="codex",
        model="m",
        prompt="контракт фазы",
        timeout=10,
        ctx=ValidateCtx(),
    )
    assert data["status"] == "done"
    assert len(provider.prompts) == 2
    assert '"status": "done"' in provider.prompts[1]
