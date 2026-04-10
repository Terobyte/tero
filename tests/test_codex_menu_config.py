"""Tests for menu/config presets and provider defaults."""

from pathlib import Path

from src.cli_entry import PROVIDER_CHOICES
from src.config import Config, get_context_window, short_model_name
from src.menu import (
    CODEX_MODEL_PRESETS,
    KILO_MODEL_PRESETS,
    OPENCODE_MODEL_PRESETS,
    PROVIDER_PRESETS,
    _save_global_default,
)
from src.runtime_controls import MODEL_PRESETS


def test_codex_menu_presets_match_native_cli_models():
    assert CODEX_MODEL_PRESETS == {
        "Medium (default)": "",
        "High": "gpt-5.4",
    }
    assert PROVIDER_PRESETS["Codex (native CLI)"] == "codex"


def test_runtime_controls_offer_native_codex_presets():
    preset_labels = [label for label, _, _ in MODEL_PRESETS]
    assert "GPT-5.4" in preset_labels
    assert "o3" in preset_labels
    assert "o4-mini" in preset_labels
    assert "GLM-5.1" in preset_labels


def test_codex_config_defaults_and_names_use_native_models():
    cfg = Config()

    assert cfg.batch_judge_provider == "codex"
    assert cfg.batch_judge_model == ""
    assert short_model_name("") == "DEFAULT"
    assert short_model_name("gpt-5.4") == "GPT-5.4"
    assert short_model_name("o3") == "o3"
    assert short_model_name("o4-mini") == "o4-mini"
    assert short_model_name("glm-5-turbo") == "GLM-5-T"
    assert short_model_name("glm-4.7") == "GLM-4.7"
    assert get_context_window("gpt-5.4") == 128_000
    assert get_context_window("o3") == 128_000
    assert get_context_window("o4-mini") == 128_000


def test_provider_presets_have_zai_and_kilo():
    assert PROVIDER_PRESETS["ZAI (Z.AI / GLM-5.1)"] == "zai"
    assert PROVIDER_PRESETS["Kilo (MIMO/MiniMax)"] == "kilo"
    assert "black" not in PROVIDER_PRESETS.values()
    assert "turbo" not in PROVIDER_PRESETS.values()
    assert "zai" in PROVIDER_CHOICES
    assert "kilo" in PROVIDER_CHOICES


def test_kilo_provider_and_model_presets_are_listed():
    assert PROVIDER_PRESETS["Kilo (MIMO/MiniMax)"] == "kilo"
    assert KILO_MODEL_PRESETS == {
        "MIMO Pro  (free)": "kilo/xiaomi/mimo-v2-pro:free",
        "MiniMax M2.5 (free)": "kilo/minimax/minimax-m2.5:free",
    }
    assert "kilo" in PROVIDER_CHOICES


def test_opencode_presets_are_correct():
    assert PROVIDER_PRESETS["OpenCode (MIMO/Kimi/Z.AI)"] == "opencode"
    assert OPENCODE_MODEL_PRESETS["MiniMax M2.5 (free)"] == "opencode/minimax-m2.5-free"


def test_save_global_default_persists_preplan_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    cfg = Config(
        working_dir=str(tmp_path / "workspace"),
        preplan_mode=True,
        preplan_provider="codex",
        preplan_model="o3",
    )

    _save_global_default(cfg)

    saved = (Path(tmp_path) / ".g3" / "config.yaml").read_text()
    assert "preplan_mode: true" in saved
    assert "preplan_provider: codex" in saved
    assert "preplan_model: o3" in saved
