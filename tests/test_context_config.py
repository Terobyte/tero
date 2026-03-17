"""Tests for context management config fields."""

from src.config import Config, resolve_config


def test_context_defaults():
    """Context management fields have sensible defaults."""
    cfg = Config()
    assert cfg.context_limit == 110_000
    assert cfg.compact_threshold == 0.85
    assert cfg.max_continuation_attempts == 2


def test_context_env_override(monkeypatch):
    """Context fields can be overridden via environment variables."""
    monkeypatch.setenv("G3_CONTEXT_LIMIT", "80000")
    monkeypatch.setenv("G3_COMPACT_THRESHOLD", "0.70")
    monkeypatch.setenv("G3_MAX_CONTINUATION_ATTEMPTS", "3")
    cfg = resolve_config({"working_dir": "."})
    assert cfg.context_limit == 80_000
    assert cfg.compact_threshold == 0.70
    assert cfg.max_continuation_attempts == 3
