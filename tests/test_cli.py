"""Tests for CLI integration behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import g3


def test_run_go_respects_project_config_defaults_when_flags_omitted(tmp_path, monkeypatch):
    """Argparse defaults should not clobber values from .g3/config.yaml."""
    config_dir = tmp_path / ".g3"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "defaults:\n"
        "  max_turns: 3\n"
        "  plan_file: custom.md\n"
        "  player_provider: ccg\n"
        "  coach_provider: ccg\n"
    )
    (tmp_path / "custom.md").write_text("1. Build it\n")

    captured = {}

    class FakeSession:
        def __init__(self, config, requirements, plan_file_path=""):
            captured["config"] = config
            captured["requirements"] = requirements
            self.plan_file_path = plan_file_path

        async def run(self):
            return SimpleNamespace(approved=True)

    exit_codes = []

    monkeypatch.setattr(g3, "CoachPlayerSession", FakeSession)
    monkeypatch.setattr(g3.sys, "exit", exit_codes.append)

    args = SimpleNamespace(
        working_dir=str(tmp_path),
        max_turns=None,
        plan=None,
        verbose=None,
        autonomous=None,
        player_provider=None,
        coach_provider=None,
        player_model=None,
        coach_model=None,
    )

    import asyncio

    asyncio.run(g3.run_go(args))

    assert captured["config"].max_turns == 3
    assert captured["config"].plan_file == "custom.md"
    assert captured["requirements"] == "1. Build it\n"
    assert exit_codes == [0]


def test_run_go_does_not_open_menu_inside_async_context(tmp_path, monkeypatch):
    """Interactive menu must be handled before asyncio.run(), not inside run_go()."""
    (tmp_path / "requirements.md").write_text("1. Build it\n")

    class FakeSession:
        def __init__(self, config, requirements, plan_file_path=""):
            self.config = config
            self.requirements = requirements
            self.plan_file_path = plan_file_path

        async def run(self):
            return SimpleNamespace(approved=True)

    exit_codes = []

    monkeypatch.setattr(g3, "CoachPlayerSession", FakeSession)
    monkeypatch.setattr(g3.sys, "exit", exit_codes.append)
    monkeypatch.setattr(
        "src.menu.run_settings_menu",
        lambda config: (_ for _ in ()).throw(AssertionError("menu should not be opened")),
    )

    args = SimpleNamespace(
        working_dir=str(tmp_path),
        max_turns=None,
        plan=None,
        verbose=None,
        autonomous=None,
        no_menu=False,
        player_provider=None,
        coach_provider=None,
        player_model=None,
        coach_model=None,
    )

    import asyncio

    asyncio.run(g3.run_go(args))

    assert exit_codes == [0]
