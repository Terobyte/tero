"""--output-schema temp file must exist when codex starts and be gone after."""

import asyncio
import json
import os

import pytest

from src.providers import codex as codex_module
from src.providers.codex import CodexConfig, CodexProvider
from src.providers.subprocess_runner import SubprocessExit

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict"],
    "properties": {"verdict": {"type": "string"}},
}


def _run(provider, system_prompt, monkeypatch):
    seen = {}

    async def fake_runner(cmd, working_dir, env=None, stdin_data=None, **_):
        schema_path = cmd[cmd.index("--output-schema") + 1]
        seen["schema_path"] = schema_path
        seen["exists_at_spawn"] = os.path.exists(schema_path)
        if seen["exists_at_spawn"]:
            with open(schema_path) as fh:
                seen["schema"] = json.load(fh)
        seen["instructions_path"] = provider._temp_instructions_path
        yield SubprocessExit(returncode=0, stderr=b"")

    monkeypatch.setattr(codex_module, "run_subprocess_jsonl", fake_runner)

    async def consume():
        async for _ in provider.run(
            prompt="judge this",
            system_prompt=system_prompt,
            working_dir=".",
            output_schema=SCHEMA,
            read_only=True,
        ):
            pass

    asyncio.run(consume())
    return seen


@pytest.mark.parametrize("system_prompt", ["", "short", "x" * 100_000])
def test_output_schema_file_exists_when_codex_starts(system_prompt, monkeypatch):
    monkeypatch.delenv("CODEX_INSTRUCTIONS", raising=False)
    provider = CodexProvider(CodexConfig())

    seen = _run(provider, system_prompt, monkeypatch)

    assert seen["exists_at_spawn"], "schema file deleted before codex started"
    assert seen["schema"] == SCHEMA


def test_temp_files_removed_after_run(monkeypatch):
    monkeypatch.delenv("CODEX_INSTRUCTIONS", raising=False)
    provider = CodexProvider(CodexConfig())

    seen = _run(provider, "x" * 100_000, monkeypatch)

    assert not os.path.exists(seen["schema_path"])
    assert seen["instructions_path"] and not os.path.exists(seen["instructions_path"])
    assert provider._temp_schema_path is None
    assert provider._temp_instructions_path is None
