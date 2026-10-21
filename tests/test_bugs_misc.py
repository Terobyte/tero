"""Red regression tests for bugs B47-B51.

B47: src/process_guard.py - AccessDenied on proc.children() interrupts cleanup.
B48: src/config.py - resolve_config resets model when provider alias is used in env.
B49: src/config.py - _coerce_value returns empty string for int field.
B50: src/loop/git.py - exclude_tero ignores foo.tero/ suffix and skips .tero/.
B51: src/menu.py - _save_global_default replaces home with ~ anywhere in path.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import psutil

from src.config import Config, resolve_config
from src.loop.git import exclude_tero
from src.menu import _save_global_default
from src.process_guard import ProcessGuard


# ---------------------------------------------------------------- B47


def test_b47_process_guard_access_denied_does_not_escape(monkeypatch):
    """B47: AccessDenied on proc.children() must not escape kill_new_processes."""
    guard = ProcessGuard()

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

        def children(self, recursive: bool = True):
            if self.pid == 1001:
                raise psutil.AccessDenied(pid=self.pid)
            return []

    monkeypatch.setattr(psutil, "Process", FakeProc)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    class OrderedSet(set):
        def __sub__(self, other):
            return OrderedSet(super().__sub__(other))

        def __iter__(self):
            return iter(sorted(super().__iter__()))

    monkeypatch.setattr(guard, "snapshot_pids", lambda: OrderedSet({1001, 1002}))

    exc: Exception | None = None
    try:
        guard.kill_new_processes(before=set())
    except Exception as e:
        exc = e

    assert exc is None


def test_b47_process_guard_access_denied_does_not_abort_cleanup(monkeypatch):
    """B47: AccessDenied on one process must not abort cleanup of remaining processes."""
    guard = ProcessGuard()

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

        def children(self, recursive: bool = True):
            if self.pid == 1001:
                raise psutil.AccessDenied(pid=self.pid)
            return []

    monkeypatch.setattr(psutil, "Process", FakeProc)

    killed: list[int] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))

    class OrderedSet(set):
        def __sub__(self, other):
            return OrderedSet(super().__sub__(other))

        def __iter__(self):
            return iter(sorted(super().__iter__()))

    monkeypatch.setattr(guard, "snapshot_pids", lambda: OrderedSet({1001, 1002}))

    try:
        guard.kill_new_processes(before=set())
    except psutil.AccessDenied:
        pass

    assert 1002 in killed


# ---------------------------------------------------------------- B48


def test_b48_env_provider_alias_does_not_reset_model(tmp_path, monkeypatch):
    """B48: TERO_PLAYER_PROVIDER alias (e.g. cursor-cli) when current provider is already
    cursor must not reset player_model."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    proj_dir = tmp_path / "proj"
    tero_dir = proj_dir / ".tero"
    tero_dir.mkdir(parents=True)
    (tero_dir / "config.yaml").write_text(
        "defaults:\n"
        "  player_provider: cursor\n"
        "  player_model: custom-cursor-model\n"
    )

    monkeypatch.setenv("TERO_PLAYER_PROVIDER", "cursor-cli")

    cfg = resolve_config({"working_dir": str(proj_dir)})
    assert cfg.player_provider == "cursor"
    assert cfg.player_model == "custom-cursor-model"


# ---------------------------------------------------------------- B49


def test_b49_empty_int_field_in_config_remains_int(tmp_path, monkeypatch):
    """B49: Empty value for int field (max_rounds) in yaml must remain int, not empty string."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    proj_dir = tmp_path / "proj"
    tero_dir = proj_dir / ".tero"
    tero_dir.mkdir(parents=True)
    (tero_dir / "config.yaml").write_text("defaults:\n  max_rounds: ''\n")

    cfg = resolve_config({"working_dir": str(proj_dir)})
    assert isinstance(cfg.max_rounds, int)


# ---------------------------------------------------------------- B50


def test_b50_exclude_tero_preserves_single_line_when_suffix_matches(tmp_path):
    """B50: If .git/info/exclude ends with 'foo.tero/', exclude_tero must still add '.tero/'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    exclude_path = repo / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("foo.tero/\n")

    exclude_tero(str(repo))

    assert ".tero/" in exclude_path.read_text().splitlines()


# ---------------------------------------------------------------- B51


def test_b51_save_global_default_replaces_home_only_at_start_of_path(
    tmp_path, monkeypatch, capsys
):
    """B51: _save_global_default replaces home with ~ only at start of path, not in middle."""
    fake_home = tmp_path / "user"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    working_dir = f"/data{fake_home}/x"
    cfg = Config(working_dir=working_dir)

    _save_global_default(cfg)

    out = capsys.readouterr().out
    assert f"(рабочая папка: {working_dir})" in out
