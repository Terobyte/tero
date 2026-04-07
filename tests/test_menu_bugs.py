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
    def test_coach_model_shows_correct_presets_for_provider(self):
        from src.config import Config
        from src.menu import _edit_setting_questionary, CCG_MODEL_PRESETS

        config = Config(
            coach_provider="black",
            coach_model="blackboxai/z-ai/glm-5",
        )

        captured_choices = []

        def mock_select(label, choices=None, **kwargs):
            captured_choices.append(choices)
            return DummyPrompt(None)

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            _edit_setting_questionary(config, "coach_model")

        assert captured_choices[0] == list(CCG_MODEL_PRESETS.keys())

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
            player_provider="black",
            player_model="blackboxai/z-ai/glm-5",
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
    def test_effective_label_shows_fixed_model(self):
        from src.config import Config
        from src.menu import _fallback_effective_slot_label

        config = Config(
            batch_pre_provider="black",
            batch_pre_model="",
            coach_provider="black",
            coach_model="",
        )

        label = _fallback_effective_slot_label(
            config, "batch_pre_provider", "batch_pre_model"
        )

        assert label == "black (GLM-5)"


class TestShortModelName:
    def test_empty_model_should_not_show_codex(self):
        from src.config import short_model_name

        assert short_model_name("") == "DEFAULT"


class TestReviewProviderMenu:
    def test_review_provider_configurable_in_questionary_menu(self):
        from src.config import Config
        from src.menu import _edit_setting_questionary

        prompts = iter(["Codex (native CLI)", "o3"])

        def mock_select(*args, **kwargs):
            return DummyPrompt(next(prompts))

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            updated = _edit_setting_questionary(
                Config(
                    coach_provider="black",
                    coach_model="blackboxai/z-ai/glm-5",
                    review_provider="",
                    review_model="",
                ),
                "review_provider",
            )

        assert updated.review_provider == "codex"
        assert updated.review_model == "o3"


class TestSaveDefault:
    def test_save_default_includes_fallback_chains(self, tmp_path, monkeypatch):
        from src.config import Config
        from src.menu import _save_global_default

        monkeypatch.setenv("HOME", str(tmp_path))

        config = Config(
            player_fallback_chain="turbo,zai",
            coach_fallback_chain="black,turbo",
            chain_retry_wait_s=30.0,
            chain_max_retries=3,
        )

        _save_global_default(config)

        saved = yaml.safe_load((tmp_path / ".g3" / "config.yaml").read_text())
        defaults = saved["defaults"]
        assert defaults["player_fallback_chain"] == "turbo,zai"
        assert defaults["coach_fallback_chain"] == "black,turbo"
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
            batch_pre_provider="opencode",
            batch_pre_model="",
        )

        captured_labels = []
        prompts = iter(["OpenCode (MIMO/Kimi/Z.AI)", None])

        def mock_select(label, choices=None, **kwargs):
            captured_labels.append(label)
            return DummyPrompt(next(prompts))

        with patch("src.menu.questionary", MagicMock(select=mock_select)):
            _edit_setting_questionary(config, "batch_pre")

        assert any("Pre-Coach" in label for label in captured_labels)
        assert all("batch_pre" not in label.lower() for label in captured_labels)

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

        menu._questionary_menu(
            Config(
                batch_pre_provider="",
                batch_pre_model="",
                coach_provider="black",
                coach_model="blackboxai/z-ai/glm-5",
            )
        )

        pre_coach_lines = [title for title in captured_titles if "Pre-Coach:" in title]
        review_lines = [title for title in captured_titles if "Review Agent:" in title]

        assert pre_coach_lines
        assert "black (GLM-5)" in pre_coach_lines[0]
        assert review_lines


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
