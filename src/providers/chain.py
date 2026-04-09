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

_MAX_BUFFER_MSGS = 10_000


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
        first_reason = ""
        for i, p in enumerate(self.providers):
            ok, reason = p.check_ready()
            if ok:
                return True, ""
            if i == 0:
                first_reason = reason
        return False, first_reason

    async def run(self, **kwargs):
        """Async generator: try providers in order, yield messages from first success.

        Messages are buffered until the provider finishes successfully.  This
        prevents partial output from a failing provider from leaking to the caller
        mixed with output from the fallback provider.
        """
        last_error = None

        for attempt in range(self.max_retries + 1):
            for provider_index, provider in enumerate(self.providers):
                buffer = []
                try:
                    async for msg in provider.run(**kwargs):
                        if len(buffer) >= _MAX_BUFFER_MSGS:
                            raise RuntimeError(
                                f"Provider output exceeded {_MAX_BUFFER_MSGS} messages; "
                                "possible runaway response"
                            )
                        buffer.append(msg)
                    # Provider completed without error — commit the buffer.
                    for msg in buffer:
                        yield msg
                    return  # Success — done
                except Exception as exc:
                    if not _is_rate_limit_error(exc):
                        raise  # non-rate-limit errors propagate immediately
                    last_error = exc
                    # Discard partial output; do not yield anything to the caller.
                    buffer.clear()
                    # Notify via callback if a next provider exists
                    if self.on_fallback:
                        failed = getattr(provider, "display_name", str(provider))
                        next_idx = provider_index + 1
                        if next_idx < len(self.providers):
                            next_p = self.providers[next_idx]
                            next_name = getattr(next_p, "display_name", str(next_p))
                            self.on_fallback(failed, next_name)
                    continue

            # All providers failed this round
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_wait_s)

        raise RateLimitError(
            f"All providers failed after {self.max_retries + 1} attempts: {last_error}"
        )
