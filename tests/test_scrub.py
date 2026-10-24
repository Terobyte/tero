"""scrub_secrets: вычищает ключи из окружения, но не короткие флаги."""

from src.errors import scrub_secrets


def test_short_env_value_is_not_a_secret(monkeypatch):
    monkeypatch.setenv("SOME_FEATURE_KEY", "1")
    monkeypatch.setenv("USE_TOKEN", "true")
    text = "вопрос 1-1, refs/tero/run/round01-compact1, true"
    assert scrub_secrets(text) == text


def test_long_env_value_is_redacted(monkeypatch):
    monkeypatch.setenv("META_MODEL_API_KEY", "sk-abcdef123456")
    assert scrub_secrets("ключ sk-abcdef123456 в логе") == "ключ [redacted] в логе"
