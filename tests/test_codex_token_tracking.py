"""Tests for Codex token tracking from SSE usage field."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.providers.codex import CodexProvider, CodexConfig


def _make_sse_lines(content: str, prompt_tokens: int) -> list[str]:
    """Create SSE lines for testing."""
    return [
        f"data: {json.dumps({'choices': [{'delta': {'content': content}, 'finish_reason': None}]})}",
        f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': 10}})}",
        "data: [DONE]",
    ]


class AsyncLineIterator:
    """Async iterator for SSE lines."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.lines):
            raise StopAsyncIteration
        line = self.lines[self.index]
        self.index += 1
        return line


class MockStreamResponse:
    """Mock streaming response with aiter_lines."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    def raise_for_status(self):
        pass

    def aiter_lines(self):
        return AsyncLineIterator(self._lines)


@pytest.mark.asyncio
async def test_last_input_tokens_stored():
    """Codex provider stores prompt_tokens from SSE usage field."""
    provider = CodexProvider(CodexConfig(api_url="http://localhost:9999"))
    lines = _make_sse_lines("hello", prompt_tokens=85000)
    mock_response = MockStreamResponse(lines)

    @asynccontextmanager
    async def mock_stream(*args, **kwargs):
        yield mock_response

    @asynccontextmanager
    async def mock_client(*args, **kwargs):
        yield MagicMock(stream=mock_stream)

    with patch("httpx.AsyncClient", mock_client):
        chunks = []
        async for c in provider.run("hi", "", ".", 10):
            chunks.append(c)

    assert provider._last_input_tokens == 85000
