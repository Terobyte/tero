"""Tests for menu/config presets and provider defaults."""

from pathlib import Path

from src.config import KNOWN_PROVIDERS, Config, short_model_name
from src.menu import (
    CODEX_MODEL_PRESETS,
    MUSE_MODEL_PRESETS,
    PROVIDER_PRESETS,
    _save_global_default,
)


def test_codex_menu_presets_match_native_cli_models():
    assert CODEX_MODEL_PRESETS == {
        "GPT-6 Astra": "gpt-6-astra",
        "GPT-5.6 Sol": "gpt-5.6-sol",
        "GPT-5.6 Terra": "gpt-5.6-terra",
        "GPT-5.6 Luna": "gpt-5.6-luna",
        "Default (~/.codex/config.toml)": "",
        "Свой model ID…": "__custom__",
    }
    assert PROVIDER_PRESETS["Codex (native CLI)"] == "codex"


def test_codex_config_defaults_and_names_use_native_models():
    cfg = Config()

    assert cfg.player_provider == "muse"
    assert cfg.player_model == "muse-spark-1.3"
    assert cfg.judge_provider == "codex"
    assert cfg.judge_model == "gpt-5.6-terra"
    assert short_model_name("") == "DEFAULT"
    assert short_model_name("gpt-5.6-sol") == "SOL"
    assert short_model_name("gpt-5.6-terra") == "TERRA"
    assert short_model_name("gpt-5.6-luna") == "LUNA"
    assert short_model_name("gpt-6-astra") == "ASTRA"
    assert short_model_name("claude-sonnet-5") == "SONNET"
    assert short_model_name("claude-fable-5-1") == "FABLE"
    assert short_model_name("muse-spark-1.3") == "SPARK"
    assert short_model_name("composer-2.5") == "COMPOSER"
    assert short_model_name("o3") == "o3"
    assert short_model_name("o4-mini") == "o4-mini"


def test_provider_presets_drop_zai_and_kilo():
    assert "zai" not in PROVIDER_PRESETS.values()
    assert "kilo" not in PROVIDER_PRESETS.values()
    assert "black" not in PROVIDER_PRESETS.values()
    assert "turbo" not in PROVIDER_PRESETS.values()
    assert PROVIDER_PRESETS["Muse API (свой ключ)"] == "muse"
    assert PROVIDER_PRESETS["Cursor Headless"] == "cursor"
    assert list(KNOWN_PROVIDERS) == [
        "muse",
        "claude",
        "codex",
        "gemini",
        "opencode",
        "cursor",
    ]


def test_muse_provider_and_model_presets_are_listed():
    assert PROVIDER_PRESETS["Muse API (свой ключ)"] == "muse"
    assert MUSE_MODEL_PRESETS == {
        "Muse Spark 1.3 (свой ключ)": "muse-spark-1.3",
        "Muse Spark 1.2 (свой ключ)": "muse-spark-1.2",
    }
    assert "muse" in KNOWN_PROVIDERS


def test_opencode_presets_are_correct():
    assert PROVIDER_PRESETS["OpenCode (бесплатные)"] == "opencode"
    # free names rotate: nothing pinned, the menu reads `opencode models`
    from src.menu import _model_presets_for_provider

    assert list(_model_presets_for_provider("opencode").values()) == ["__custom__"]


def test_spark_free_short_name_differs_from_key_path():
    assert short_model_name("muse-spark-1.3") == "SPARK"
    assert short_model_name("opencode/muse-spark-1.3-contributor-free") == "SPARK$0"


def test_save_global_default_persists_core_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    cfg = Config(
        working_dir=str(tmp_path / "workspace"),
        player_provider="codex",
        player_model="gpt-5.4",
    )

    _save_global_default(cfg)

    saved = (Path(tmp_path) / ".tero" / "config.yaml").read_text()
    assert "player_provider: codex" in saved
    assert "player_model: gpt-5.4" in saved
