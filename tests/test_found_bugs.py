"""Focused regression tests for bugs discovered in the main Python app."""

from pathlib import Path

from src.config import resolve_config


def _empty_home(tmp_path, monkeypatch) -> Path:
    """Point HOME to an isolated directory so user-level config does not leak in."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    return home_dir


def test_global_batch_default_is_ignored_after_batch_mode_removal(
    tmp_path, monkeypatch
):
    """Removed `batch_mode` defaults must not reappear on resolved config."""
    home_dir = _empty_home(tmp_path, monkeypatch)
    home_config_dir = home_dir / ".g3"
    home_config_dir.mkdir()
    (home_config_dir / "config.yaml").write_text("defaults:\n  batch_mode: true\n")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    cfg = resolve_config({"working_dir": str(workspace)})

    assert not hasattr(cfg, "batch_mode")
