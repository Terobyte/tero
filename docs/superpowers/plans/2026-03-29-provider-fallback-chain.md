# Provider Fallback Chain — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to execute this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a provider fallback chain that automatically switches to the next provider on rate limit (429) or transient errors, then retries from the top if all are exhausted.

**Architecture:** `ProviderChain` wraps an ordered list of providers and exposes the same `run()` / `check_ready()` / `display_name` interface via duck typing. It operates at the `_run_turn()` level in `coach_player.py` — callers don't know they're talking to a chain. Two-layer design: the chain handles **infrastructure failures** (rate limits, transient errors), while the existing `coach_fallback_provider` handles **quality failures** (NoVerdict). These are complementary layers.

**Tech Stack:** Python 3.12+, asyncio, pytest-asyncio

---

## Chunk 1: Core — ProviderChain + Config

### Task 1: ProviderChain core (TDD)

**Files:**
- Create: `src/providers/chain.py`
- Create: `tests/test_chain.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_chain.py`:
```python
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


def test_empty_providers_raises():
    """Chain with empty list raises ValueError."""
    with pytest.raises(ValueError, match="at least one"):
        ProviderChain([])
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/test_chain.py -v
```
Expected: FAIL (module `src.providers.chain` does not exist)

- [ ] **Step 3: Implement `src/providers/chain.py`**

Create `src/providers/chain.py`:
```python
"""Provider chain with automatic fallback on rate limit or transient errors.

On 429/rate limit from provider N, tries provider N+1.
If all providers exhausted, waits retry_wait_s and retries from the top.
After max_retries full cycles, raises RateLimitError.

The chain exposes the same duck-typed interface as individual providers:
  - run() -> async generator of messages
  - check_ready() -> (bool, str)
  - display_name -> str
"""

import asyncio


class RateLimitError(Exception):
    """Raised when all providers in the chain are exhausted."""


def _is_rate_limit_error(exc: Exception) -> bool:
    """Heuristic: does this exception indicate a rate limit?"""
    msg = str(exc).lower()
    return any(kw in msg for kw in ("429", "rate", "limit", "too many requests"))


class ProviderChain:
    """Ordered chain of providers with automatic fallback.

    Yields messages from the first successful provider.
    On error (rate limit or transient), tries next provider.
    On total exhaustion, retries from the top after retry_wait_s.
    """

    def __init__(
        self,
        providers: list,
        retry_wait_s: float = 60.0,
        max_retries: int = 2,
    ):
        if not providers:
            raise ValueError("ProviderChain requires at least one provider")
        self.providers = providers
        self.retry_wait_s = retry_wait_s
        self.max_retries = max_retries

    @property
    def display_name(self) -> str:
        names = [getattr(p, "display_name", str(p)) for p in self.providers]
        return " -> ".join(names)

    def check_ready(self) -> tuple[bool, str]:
        """Return True if at least one provider is ready."""
        for p in self.providers:
            ok, reason = p.check_ready()
            if ok:
                return True, ""
        # All failed — return first reason
        return self.providers[0].check_ready()

    async def run(self, **kwargs):
        """Async generator: try providers in order, yield messages from first success."""
        last_error = None

        for attempt in range(self.max_retries + 1):
            for provider in self.providers:
                try:
                    # Stream messages from this provider
                    async for msg in provider.run(**kwargs):
                        yield msg
                    return  # Success — done
                except Exception as exc:
                    last_error = exc
                    # All errors trigger fallback (rate limit, transient, etc.)
                    continue

            # All providers failed this round
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_wait_s)

        raise RateLimitError(
            f"All providers failed after {self.max_retries + 1} attempts: {last_error}"
        )
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/test_chain.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/providers/chain.py tests/test_chain.py
git commit -m "feat: ProviderChain with fallback on rate limit and transient errors"
```

---

### Task 2: Config fields for fallback chains

**Files:**
- Modify: `src/config.py` — add chain config fields to `Config` dataclass and `resolve_config()`

- [ ] **Step 1: Add fields to Config dataclass**

In `src/config.py`, add these fields to the `Config` dataclass (after the `coach_fallback_model` field around line 304):

