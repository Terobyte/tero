"""Shared CLI implementation for both repository entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.config import Config, UnknownProviderError, apply_cli_args, resolve_config


def resolve_go_config(args) -> Config:
    """Resolve config for `tero go` from CLI-like args."""
    return resolve_config(
        {
            "working_dir": args.working_dir,
            "plan_file": args.plan,
            "verbose": args.verbose,
        }
    )


def prepare_go_config(args) -> Config:
    """Resolve config, apply CLI flags, then prompt for interactive settings."""
    config = resolve_go_config(args)
    config = apply_cli_args(config, args)
    no_menu = getattr(args, "no_menu", False)

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
        description="Coach-player feedback loop for implementation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    go_parser = subparsers.add_parser("go", help="Run coach-player loop")
    go_parser.add_argument("--plan", "-p", type=str, default=None)
    go_parser.add_argument("--working-dir", "-w", type=str, default=".")
    go_parser.add_argument("--no-menu", action="store_true", default=False)
    go_parser.add_argument("--verbose", "-v", action="store_true", default=None)
    go_parser.add_argument("--player", dest="player_spec", default=None)
    go_parser.add_argument("--judge", dest="judge_spec", default=None)
    go_parser.add_argument("--tester", dest="tester_spec", default=None)
    go_parser.add_argument("--escalation", dest="escalation_spec", default=None)
    go_parser.add_argument("--no-escalation", action="store_true", default=False)
    go_parser.add_argument("--test-command", default=None)
    go_parser.add_argument("--test-command-scoped", default=None)
    go_parser.add_argument("--max-rounds", type=int, default=None)

    history_parser = subparsers.add_parser("history", help="Show run history")
    history_parser.add_argument("--limit", "-l", type=int, default=10)
    history_parser.add_argument("--working-dir", "-w", type=str, default=".")

    return parser


async def run_go(args, config: Config | None = None):
    """Run the v4 player → judge loop."""
    from src.loop.agents import LiveAgents
    from src.loop.run import run as run_loop

    loop_cfg = config if config is not None else apply_cli_args(resolve_go_config(args), args)

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
        f"tester {loop_cfg.tester_provider}({loop_cfg.tester_model}"
        f"{' ' + loop_cfg.tester_effort if loop_cfg.tester_effort else ''})",
        flush=True,
    )
    print(f"тесты {loop_cfg.test_command or 'автодетект'}", flush=True)

    def _ready(provider: str, model: str) -> tuple[bool, str]:
        from src.config import load_provider_configs
        from src.providers import create_provider

        try:
            inst = create_provider(
                provider, load_provider_configs(loop_cfg.working_dir).get(provider)
            )
        except Exception as exc:
            return False, str(exc)
        return inst.check_ready()

    agents = LiveAgents(loop_cfg.working_dir, verbose=loop_cfg.verbose)
    outcome = await run_loop(loop_cfg, agents=agents, check_ready=_ready)
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


def run_history(args):
    """Show recent v4 runs from .tero/runs."""
    from src.loop.ledger import history

    rows = history(Path(args.working_dir).resolve(), limit=args.limit)
    if not rows:
        print("История пуста.")
        return
    print(f"\n--- История ({len(rows)} запусков) ---\n")
    for row in rows:
        print(
            f"  {row['slug']}: {row['reason'] or '?'}  "
            f"{row['phases_shipped']}/{row['phases_total']}  {row['plan']}"
        )


def main():
    """Package-safe entrypoint used by the installed `tero` script."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "go":
        try:
            config = prepare_go_config(args)
        except UnknownProviderError as exc:
            print(f"Ошибка: неизвестный провайдер: {exc}")
            sys.exit(1)
        try:
            asyncio.run(run_go(args, config=config))
        except KeyboardInterrupt:
            print("\nПрервано.")
            sys.exit(130)
    elif args.command == "history":
        run_history(args)
