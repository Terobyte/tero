"""Regression tests for menu/config bugs that were previously confirmed."""

from types import SimpleNamespace
import sys

import yaml
from unittest.mock import MagicMock, patch


class DummyPrompt:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


class TestCoachModelStandalone:
    def test_coach_model_shows_correct_presets_for_claude(self):
        from src.config import Config
        from src.menu import _edit_setting_questionary, CLAUDE_MODEL_PRESETS

        config = Config(
            coach_provider="claude",
            coach_model="sonnet",
        )

        captured_choices = []

        def mock_select(label, choices=None, **kwargs):
            captured_choices.append(choices)
            return DummyPrompt(None)

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            _edit_setting_questionary(config, "coach_model")

        assert captured_choices[0] == list(CLAUDE_MODEL_PRESETS.keys())


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


class TestFallbackEffectiveSlotLabel:
    def test_effective_label_shows_provider_default(self):
        from src.config import Config
        from src.menu import _fallback_effective_slot_label

        config = Config(
            batch_pre_provider="muse",
            batch_pre_model="muse-spark-1.3",
            coach_provider="muse",
            coach_model="muse-spark-1.3",
        )

        label = _fallback_effective_slot_label(
            config, "batch_pre_provider", "batch_pre_model"
        )

        assert label == "muse (SPARK)"


class TestShortModelName:
    def test_empty_model_should_not_show_codex(self):
        from src.config import short_model_name

        assert short_model_name("") == "DEFAULT"


class TestSaveDefault:
    def test_save_default_includes_fallback_chains(self, tmp_path, monkeypatch):
        from src.config import Config
        from src.menu import _save_global_default

        monkeypatch.setenv("HOME", str(tmp_path))

        config = Config(
            player_fallback_chain="claude,muse",
            coach_fallback_chain="muse,claude",
            chain_retry_wait_s=30.0,
            chain_max_retries=3,
        )

        _save_global_default(config)

        saved = yaml.safe_load((tmp_path / ".g3" / "config.yaml").read_text())
        defaults = saved["defaults"]
        assert defaults["player_fallback_chain"] == "claude,muse"
        assert defaults["coach_fallback_chain"] == "muse,claude"
        assert defaults["chain_retry_wait_s"] == 30.0
        assert defaults["chain_max_retries"] == 3

    def test_save_default_includes_context_settings(self, tmp_path, monkeypatch):
        from src.config import Config
        from src.menu import _save_global_default

        monkeypatch.setenv("HOME", str(tmp_path))

        config = Config(
            context_limit=200_000,
            compact_threshold=0.9,
            max_continuation_attempts=3,
        )

        _save_global_default(config)

        saved = yaml.safe_load((tmp_path / ".g3" / "config.yaml").read_text())
        defaults = saved["defaults"]
        assert defaults["context_limit"] == 200_000
        assert defaults["compact_threshold"] == 0.9
        assert defaults["max_continuation_attempts"] == 3


class TestBatchRolePrompts:
    def test_batch_model_prompt_uses_friendly_label(self):
        from src.config import Config
        from src.menu import _edit_setting_questionary

        config = Config(
            batch_judge_provider="opencode",
            batch_judge_model="",
        )

        captured_labels = []
        prompts = iter(["OpenCode (MIMO/Kimi)", None])

        def mock_select(label, choices=None, **kwargs):
            captured_labels.append(label)
            return DummyPrompt(next(prompts))

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            _edit_setting_questionary(config, "batch_judge")

        assert any("Judge" in label for label in captured_labels)
        assert all("batch_judge" not in label.lower() for label in captured_labels)

    def test_questionary_batch_display_shows_effective_fallback(self, monkeypatch):
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

        menu._questionary_menu(Config(batch_judge_provider="codex", batch_judge_model="gpt-5.4"))

        judge_lines = [title for title in captured_titles if "Judge:" in title]

        assert judge_lines
        assert "codex (GPT-5.4)" in judge_lines[0]


class TestMuseModelPresets:
    def test_muse_has_selectable_presets(self):
        from src.menu import _fixed_model_for_provider, _model_presets_for_provider

        assert _fixed_model_for_provider("muse") == ""
        assert _model_presets_for_provider("muse")["Muse Spark 1.3"] == "muse-spark-1.3"

    def test_selecting_muse_prompts_for_model(self):
        from src.config import Config
        from src.menu import _questionary_select_provider_model

        select_calls = []

        def mock_select(*args, **kwargs):
            select_calls.append(kwargs.get("choices", args[1] if len(args) > 1 else None))
            if len(select_calls) == 1:
                return DummyPrompt("Muse Code (Spark)")
            return DummyPrompt("Muse Spark 1.3")

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            result = _questionary_select_provider_model(
                Config(player_provider="claude", player_model="sonnet"),
                "player_provider",
                "player_model",
                "player",
            )

        assert result.player_provider == "muse"
        assert result.player_model == "muse-spark-1.3"
        assert len(select_calls) == 2


class TestCustomModelHandling:
    def test_custom_model_stored_as_literal_for_unsupported_provider(self):
        from src.config import Config
        from src.menu import _resolve_model_choice

        config = Config(coach_model="sonnet")
        test_presets = {"Sonnet": "sonnet", "Custom": "__custom__"}

        with patch("src.menu._model_presets_for_provider", return_value=test_presets):
            with patch("src.menu._custom_model_allowed", return_value=False):
                result = _resolve_model_choice(
                    config, "claude", "coach_model", "Custom"
                )

        assert result.coach_model == "sonnet"
