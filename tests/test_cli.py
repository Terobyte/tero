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
        player_provider=None,
        player_model=None,
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
        player_provider=None,
        player_model=None,
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

    asyncio.run(cli_entry.run_go(args))

    assert exit_codes == [0]


def test_prepare_go_config_still_opens_menu_when_autonomous_comes_from_defaults(
    tmp_path, monkeypatch
):
    """Saved defaults must not make the menu unreachable."""
    config_dir = tmp_path / ".g3"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("defaults:\n  autonomous: true\n")

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
        code_review=None,
        review_provider=None,
        review_model=None,
        coach_retry_max=None,
        coach_fallback_provider=None,
        coach_fallback_model=None,
        context_limit=None,
        compact_threshold=None,
        max_continuation_attempts=None,
    )

    seen = {}

    def fake_menu(config):
        seen["autonomous"] = config.autonomous
        return config

    monkeypatch.setattr("src.menu.run_settings_menu", fake_menu)

    config = cli_entry.prepare_go_config(args)

    assert seen["autonomous"] is True
    assert config.autonomous is True


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
            "max_turns": None,
            "plan_file": None,
            "verbose": None,
            "autonomous": None,
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
            "max_turns": None,
            "plan_file": None,
            "verbose": None,
            "autonomous": None,
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


@pytest.mark.parametrize(
    ("module", "module_name"),
    [
        (cli_entry, "packaged"),
    ],
)
def test_main_accepts_opencode_provider_for_coach_and_player(monkeypatch, module, module_name):
    """Both entrypoints must accept opencode in CLI parsing."""
    captured = {}

    def fake_prepare(args):
        captured["args"] = args
        return SimpleNamespace(
            working_dir=".",
            plan_file="requirements.md",
        )

    async def fake_run_go(args, config=None):
        captured["run_go_args"] = args
        captured["run_go_config"] = config

    def fake_asyncio_run(coro):
        coro.close()

    monkeypatch.setattr(module, "prepare_go_config", fake_prepare)
    monkeypatch.setattr(module, "run_go", fake_run_go)
    monkeypatch.setattr(module.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "tero",
            "go",
            "--no-menu",
            "--player-provider",
            "opencode",
            "--coach-provider",
            "opencode",
            "--player-model",
            "opencode/mimo-v2-pro-free",
            "--coach-model",
            "openrouter/moonshotai/kimi-k2:free",
        ],
    )

    module.main()

    assert captured["args"].player_provider == "opencode", module_name
    assert captured["args"].coach_provider == "opencode", module_name
    assert captured["args"].player_model == "opencode/mimo-v2-pro-free", module_name
    assert (
        captured["args"].coach_model == "openrouter/moonshotai/kimi-k2:free"
    ), module_name


@pytest.mark.parametrize(
    ("module", "module_name"),
    [
        (cli_entry, "packaged"),
    ],
)
def test_main_accepts_muse_provider_for_coach_and_player(monkeypatch, module, module_name):
    """Both entrypoints must accept muse in CLI parsing."""
    captured = {}

    def fake_prepare(args):
        captured["args"] = args
        return SimpleNamespace(
            working_dir=".",
            plan_file="requirements.md",
        )

    async def fake_run_go(args, config=None):
        captured["run_go_args"] = args
        captured["run_go_config"] = config

    def fake_asyncio_run(coro):
        coro.close()

    monkeypatch.setattr(module, "prepare_go_config", fake_prepare)
    monkeypatch.setattr(module, "run_go", fake_run_go)
    monkeypatch.setattr(module.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "tero",
            "go",
            "--no-menu",
            "--player-provider",
            "muse",
            "--coach-provider",
            "muse",
            "--player-model",
            "muse-spark-1.3",
            "--coach-model",
            "muse-spark-1.3",
        ],
    )

    module.main()

    assert captured["args"].player_provider == "muse", module_name
    assert captured["args"].coach_provider == "muse", module_name
    assert captured["args"].player_model == "muse-spark-1.3", module_name
    assert captured["args"].coach_model == "muse-spark-1.3", module_name


