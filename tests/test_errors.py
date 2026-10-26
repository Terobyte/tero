"""Tests for src/errors.py — base exceptions."""

import pytest

from src.errors import ProviderError, TeroError, classify_error, strip_mcp_noise
from src.providers.subprocess_runner import raise_for_exit


class TestHierarchy:
    def test_provider_error_is_tero_error(self):
        assert issubclass(ProviderError, TeroError)

    def test_catch_all_with_tero_error(self):
        with pytest.raises(TeroError):
            raise ProviderError("boom")


def test_rmcp_noise_stripped_from_error_text():
    noisy = "rmcp::server failed AuthRequired: bad key\nreal failure here"
    cleaned = strip_mcp_noise(noisy)
    assert "rmcp::" not in cleaned
    assert "AuthRequired" not in cleaned
    assert "real failure here" in cleaned
    with pytest.raises(ProviderError) as excinfo:
        raise_for_exit("codex", 1, noisy.encode())
    assert "rmcp::" not in str(excinfo.value)


def test_classify_error_old_markers_unchanged():
    assert classify_error(None, "401 unauthorized") == "auth"
    assert classify_error(None, "invalid api key") == "auth"
    assert classify_error(None, "quota exceeded, usage limit hit") == "quota"
    assert classify_error(None, "econnreset by peer") == "network"
    assert classify_error(None, "429 rate limit, try later") == "network"
    assert classify_error(None, "something totally weird") == "other"
