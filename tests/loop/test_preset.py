"""Пресет golden: порядок загрузки, none, guide и status."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Config, ConfigError, resolve_config


@pytest.fixture
def iso(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TERO_PRESET", raising=False)
    monkeypatch.delenv("TERO_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("TERO_JUDGE_PROVIDER", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return SimpleNamespace(home=home, work=work)


def _yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_golden_file_values():
    from src.config_preset import load_preset

    vals = load_preset("golden")
    assert vals["judge_provider"] == "codex" and vals["judge_model"] == "gpt-6-luna"
    assert vals["player_provider"] == "opencode"
    assert vals["player_model"] == "meta-model-api/muse-spark-1.3-contributor"
    assert vals["no_tester"] is True and vals["escalation"] == "situational"
    assert vals["escalation_provider"] == "claude" and vals["escalation_model"] == "sonnet"
    assert vals["jev_enabled"] is True and vals["decisions"] is True
    assert vals["max_debt"] == 3 and vals["stall_timeout_s"] == 600
    assert load_preset("none") == {}


def test_golden_is_default(iso):
    cfg = resolve_config({"working_dir": str(iso.work)})
    assert cfg.preset == "golden"
    assert cfg.judge_model == "gpt-6-luna"
    assert cfg.player_provider == "opencode"
    assert cfg.no_tester is True and cfg.jev_enabled is True and cfg.decisions is True
    assert cfg.escalation == "situational" and cfg.max_debt == 3 and cfg.stall_timeout_s == 600


def test_yaml_env_cli_override_preset(iso, monkeypatch):
    _yaml(iso.work / ".tero" / "config.yaml", "defaults:\n  judge_model: from-yaml\n")
    assert resolve_config({"working_dir": str(iso.work)}).judge_model == "from-yaml"
    monkeypatch.setenv("TERO_JUDGE_MODEL", "from-env")
    assert resolve_config({"working_dir": str(iso.work)}).judge_model == "from-env"
    cli = resolve_config({"working_dir": str(iso.work), "judge_model": "from-cli"})
    assert cli.judge_model == "from-cli"


def test_preset_none_disables(iso):
    _yaml(iso.home / ".tero" / "config.yaml", "defaults:\n  preset: none\n")
    cfg = resolve_config({"working_dir": str(iso.work)})
    assert cfg.preset == "none"
    assert cfg.judge_model == Config().judge_model
    assert cfg.jev_enabled is False


def test_preset_selection_order(iso, monkeypatch):
    _yaml(iso.home / ".tero" / "config.yaml", "defaults:\n  preset: none\n")
    _yaml(iso.work / ".tero" / "config.yaml", "defaults:\n  preset: golden\n")
    # проектный yaml поверх глобального
    assert resolve_config({"working_dir": str(iso.work)}).preset == "golden"
    monkeypatch.setenv("TERO_PRESET", "none")
    cfg = resolve_config({"working_dir": str(iso.work)})
    assert cfg.preset == "none" and cfg.judge_model == Config().judge_model
    cfg = resolve_config({"working_dir": str(iso.work), "preset": "golden"})
    assert cfg.preset == "golden" and cfg.judge_model == "gpt-6-luna"


def test_unknown_preset_is_error(iso):
    with pytest.raises(ConfigError):
        resolve_config({"working_dir": str(iso.work), "preset": "nope"})


def test_go_flag_preset(iso):
    from src.cli_entry import _config_from_args, build_parser

    args = build_parser().parse_args(["go", "-w", str(iso.work), "--preset", "none"])
    cfg = _config_from_args(args)
    assert cfg.preset == "none" and cfg.judge_model == Config().judge_model


def test_guide_shows_preset(iso, capsys):
    from src.commands.guide_cmds import run_guide

    assert run_guide(SimpleNamespace()) == 0
    head = capsys.readouterr().out.splitlines()[:20]
    text = "\n".join(head)
    assert "пресет: golden" in text
    assert "gpt-6-luna" in text


def test_status_shows_preset(tmp_path: Path, capsys):
    from src.commands.runs import run_status
    from src.loop.state import RunState

    run = tmp_path / "run"
    run.mkdir()
    RunState(run / "state.json").update(status="running", phase="p", phase_index=0, round=1,
                                        config={"preset": "golden"})
    assert run_status(SimpleNamespace(working_dir=str(tmp_path), run=str(run), json=False)) == 0
    assert "пресет: golden" in capsys.readouterr().out
