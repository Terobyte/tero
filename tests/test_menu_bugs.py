"""Regression tests for menu/config behavior that was previously confirmed."""

from types import SimpleNamespace
import sys

import yaml
from unittest.mock import MagicMock, patch


class DummyPrompt:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


class TestProviderModelCarryover:
    def test_switching_provider_clears_incompatible_model(self):
        from src.config import Config
        from src.menu import _questionary_select_provider_model

        config = Config(
            player_provider="muse",
            player_model="muse-spark-1.3",
        )

        call_count = [0]

        def mock_select(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return DummyPrompt("Codex (native CLI)")
            return DummyPrompt(None)

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            result = _questionary_select_provider_model(
                config, "player_provider", "player_model", "player"
            )

        assert result.player_provider == "codex"
        assert result.player_model == ""
        assert result.player_effort == ""


class TestShortModelName:
    def test_empty_model_should_not_show_codex(self):
        from src.config import short_model_name

        assert short_model_name("") == "DEFAULT"


class TestSaveDefault:
    def test_save_default_writes_dataclass_dict(self, tmp_path, monkeypatch):
        from src.config import Config
        from src.menu import _save_global_default

        monkeypatch.setenv("HOME", str(tmp_path))

        config = Config(
            player_provider="codex",
            player_model="gpt-5.6-terra",
            max_rounds=12,
        )

        _save_global_default(config)

        saved = yaml.safe_load((tmp_path / ".tero" / "config.yaml").read_text())
        defaults = saved["defaults"]
        assert defaults["player_provider"] == "codex"
        assert defaults["player_model"] == "gpt-5.6-terra"
        assert defaults["max_rounds"] == 12


class TestV4MenuRoles:
    def test_questionary_menu_shows_tester_and_tests(self, monkeypatch):
        import src.menu as menu
        from src.config import Config

        captured_titles = []

        def fake_select(_message, choices=None, **kwargs):
            if choices is not None:
                for choice in choices:
                    if hasattr(choice, "value"):
                        captured_titles.append(choice.title)
            return DummyPrompt("quit")

        dummy_questionary = SimpleNamespace(
            Choice=lambda title, value: SimpleNamespace(title=title, value=value),
            Separator=lambda text: SimpleNamespace(separator=text),
            select=fake_select,
        )

        monkeypatch.setitem(sys.modules, "questionary", dummy_questionary)
        monkeypatch.setattr(menu, "QUESTIONARY_AVAILABLE", True)

        menu._questionary_menu(
            Config(
                judge_provider="codex",
                judge_model="gpt-5.6-terra",
                tester_provider="gemini",
                tester_model="gemini-3.8-flash",
                test_command="./scripts/test.sh",
            )
        )

        titles = "\n".join(captured_titles)
        assert "Tester:" in titles
        assert "Тесты:" in titles
        assert "./scripts/test.sh" in titles
        assert "gemini" in titles.lower()

    def test_questionary_start_saves_and_returns_config(self, monkeypatch):
        import src.menu as menu
        from src.config import Config

        def fake_select(_message, choices=None, **kwargs):
            return DummyPrompt("start")

        dummy_questionary = SimpleNamespace(
            Choice=lambda title, value: SimpleNamespace(title=title, value=value),
            Separator=lambda text: SimpleNamespace(separator=text),
            select=fake_select,
        )

        monkeypatch.setitem(sys.modules, "questionary", dummy_questionary)
        monkeypatch.setattr(menu, "QUESTIONARY_AVAILABLE", True)

        with patch("src.menu._save_global_default") as save:
            result = menu._questionary_menu(Config())

        assert isinstance(result, Config)
        save.assert_called_once()


class TestMuseModelPresets:
    def test_muse_has_selectable_presets(self):
        from src.menu import _model_presets_for_provider

        assert _model_presets_for_provider("muse")["Muse Spark 1.3 (свой ключ)"] == "muse-spark-1.3"

    def test_selecting_muse_prompts_for_model(self):
        from src.config import Config
        from src.menu import _questionary_select_provider_model

        select_calls = []

        def mock_select(*args, **kwargs):
            select_calls.append(kwargs.get("choices", args[1] if len(args) > 1 else None))
            if len(select_calls) == 1:
                return DummyPrompt("Muse API (свой ключ)")
            if len(select_calls) == 2:
                return DummyPrompt("Muse Spark 1.3 (свой ключ)")
            return DummyPrompt("High")

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            result = _questionary_select_provider_model(
                Config(player_provider="claude", player_model="sonnet"),
                "player_provider",
                "player_model",
                "player",
            )

        assert result.player_provider == "muse"
        assert result.player_model == "muse-spark-1.3"
        assert result.player_effort == "high"
        assert len(select_calls) == 3


class TestCustomModelHandling:
    def test_custom_model_stored_as_literal_for_unsupported_provider(self):
        from src.config import Config
        from src.menu import _resolve_model_choice

        config = Config(judge_model="sonnet")
        test_presets = {"Sonnet": "sonnet", "Custom": "__custom__"}

        with patch("src.menu._model_presets_for_provider", return_value=test_presets):
            with patch("src.menu._custom_model_allowed", return_value=False):
                result = _resolve_model_choice(
                    config, "claude", "judge_model", "Custom"
                )

        assert result.judge_model == "sonnet"


class TestEffortPicker:
    def test_gemini_skips_effort_window(self):
        from src.config import Config
        from src.menu import _questionary_select_provider_model

        calls = []

        def mock_select(*args, **kwargs):
            calls.append(kwargs.get("choices") or (args[1] if len(args) > 1 else None))
            if len(calls) == 1:
                return DummyPrompt("Gemini (Google CLI)")
            return DummyPrompt("Gemini 3.8 Flash")

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            result = _questionary_select_provider_model(
                Config(player_provider="muse", player_model="muse-spark-1.3", player_effort="high"),
                "player_provider",
                "player_model",
                "player",
            )

        assert result.player_provider == "gemini"
        assert result.player_model == "gemini-3.8-flash"
        assert result.player_effort == ""
        assert len(calls) == 2

    def test_cursor_presets_include_grok(self):
        from src.menu import CURSOR_MODEL_PRESETS

        assert CURSOR_MODEL_PRESETS["Grok 4.6"] == "cursor-grok-4.6"

    def test_label_appends_effort(self):
        from src.menu import _provider_model_label

        assert "high" in _provider_model_label("cursor", "cursor-grok-4.6", effort="high")

    def test_fallback_menu_escalation_off(self, monkeypatch):
        from src.config import Config
        from src.menu import _fallback_menu

        answers = iter(["e", "off", ""])
        monkeypatch.setattr("builtins.input", lambda _="": next(answers))

        result = _fallback_menu(Config(escalation_provider="claude"))

        assert result is not None
        assert result.escalation_provider == ""
        assert result.escalation_model == ""
