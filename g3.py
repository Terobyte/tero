#!/usr/bin/env python3
"""tero CLI — coach-player feedback loop via Claude Agent SDK."""

import argparse
import asyncio
import sys
from pathlib import Path

from src.config import resolve_config
from src.coach_player import CoachPlayerSession
from src.learning.recorder import RunRecorder


def _resolve_go_config(args):
    """Resolve config for `tero go` from CLI-like args."""
    return resolve_config({
        "working_dir": args.working_dir,
        "max_turns": args.max_turns,
        "plan_file": args.plan,
        "verbose": args.verbose,
        "autonomous": args.autonomous,
        "coach_model": getattr(args, "coach_model", None),
        "player_provider": getattr(args, "player_provider", None),
        "coach_provider": getattr(args, "coach_provider", None),
        "player_model": getattr(args, "player_model", None),
    })


def _prepare_go_config(args):
    """Resolve config and, when requested, prompt for interactive settings."""
    config = _resolve_go_config(args)
    no_menu = getattr(args, "no_menu", False)

    if no_menu or config.autonomous:
        return config

    from src.menu import run_settings_menu

    config = run_settings_menu(config)
    if config is None:
        print("Выход.")
        sys.exit(0)
    return config


def main():
    parser = argparse.ArgumentParser(
        prog="tero",
        description="Coach-player feedback loop for implementation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # `go` subcommand
    go_parser = subparsers.add_parser("go", help="Run coach-player loop")
    go_parser.add_argument(
        "--max-turns", "-n",
        type=int,
        default=None,
        help="Max retries per step (default: 10)",
    )
    go_parser.add_argument(
        "--plan", "-p",
        type=str,
        default=None,
        help="Requirements file (default: requirements.md)",
    )
    go_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=None,
        help="Show full agent output including tool results",
    )
    go_parser.add_argument(
        "--autonomous",
        action="store_true",
        default=None,
        help="Skip all confirmations",
    )
    go_parser.add_argument(
        "--no-menu",
        action="store_true",
        default=False,
        help="Skip interactive settings menu and start immediately",
    )
    go_parser.add_argument(
        "--working-dir", "-w",
        type=str,
        default=".",
        help="Working directory (default: current)",
    )

    # Provider selection (NEW)
    go_parser.add_argument(
        "--player-provider", "-pp",
        type=str,
        default=None,
        choices=["ccg", "claude"],
        help="Provider for player: ccg (Blackbox) or claude (Pro)",
    )
    go_parser.add_argument(
        "--coach-provider", "-cp",
        type=str,
        default=None,
        choices=["ccg", "claude"],
        help="Provider for coach: ccg (Blackbox) or claude (Pro)",
    )
    go_parser.add_argument(
        "--player-model", "-pm",
        type=str,
        default=None,
        help="Model for player (e.g., sonnet, opus, haiku for Claude)",
    )
    go_parser.add_argument(
        "--coach-model", "-cm",
        type=str,
        default=None,
        dest="coach_model",
        help="Model for coach (e.g., sonnet, opus, haiku for Claude)",
    )

    # `history` subcommand
    history_parser = subparsers.add_parser("history", help="Show run history")
    history_parser.add_argument("--limit", "-l", type=int, default=10)
    history_parser.add_argument("--working-dir", "-w", type=str, default=".")

    args = parser.parse_args()

    if args.command == "go":
        config = _prepare_go_config(args)
        asyncio.run(run_go(args, config=config))
    elif args.command == "history":
        run_history(args)


async def run_go(args, config=None):
    """Run the coach-player session."""
    if config is None:
        config = _resolve_go_config(args)

    # Read requirements
    plan_path = Path(config.working_dir) / config.plan_file
    if not plan_path.exists():
        print(f"Ошибка: файл плана не найден: {plan_path}")
        sys.exit(1)

    requirements = plan_path.read_text()
    print(f"\nПлан: {plan_path} ({len(requirements)} байт)")

    try:
        session = CoachPlayerSession(config, requirements, str(plan_path))
        result = await session.run()
        sys.exit(0 if result.approved else 1)
    except RuntimeError as e:
        print(f"\nОшибка: {e}")
        sys.exit(1)


def run_history(args):
    """Show run history."""
    working_dir = Path(args.working_dir).resolve()
    recorder = RunRecorder(f"{working_dir}/.g3/knowledge")
    records = recorder.history(limit=args.limit)

    if not records:
        print("История пуста.")
        return

    print(f"\n--- История ({len(records)} запусков) ---\n")
    for record in records:
        icon = "✓" if record.status == "approved" else "✗"
        print(f"  {icon} {record.timestamp}")
        print(f"      Ходов: {record.turns_used}/{record.max_turns}")
        print(f"      Время: {record.total_duration_s:.0f}s")
        print(f"      Статус: {record.status}")
        print()


if __name__ == "__main__":
    main()
