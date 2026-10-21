"""Regression tests for legacy provider names (spec §8)."""

from pathlib import Path

from src.config import resolve_config


def _isolate_home(tmp_path, monkeypatch) -> Path:
    """Point HOME at an empty temp dir so ~/.tero does not leak in."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    return home_dir


def test_project_alias_muse_normalizes_to_muse(tmp_path, monkeypatch):
    """Legacy `muse` alias must resolve to canonical `muse`."""
    _isolate_home(tmp_path, monkeypatch)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = workspace / ".tero"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "defaults:\n"
        "  player_provider: muse\n"
    )

    cfg = resolve_config({"working_dir": str(workspace)})

    assert cfg.player_provider == "muse"


def test_project_alias_cursor_cli_normalizes_to_cursor(tmp_path, monkeypatch):
    """Legacy `cursor-cli` alias must resolve to canonical `cursor`."""
    _isolate_home(tmp_path, monkeypatch)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = workspace / ".tero"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "defaults:\n"
        "  player_provider: cursor-cli\n"
    )

    cfg = resolve_config({"working_dir": str(workspace)})

    assert cfg.player_provider == "cursor"
