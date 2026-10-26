"""HUD tests never touch the real `~/.tero` lock, target or registry."""

from __future__ import annotations

import pytest

from src.hud import registry, single


@pytest.fixture(autouse=True)
def _hud_home(monkeypatch, tmp_path_factory):
    # own dir: some tests use tmp_path as a git repo that must stay clean
    home = tmp_path_factory.mktemp("hud-home")
    monkeypatch.setattr(single, "lock_path", lambda: home / "hud.lock")
    monkeypatch.setattr(single, "target_path", lambda: home / "hud-target")
    monkeypatch.setattr(registry, "registry_dir", lambda: home / "registry")
    return home
