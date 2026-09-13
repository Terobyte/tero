"""Tests for menu/config presets and provider defaults."""

from pathlib import Path

from src.cli_entry import PROVIDER_CHOICES
from src.config import Config, get_context_window, short_model_name
from src.menu import (
    CODEX_MODEL_PRESETS,
    MUSE_MODEL_PRESETS,
    OPENCODE_MODEL_PRESETS,
    PROVIDER_PRESETS,
    _save_global_default,
)
from src.runtime_controls import MODEL_PRESETS


def test_codex_menu_presets_match_native_cli_models():
    assert CODEX_MODEL_PRESETS == {
        "GPT-6 Astra": "gpt-6-astra",
        "GPT-5.6 Sol": "gpt-5.6-sol",
        "GPT-5.6 Terra": "gpt-5.6-terra",
        "GPT-5.6 Luna": "gpt-5.6-luna",
        "Default (~/.codex/config.toml)": "",
    }
    assert PROVIDER_PRESETS["Codex (native CLI)"] == "codex"


def test_runtime_controls_offer_native_codex_presets():
    preset_labels = [label for label, _, _ in MODEL_PRESETS]
    assert "GPT-5.6 Sol" in preset_labels
    assert "GPT-5.6 Terra" in preset_labels
    assert "GPT-5.6 Luna" in preset_labels
    assert "GPT-6 Astra" in preset_labels
    assert "Spark 1.3" in preset_labels
    assert "GLM-5.1" not in preset_labels


def test_codex_config_defaults_and_names_use_native_models():
    cfg = Config()

    assert cfg.player_provider == "muse"
    assert cfg.player_model == "muse-spark-1.3"
    assert cfg.coach_provider == "muse"
    assert cfg.batch_judge_provider == "codex"
    assert cfg.batch_judge_model == "gpt-5.6-terra"
    assert cfg.ldb_tester_provider == "gemini"
    assert cfg.ldb_tester_model == "gemini-3.8-flash"
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
    assert get_context_window("gpt-5.6-sol") == 1_048_576
    assert get_context_window("gpt-6-astra") == 1_048_576
    assert get_context_window("muse-spark-1.3") == 1_048_576
    assert get_context_window("composer-2.5") == 200_000
    assert get_context_window("claude-haiku-4-5") == 200_000
    assert get_context_window("o3") == 128_000
    assert get_context_window("o4-mini") == 128_000


def test_provider_presets_drop_zai_and_kilo():
    assert "zai" not in PROVIDER_PRESETS.values()
    assert "kilo" not in PROVIDER_PRESETS.values()
    assert "black" not in PROVIDER_PRESETS.values()
    assert "turbo" not in PROVIDER_PRESETS.values()
    assert PROVIDER_PRESETS["Muse Code (Spark)"] == "muse"
    assert PROVIDER_PRESETS["Cursor Headless"] == "cursor"
    assert PROVIDER_CHOICES == [
        "muse",
        "claude",
        "codex",
        "gemini",
        "opencode",
        "cursor",
    ]


def test_muse_provider_and_model_presets_are_listed():
    assert PROVIDER_PRESETS["Muse Code (Spark)"] == "muse"
    assert MUSE_MODEL_PRESETS == {
        "Muse Spark 1.3": "muse-spark-1.3",
        "Muse Spark 1.2": "muse-spark-1.2",
    }
    assert "muse" in PROVIDER_CHOICES


def test_opencode_presets_are_correct():
    assert PROVIDER_PRESETS["OpenCode (MIMO/Kimi)"] == "opencode"
    assert OPENCODE_MODEL_PRESETS["MiniMax M2.5 (free)"] == "opencode/minimax-m2.5-free"
    assert "zai/glm-5.1" not in OPENCODE_MODEL_PRESETS.values()


def test_save_global_default_persists_core_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    cfg = Config(
        working_dir=str(tmp_path / "workspace"),
        player_provider="codex",
        player_model="gpt-5.4",
    )

    _save_global_default(cfg)

    saved = (Path(tmp_path) / ".g3" / "config.yaml").read_text()
    assert "player_provider: codex" in saved
    assert "player_model: gpt-5.4" in saved
