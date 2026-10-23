"""Виды ошибок провайдера: классификация и таймаут без «провайдер недоступен»."""

from __future__ import annotations

import asyncio

import pytest

from src.errors import ProviderStalled, classify_error
from src.loop.agents import LiveAgents
from src.loop.schemas import ValidateCtx


@pytest.mark.parametrize(
    ("exc", "text", "expected"),
    [
        (TimeoutError("timed out"), "", "timeout"),
        (asyncio.TimeoutError(), "", "timeout"),
        (ProviderStalled("нет вывода 5 с"), "", "stall"),
        (None, "Unauthorized", "auth"),
        (RuntimeError("HTTP 401"), "HTTP 401", "auth"),
        (None, "please log in to continue", "auth"),
        (None, "quota exceeded", "quota"),
        (None, "insufficient credits", "quota"),
        (ConnectionError("reset by peer"), "", "network"),
        (None, "ECONNRESET", "network"),
        (None, "429 Too Many Requests", "network"),
        (None, "something completely different", "other"),
    ],
)
def test_classify_error_table(exc, text, expected):
    assert classify_error(exc, text) == expected


@pytest.mark.asyncio
async def test_timeout_is_not_provider_unavailable(tmp_path, capsys):
    class _HangingProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            await asyncio.sleep(30)
            yield "{}"  # pragma: no cover - wait_for cancels the sleep first

    agents = LiveAgents(
        str(tmp_path), factory=lambda _n: _HangingProvider(), retry_wait_s=0
    )
    data = await agents.run_role(
        "player",
        provider="opencode",
        model="m",
        prompt="P",
        timeout=1,
        ctx=ValidateCtx(),
    )
    assert data["__unavailable__"] is True
    assert data["kind"] == "timeout"
    out = capsys.readouterr().out
    assert "провайдер недоступен" not in out
    assert "не уложился в таймаут" in out
    assert agents.last_error == data["error"]
    assert agents.last_error
    assert agents.last_error != "TimeoutError"
