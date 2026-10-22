"""Picking free opencode models at run start."""

import pytest

from src.config import Config, ConfigError, family_key
from src.loop.free import resolve_free_models

FREE = ["opencode/gpt-oss-120b-free", "opencode/mimo-v3-free", "opencode/paid-pro"]


def _cfg(**kw) -> Config:
    data = {
        "player_provider": "codex",
        "player_model": "gpt-5.6-terra",
        "judge_provider": "opencode",
        "judge_model": "free",
        "tester_provider": "gemini",
        "tester_model": "gemini-3.8-flash",
        "escalation_provider": "",
        "escalation_model": "",
    }
    data.update(kw)
    return Config(**data)


def test_free_judge_skips_player_family():
    cfg, notes = resolve_free_models(_cfg(), lambda: FREE)
    assert cfg.judge_model == "opencode/mimo-v3-free"
    assert family_key(f"opencode:{cfg.judge_model}") != family_key("codex:gpt-5.6-terra")
    assert any("mimo-v3-free" in n for n in notes)


def test_empty_opencode_model_means_free():
    cfg, _ = resolve_free_models(_cfg(judge_model=""), lambda: FREE)
    assert cfg.judge_model == "opencode/mimo-v3-free"


def test_rotated_out_free_model_is_replaced():
    cfg, notes = resolve_free_models(
        _cfg(player_provider="opencode", player_model="opencode/gone-free",
             judge_provider="claude", judge_model="opus"),
        lambda: FREE,
    )
    assert cfg.player_model == "opencode/gpt-oss-120b-free"
    assert any("gone-free" in n for n in notes)


def test_pinned_free_model_kept_when_list_unavailable():
    cfg, _ = resolve_free_models(
        _cfg(player_provider="opencode", player_model="opencode/pinned-free",
             judge_provider="claude", judge_model="opus"),
        lambda: [],
    )
    assert cfg.player_model == "opencode/pinned-free"


def test_free_keyword_without_any_free_model_fails():
    with pytest.raises(ConfigError, match="бесплатн"):
        resolve_free_models(_cfg(), lambda: ["opencode/paid-pro"])


def test_free_judge_with_only_player_family_fails():
    with pytest.raises(ConfigError, match="openai"):
        resolve_free_models(_cfg(), lambda: ["opencode/gpt-oss-120b-free"])


def test_no_free_roles_does_not_list():
    def boom():
        raise AssertionError("opencode models called")

    cfg = _cfg(judge_provider="claude", judge_model="opus")
    assert resolve_free_models(cfg, boom)[0] == cfg
