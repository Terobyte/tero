"""Tests for menu/config presets and provider defaults."""

from src.cli_entry import PROVIDER_CHOICES
from src.config import Config, FIXED_PROVIDER_MODELS, get_context_window, short_model_name
from src.menu import CODEX_MODEL_PRESETS, KILO_MODEL_PRESETS, PROVIDER_PRESETS
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
    assert ("Turbo", "turbo", "glm-5-turbo") in MODEL_PRESETS
    assert ("ZAI", "zai", "glm-5.1") in MODEL_PRESETS


def test_codex_config_defaults_and_names_use_native_models():
    cfg = Config()

    assert cfg.batch_judge_provider == "codex"
    assert cfg.batch_judge_model == ""
    assert short_model_name("") == "CODEX"
    assert short_model_name("gpt-5.4") == "GPT-5.4"
    assert short_model_name("o3") == "o3"
    assert short_model_name("o4-mini") == "o4-mini"
    assert short_model_name("glm-5.1") == "GLM-5.1"
    assert short_model_name("glm-5-turbo") == "GLM-5-T"
    assert short_model_name("glm-4.7") == "GLM-4.7"
    assert get_context_window("gpt-5.4") == 128_000
    assert get_context_window("o3") == 128_000
    assert get_context_window("o4-mini") == 128_000
    assert get_context_window("glm-5.1") == 204_800


def test_fixed_glm_provider_presets_are_locked_and_listed():
    assert FIXED_PROVIDER_MODELS == {
        "black": "blackboxai/z-ai/glm-5",
        "turbo": "glm-5-turbo",
        "zai": "glm-5.1",
    }
    assert PROVIDER_PRESETS["BLACK (Blackbox/GLM-5)"] == "black"
    assert PROVIDER_PRESETS["TURBO (Z.AI / GLM-5 Turbo)"] == "turbo"
    assert PROVIDER_PRESETS["ZAI (Z.AI / GLM-5.1)"] == "zai"
    assert "zai" in PROVIDER_CHOICES


def test_kilo_provider_and_model_presets_are_listed():
    assert PROVIDER_PRESETS["Kilo (MIMO/MiniMax)"] == "kilo"
    assert KILO_MODEL_PRESETS == {
        "MIMO Pro  (free)": "kilo/xiaomi/mimo-v2-pro:free",
        "MiniMax M2.5 (free)": "kilo/minimax/minimax-m2.5:free",
    }
    assert "kilo" in PROVIDER_CHOICES
