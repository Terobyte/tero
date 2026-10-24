"""Tests for src/providers/agy.py — AgyProvider (agy CLI, replaces gemini)."""

from __future__ import annotations

import asyncio
import json
import sys
import time

import pytest

from src.errors import ProviderError
from src.providers.agy import AgyConfig, AgyProvider


def _stub_cli(tmp_path, body: str) -> str:
    script = tmp_path / "stub-agy"
    script.write_text(f"#!{sys.executable}\n{body}\n")
    script.chmod(0o755)
    return str(script)


def _provider(tmp_path, body: str, **cfg_kwargs) -> AgyProvider:
    return AgyProvider(AgyConfig(command=_stub_cli(tmp_path, body), **cfg_kwargs))


async def _drain(provider: AgyProvider, working_dir: str) -> list:
    return [m async for m in provider.run("P", "", working_dir)]


def test_agy_argv_judge_and_player():
    p = AgyProvider(AgyConfig(command="agy"))
    judge = p._build_command(
        prompt="P", model="gemini-3.8-flash-high", read_only=True
    )
    assert judge == [
        "agy", "-p", "P",
        "--model", "gemini-3.8-flash-high",
        "--output-format", "stream-json",
        "--print-timeout", "3600s",
        "--disable-slash-commands",
        "--mode", "plan",
    ]
    assert "--dangerously-skip-permissions" not in judge

    player = p._build_command(
        prompt="P", model="gemini-3.8-flash-high", read_only=False
    )
    assert player == [
        "agy", "-p", "P",
        "--model", "gemini-3.8-flash-high",
        "--output-format", "stream-json",
        "--print-timeout", "3600s",
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
    ]
    assert "--mode" not in player

    schema = {"type": "object"}
    full = p._build_command(
        prompt="P", model="m", output_schema=schema,
        read_only=True, effort="medium",
    )
    assert json.loads(full[full.index("--json-schema") + 1]) == schema
    assert full[full.index("--effort") + 1] == "medium"

    bare = p._build_command(prompt="P", model="gemini-3.8-flash")
    assert bare[bare.index("--model") + 1] == "gemini-3.8-flash-high"

    empty_schema = p._build_command(prompt="P", model="m", output_schema={})
    assert json.loads(empty_schema[empty_schema.index("--json-schema") + 1]) == {}


@pytest.mark.asyncio
async def test_agy_structured_output_is_final_text(tmp_path):
    verdict = {"verdict": "SHIP", "previous": [], "findings": [], "user_question": ""}
    stream = [
        {"event": "init", "conversation_id": "c1",
         "init": {"model": "gemini-3.8-flash-high", "permission_mode": "always-proceed"}},
        {"event": "step_update", "step_update": {
            "step_index": 0, "state": "ACTIVE",
            "step_type": "agent_response", "text_delta": "looking at the diff"}},
        {"event": "step_update", "step_update": {
            "step_index": 1, "state": "ACTIVE", "step_type": "tool",
            "tool_name": "write_file",
            "tool_info": {"name": "write_file",
                          "parameters": {"file_path": "src/greet.py", "content": "x"}}}},
        {"event": "step_update", "step_update": {
            "step_index": 1, "state": "DONE", "step_type": "tool",
            "usage": {"input_tokens": 10, "output_tokens": 5}}},
        {"event": "result", "result": {
            "status": "SUCCESS", "response": "done",
            "structured_output": verdict,
            "usage": {"input_tokens": 100, "output_tokens": 50}}},
    ]
    body = (
        "import json, sys\n"
        f"_STREAM = {stream!r}\n"
        "for _obj in _STREAM:\n"
        "    sys.stdout.write(json.dumps(_obj) + chr(10))\n"
    )
    msgs = await _drain(_provider(tmp_path, body), str(tmp_path))
    texts = [m.get_text_content() for m in msgs if m.get_text_content()]
    assert texts, "agy stream produced no text"
    assert texts[-1] == json.dumps(verdict)

    from src.loop.agents import _activity

    tool_blocks = [
        b for m in msgs for b in (m.content or [])
        if getattr(b, "type", "") == "tool_use"
    ]
    assert tool_blocks, "tool call invisible to normalize_message/_activity"
    line = _activity(tool_blocks[0])
    assert "write_file" in line
    assert "src/greet.py" in line


@pytest.mark.asyncio
async def test_agy_error_result_raises(tmp_path):
    body = (
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'event': 'result', 'result': "
        "{'status': 'ERROR', 'error': 'model not found'}}) + chr(10))\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('unknown model xyz\\n')\n"
        "sys.exit(1)\n"
    )
    with pytest.raises(ProviderError, match="model not found"):
        await _drain(_provider(tmp_path, body), str(tmp_path))


@pytest.mark.asyncio
async def test_agy_garbage_is_judge_failure(tmp_path):
    from src.loop.agents import LiveAgents
    from src.loop.schemas import ValidateCtx

    body = "import sys\nsys.stdout.write('not json at all\\nstill not json\\n')\n"
    provider = _provider(tmp_path, body)
    agents = LiveAgents(
        str(tmp_path), factory=lambda _n: provider, retry_wait_s=0
    )
    data = await agents.run_role(
        "judge", provider="agy", model="gemini-3.8-flash-high",
        prompt="P", timeout=10, ctx=ValidateCtx(),
    )
    assert data.get("__invalid__") is True


@pytest.mark.asyncio
async def test_agy_one_call_at_a_time(tmp_path):
    times = tmp_path / "times.log"
    body = (
        "import sys, time\n"
        f"open({str(times)!r}, 'a').write(repr(time.monotonic()) + ' start\\n')\n"
        "sys.stdout.write('{\"event\":\"init\"}\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.6)\n"
        f"open({str(times)!r}, 'a').write(repr(time.monotonic()) + ' end\\n')\n"
    )
    cmd = _stub_cli(tmp_path, body)
    first = AgyProvider(AgyConfig(command=cmd))
    second = AgyProvider(AgyConfig(command=cmd))

    async def _go(provider):
        async for _ in provider.run("P", "", str(tmp_path)):
            pass

    await asyncio.wait_for(
        asyncio.gather(_go(first), _go(second)), timeout=10
    )
    rows = [
        (float(ts), kind)
        for ts, kind in (line.split() for line in times.read_text().splitlines())
    ]
    starts = sorted(ts for ts, kind in rows if kind == "start")
    ends = sorted(ts for ts, kind in rows if kind == "end")
    assert len(starts) == 2 and len(ends) == 2
    intervals = sorted(zip(starts, ends))
    assert intervals[0][1] <= intervals[1][0], f"agy calls overlapped: {intervals}"


def test_gemini_routes_to_agy():
    from src.providers import create_provider

    with pytest.warns(UserWarning, match="gemini CLI больше не используется"):
        provider = create_provider("gemini")
    assert isinstance(provider, AgyProvider)
    assert provider.config.command == "agy"


def test_agy_config_defaults():
    cfg = AgyConfig()
    assert cfg.command == "agy"
    assert cfg.default_model == "gemini-3.8-flash-high"
    assert cfg.print_timeout_s == 3600
    assert AgyProvider.streams is True


def test_agy_check_ready_without_binary():
    p = AgyProvider(AgyConfig(command="nonexistent_agy_binary_xyz"))
    ok, msg = p.check_ready()
    assert ok is False
    assert "not found" in msg
