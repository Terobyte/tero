"""Tests for ProviderChain fallback logic."""

import asyncio
import pytest

from src.providers.chain import ProviderChain, RateLimitError


class FakeProvider:
    """Mock provider with configurable failures."""

    def __init__(self, name: str, fail_count: int = 0, fail_msg: str = "rate limited"):
        self.name = name
        self.fail_count = fail_count
        self.fail_msg = fail_msg
        self.calls = 0
        self._display_name = name

    @property
    def display_name(self) -> str:
        return self._display_name

    def check_ready(self) -> tuple[bool, str]:
        return True, ""

    async def run(self, prompt="", system_prompt="", working_dir=".", max_turns=1, model=""):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise RuntimeError(f"429 {self.fail_msg}")
        yield type("Msg", (), {"content": f"response from {self.name}", "role": "assistant"})()


@pytest.mark.asyncio
async def test_first_provider_succeeds():
    """First provider works — use it, don't try others."""
    p1 = FakeProvider("alpha")
    p2 = FakeProvider("beta")
    chain = ProviderChain([p1, p2])

    messages = []
    async for msg in chain.run(prompt="test"):
        messages.append(msg)

    assert any("alpha" in str(getattr(m, "content", "")) for m in messages)
    assert p1.calls == 1
    assert p2.calls == 0  # never tried


@pytest.mark.asyncio
async def test_fallback_on_rate_limit():
    """First provider rate limited — fall back to second."""
    p1 = FakeProvider("alpha", fail_count=999)
    p2 = FakeProvider("beta")
    chain = ProviderChain([p1, p2])

    messages = []
    async for msg in chain.run(prompt="test"):
        messages.append(msg)

    assert any("beta" in str(getattr(m, "content", "")) for m in messages)
    assert p1.calls == 1
    assert p2.calls == 1


@pytest.mark.asyncio
async def test_fallback_on_generic_error():
    """Non-rate-limit error also triggers fallback."""
    p1 = FakeProvider("alpha", fail_count=1, fail_msg="connection reset")
    p2 = FakeProvider("beta")
    chain = ProviderChain([p1, p2])

    messages = []
    async for msg in chain.run(prompt="test"):
        messages.append(msg)

    assert any("beta" in str(getattr(m, "content", "")) for m in messages)


@pytest.mark.asyncio
async def test_chain_exhausted_raises():
    """All providers fail — retry once, then raise."""
    p1 = FakeProvider("alpha", fail_count=999)
    p2 = FakeProvider("beta", fail_count=999)
    chain = ProviderChain([p1, p2], retry_wait_s=0.01, max_retries=1)

    with pytest.raises(RateLimitError, match="All providers failed"):
        async for _ in chain.run(prompt="test"):
            pass


@pytest.mark.asyncio
async def test_chain_display_name():
    """Chain display_name shows the chain."""
    chain = ProviderChain([FakeProvider("a"), FakeProvider("b")])
    assert "a" in chain.display_name
    assert "b" in chain.display_name


@pytest.mark.asyncio
async def test_chain_check_ready():
    """check_ready returns True if any provider is ready."""
    chain = ProviderChain([FakeProvider("a")])
    ok, reason = chain.check_ready()
    assert ok is True


@pytest.mark.asyncio
async def test_single_provider_chain():
    """Chain with one provider works like bare provider."""
    p = FakeProvider("solo")
    chain = ProviderChain([p])

    messages = []
    async for msg in chain.run(prompt="test"):
        messages.append(msg)

    assert p.calls == 1
