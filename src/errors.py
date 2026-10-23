"""Base exceptions for tero."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any


class TeroError(Exception):
    """Base exception for all tero application errors."""


class ProviderError(TeroError):
    """Base for provider / LLM related errors."""


class ProviderStalled(ProviderError):
    """No output from a streaming provider for too long; caller killed it."""


ERROR_TEXT_MARKERS: dict[str, tuple[str, ...]] = {
    "auth": (
        "unauthorized",
        "401",
        "403",
        "not logged in",
        "please log in",
        "login required",
        "authentication",
        "invalid api key",
        "token expired",
        "credentials",
    ),
    "quota": (
        "quota",
        "usage limit",
        "insufficient credits",
        "billing",
        "exceeded your current",
    ),
    "network": (
        "econnreset",
        "etimedout",
        "econnrefused",
        "connection reset",
        "connection refused",
        "network",
        "502",
        "503",
        "504",
        "429",
        "rate limit",
        "temporarily unavailable",
    ),
}


def classify_error(exc: BaseException | None, text: str) -> str:
    """One of auth | quota | network | timeout | stall | other."""
    if isinstance(exc, ProviderStalled):
        return "stall"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "network"
    hay = f"{text or ''} {exc or ''}".lower()
    for kind in ("auth", "quota", "network"):
        if any(marker in hay for marker in ERROR_TEXT_MARKERS[kind]):
            return kind
    return "other"


def scrub_data(value: Any) -> Any:
    """Scrub strings inside nested containers so lists/dicts in stream/state/ledger stay clean."""
    if isinstance(value, str):
        return scrub_secrets(value)
    if isinstance(value, dict):
        return {k: scrub_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        scrubbed = [scrub_data(v) for v in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    return value


def scrub_secrets(text: str) -> str:
    """Mask key/token values so they never reach console, state, or ledger."""
    if not text:
        return text
    redacted = text
    for name, value in os.environ.items():
        folded = name.upper()
        if ("KEY" in folded or "TOKEN" in folded or "SECRET" in folded) and value:
            redacted = redacted.replace(value, "[redacted]")
    # Generic long key/token assignments: `key=...`, `token: ...`, bearer etc.
    redacted = re.sub(
        r"(?i)\b((?:api[_-]?key|auth[_-]?token|access[_-]?token|bearer|secret)\s*[:=]\s*)([\"']?)([A-Za-z0-9_\-./+]{8,})\2",
        r"\1\2[redacted]\2",
        redacted,
    )
    return redacted
