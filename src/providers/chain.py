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
        on_fallback=None,
    ):
        if not providers:
            raise ValueError("ProviderChain requires at least one provider")
        self.providers = providers
        self.retry_wait_s = retry_wait_s
        self.max_retries = max_retries
        self.on_fallback = on_fallback

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
                    # Notify via callback if a next provider exists
                    if self.on_fallback:
                        failed = getattr(provider, "display_name", str(provider))
                        next_idx = self.providers.index(provider) + 1
                        if next_idx < len(self.providers):
                            next_p = self.providers[next_idx]
                            next_name = getattr(next_p, "display_name", str(next_p))
                            self.on_fallback(failed, next_name)
                    # All errors trigger fallback (rate limit, transient, etc.)
                    continue

            # All providers failed this round
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_wait_s)

        raise RateLimitError(
            f"All providers failed after {self.max_retries + 1} attempts: {last_error}"
        )
