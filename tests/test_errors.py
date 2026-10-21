"""Tests for src/errors.py — base exceptions."""

import pytest

from src.errors import ProviderError, TeroError


class TestHierarchy:
    def test_provider_error_is_tero_error(self):
        assert issubclass(ProviderError, TeroError)

    def test_catch_all_with_tero_error(self):
        with pytest.raises(TeroError):
            raise ProviderError("boom")
