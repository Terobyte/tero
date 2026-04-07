"""Tests for CLI integration behavior."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import g3
from src.config import resolve_config


def _load_packaged_g3_module():
    module_path = Path(__file__).resolve().parent.parent / "g3.py"
    spec = importlib.util.spec_from_file_location("packaged_g3_for_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_run_go_respects_project_config_defaults_when_flags_omitted(tmp_path, monkeypatch):
    """Argparse defaults should not clobber values from .g3/config.yaml."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    config_dir = tmp_path / ".g3"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "defaults:\n"
        "  max_turns: 3\n"
        "  plan_file: custom.md\n"
        "  player_provider: black\n"
        "  coach_provider: black\n"
    )
    (tmp_path / "custom.md").write_text("1. Build it\n")

    captured = {}

    class FakeSession:
        def __init__(self, config, requirements, plan_file_path=""):
            captured["config"] = config
            captured["requirements"] = requirements
            self.plan_file_path = plan_file_path

        async def run(self):
            return SimpleNamespace(approved=True)

    exit_codes = []

    monkeypatch.setattr(g3, "CoachPlayerSession", FakeSession)
    monkeypatch.setattr(g3.sys, "exit", exit_codes.append)

    args = SimpleNamespace(
        working_dir=str(tmp_path),
        max_turns=None,
        plan=None,
        verbose=None,
        autonomous=None,
        player_provider=None,
        coach_provider=None,
        player_model=None,
        coach_model=None,
    )

    import asyncio

    asyncio.run(g3.run_go(args))

    assert captured["config"].max_turns == 3
    assert captured["config"].plan_file == "custom.md"
    assert captured["requirements"] == "1. Build it\n"
    assert exit_codes == [0]