```python
    # Provider fallback chain
    player_fallback_chain: str = ""   # comma-separated: "turbo,zai"
    coach_fallback_chain: str = ""    # comma-separated: "black,turbo"
    chain_retry_wait_s: float = 60.0
    chain_max_retries: int = 2
```

- [ ] **Step 2: Add env var mapping in `resolve_config()`**

In the `env_map` dict inside `resolve_config()`, add:

```python
        "G3_PLAYER_FALLBACK_CHAIN": ("player_fallback_chain", str),
        "G3_COACH_FALLBACK_CHAIN": ("coach_fallback_chain", str),
        "G3_CHAIN_RETRY_WAIT_S": ("chain_retry_wait_s", float),
        "G3_CHAIN_MAX_RETRIES": ("chain_max_retries", int),
```

- [ ] **Step 3: Run existing tests to verify no breakage**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/test_cli.py -v
```
Expected: ALL PASS (new fields have defaults, nothing breaks)

- [ ] **Step 4: Commit**

```bash
git add src/config.py
git commit -m "feat: add provider chain config fields (player/coach fallback chain)"
```

---

## Chunk 2: Integration into coach_player + Streaming UI

### Task 3: Integration into `_run_turn()` via `_build_chain_for_role()`

**Files:**
- Modify: `src/coach_player.py` — add `_build_chain_for_role()`, change `_run_turn()` line 1087
- Create: `tests/test_chain_integration.py`

- [ ] **Step 1: Write failing integration test**

Create `tests/test_chain_integration.py`:
```python
"""Integration test: _build_chain_for_role returns chain when configured."""

from unittest.mock import MagicMock, patch

from src.providers.chain import ProviderChain


def test_build_chain_returns_chain_when_configured():
    """When fallback_chain is set, _build_chain_for_role returns a ProviderChain."""
    from src.config import Config
    from src.coach_player import CoachPlayerSession

    config = Config(
        working_dir="/tmp",
        coach_provider="black",
        coach_fallback_chain="turbo,zai",
    )

    player = object.__new__(CoachPlayerSession)
    player.config = config
    player._provider_cache = {}

    # Mock _get_or_create_provider to return fake providers
    fake_providers = {
        "turbo": MagicMock(display_name="turbo"),
        "zai": MagicMock(display_name="zai"),
    }
    player._get_or_create_provider = lambda name: fake_providers[name]

    result = player._build_chain_for_role("coach")
    assert isinstance(result, ProviderChain)
    assert result.display_name == "turbo -> zai"


def test_build_chain_returns_bare_provider_when_no_chain():
    """When fallback_chain is empty, _build_chain_for_role returns bare provider."""
    from src.config import Config
    from src.coach_player import CoachPlayerSession

    config = Config(
        working_dir="/tmp",
        coach_provider="black",
        coach_fallback_chain="",  # no chain
    )

    player = object.__new__(CoachPlayerSession)
    player.config = config
    player._provider_cache = {}

    fake_provider = MagicMock(display_name="black")
    player._get_or_create_provider = lambda name: fake_provider

    result = player._build_chain_for_role("coach")
    # Should be the bare provider, NOT a ProviderChain
    assert not isinstance(result, ProviderChain)
    assert result == fake_provider
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/test_chain_integration.py -v
```
Expected: FAIL (attribute error — `_build_chain_for_role` doesn't exist)

- [ ] **Step 3: Add `_build_chain_for_role()` to `coach_player.py`**

At the top of `coach_player.py`, add import:
```python
from src.providers.chain import ProviderChain
```

Add method after `_provider_for_role()` (after line 219):

```python
    def _build_chain_for_role(self, role: str):
        """Return a ProviderChain if fallback_chain is configured, else bare provider.

        The chain wraps multiple providers and yields from the first one that works.
        When no chain is configured, returns the bare provider (no wrapping).
        """
        chain_str = ""
        if role == "player":
            chain_str = self.config.player_fallback_chain
        elif role == "coach":
            chain_str = self.config.coach_fallback_chain

        if not chain_str:
            return self._provider_for_role(role)

        provider_names = [n.strip() for n in chain_str.split(",") if n.strip()]
        if not provider_names:
            return self._provider_for_role(role)

        providers = [self._get_or_create_provider(name) for name in provider_names]
        return ProviderChain(
            providers,
            retry_wait_s=self.config.chain_retry_wait_s,
            max_retries=self.config.chain_max_retries,
        )
