"""Config: provider names, resolve_config merge rules, apply_cli_args."""

import pytest

from types import SimpleNamespace

from src.config import (
    Config,
    UnknownProviderError,
    apply_cli_args,
    normalize_provider,
    resolve_config,
    split_role_spec,
)


def _isolate_home(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    return home_dir


def _write_yaml(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_defaults_match_canon():
    cfg = Config()
    assert cfg.player_provider == "muse"
    assert cfg.player_model == "muse-spark-1.3"
    assert cfg.judge_provider == "codex"
    assert cfg.judge_model == "gpt-5.6-terra"
    assert cfg.tester_provider == "gemini"
    assert cfg.tester_model == "gemini-3.8-flash"
    assert cfg.max_rounds == 15


def test_aliases_map_to_live_providers():
    assert normalize_provider("cursor-headless") == "cursor"


def test_unknown_provider_zai_rejected():
    with pytest.raises(UnknownProviderError, match="zai"):
        normalize_provider("zai")


def test_unknown_provider_kilo_rejected():
    with pytest.raises(UnknownProviderError, match="kilo"):
        normalize_provider("kilo")


def test_split_role_spec():
    assert split_role_spec("muse:muse-spark-1.3") == ("muse", "muse-spark-1.3")
    assert split_role_spec("codex") == ("codex", "")


def test_apply_cli_args_player_spec():
    cfg = apply_cli_args(
        Config(),
        SimpleNamespace(
            plan="plan.md",
            player_spec="muse:muse-spark-1.3",
            judge_spec="codex:gpt-5.6-terra",
            tester_spec=None,
            escalation_spec=None,
            no_escalation=False,
            test_command=None,
            test_command_scoped=None,
            max_rounds=12,
            verbose=False,
        ),
    )
    assert cfg.player_provider == "muse"
    assert cfg.judge_model == "gpt-5.6-terra"
    assert cfg.max_rounds == 12


def test_apply_cli_args_provider_switch_keeps_current_model():
    cfg = apply_cli_args(
        Config(player_provider="muse", player_model="muse-spark-1.2"),
        SimpleNamespace(
            plan=None,
            player_spec="codex",
            judge_spec=None,
            tester_spec=None,
            escalation_spec=None,
            no_escalation=False,
            test_command=None,
            test_command_scoped=None,
            max_rounds=None,
            verbose=False,
        ),
    )
    assert cfg.player_provider == "codex"
    assert cfg.player_model == "muse-spark-1.2"


def test_apply_cli_args_unknown_provider_rejected():
    with pytest.raises(UnknownProviderError, match="nope"):
        apply_cli_args(
            Config(),
            SimpleNamespace(
                plan=None,
                player_spec="nope",
                judge_spec=None,
                tester_spec=None,
                escalation_spec=None,
                no_escalation=False,
                test_command=None,
                test_command_scoped=None,
                max_rounds=None,
                verbose=False,
            ),
        )


def test_apply_cli_args_no_escalation_clears_all():
    cfg = apply_cli_args(
        Config(),
        SimpleNamespace(
            plan=None,
            player_spec=None,
            judge_spec=None,
            tester_spec=None,
            escalation_spec=None,
            no_escalation=True,
            test_command=None,
            test_command_scoped=None,
            max_rounds=None,
            verbose=False,
        ),
    )
    assert cfg.escalation_provider == ""
    assert cfg.escalation_model == ""
    assert cfg.escalation_effort == ""


def test_resolve_config_ignores_legacy_keys(tmp_path, monkeypatch):
    """(a) Old coach_*/batch_*/context_limit yaml keys don't break startup."""
    home_dir = _isolate_home(tmp_path, monkeypatch)
    _write_yaml(
        home_dir / ".g3" / "config.yaml",
        "defaults:\n"
        "  coach_provider: muse\n"
        "  batch_judge_provider: claude\n"
        "  context_limit: 80000\n",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = resolve_config({"working_dir": str(workspace)})

    assert cfg.player_provider == "muse"
    assert cfg.judge_provider == "codex"
    assert cfg.judge_model == "gpt-5.6-terra"


def test_resolve_config_escalation_empty_is_off(tmp_path, monkeypatch):
    """(b) yaml escalation_provider: '' disables escalation."""
    home_dir = _isolate_home(tmp_path, monkeypatch)
    _write_yaml(
        home_dir / ".g3" / "config.yaml",
        "defaults:\n  escalation_provider: ''\n",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    cfg = resolve_config({"working_dir": str(workspace)})

    assert cfg.escalation_provider == ""
    assert cfg.escalation_model == ""
    assert cfg.escalation_effort == ""


def test_resolve_config_judge_empty_keeps_default(tmp_path, monkeypatch):
    """(c) yaml judge_provider: '' keeps the codex default."""
    home_dir = _isolate_home(tmp_path, monkeypatch)
    _write_yaml(
        home_dir / ".g3" / "config.yaml",
        "defaults:\n  judge_provider: ''\n  judge_model: ''\n",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    cfg = resolve_config({"working_dir": str(workspace)})

    assert cfg.judge_provider == "codex"
    assert cfg.judge_model == "gpt-5.6-terra"


def test_resolve_config_unknown_provider_rejected(tmp_path, monkeypatch):
    """(d) yaml player_provider: nope raises UnknownProviderError."""
    home_dir = _isolate_home(tmp_path, monkeypatch)
    _write_yaml(home_dir / ".g3" / "config.yaml", "defaults:\n  player_provider: nope\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(UnknownProviderError, match="nope"):
        resolve_config({"working_dir": str(workspace)})


def test_apply_cli_args_judge_overrides_yaml(tmp_path, monkeypatch):
    """(e) CLI --judge claude:opus wins over yaml judge."""
    home_dir = _isolate_home(tmp_path, monkeypatch)
    _write_yaml(
        home_dir / ".g3" / "config.yaml",
        "defaults:\n  judge_provider: codex\n  judge_model: gpt-5.6-terra\n",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    base = resolve_config({"working_dir": str(workspace)})
    cfg = apply_cli_args(
        base,
        SimpleNamespace(
            plan=None,
            player_spec=None,
            judge_spec="claude:opus",
            tester_spec=None,
            escalation_spec=None,
            no_escalation=False,
            test_command=None,
            test_command_scoped=None,
            max_rounds=None,
            verbose=False,
        ),
    )
    assert cfg.judge_provider == "claude"
    assert cfg.judge_model == "opus"


def test_go_rejects_unknown_provider_with_exit_1(tmp_path, monkeypatch, capsys):
    """(g) tero go --player nope --no-menu exits 1 with 'неизвестный провайдер'."""
    import sys

    from src import cli_entry

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.delenv("G3_PLAYER_PROVIDER", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "plan.md").write_text("## One\n\n- [ ] do it\n")

    monkeypatch.setattr(
        sys,
        "argv",
        ["tero", "go", "--player", "nope", "--no-menu", "-w", str(workspace)],
    )
    with pytest.raises(SystemExit) as exc:
        cli_entry.main()
    assert exc.value.code == 1
    assert "неизвестный провайдер" in capsys.readouterr().out


def test_questionary_start_saves_and_quit_returns_none(monkeypatch):
    """(h) questionary 'Запустить' saves + returns Config, 'Выход' → None."""
    import sys

    import src.menu as menu
    from src.menu import _questionary_menu

    class DummyPrompt:
        def __init__(self, value):
            self._value = value

        def ask(self):
            return self._value

    def fake_select_start(_message, choices=None, **kwargs):
        return DummyPrompt("start")

    dummy_start = SimpleNamespace(
        Choice=lambda title, value: SimpleNamespace(title=title, value=value),
        Separator=lambda text: SimpleNamespace(separator=text),
        select=fake_select_start,
    )
    monkeypatch.setitem(sys.modules, "questionary", dummy_start)
    monkeypatch.setattr(menu, "QUESTIONARY_AVAILABLE", True)

    called = []
    monkeypatch.setattr(
        menu, "_save_global_default", lambda cfg: called.append(cfg)
    )
    result = _questionary_menu(Config())
    assert isinstance(result, Config)
    assert len(called) == 1

    def fake_select_quit(_message, choices=None, **kwargs):
        return DummyPrompt("quit")

    dummy_quit = SimpleNamespace(
        Choice=lambda title, value: SimpleNamespace(title=title, value=value),
        Separator=lambda text: SimpleNamespace(separator=text),
        select=fake_select_quit,
    )
    monkeypatch.setitem(sys.modules, "questionary", dummy_quit)
    assert _questionary_menu(Config()) is None


def test_fallback_menu_escalation_off(monkeypatch):
    """(i) fallback 'e' → 'off' disables escalation."""
    from src.menu import _fallback_menu

    answers = iter(["e", "off", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))
    monkeypatch.setattr("src.menu._save_global_default", lambda _cfg: None)

    result = _fallback_menu(Config(escalation_provider="claude"))
    assert result is not None
    assert result.escalation_provider == ""
    assert result.escalation_model == ""


def test_plan_label_shows_phase_counts(tmp_path):
    """(j) plan signature shows 'фаз N, готово M' for a 2-phase plan, one [x]."""
    from src.menu import _plan_label

    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Demo\n\n## One\n\n- [x] done\n\n## Two\n\n- [ ] todo\n"
    )
    label = _plan_label(Config(working_dir=str(tmp_path), plan_file="plan.md"))
    assert "фаз 2" in label
    assert "готово 1" in label

    missing = _plan_label(Config(working_dir=str(tmp_path), plan_file="nope.md"))
    assert "не найден" in missing


def test_apply_cli_args_role_spec_resets_effort():
    """A provider given on the CLI drops the effort saved for the previous one."""
    cfg = Config(judge_provider="codex", judge_effort="max")
    ns = SimpleNamespace(judge_spec="claude:opus")

    out = apply_cli_args(cfg, ns)

    assert (out.judge_provider, out.judge_model, out.judge_effort) == ("claude", "opus", "")


def test_apply_cli_args_escalation_provider_after_off_gets_default_model():
    """--escalation claude after escalation was switched off must still escalate."""
    cfg = Config(escalation_provider="", escalation_model="", escalation_effort="")
    ns = SimpleNamespace(escalation_spec="claude")

    out = apply_cli_args(cfg, ns)

    assert (out.escalation_provider, out.escalation_model) == ("claude", "opus")
