"""Regression tests for canonical Blackbox provider naming."""

from pathlib import Path

import pytest

from src.cli_entry import build_parser
from src.config import CcgEnv, resolve_config


def _isolate_home(tmp_path, monkeypatch) -> Path:
    """Point HOME at an empty temp dir so ~/.g3 and ~/.zshrc do not leak in."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    return home_dir


def test_project_defaults_normalize_legacy_ccg_aliases_to_black(tmp_path, monkeypatch):
    """Legacy `ccg` names should collapse onto canonical `black`."""
    _isolate_home(tmp_path, monkeypatch)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = workspace / ".g3"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "defaults:\n"
        "  player_provider: ccg2\n"
        "  coach_provider: ccg\n"
    )

    cfg = resolve_config({"working_dir": str(workspace)})

    assert cfg.player_provider == "black"
    assert cfg.coach_provider == "black"


def test_black_prefers_generic_blackbox_key(tmp_path, monkeypatch):
    """Canonical `black` should use the generic Blackbox token when present."""
    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setenv("BLACKBOX_API_KEY", "generic-token")
    monkeypatch.setenv("BLACKBOX_ACCOUNT_A_TOKEN", "token-a")
    monkeypatch.setenv("BLACKBOX_ACCOUNT_B_TOKEN", "token-b")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    env = CcgEnv.for_account("black")

    assert env.auth_token == "generic-token"


def test_cli_parser_rejects_legacy_ccg_aliases():
    """CLI should only accept canonical provider names."""
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "go",
                "--player-provider",
                "ccg2",
                "--coach-provider",
                "ccg",
            ]
        )