def test_run_go_does_not_open_menu_inside_async_context(tmp_path, monkeypatch):
    """Interactive menu must be handled before asyncio.run(), not inside run_go()."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    (tmp_path / "requirements.md").write_text("1. Build it\n")

    class FakeSession:
        def __init__(self, config, requirements, plan_file_path=""):
            self.config = config
            self.requirements = requirements
            self.plan_file_path = plan_file_path

        async def run(self):
            return SimpleNamespace(approved=True)

    exit_codes = []

    monkeypatch.setattr(g3, "CoachPlayerSession", FakeSession)
    monkeypatch.setattr(g3.sys, "exit", exit_codes.append)
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

    asyncio.run(g3.run_go(args))

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
        tdd_mode=None,
        test_command=None,
        test_timeout_s=None,
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

    config = g3._prepare_go_config(args)

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
        "  player_provider: kilo\n"
        "  player_model: kilo/xiaomi/mimo-v2-pro:free\n"
        "  coach_provider: kilo\n"
        "  coach_model: kilo/minimax/minimax-m2.5:free\n"
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

    assert cfg.player_provider == "kilo"
    assert cfg.player_model == "kilo/xiaomi/mimo-v2-pro:free"
    assert cfg.coach_provider == "kilo"
    assert cfg.coach_model == "kilo/minimax/minimax-m2.5:free"


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
        "  player_provider: zai\n"
        "  player_model: glm-5.1\n"
        "  coach_provider: zai\n"
        "  coach_model: glm-5.1\n"
        "  tdd_mode: true\n"
        "  code_review: true\n"
        "  preplan_mode: true\n"
        "  test_writer_provider: codex\n"
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

    assert cfg.player_provider == "zai"
    assert cfg.player_model == "glm-5.1"
    assert cfg.coach_provider == "zai"
    assert cfg.coach_model == "glm-5.1"
    assert cfg.tdd_mode is False
    assert cfg.code_review is False
    assert cfg.preplan_mode is False


@pytest.mark.parametrize(
    ("module", "module_name"),
    [
        (g3, "root"),
        (_load_packaged_g3_module(), "packaged"),
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
            batch_mode=False,
        )

    async def fake_run_go(args, config=None):
        captured["run_go_args"] = args
        captured["run_go_config"] = config

    def fake_asyncio_run(coro):
        coro.close()

    monkeypatch.setattr(module, "_prepare_go_config", fake_prepare)
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
        (g3, "root"),
        (_load_packaged_g3_module(), "packaged"),
    ],
)
def test_main_accepts_zai_provider_for_coach_and_player(monkeypatch, module, module_name):
    """Both entrypoints must accept zai in CLI parsing."""
    captured = {}

    def fake_prepare(args):
        captured["args"] = args
        return SimpleNamespace(
            working_dir=".",
            plan_file="requirements.md",
            batch_mode=False,
        )

    async def fake_run_go(args, config=None):
        captured["run_go_args"] = args
        captured["run_go_config"] = config

    def fake_asyncio_run(coro):
        coro.close()

    monkeypatch.setattr(module, "_prepare_go_config", fake_prepare)
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
            "zai",
            "--coach-provider",
            "zai",
            "--player-model",
            "glm-5.1",
            "--coach-model",
            "glm-5.1",
        ],
    )

    module.main()

    assert captured["args"].player_provider == "zai", module_name
    assert captured["args"].coach_provider == "zai", module_name
    assert captured["args"].player_model == "glm-5.1", module_name
    assert captured["args"].coach_model == "glm-5.1", module_name


@pytest.mark.parametrize(
    ("module", "module_name"),
    [
        (g3, "root"),
        (_load_packaged_g3_module(), "packaged"),
    ],
)
def test_main_accepts_kilo_provider_for_coach_and_player(monkeypatch, module, module_name):
    """Both entrypoints must accept kilo in CLI parsing."""
    captured = {}

    def fake_prepare(args):
        captured["args"] = args
        return SimpleNamespace(
            working_dir=".",
            plan_file="requirements.md",
            batch_mode=False,
        )

    async def fake_run_go(args, config=None):
        captured["run_go_args"] = args
        captured["run_go_config"] = config

    def fake_asyncio_run(coro):
        coro.close()

    monkeypatch.setattr(module, "_prepare_go_config", fake_prepare)
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
            "kilo",
            "--coach-provider",
            "kilo",
            "--player-model",
            "kilo/xiaomi/mimo-v2-pro:free",
            "--coach-model",
            "kilo/minimax/minimax-m2.5:free",
        ],
    )

    module.main()

    assert captured["args"].player_provider == "kilo", module_name
    assert captured["args"].coach_provider == "kilo", module_name
    assert captured["args"].player_model == "kilo/xiaomi/mimo-v2-pro:free", module_name
    assert captured["args"].coach_model == "kilo/minimax/minimax-m2.5:free", module_name


def test_importing_g3_outside_repo_root_resolves_packaged_entrypoint():
    """Document current import behavior outside the repo root."""
    packaged = _load_packaged_g3_module()
    assert packaged.__file__.endswith("/g3.py")


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
    assert args.batch_mode is None
    assert args.tdd_mode is None


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
        "--coach-provider", "black",
        "--player-model", "claude-sonnet",
        "--batch",
        "--tdd",
        "--test-command", "pytest -x",
        "--test-timeout-s", "120",
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
    assert args.coach_provider == "black"
    assert args.player_model == "claude-sonnet"
    assert args.batch_mode is True
    assert args.tdd_mode is True
    assert args.test_command == "pytest -x"
    assert args.test_timeout_s == 120
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
        "-pp", "kilo",
        "-cp", "kilo",
        "-pm", "kilo/test",
        "-cm", "kilo/test2",
    ])

    assert args.max_turns == 3
    assert args.plan == "req.md"
    assert args.player_provider == "kilo"
    assert args.coach_provider == "kilo"
    assert args.player_model == "kilo/test"
    assert args.coach_model == "kilo/test2"


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
    """build_parser() must parse fallback-chain and explicit preplan flags."""
    from src.cli_entry import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "go",
        "--player-fallback-chain", "claude,black",
        "--coach-fallback-chain", "black,claude",
        "--chain-retry-wait", "2.5",
        "--chain-max-retries", "3",
        "--preplan-provider", "claude",
        "--preplan-model", "gpt-4",
        "--preplan",
        "--compact-threshold", "0.8",
        "--max-continuation", "5",
        "--max-review-iterations", "2",
    ])

    assert args.player_fallback_chain == "claude,black"
    assert args.coach_fallback_chain == "black,claude"
    assert args.chain_retry_wait_s == 2.5
    assert args.chain_max_retries == 3
    assert args.preplan_provider == "claude"
    assert args.preplan_model == "gpt-4"
    assert args.preplan_mode is True
    assert args.compact_threshold == 0.8
    assert args.max_continuation_attempts == 5
    assert args.max_review_iterations == 2


def test_smoke_parse_go_with_no_preplan_flag():
    """build_parser() must still allow explicit preplan disable."""
    from src.cli_entry import build_parser

    parser = build_parser()
    args = parser.parse_args(["go", "--no-preplan"])

    assert args.preplan_mode is False


def test_smoke_parse_requires_subcommand():
    """build_parser() must reject invocation with no subcommand."""
    from src.cli_entry import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