@pytest.mark.parametrize(
    ("module", "module_name"),
    [
        (cli_entry, "packaged"),
    ],
)
def test_main_accepts_gemini_provider_for_coach_and_player(monkeypatch, module, module_name):
    """Both entrypoints must accept gemini in CLI parsing."""
    captured = {}

    def fake_prepare(args):
        captured["args"] = args
        return SimpleNamespace(
            working_dir=".",
            plan_file="requirements.md",
        )

    async def fake_run_go(args, config=None):
        captured["run_go_args"] = args
        captured["run_go_config"] = config

    def fake_asyncio_run(coro):
        coro.close()

    monkeypatch.setattr(module, "prepare_go_config", fake_prepare)
    monkeypatch.setattr(module, "run_go", fake_run_go)
    monkeypatch.setattr(module.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "tero",
            "go",
            "--no-menu",
            "--player-provider",
            "gemini",
            "--coach-provider",
            "gemini",
            "--player-model",
            "gemini-3.8-flash",
            "--coach-model",
            "gemini-3.1-pro-preview",
        ],
    )

    module.main()

    assert captured["args"].player_provider == "gemini", module_name
    assert captured["args"].coach_provider == "gemini", module_name
    assert captured["args"].player_model == "gemini-3.8-flash", module_name
    assert captured["args"].coach_model == "gemini-3.1-pro-preview", module_name


def test_installed_script_points_at_package_entrypoint():
    """The console script should resolve inside the installed package."""
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert 'tero = "src.cli_entry:main"' in pyproject


def test_smoke_build_parser_and_parse_go_without_error():
    """build_parser() must parse `go` with no flags without error."""
    from src.cli_entry import build_parser

    parser = build_parser()
    args = parser.parse_args(["go"])

    assert args.command == "go"
    assert args.max_turns is None
    assert args.plan is None
    assert args.verbose is None
    assert args.autonomous is None
    assert args.working_dir == "."
    assert args.player_provider is None
    assert args.coach_provider is None
    assert args.player_model is None
    assert args.coach_model is None


def test_smoke_parse_go_with_common_flags():
    """build_parser() must parse `go` with typical flag combinations."""
    from src.cli_entry import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "go",
        "-n", "5",
        "-p", "plan.md",
        "-v",
        "--autonomous",
        "--no-menu",
        "-w", "/tmp/project",
        "--player-provider", "claude",
        "--coach-provider", "muse",
        "--player-model", "claude-sonnet",
        "--code-review",
        "--context-limit", "8000",
    ])

    assert args.command == "go"
    assert args.max_turns == 5
    assert args.plan == "plan.md"
    assert args.verbose is True
    assert args.autonomous is True
    assert args.no_menu is True
    assert args.working_dir == "/tmp/project"
    assert args.player_provider == "claude"
    assert args.coach_provider == "muse"
    assert args.player_model == "claude-sonnet"
    assert args.code_review is True
    assert args.context_limit == 8000


def test_smoke_parse_go_with_short_flags():
    """build_parser() must accept short flag aliases."""
    from src.cli_entry import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "go",
        "-n", "3",
        "-p", "req.md",
        "-w", ".",
        "-pp", "muse",
        "-cp", "muse",
        "-pm", "muse-spark-1.3",
        "-cm", "muse-spark-1.2",
    ])

    assert args.max_turns == 3
    assert args.plan == "req.md"
    assert args.player_provider == "muse"
    assert args.coach_provider == "muse"
    assert args.player_model == "muse-spark-1.3"
    assert args.coach_model == "muse-spark-1.2"


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


def test_smoke_parse_go_with_fallback_chain_flags():
    """build_parser() must parse fallback-chain flags."""
    from src.cli_entry import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "go",
        "--player-fallback-chain", "claude,muse",
        "--coach-fallback-chain", "muse,claude",
        "--chain-retry-wait", "2.5",
        "--chain-max-retries", "3",
        "--compact-threshold", "0.8",
        "--max-continuation", "5",
        "--max-review-iterations", "2",
    ])

    assert args.player_fallback_chain == "claude,muse"
    assert args.coach_fallback_chain == "muse,claude"
    assert args.chain_retry_wait_s == 2.5
    assert args.chain_max_retries == 3
    assert args.compact_threshold == 0.8
    assert args.max_continuation_attempts == 5
    assert args.max_review_iterations == 2


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


