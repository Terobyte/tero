"""Tests for Codex native model presets and config defaults."""

from src.config import Config, get_context_window, short_model_name
from src.menu import CODEX_MODEL_PRESETS, PROVIDER_PRESETS
from src.runtime_controls import MODEL_PRESETS


def test_codex_menu_presets_match_native_cli_models():
    assert CODEX_MODEL_PRESETS == {
        "GPT-5.4 (default)": "",
        "GPT-5.4 xhigh reasoning": "gpt-5.4",
        "o3": "o3",
        "o4-mini": "o4-mini",
        "Ввести вручную...": "__custom__",
    }
    assert PROVIDER_PRESETS["Codex (native CLI)"] == "codex"


def test_runtime_controls_offer_native_codex_presets():
    assert ("GPT-5.4", "codex", "") in MODEL_PRESETS
    assert ("o3", "codex", "o3") in MODEL_PRESETS
    assert ("o4-mini", "codex", "o4-mini") in MODEL_PRESETS


def test_codex_config_defaults_and_names_use_native_models():
    cfg = Config()

    assert cfg.batch_judge_provider == "codex"
    assert cfg.batch_judge_model == ""
    assert short_model_name("") == "CODEX"
    assert short_model_name("gpt-5.4") == "GPT-5.4"
    assert short_model_name("o3") == "o3"
    assert short_model_name("o4-mini") == "o4-mini"
    assert get_context_window("gpt-5.4") == 128_000
    assert get_context_window("o3") == 128_000
    assert get_context_window("o4-mini") == 128_000
