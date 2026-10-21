"""Tests for CLI integration behavior."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src import cli_entry
from src.config import resolve_config


def test_run_go_uses_v4_loop(tmp_path, monkeypatch):
    """tero go calls the v4 loop, not the old batch executor."""
    (tmp_path / "plan.md").write_text("## One\n\n- [ ] do it\n")
    captured = {}

    async def fake_run(cfg, *, agents, check_ready=None):
        captured["plan"] = cfg.plan_file
        captured["player"] = cfg.player_provider
        from src.loop.run import RunOutcome

        return RunOutcome(status="ok", exit_code=0, reason="ship")

    exit_codes = []
    monkeypatch.setattr("src.loop.run.run", fake_run)
    monkeypatch.setattr("src.cli_entry.sys.exit", exit_codes.append)

    args = SimpleNamespace(
        working_dir=str(tmp_path),
        plan="plan.md",
        verbose=False,
        no_menu=True,
        player_spec="muse:muse-spark-1.3",
        judge_spec=None,
        tester_spec=None,
        escalation_spec=None,
        no_escalation=False,
        test_command=None,
        test_command_scoped=None,
        max_rounds=None,
    )

    import asyncio

    asyncio.run(cli_entry.run_go(args))

    assert captured["plan"] == "plan.md"
    assert captured["player"] == "muse"
    assert exit_codes == [0]


def test_run_go_applies_menu_config_even_when_plan_flag_set(tmp_path, monkeypatch):
    """▶ Запустить must copy Judge/tests from the menu, even if -p was passed."""
    (tmp_path / "plan.md").write_text("## One\n\n- [ ] do it\n")
    captured = {}

    async def fake_run(cfg, *, agents, check_ready=None):
        captured["cfg"] = cfg
        from src.loop.run import RunOutcome

        return RunOutcome(status="ok", exit_code=0, reason="ship")

    exit_codes = []
    monkeypatch.setattr("src.loop.run.run", fake_run)
    monkeypatch.setattr("src.cli_entry.sys.exit", exit_codes.append)

    from src.config import Config

    args = SimpleNamespace(
        working_dir=str(tmp_path),
        plan="plan.md",
        verbose=False,
        no_menu=False,
        player_spec=None,
        judge_spec=None,
        tester_spec=None,
        escalation_spec=None,
        no_escalation=False,
        test_command=None,
        test_command_scoped=None,
        max_rounds=None,
    )
    config = Config(
        working_dir=str(tmp_path),
        plan_file="plan.md",
        player_provider="opencode",
        player_model="opencode/muse-spark-1.3-contributor-free",
        batch_judge_provider="claude",
        batch_judge_model="claude-opus-5",
        tester_provider="gemini",
        tester_model="gemini-3.8-flash",
        test_command="./scripts/test.sh",
    )

    import asyncio

    asyncio.run(cli_entry.run_go(args, config=config))

    cfg = captured["cfg"]
    assert cfg.player_provider == "opencode"
    assert cfg.judge_provider == "claude"
    assert cfg.judge_model == "claude-opus-5"
    assert cfg.test_command == "./scripts/test.sh"
    assert exit_codes == [0]


def test_run_go_does_not_open_menu_inside_async_context(tmp_path, monkeypatch):
    """Interactive menu must be handled before asyncio.run(), not inside run_go()."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    (tmp_path / "requirements.md").write_text("1. Build it\n")

    async def fake_run(cfg, *, agents, check_ready=None):
        from src.loop.run import RunOutcome

        return RunOutcome(status="ok", exit_code=0, reason="ship")

    exit_codes = []

    monkeypatch.setattr("src.loop.run.run", fake_run)
    monkeypatch.setattr("src.cli_entry.sys.exit", exit_codes.append)
    monkeypatch.setattr(
        "src.menu.run_settings_menu",
        lambda config: (_ for _ in ()).throw(AssertionError("menu should not be opened")),
    )

    args = SimpleNamespace(
        working_dir=str(tmp_path),
        plan=None,
        verbose=None,
        no_menu=False,
    )

    import asyncio

    asyncio.run(cli_entry.run_go(args))

    assert exit_codes == [0]


def test_resolve_config_loads_saved_global_defaults(tmp_path, monkeypatch):
    """Settings saved to ~/.g3/config.yaml should be applied on later runs."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    global_config_dir = home_dir / ".g3"
    global_config_dir.mkdir()
    (global_config_dir / "config.yaml").write_text(
        "defaults:\n"
        "  player_provider: muse\n"
        "  player_model: muse-spark-1.3\n"
        "  coach_provider: muse\n"
        "  coach_model: muse-spark-1.3\n"
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home_dir))

    cfg = resolve_config(
        {
            "working_dir": str(workspace),
            "plan_file": None,
            "verbose": None,
            "player_provider": None,
            "coach_provider": None,
            "player_model": None,
            "coach_model": None,
        }
    )

    assert cfg.player_provider == "muse"
    assert cfg.player_model == "muse-spark-1.3"
    assert cfg.coach_provider == "muse"
    assert cfg.coach_model == "muse-spark-1.3"


def test_resolve_config_filters_opt_in_runtime_modes_from_global_defaults(
    tmp_path, monkeypatch
):
    """Global defaults must not silently enable opt-in runtime modes."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    global_config_dir = home_dir / ".g3"
    global_config_dir.mkdir()
    (global_config_dir / "config.yaml").write_text(
        "defaults:\n"
        "  player_provider: muse\n"
        "  player_model: muse-spark-1.3\n"
        "  coach_provider: muse\n"
        "  coach_model: muse-spark-1.3\n"
        "  code_review: true\n"
        "  review_provider: codex\n"
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home_dir))

    cfg = resolve_config(
        {
            "working_dir": str(workspace),
            "plan_file": None,
            "verbose": None,
            "player_provider": None,
            "coach_provider": None,
            "player_model": None,
            "coach_model": None,
        }
    )

    assert cfg.player_provider == "muse"
    assert cfg.player_model == "muse-spark-1.3"
    assert cfg.coach_provider == "muse"
    assert cfg.coach_model == "muse-spark-1.3"
    assert cfg.code_review is False
    assert not hasattr(cfg, "tdd_mode")
    assert not hasattr(cfg, "preplan_mode")


def test_installed_script_points_at_package_entrypoint():
    """The console script should resolve inside the installed package."""
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert 'tero = "src.cli_entry:main"' in pyproject


def test_smoke_parse_history_without_error():
    """build_parser() must parse `history` without error."""
    from src.cli_entry import build_parser

    parser = build_parser()
    args = parser.parse_args(["history"])

    assert args.command == "history"
    assert args.limit == 10
    assert args.working_dir == "."


def test_smoke_parse_history_with_flags():
    """build_parser() must parse `history` with flags."""
    from src.cli_entry import build_parser

    parser = build_parser()
    args = parser.parse_args(["history", "-l", "5", "-w", "/tmp/proj"])

    assert args.command == "history"
    assert args.limit == 5
    assert args.working_dir == "/tmp/proj"


def test_removed_go_mode_flags_are_rejected():
    """Removed simplification flags should no longer parse."""
    from src.cli_entry import build_parser

    parser = build_parser()
    for flag in ("--batch", "--tdd", "--preplan", "--no-preplan"):
        with pytest.raises(SystemExit):
            parser.parse_args(["go", flag])


def test_smoke_parse_requires_subcommand():
    """build_parser() must reject invocation with no subcommand."""
    from src.cli_entry import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


