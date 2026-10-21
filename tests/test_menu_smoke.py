"""Smoke tests for the interactive TUI menu (src/menu.py).

Verifies that the menu module loads, all entry points exist, and preset
dictionaries are populated.
"""

import sys
from types import SimpleNamespace
from unittest.mock import patch

from src.config import Config
from src.menu import (
    CLAUDE_MODEL_PRESETS,
    CODEX_MODEL_PRESETS,
    FALLBACK_PROVIDER_PRESETS,
    GEMINI_MODEL_PRESETS,
    MUSE_MODEL_PRESETS,
    OPENCODE_MODEL_PRESETS,
    PROVIDER_PRESETS,
    QUESTIONARY_AVAILABLE,
    _custom_model_allowed,
    _fallback_menu,
    _fallback_select_provider_model,
    _model_presets_for_provider,
    _provider_model_label,
    _questionary_menu,
    run_settings_menu,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

class DummyPrompt:
    """Mimics questionary.*.ask() return."""

    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


def _fake_questionary(responses):
    """Build a mock questionary namespace that returns *responses* in order."""
    iterator = iter(responses)

    def _select(_label, choices=None, **kwargs):
        return DummyPrompt(next(iterator))

    return SimpleNamespace(
        select=_select,
        Choice=lambda title, value: SimpleNamespace(title=title, value=value),
        Separator=lambda text: SimpleNamespace(separator=text),
        text=lambda _label, **kw: DummyPrompt(next(iterator)),
    )


# ── 1. Module loads & entry points exist ─────────────────────────────────────

class TestMenuModuleLoads:
    def test_import_menu_module(self):
        """Menu module can be imported without errors."""
        import src.menu  # noqa: F401 – smoke test

    def test_run_settings_menu_exists(self):
        assert callable(run_settings_menu)


# ── 2. Preset dictionaries are populated ─────────────────────────────────────

class TestPresetsPopulated:
    def test_provider_presets_not_empty(self):
        assert len(PROVIDER_PRESETS) >= 5
        # Every preset maps to a non-empty provider id
        for label, value in PROVIDER_PRESETS.items():
            assert value, f"PROVIDER_PRESETS[{label!r}] is empty"

    def test_fallback_provider_presets_include_off(self):
        """FALLBACK_PROVIDER_PRESETS must include an empty-string 'off' option."""
        assert "" in FALLBACK_PROVIDER_PRESETS.values()

    def test_claude_model_presets(self):
        assert "claude-sonnet-5" in CLAUDE_MODEL_PRESETS.values()
        assert "claude-opus-5" in CLAUDE_MODEL_PRESETS.values()
        assert "claude-haiku-4-5" in CLAUDE_MODEL_PRESETS.values()
        assert "claude-fable-5-1" in CLAUDE_MODEL_PRESETS.values()

    def test_codex_model_presets(self):
        values = list(CODEX_MODEL_PRESETS.values())
        assert "gpt-5.6-sol" in values
        assert "gpt-5.6-terra" in values
        assert "gpt-5.6-luna" in values
        assert "gpt-6-astra" in values

    def test_opencode_model_presets(self):
        assert any("minimax" in v.lower() for v in OPENCODE_MODEL_PRESETS.values())

    def test_muse_model_presets(self):
        assert "muse-spark-1.3" in MUSE_MODEL_PRESETS.values()

    def test_gemini_model_presets(self):
        assert any("gemini" in v.lower() for v in GEMINI_MODEL_PRESETS.values())


# ── 3. Helper functions ──────────────────────────────────────────────────────

class TestHelperFunctions:
    def test_model_presets_for_provider_returns_dict(self):
        for p in ("claude", "codex", "opencode", "gemini", "muse", "cursor"):
            presets = _model_presets_for_provider(p)
            assert isinstance(presets, dict)
            assert len(presets) > 0

    def test_model_presets_for_unknown_provider_empty(self):
        assert _model_presets_for_provider("unknown_xyz") == {}

    def test_custom_model_allowed_only_for_codex_opencode_cursor(self):
        assert _custom_model_allowed("codex") is True
        assert _custom_model_allowed("opencode") is True
        assert _custom_model_allowed("claude") is False
        assert _custom_model_allowed("muse") is False
        assert _custom_model_allowed("cursor") is True

    def test_provider_model_label_off(self):
        assert _provider_model_label("", "") == "выкл"

    def test_provider_model_label_with_model(self):
        assert "sonnet" in _provider_model_label("claude", "sonnet").lower() or \
                "SONNET" in _provider_model_label("claude", "sonnet")

    def test_provider_model_label_default(self):
        label = _provider_model_label("codex", "")
        assert "по умолчанию" in label


# ── 5. Questionary settings menu smoke ───────────────────────────────────────

class TestQuestionarySettingsMenu:
    def test_quit_returns_none(self, monkeypatch):
        mock_q = _fake_questionary(["quit"])
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = _questionary_menu(Config())
        assert result is None

    def test_start_returns_config(self, monkeypatch):
        mock_q = _fake_questionary(["start"])
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        with patch("src.menu._save_global_default"):
            result = _questionary_menu(Config())
        assert isinstance(result, Config)

    def test_player_provider_editing(self, monkeypatch):
        """Selecting player_provider then quit should not change anything yet."""
        mock_q = _fake_questionary(
            ["player_provider", "Muse API (свой ключ)", "Muse Spark 1.3 (свой ключ)", "High", "quit"]
        )
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = _questionary_menu(Config(player_provider="claude"))
        assert result is None  # quit


# ── 8. Fallback menu (no questionary) smoke ──────────────────────────────────

class TestFallbackMenu:
    def test_quit_returns_none(self, monkeypatch):
        monkeypatch.setattr("src.menu.QUESTIONARY_AVAILABLE", False)
        answers = iter(["q"])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = _fallback_menu(Config())
        assert result is None

    def test_start_returns_config(self, monkeypatch):
        monkeypatch.setattr("src.menu.QUESTIONARY_AVAILABLE", False)
        answers = iter([""])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        with patch("src.menu._save_global_default"):
            result = _fallback_menu(Config())
        assert isinstance(result, Config)

    def test_run_settings_menu_dispatches_to_fallback(self, monkeypatch):
        """When questionary is unavailable, run_settings_menu uses fallback."""
        monkeypatch.setattr("src.menu.QUESTIONARY_AVAILABLE", False)
        answers = iter(["q"])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = run_settings_menu(Config())
        assert result is None
