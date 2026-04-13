"""Tests for TDD test_writer provider routing.

Verifies that the test_writer role uses `test_writer_provider` when configured,
and does NOT fall back to coach_provider.
"""

import pytest
from unittest.mock import MagicMock

from src.coach_player import CoachPlayerSession
from src.config import Config


def _make_mock_provider(name="Mock Provider"):
    provider = MagicMock()
    provider.check_ready = MagicMock(return_value=(True, ""))
    provider.display_name = name
    return provider


class TestTestWriterProviderRouting:
    """test_writer role must route to test_writer_provider, not coach."""

    def test_writer_uses_test_writer_provider_when_set(self, tmp_path, monkeypatch):
        """When test_writer_provider is configured, test_writer uses it."""
        mock_tw = _make_mock_provider("TW Provider")
        mock_coach = _make_mock_provider("Coach Provider")
        mock_player = _make_mock_provider("Player Provider")

        def fake_create(name, env=None, cfg=None):
            if name == "test_writer_prov":
                return mock_tw
            if name == "coach_prov":
                return mock_coach
            if name == "player_prov":
                return mock_player
            return _make_mock_provider()

        monkeypatch.setattr("src.coach_player.create_provider", fake_create)

        cfg = Config(
            working_dir=str(tmp_path),
            player_provider="player_prov",
            coach_provider="coach_prov",
            test_writer_provider="test_writer_prov",
        )
        session = CoachPlayerSession(cfg, "1. Step")
        session.player_provider = mock_player
        session.coach_provider = mock_coach

        assert session.router.provider_for("test_writer") is mock_tw, (
            "test_writer should use test_writer_provider, not coach"
        )

    def test_writer_provider_name_returns_test_writer_provider(
        self, tmp_path, monkeypatch
    ):
        """_provider_name_for_role('test_writer') returns test_writer_provider value."""
        mock_provider = _make_mock_provider()
        monkeypatch.setattr(
            "src.coach_player.create_provider",
            lambda name, env=None, cfg=None: mock_provider,
        )

        cfg = Config(
            working_dir=str(tmp_path),
            coach_provider="coach_prov",
            test_writer_provider="tw_prov",
        )
        session = CoachPlayerSession(cfg, "1. Step")
        session.coach_provider = mock_provider

        assert session.router.provider_name_for("test_writer") == "tw_prov"

    def test_writer_falls_back_to_coach_when_test_writer_provider_empty(
        self, tmp_path, monkeypatch
    ):
        """When test_writer_provider is empty, test_writer falls back to coach_provider."""
        mock_coach = _make_mock_provider("Coach Provider")
        monkeypatch.setattr(
            "src.coach_player.create_provider",
            lambda name, env=None, cfg=None: mock_coach,
        )

        cfg = Config(
            working_dir=str(tmp_path),
            coach_provider="coach_prov",
            test_writer_provider="",
        )
        session = CoachPlayerSession(cfg, "1. Step")
        session.coach_provider = mock_coach

        assert session.router.provider_name_for("test_writer") == "coach_prov"

    def test_writer_not_same_as_coach_when_test_writer_provider_differs(
        self, tmp_path, monkeypatch
    ):
        """test_writer resolves to a different provider than coach when test_writer_provider is set."""
        mock_tw = _make_mock_provider("TW")
        mock_coach = _make_mock_provider("Coach")

        def fake_create(name, env=None, cfg=None):
            if name == "tw_prov":
                return mock_tw
            return mock_coach

        monkeypatch.setattr("src.coach_player.create_provider", fake_create)

        cfg = Config(
            working_dir=str(tmp_path),
            coach_provider="coach_prov",
            test_writer_provider="tw_prov",
        )
        session = CoachPlayerSession(cfg, "1. Step")
        session.coach_provider = mock_coach

        tw_provider = session.router.provider_for("test_writer")
        coach_provider = session.router.provider_for("coach")
        assert tw_provider is not coach_provider, (
            "test_writer should use a different provider than coach when test_writer_provider is set"
        )

    def test_writer_model_override_uses_test_writer_model(self, tmp_path):
        """Config exposes test_writer_model for model override during test_writer turns."""
        cfg = Config(
            working_dir=str(tmp_path),
            test_writer_provider="tw_prov",
            test_writer_model="gpt-5",
        )
        assert cfg.test_writer_model == "gpt-5"

    def test_writer_model_empty_by_default(self):
        """Default test_writer_model is empty string."""
        cfg = Config()
        assert cfg.test_writer_model == ""
