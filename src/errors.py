"""Base exceptions for tero."""

from __future__ import annotations


class TeroError(Exception):
    """Base exception for all tero application errors."""


class ProviderError(TeroError):
    """Base for provider / LLM related errors."""
