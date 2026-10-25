"""Shared CLI implementation for both repository entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.config import Config, ConfigError, UnknownProviderError, apply_cli_args, resolve_config
from src.commands import resolve_run_dir
from src.commands.inbox_cmds import run_answer, run_compact
from src.commands.runs import run_history, run_review, run_status, run_wait

__all__ = [
    "build_parser",
    "main",
    "prepare_go_config",
    "run_answer",
    "run_compact",
    "run_go",
    "run_history",
    "run_resume",
    "run_review",
    "run_status",
    "run_wait",
]


def _config_from_args(args) -> Config:
    """Resolve config for `tero go` from CLI-like args, then apply CLI flags."""
    config = resolve_config(
        {
            "working_dir": args.working_dir,
            "plan_file": args.plan,
            "verbose": args.verbose,
        }
    )
    return apply_cli_args(config, args)


def prepare_go_config(args) -> Config:
    """Resolve config, apply CLI flags, then prompt for interactive settings."""
    config = _config_from_args(args)
    detach = getattr(args, "detach", False)
    owner = getattr(args, "owner", False)
    no_menu = getattr(args, "no_menu", False) or detach or owner

    if no_menu:
        return config

    from src.menu import run_settings_menu

    config = run_settings_menu(config)
    if config is None:
        print("Выход.")
        sys.exit(0)
    return config


def build_parser() -> argparse.ArgumentParser:
    """Build the canonical CLI parser."""
    parser = argparse.ArgumentParser(
        prog="tero",
        description="Plan-driven player → judge loop over coding-agent CLIs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    go_parser = subparsers.add_parser("go", help="Run the player → judge loop over a plan")
    go_parser.add_argument("--plan", "-p", type=str, default=None)
    go_parser.add_argument("--working-dir", "-w", type=str, default=".")
    go_parser.add_argument("--no-menu", action="store_true", default=False)
    go_parser.add_argument("--no-hud", action="store_true", default=False)
    go_parser.add_argument("--verbose", "-v", action="store_true", default=None)
    go_parser.add_argument("--player", dest="player_spec", default=None)
    go_parser.add_argument("--judge", dest="judge_spec", default=None)
    go_parser.add_argument("--tester", dest="tester_spec", default=None)
    go_parser.add_argument("--escalation", dest="escalation_spec", default=None)
    go_parser.add_argument("--no-escalation", action="store_true", default=False)
    go_parser.add_argument("--no-tester", action="store_true", default=False)
    go_parser.add_argument("--decisions", action="store_true", default=False)
    go_parser.add_argument("--detach", action="store_true", default=False)
    go_parser.add_argument("--owner", action="store_true", default=False, help=argparse.SUPPRESS)
    go_parser.add_argument("--test-command", default=None)
    go_parser.add_argument("--test-command-scoped", default=None)
    go_parser.add_argument("--max-rounds", type=int, default=None)

    history_parser = subparsers.add_parser("history", help="Show run history")
    history_parser.add_argument("--limit", "-l", type=int, default=10)
    history_parser.add_argument("--working-dir", "-w", type=str, default=".")

    review_parser = subparsers.add_parser("review", help="Show snapshot ref and diff for a run")
    review_parser.add_argument("--working-dir", "-w", type=str, default=".")
    review_parser.add_argument("--run", type=str, default=None)

    resume_parser = subparsers.add_parser("resume", help="Continue a stopped run from state.json")
    resume_parser.add_argument("--working-dir", "-w", type=str, default=".")
    resume_parser.add_argument("--run", type=str, default=None)

    hud_parser = subparsers.add_parser("hud", help="Open the HUD window for a run")
    hud_parser.add_argument("--run", type=str, default=None)

    compact_parser = subparsers.add_parser(
        "compact", help="Request a context compact for a running run"
    )
    compact_parser.add_argument("--working-dir", "-w", type=str, default=".")
    compact_parser.add_argument("--run", type=str, default=None)
    compact_parser.add_argument(
        "--role", type=str, default="player", choices=("player", "judge")
    )

    wait_parser = subparsers.add_parser(
        "wait", help="Sleep until the run needs the agent, ends, or looks dead"
    )
    wait_parser.add_argument("--working-dir", "-w", type=str, default=".")
    wait_parser.add_argument("--run", type=str, default=None)
    wait_parser.add_argument("--timeout", type=float, default=None)

    status_parser = subparsers.add_parser("status", help="Show run status")
    status_parser.add_argument("--working-dir", "-w", type=str, default=".")
    status_parser.add_argument("--run", type=str, default=None)
    status_parser.add_argument("--json", action="store_true", default=False)

    answer_parser = subparsers.add_parser("answer", help="Answer the open decision question")
    answer_parser.add_argument("question_id", type=str)
    answer_parser.add_argument("action", type=str)
    answer_parser.add_argument("text", type=str, nargs="?", default="")
    answer_parser.add_argument("--why", type=str, required=True)
    answer_parser.add_argument("--human", action="store_true", default=False)
    answer_parser.add_argument("--working-dir", "-w", type=str, default=".")
    answer_parser.add_argument("--run", type=str, default=None)

    return parser


async def run_go(args, config: Config | None = None):
    """Run the player → judge loop."""
    from src.loop.agents import LiveAgents
    from src.loop.run import agy_preflight_check, run as run_loop

    loop_cfg = config if config is not None else _config_from_args(args)

    plan_path = Path(loop_cfg.working_dir) / loop_cfg.plan_file
    if not plan_path.exists():
        print(f"Ошибка: файл плана не найден: {plan_path}")
        sys.exit(1)

    print(f"\nПлан: {plan_path}", flush=True)
    print(
        f"player {loop_cfg.player_provider}({loop_cfg.player_model}"
        f"{' ' + loop_cfg.player_effort if loop_cfg.player_effort else ''})  "
        f"judge {loop_cfg.judge_provider}({loop_cfg.judge_model}"
        f"{' ' + loop_cfg.judge_effort if loop_cfg.judge_effort else ''})  "
        + (
            "tester выкл"
            if loop_cfg.no_tester
            else f"tester {loop_cfg.tester_provider}({loop_cfg.tester_model}"
            f"{' ' + loop_cfg.tester_effort if loop_cfg.tester_effort else ''})"
        ),
        flush=True,
    )
    print(f"тесты {loop_cfg.test_command or 'автодетект'}", flush=True)

    def _ready(provider: str, model: str) -> tuple[bool, str]:
        try:
            from src.loop.agents import _default_factory

            inst = _default_factory(loop_cfg.working_dir)(provider)
        except Exception as exc:
            return False, str(exc)
        return inst.check_ready()

    agents = LiveAgents(loop_cfg.working_dir, verbose=loop_cfg.verbose)
    outcome = await run_loop(
        loop_cfg, agents=agents, check_ready=_ready, agy_preflight=agy_preflight_check
    )
    if outcome.reason == "nothing_to_do":
        print(
            f"делать нечего: все фазы плана ({outcome.phases_total}) уже отмечены [x]",
            flush=True,
        )
    elif outcome.exit_code == 0:
        print(
            f"готово: закрыто фаз {outcome.phases_shipped} из {outcome.phases_total}",
            flush=True,
        )
    elif outcome.reason:
        print(f"стоп: {outcome.reason} (код {outcome.exit_code})", flush=True)
    if outcome.run_dir:
        print(f"журнал: {outcome.run_dir}", flush=True)
    sys.exit(outcome.exit_code)


async def run_resume(args) -> int:
    """Continue a stopped run. Only judge_pending is supported in this step."""
    from src.loop.agents import LiveAgents
    from src.loop.run import resume as resume_run
    from src.loop.state import RunState

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = resolve_run_dir(args)
    if run_dir is None:
        return 1
    state = RunState.read(run_dir / "state.json") or {}
    status = state.get("status") or ""
    if status != "judge_pending":
        print(f"стоп: resume для `{status}` пока не умеет (код 1)", flush=True)
        return 1
    cfg_state = state.get("config") or {}
    cfg_fields = cfg_state.get("fields") if isinstance(cfg_state.get("fields"), dict) else cfg_state
    working_dir = cfg_fields.get("working_dir") or str(repo)
    agents = LiveAgents(working_dir, verbose=bool(cfg_fields.get("verbose", False)))
    outcome = await resume_run(repo, run_dir, agents=agents)
    if outcome.reason:
        print(f"стоп: {outcome.reason} (код {outcome.exit_code})", flush=True)
    if outcome.run_dir:
        print(f"журнал: {outcome.run_dir}", flush=True)
    return outcome.exit_code


def main():
    """Package-safe entrypoint used by the installed `tero` script."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "go":
        if getattr(args, "detach", False):
            from src.loop.detach import detach_go

            sys.exit(detach_go(args))
        try:
            config = prepare_go_config(args)
        except UnknownProviderError as exc:
            print(f"Ошибка: неизвестный провайдер: {exc}")
            sys.exit(1)
        except ConfigError as exc:
            print(f"Ошибка: {exc}")
            sys.exit(1)
        try:
            asyncio.run(run_go(args, config=config))
        except KeyboardInterrupt:
            print("\nПрервано.")
            sys.exit(130)
    elif args.command == "history":
        run_history(args)
    elif args.command == "review":
        sys.exit(run_review(args))
    elif args.command == "compact":
        sys.exit(run_compact(args))
    elif args.command == "resume":
        sys.exit(asyncio.run(run_resume(args)))
    elif args.command == "wait":
        sys.exit(run_wait(args))
    elif args.command == "status":
        sys.exit(run_status(args))
    elif args.command == "answer":
        sys.exit(run_answer(args))
    elif args.command == "hud":
        from src.hud.app import main as hud_main

        sys.exit(hud_main())


if __name__ == "__main__":
    main()
