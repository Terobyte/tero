"""Red tests for provider-level bugs found in the 2026-10-24 code review."""

from __future__ import annotations

import pytest

from src.config import short_model_name
from src.providers.cursor import CursorProvider
from src.providers.subprocess_runner import SubprocessExit


@pytest.mark.asyncio
async def test_cursor_prompt_travels_via_stdin_not_argv(monkeypatch, tmp_path):
    """The cursor provider passes the whole prompt (the tester's includes the
    full phase diff) as a single argv element. Linux caps one argument at
    ~128 KB, so a large diff fails exec with E2BIG and the run halts as
    provider_unavailable. Every other provider reads the prompt from stdin
    (see the comment in gemini.py explaining exactly this limit)."""
    from src.providers import cursor as cursor_module

    captured: dict = {}

    async def fake_runner(cmd, working_dir, env=None, stdin_data=None, stall_timeout=0):
        captured["cmd"] = list(cmd)
        captured["stdin_data"] = stdin_data
        yield SubprocessExit(returncode=0, stderr=b"")

    monkeypatch.setattr(cursor_module, "run_subprocess_jsonl", fake_runner)

    prompt = "Ты tester. " + "x" * 200_000
    provider = CursorProvider()
    async for _ in provider.run(
        prompt=prompt,
        system_prompt="",
        working_dir=str(tmp_path),
        model="composer-2.5",
    ):
        pass

    assert captured["stdin_data"] == prompt.encode("utf-8")
    assert not any(prompt in str(part) for part in captured["cmd"])


def test_short_model_name_gemini_3_1_flash_lite():
    """gemini-3.1-flash-lite renders as GEMINI-3-LITE: the gemini-3-flash
    branch shadows it (copy-paste), while the sibling 3.5 keeps its version."""
    assert short_model_name("gemini-3.5-flash-lite") == "GEMINI-3.5-LITE"
    assert short_model_name("gemini-3.1-flash-lite") == "GEMINI-3.1-LITE"
