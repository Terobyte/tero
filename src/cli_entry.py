"""Shared CLI implementation for both repository entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.config import Config, ConfigError, UnknownProviderError, apply_cli_args, resolve_config
from src.commands import resolve_run_dir
from src.commands.inbox_cmds import run_answer, run_compact
from src.commands.parsers import add_all_parsers
from src.commands.runs import run_history, run_review, run_status, run_wait
from src.commands.stop_cmd import run_stop

__all__ = [
    "add_all_parsers",
    "build_parser",
    "main",
    "prepare_go_config",
    "run_answer",
    "run_compact",
    "run_go",
    "run_stop",
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
            "preset": getattr(args, "preset", None),
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
    add_all_parsers(subparsers)
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
    elif outcome.exit_code == 4:
        print(f"готово с долгом: {outcome.debt}", flush=True)
    elif outcome.reason:
        print(f"стоп: {outcome.reason} (код {outcome.exit_code})", flush=True)
    if outcome.run_dir:
        print(f"журнал: {outcome.run_dir}", flush=True)
    sys.exit(outcome.exit_code)


async def run_resume(args) -> int:
    """Continue a stopped run. judge_pending and stopped with a ref."""
    from src.loop.agents import LiveAgents
    from src.loop.run import resume as resume_run
    from src.loop.session import resume_refusal
    from src.loop.state import RunState

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = resolve_run_dir(args)
    if run_dir is None:
        return 1
    state = RunState.read(run_dir / "state.json") or {}
    answer = getattr(args, "answer", "") or ""
    force = bool(getattr(args, "force", False))
    refusal = resume_refusal(state, answer, force)
    if refusal:
        print(f"стоп: {refusal} (код 1)", flush=True)
        return 1
    cfg_state = state.get("config") or {}
    cfg_fields = cfg_state.get("fields") if isinstance(cfg_state.get("fields"), dict) else cfg_state
    working_dir = cfg_fields.get("working_dir") or str(repo)
    agents = LiveAgents(working_dir, verbose=bool(cfg_fields.get("verbose", False)))
    outcome = await resume_run(repo, run_dir, agents=agents, answer=answer, force=force)
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
    elif args.command == "stop":
        sys.exit(run_stop(args))
    elif args.command == "guide":
        from src.commands.guide_cmds import run_guide

        sys.exit(run_guide(args))
    elif args.command == "notify":
        from src.commands.guide_cmds import run_notify

        sys.exit(run_notify(args))
    elif args.command == "relay":
        from src.commands.relay_cmds import run_relay_setup

        if args.relay_command == "setup":
            sys.exit(run_relay_setup(args))
    elif args.command == "pair":
        from src.commands.relay_cmds import run_pair

        sys.exit(run_pair(args))
    elif args.command == "hud":
        from src.hud.app import main as hud_main

        sys.exit(hud_main())


if __name__ == "__main__":
    main()
