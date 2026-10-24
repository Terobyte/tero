"""Экзамен судей: одноразовый judge-вызов на каждый CLI (только stub-CLI, руками).

Ни один тест не запускает настоящий `agy`/CLI: все ответы отдают stub-скрипты
в формате своего CLI (как в tests/test_agy_provider.py, tests/test_opencode_provider.py).
"""

from __future__ import annotations

import json
import sys

import pytest

from tools.judge_exam import exam_adapter
from tools.judge_exam import run as run_mod

VALID_VERDICT = {
    "verdict": "SHIP",
    "previous": [],
    "findings": [],
    "user_question": "",
}

MODELS = {
    "agy": "gemini-3.8-flash-high",
    "claude": "claude-sonnet-5",
    "codex": "gpt-5.6-terra",
    "opencode": "opencode/mimo-v2-pro-free",
}


def _stub_script(tmp_path, name: str, body: str) -> str:
    script = tmp_path / name
    script.write_text(f"#!{sys.executable}\n{body}\n")
    script.chmod(0o755)
    return str(script)


def _valid_body(provider: str) -> str:
    payload = json.dumps(VALID_VERDICT)
    if provider == "agy":
        events = [
            {"event": "init", "conversation_id": "exam"},
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "structured_output": VALID_VERDICT,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        return (
            "import json, sys\n"
            f"_STREAM = {events!r}\n"
            "for _obj in _STREAM:\n"
            "    sys.stdout.write(json.dumps(_obj) + chr(10))\n"
        )
    if provider == "claude":
        return (
            "import json, sys\n"
            f"sys.stdout.write(json.dumps({{'type': 'text', 'text': {payload!r}}}) + chr(10))\n"
        )
    if provider == "codex":
        events = [
            {"type": "thread.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": payload},
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ]
        return (
            "import json, sys\n"
            f"_STREAM = {events!r}\n"
            "for _obj in _STREAM:\n"
            "    sys.stdout.write(json.dumps(_obj) + chr(10))\n"
        )
    if provider == "opencode":
        events = [{"type": "text", "part": {"type": "text", "text": payload}}]
        return (
            "import json, sys\n"
            f"_STREAM = {events!r}\n"
            "for _obj in _STREAM:\n"
            "    sys.stdout.write(json.dumps(_obj) + chr(10))\n"
        )
    raise AssertionError(f"unknown provider: {provider}")


def _factory(provider: str, command: str):
    from src.providers import create_provider

    def factory(name: str):
        assert name == provider, f"unexpected provider: {name}"
        return create_provider(name, {"command": command})

    return factory


@pytest.mark.parametrize("provider", ["agy", "claude", "codex", "opencode"])
def test_adapter_valid_verdict_each_cli(tmp_path, provider):
    cmd = _stub_script(tmp_path, f"stub-{provider}", _valid_body(provider))
    result = exam_adapter(provider, MODELS[provider], factory=_factory(provider, cmd))
    assert result["provider"] == provider
    assert result["model"] == MODELS[provider]
    assert result["ok"] is True
    assert result["verdict"] == "SHIP"
    assert result["error"] == ""
    assert result["seconds"] >= 0


def test_adapter_garbage_is_not_ok(tmp_path):
    cmd = _stub_script(
        tmp_path,
        "stub-claude-garbage",
        "import sys\nsys.stdout.write('not json at all\\nstill not json\\n')\n",
    )
    result = exam_adapter("claude", MODELS["claude"], factory=_factory("claude", cmd))
    assert result["ok"] is False
    assert "не по схеме" in result["error"]
    assert result["verdict"] == ""


def test_adapter_table_output(tmp_path, capsys, monkeypatch):
    class _FakeProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield json.dumps(VALID_VERDICT)

    monkeypatch.setattr(run_mod, "FACTORY", lambda _name: _FakeProvider())
    assert run_mod.main(["--adapters"]) == 0
    out = capsys.readouterr().out
    for provider, _model in run_mod.DEFAULT_JUDGES:
        assert provider in out, f"нет строки таблицы для {provider}:\n{out}"
    assert "SHIP" in out


def test_adapter_table_judge_filter(tmp_path, capsys, monkeypatch):
    class _FakeProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield json.dumps(VALID_VERDICT)

    monkeypatch.setattr(run_mod, "FACTORY", lambda _name: _FakeProvider())
    assert run_mod.main(["--adapters", "--judge", "claude:claude-sonnet-5"]) == 0
    out = capsys.readouterr().out
    assert "claude" in out
    assert "codex" not in out


def test_adapter_table_without_adapters_errors(capsys):
    assert run_mod.main(["--judge", "claude:claude-sonnet-5"]) == 2
    assert run_mod.main([]) == 2
