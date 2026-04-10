"""Regression tests for provider naming and parser validation."""

from pathlib import Path

import pytest

from src.cli_entry import build_parser
from src.config import resolve_config


def _isolate_home(tmp_path, monkeypatch) -> Path:
    """Point HOME at an empty temp dir so ~/.g3 and ~/.zshrc do not leak in."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    return home_dir


def test_project_defaults_preserve_canonical_provider_names(tmp_path, monkeypatch):
    """Canonical provider names from project config should remain unchanged."""
    _isolate_home(tmp_path, monkeypatch)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = workspace / ".g3"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "defaults:\n"
        "  player_provider: zai\n"
        "  coach_provider: zai\n"
    )

    cfg = resolve_config({"working_dir": str(workspace)})

    assert cfg.player_provider == "zai"
    assert cfg.coach_provider == "zai"


def test_cli_parser_rejects_invalid_provider_names():
    """CLI should only accept configured canonical provider names."""
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "go",
                "--player-provider",
                "not-a-provider",
                "--coach-provider",
                "still-not-a-provider",
            ]
        )