```

- [ ] **Step 4: Modify `_run_turn()` to use chain**

In `src/coach_player.py`, change line 1087 from:

```python
        provider = provider_override or self._provider_for_role(role)
```

to:

```python
        provider = provider_override or self._build_chain_for_role(role)
```

- [ ] **Step 5: Run integration tests — verify they pass**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/test_chain_integration.py -v
```
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/coach_player.py tests/test_chain_integration.py
git commit -m "feat: integrate ProviderChain into _run_turn via _build_chain_for_role"
```

---

### Task 4: Streaming UI — fallback notification

**Files:**
- Modify: `src/streaming.py` — add `print_provider_fallback()` function
- Modify: `src/coach_player.py` — call fallback notification when chain switches providers

- [ ] **Step 1: Add `print_provider_fallback()` to `src/streaming.py`**

Add at the end of `src/streaming.py`:

```python
def print_provider_fallback(failed_name: str, next_name: str):
    """Print fallback notification when switching providers."""
    print(
        f"\n  {BOLD}{YELLOW}⚠ Provider fallback: {failed_name} failed → trying {next_name}{RESET}",
        flush=True,
    )
```

- [ ] **Step 2: Hook into ProviderChain for notifications**

In `src/providers/chain.py`, add an optional callback parameter to `__init__` and call it on fallback:

In `ProviderChain.__init__`, add `on_fallback=None` parameter:

```python
    def __init__(
        self,
        providers: list,
        retry_wait_s: float = 60.0,
        max_retries: int = 2,
        on_fallback=None,
    ):
```

Store it:
```python
        self.on_fallback = on_fallback
```

In `run()`, inside the except block, add notification:

```python
                except Exception as exc:
                    last_error = exc
                    if self.on_fallback:
                        failed = getattr(provider, "display_name", str(provider))
                        next_idx = self.providers.index(provider) + 1
                        if next_idx < len(self.providers):
                            next_p = self.providers[next_idx]
                            next_name = getattr(next_p, "display_name", str(next_p))
                            self.on_fallback(failed, next_name)
                    continue
```

- [ ] **Step 3: Wire callback in `_build_chain_for_role()`**

In `src/coach_player.py`, update `_build_chain_for_role` to pass the callback:

```python
        from src.streaming import print_provider_fallback

        return ProviderChain(
            providers,
            retry_wait_s=self.config.chain_retry_wait_s,
            max_retries=self.config.chain_max_retries,
            on_fallback=print_provider_fallback,
        )
```

- [ ] **Step 4: Run all chain tests**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/test_chain.py tests/test_chain_integration.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/providers/chain.py src/streaming.py src/coach_player.py
git commit -m "feat: streaming UI notification on provider fallback"
```

---

## Chunk 3: Regression + config wiring

### Task 5: Regression tests — ensure existing tests pass

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/ -v --timeout=120
```
Expected: ALL PASS (new fields have defaults, `_build_chain_for_role` returns bare provider when chain is empty)

- [ ] **Step 2: Verify YAML config works**

Add to `.g3/config.yaml` for testing (don't commit):

```yaml
defaults:
  player_fallback_chain: "turbo,zai"
  coach_fallback_chain: "turbo"
```

Run a dry invocation to verify config loads:
```bash
python -c "from src.config import resolve_config; c = resolve_config({'working_dir': '.'}); print(f'player chain: {c.player_fallback_chain!r}, coach chain: {c.coach_fallback_chain!r}')"
```
Expected: prints the configured chains

---

## Summary

| Task | What | Files | Type |
|------|------|-------|------|
| 1 | ProviderChain core (TDD) | `src/providers/chain.py`, `tests/test_chain.py` | Core |
| 2 | Config fields | `src/config.py` | Config |
| 3 | Integration into `_run_turn()` | `src/coach_player.py`, `tests/test_chain_integration.py` | Integration |
| 4 | Streaming UI fallback notification | `src/streaming.py`, `src/providers/chain.py`, `src/coach_player.py` | UI |
| 5 | Regression tests | — | Verification |
