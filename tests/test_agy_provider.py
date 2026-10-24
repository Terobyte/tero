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


# --- pre-flight перед очередью (T9a): короткий `agy -p`, висящий = стоп ---

def _stub_agy(tmp_path, argv_log, behavior: str):
    """Stub-`agy`, различающий `models` и pre-flight `-p`.

    behavior: "ok" | "not_logged_in" | "hang".
    """
    name = f"agy-{behavior}"
    script = tmp_path / name
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, time\n"
        "argv = sys.argv[1:]\n"
        "log = " + repr(str(argv_log)) + "\n"
        "open(log, 'a').write(' '.join(argv) + chr(10))\n"
        + (
            "if 'models' in argv:\n"
            "    sys.stdout.write('gemini-3.8-flash-high\\tGemini 3.8 Flash\\n')\n"
            "    sys.exit(0)\n"
            "if len(argv) >= 2 and argv[0] == '-p' and argv[1] == 'ок':\n"
            "    time.sleep(3600)\n"
            "    sys.exit(0)\n"
            if behavior == "hang" else
            "if 'models' in argv:\n"
            "    sys.stdout.write('gemini-3.8-flash-high\\tGemini 3.8 Flash\\n')\n"
            "    sys.exit(0)\n"
            "if len(argv) >= 2 and argv[0] == '-p' and argv[1] == 'ок':\n"
            + (
                "    sys.stdout.write(json.dumps({'status': 'SUCCESS',}) + chr(10))\n"
                "    sys.exit(0)\n"
                if behavior == "ok" else
                "    sys.stderr.write('ERROR: not logged in\\n')\n"
                "    sys.exit(1)\n"
            )
        )
    )
    script.chmod(0o755)
    return str(script)


async def _run_with_agy(cfg_overrides, tmp_path, monkeypatch, steps):
    """Прогон run() с одной ролью через stub-agy. Возвращает outcome и argv-лог."""
    import asyncio as _asyncio

    from src.config import Config
    from src.loop import run as run_mod
    from tests.loop import fakes as scripted

    repo = tmp_path / "repo"
    repo.mkdir()
    scripted.git(repo, "init")
    scripted.git(repo, "config", "user.email", "tero@test")
    scripted.git(repo, "config", "user.name", "tero")
    scripted.write_file(repo, "plan.md", "# Demo\n\n## Greet\n\n- [ ] greet\n")
    scripted.write_file(repo, "src/__init__.py", "")
    scripted.write_file(repo, "tests/test_smoke.py", "def test_smoke():\n    assert True\n")
    scripted.git(repo, "add", "-A")
    scripted.git(repo, "commit", "-m", "init")

    argv_log = tmp_path / "argv.log"
    argv_log.write_text("")
    stub = _stub_agy(tmp_path, argv_log, cfg_overrides.pop("_stub"))
    monkeypatch.setattr(
        run_mod, "load_provider_configs", lambda *a, **k: {"agy": {"command": stub}}
    )
    data = {
        "plan_file": "plan.md",
        "working_dir": str(repo),
        "player_provider": "muse",
        "player_model": "muse-spark-1.3",
        "judge_provider": "codex",
        "judge_model": "gpt-5.6-terra",
        "tester_provider": "muse",
        "tester_model": "muse-spark-1.3",
        "test_timeout_s": 30,
    }
    data.update(cfg_overrides)
    cfg = Config(**data)
    outcome_holder: dict = {}

    async def _go():
        outcome_holder["outcome"] = await run_mod.run(
            cfg,
            agents=scripted.FakeAgents(repo, list(steps)),
            agy_preflight=run_mod.agy_preflight_check,
        )

    try:
        await _asyncio.wait_for(_go(), timeout=20)
    except _asyncio.TimeoutError:
        raise AssertionError("прогон завис — pre-flight должен останавливать по таймауту")
    return outcome_holder["outcome"], argv_log.read_text()


@pytest.mark.asyncio
async def test_agy_preflight_ok(tmp_path, monkeypatch):
    from tests.loop import fakes as scripted

    repo_calls: list = []

    def _player(role, kwargs):
        repo = tmp_path / "repo" / "src" / "greet.py"
        repo.parent.mkdir(parents=True, exist_ok=True)
        repo.write_text("def greet():\n    return 'hello'\n")
        repo_calls.append(role)
        return scripted.PLAYER_DONE

    outcome, argv = await _run_with_agy(
        {"judge_provider": "agy", "judge_model": "gemini-3.8-flash-high", "_stub": "ok"},
        tmp_path, monkeypatch,
        [_player, scripted.judge_returns(scripted.ship())],
    )
    assert outcome.exit_code == 0, outcome.reason
    assert repo_calls == ["player"], "agy вызван до исполнителя, исполнитель не пропущен"
    assert "-p" in argv and "ок" in argv


@pytest.mark.asyncio
async def test_agy_preflight_no_login_stops(tmp_path, monkeypatch):
    from tests.loop import fakes as scripted

    outcome, _argv = await _run_with_agy(
        {"judge_provider": "agy", "judge_model": "gemini-3.8-flash-high", "_stub": "not_logged_in"},
        tmp_path, monkeypatch,
        [scripted.judge_returns(scripted.ship())],
    )
    assert outcome.exit_code == 1
    assert outcome.status == "stopped"
    assert "Войди" in outcome.reason
    assert "agy" in outcome.reason


@pytest.mark.asyncio
async def test_agy_preflight_hang_stops(tmp_path, monkeypatch):
    from src.loop import run as run_mod

    monkeypatch.setattr("src.providers.agy.AGY_PREFLIGHT_TIMEOUT_S", 1)
    from tests.loop import fakes as scripted

    outcome, _argv = await _run_with_agy(
        {"judge_provider": "agy", "judge_model": "gemini-3.8-flash-high", "_stub": "hang"},
        tmp_path, monkeypatch,
        [scripted.judge_returns(scripted.ship())],
    )
    assert outcome.exit_code == 1
    assert outcome.status == "stopped"
    assert "не отвечает" in outcome.reason


@pytest.mark.asyncio
async def test_agy_unknown_model_warns(tmp_path, monkeypatch, capsys):
    from tests.loop import fakes as scripted

    def _player(role, kwargs):
        scripted.write_file(
            tmp_path / "repo", "src/greet.py", "def greet():\n    return 'hello'\n"
        )
        return scripted.PLAYER_DONE

    outcome, _argv = await _run_with_agy(
        {"judge_provider": "agy", "judge_model": "gemini-9.9-ultra", "_stub": "ok"},
        tmp_path, monkeypatch,
        [_player, scripted.judge_returns(scripted.ship())],
    )
    assert outcome.exit_code == 0, outcome.reason
    out = capsys.readouterr().out
    assert "gemini-9.9-ultra" in out
    assert "agy models" in out


@pytest.mark.parametrize(
    "line, ok",
    [
        ('{"status": "SUCCESS"}', True),
        ('{"result": {"status": "SUCCESS"}}', True),
        ('{"result": {"status": "ERROR", "error": "not logged in"}}', False),
    ],
)
def test_agy_preflight_reads_bare_and_wrapped_result(tmp_path, line, ok):
    from src.providers import agy as agy_mod

    script = tmp_path / "agy-json"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.stdout.write({line!r} + chr(10))\n"
    )
    script.chmod(0o755)
    got, detail = agy_mod.preflight(str(script), "gemini-3.8-flash", timeout_s=10)
    assert got is ok
    if not ok:
        assert "not logged in" in detail
