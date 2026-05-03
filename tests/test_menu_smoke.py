"""Smoke tests for the interactive TUI menu (src/menu.py).

Verifies that the menu module loads, all entry points exist, preset
dictionaries are populated, and key interactions (especially LDB Mode 2/3
switching) work correctly — both with and without questionary.
"""

import dataclasses
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config import Config
from src.menu import (
    CLAUDE_MODEL_PRESETS,
    CODEX_MODEL_PRESETS,
    DEBUG_INTENSITY_PRESETS,
    DEBUG_LIMIT_PRESETS,
    FALLBACK_PROVIDER_PRESETS,
    GEMINI_MODEL_PRESETS,
    KILO_MODEL_PRESETS,
    LDB_MODE_PRESETS,
    OPENCODE_MODEL_PRESETS,
    PROVIDER_PRESETS,
    QUESTIONARY_AVAILABLE,
    _custom_model_allowed,
    _effective_provider_model,
    _fallback_effective_slot_label,
    _fallback_ldb_menu,
    _fallback_menu,
    _fallback_select_provider_model,
    _fixed_model_for_provider,
    _format_context_limit,
    _model_presets_for_provider,
    _provider_model_label,
    _questionary_menu,
    _sync_batch_roles_with_coach,
    run_debugger_menu,
    run_ldb_menu,
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

    def test_run_debugger_menu_exists(self):
        assert callable(run_debugger_menu)

    def test_run_ldb_menu_exists(self):
        assert callable(run_ldb_menu)


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
        assert "sonnet" in CLAUDE_MODEL_PRESETS.values()
        assert "opus" in CLAUDE_MODEL_PRESETS.values()
        assert "haiku" in CLAUDE_MODEL_PRESETS.values()

    def test_codex_model_presets(self):
        assert any("gpt-5.4" in v for v in CODEX_MODEL_PRESETS.values())

    def test_opencode_model_presets(self):
        assert any("minimax" in v.lower() for v in OPENCODE_MODEL_PRESETS.values())

    def test_kilo_model_presets(self):
        assert any("mimo" in v.lower() for v in KILO_MODEL_PRESETS.values())

    def test_gemini_model_presets(self):
        assert any("gemini" in v.lower() for v in GEMINI_MODEL_PRESETS.values())

    def test_debug_intensity_presets(self):
        assert set(DEBUG_INTENSITY_PRESETS.values()) == {"low", "medium", "high"}

    def test_debug_limit_presets(self):
        assert len(DEBUG_LIMIT_PRESETS) >= 3

    def test_ldb_mode_presets_contain_2_and_3(self):
        """LDB menu must offer Mode 2 and Mode 3."""
        assert set(LDB_MODE_PRESETS.values()) == {2, 3}


# ── 3. Helper functions ──────────────────────────────────────────────────────

class TestHelperFunctions:
    @pytest.mark.parametrize(
        "provider,expected",
        [("zai", "glm-5.1"), ("lite", "glm-5.1"), ("claude", ""), ("codex", "")],
    )
    def test_fixed_model_for_provider(self, provider, expected):
        assert _fixed_model_for_provider(provider) == expected

    def test_model_presets_for_provider_returns_dict(self):
        for p in ("claude", "codex", "opencode", "kilo", "gemini"):
            presets = _model_presets_for_provider(p)
            assert isinstance(presets, dict)
            assert len(presets) > 0

    def test_model_presets_for_unknown_provider_empty(self):
        assert _model_presets_for_provider("unknown_xyz") == {}

    def test_custom_model_allowed_only_for_codex_and_opencode(self):
        assert _custom_model_allowed("codex") is True
        assert _custom_model_allowed("opencode") is True
        assert _custom_model_allowed("claude") is False
        assert _custom_model_allowed("zai") is False

    @pytest.mark.parametrize(
        "limit,expected",
        [
            (110_000, "авто (по модели)"),  # DEFAULT_CONTEXT_LIMIT
            (500, "500"),
            (200_000, "200K"),
            (1_000_000, "1M"),
            (128_000, "128K"),
        ],
    )
    def test_format_context_limit(self, limit, expected):
        assert _format_context_limit(limit) == expected

    def test_provider_model_label_off(self):
        assert _provider_model_label("", "") == "выкл"

    def test_provider_model_label_with_model(self):
        assert "sonnet" in _provider_model_label("claude", "sonnet").lower() or \
               "SONNET" in _provider_model_label("claude", "sonnet")

    def test_provider_model_label_default(self):
        label = _provider_model_label("codex", "")
        assert "по умолчанию" in label

    def test_effective_provider_model_fallback(self):
        p, m = _effective_provider_model(
            "", "",
            fallback_provider="claude",
            fallback_model="sonnet",
        )
        assert p == "claude"
        assert m == "sonnet"

    def test_effective_provider_model_fixed_for_zai(self):
        p, m = _effective_provider_model("zai", "")
        assert p == "zai"
        assert m == "glm-5.1"


# ── 4. Sync batch roles with coach ───────────────────────────────────────────

class TestSyncBatchRoles:
    def test_sync_updates_matching_batch_roles(self):
        config = Config(
            coach_provider="zai",
            coach_model="glm-5.1",
            batch_pre_provider="codex",
            batch_pre_model="gpt-5.4",
            batch_post_provider="codex",
            batch_post_model="gpt-5.4",
        )
        updated = _sync_batch_roles_with_coach(config, "codex", "gpt-5.4")
        assert updated.batch_pre_provider == "zai"
        assert updated.batch_pre_model == "glm-5.1"
        assert updated.batch_post_provider == "zai"
        assert updated.batch_post_model == "glm-5.1"

    def test_sync_does_not_touch_mismatched_roles(self):
        config = Config(
            coach_provider="zai",
            coach_model="glm-5.1",
            batch_pre_provider="claude",
            batch_pre_model="sonnet",
        )
        updated = _sync_batch_roles_with_coach(config, "codex", "gpt-5.4")
        assert updated.batch_pre_provider == "claude"
        assert updated.batch_pre_model == "sonnet"


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
        """Selecting player_provider then quit should update provider."""
        mock_q = _fake_questionary(["player_provider", "ZAI (Z.AI / GLM-5.1)", "quit"])
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = _questionary_menu(Config(player_provider="claude"))
        assert result is None  # quit


# ── 6. Questionary debugger menu smoke ───────────────────────────────────────

class TestQuestionaryDebuggerMenu:
    def test_back_returns_config(self):
        mock_q = _fake_questionary(["back"])
        with patch("src.menu.questionary", mock_q):
            result = run_debugger_menu(Config())
        assert isinstance(result, Config)

    def test_start_returns_config(self):
        mock_q = _fake_questionary(["start"])
        with patch("src.menu.questionary", mock_q):
            result = run_debugger_menu(Config())
        assert isinstance(result, Config)

    def test_intensity_switching(self):
        """Selecting intensity → low then back should update config."""
        mock_q = _fake_questionary(["intensity", "Low  (1 pass — structural analysis)", "back"])
        with patch("src.menu.questionary", mock_q):
            result = run_debugger_menu(Config(debug_intensity="medium"))
        assert result.debug_intensity == "low"

    def test_limit_switching(self):
        """Selecting limit → 5 iterations then back should update config."""
        preset_key = "5 iterations"
        mock_q = _fake_questionary(["limit", preset_key, "back"])
        with patch("src.menu.questionary", mock_q):
            result = run_debugger_menu(Config())
        assert result.debug_limit_mode == "iterations"
        assert result.debug_limit_value == 5


# ── 7. Questionary LDB menu smoke ────────────────────────────────────────────

class TestQuestionaryLdbMenu:
    def test_back_returns_none(self, monkeypatch):
        mock_q = _fake_questionary(["back"])
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = run_ldb_menu(Config())
        assert result is None

    def test_start_returns_config(self, monkeypatch):
        mock_q = _fake_questionary(["start"])
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = run_ldb_menu(Config())
        assert isinstance(result, Config)

    def test_mode_2_switching(self, monkeypatch):
        """Switching to Mode 2 should set ldb_mode=2."""
        mode2_key = next(k for k, v in LDB_MODE_PRESETS.items() if v == 2)
        mock_q = _fake_questionary(["mode", mode2_key, "start"])
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = run_ldb_menu(Config(ldb_mode=3))
        assert result.ldb_mode == 2

    def test_mode_3_switching(self, monkeypatch):
        """Switching to Mode 3 should set ldb_mode=3."""
        mode3_key = next(k for k, v in LDB_MODE_PRESETS.items() if v == 3)
        mock_q = _fake_questionary(["mode", mode3_key, "start"])
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = run_ldb_menu(Config(ldb_mode=2))
        assert result.ldb_mode == 3

    def test_scope_all_switching(self, monkeypatch):
        """Switching scope to 'All public functions' sets ldb_scope_all=True."""
        mock_q = _fake_questionary(
            ["scope", "All public functions (--all)", "start"]
        )
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = run_ldb_menu(Config(ldb_scope_all=False))
        assert result.ldb_scope_all is True

    def test_scope_single_function(self, monkeypatch):
        """Switching scope to single function sets file and entry."""
        mock_q = _fake_questionary(
            [
                "scope",
                "Single function (--file --entry)",
                "src/menu.py",  # file input
                "run_ldb_menu",  # entry input
                "start",
            ]
        )
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = run_ldb_menu(Config(ldb_scope_all=True))
        assert result.ldb_scope_all is False
        assert result.ldb_target_file == "src/menu.py"
        assert result.ldb_target_entry == "run_ldb_menu"

    def test_test_input_update(self, monkeypatch):
        """Editing test input should persist the value."""
        mock_q = _fake_questionary(["test", "assert foo == bar", "start"])
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = run_ldb_menu(Config(ldb_test_input=""))
        assert result.ldb_test_input == "assert foo == bar"

    def test_agent_provider_editing(self, monkeypatch):
        """Editing the input agent provider should update config."""
        mock_q = _fake_questionary(
            ["input", "Claude Pro (native)", "Sonnet (balanced)", "start"]
        )
        monkeypatch.setitem(sys.modules, "questionary", mock_q)
        monkeypatch.setattr("src.menu.questionary", mock_q)
        result = run_ldb_menu(Config(ldb_input_provider="zai"))
        assert result.ldb_input_provider == "claude"
        assert result.ldb_input_model == "sonnet"


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


# ── 9. Fallback LDB menu smoke ───────────────────────────────────────────────

class TestFallbackLdbMenu:
    def test_quit_returns_none(self, monkeypatch):
        answers = iter(["q"])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = _fallback_ldb_menu(Config())
        assert result is None

    def test_enter_returns_config(self, monkeypatch):
        answers = iter([""])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = _fallback_ldb_menu(Config())
        assert isinstance(result, Config)

    def test_mode_2_switching(self, monkeypatch):
        answers = iter(["3", "2", ""])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = _fallback_ldb_menu(Config(ldb_mode=3))
        assert result.ldb_mode == 2

    def test_mode_3_switching(self, monkeypatch):
        answers = iter(["3", "3", ""])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = _fallback_ldb_menu(Config(ldb_mode=2))
        assert result.ldb_mode == 3

    def test_scope_all_switching(self, monkeypatch):
        answers = iter(["1", "a", ""])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = _fallback_ldb_menu(Config(ldb_scope_all=False))
        assert result.ldb_scope_all is True

    def test_scope_single_function(self, monkeypatch):
        answers = iter(["1", "s", "src/foo.py", "my_func", ""])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = _fallback_ldb_menu(Config(ldb_scope_all=True))
        assert result.ldb_scope_all is False
        assert result.ldb_target_file == "src/foo.py"
        assert result.ldb_target_entry == "my_func"

    def test_test_input_update(self, monkeypatch):
        answers = iter(["2", "assert bar == baz", ""])
        monkeypatch.setattr("builtins.input", lambda *_: next(answers))
        result = _fallback_ldb_menu(Config(ldb_test_input=""))
        assert result.ldb_test_input == "assert bar == baz"


# ── 10. Fallback effective slot label ────────────────────────────────────────

class TestFallbackEffectiveSlotLabel:
    def test_shows_provider_default(self):
        config = Config(
            batch_pre_provider="zai",
            batch_pre_model="",
            coach_provider="zai",
            coach_model="",
        )
        label = _fallback_effective_slot_label(
            config, "batch_pre_provider", "batch_pre_model"
        )
        assert "zai" in label
