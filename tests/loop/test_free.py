"""Free opencode models: the user picks one, tero only checks it is still free."""

import pytest

from src.config import Config, ConfigError
from src.loop.free import check_free_models

LISTED = ["opencode/gpt-oss-120b-free", "opencode/mimo-v3-free", "opencode/paid-pro"]


def _cfg(**kw) -> Config:
    data = {
        "player_provider": "opencode",
        "player_model": "opencode/mimo-v3-free",
        "judge_provider": "claude",
        "judge_model": "opus",
        "tester_provider": "gemini",
        "tester_model": "gemini-3.8-flash",
        "escalation_provider": "",
        "escalation_model": "",
    }
    data.update(kw)
    return Config(**data)


def test_listed_free_model_passes():
    check_free_models(_cfg(), lambda: LISTED)


def test_rotated_out_free_model_stops_with_current_list():
    with pytest.raises(ConfigError) as err:
        check_free_models(_cfg(player_model="opencode/gone-free"), lambda: LISTED)
    text = str(err.value)
    assert "player" in text and "opencode/gone-free" in text
    assert "opencode/mimo-v3-free" in text and "opencode/gpt-oss-120b-free" in text
    assert "opencode/paid-pro" not in text


def test_every_stale_role_is_named():
    cfg = _cfg(
        player_model="opencode/gone-free",
        judge_provider="opencode",
        judge_model="openrouter/old:free",
    )
    with pytest.raises(ConfigError) as err:
        check_free_models(cfg, lambda: LISTED)
    assert "player" in str(err.value) and "judge" in str(err.value)


def test_no_list_means_no_verdict():
    check_free_models(_cfg(player_model="opencode/pinned-free"), lambda: [])


def test_paid_or_empty_opencode_model_is_not_checked():
    def boom():
        raise AssertionError("opencode models called")

    check_free_models(_cfg(player_model="opencode/paid-pro"), boom)
    check_free_models(_cfg(player_model=""), boom)
    check_free_models(_cfg(player_provider="codex", player_model="gpt-5.6-terra"), boom)
