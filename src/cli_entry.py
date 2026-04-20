"""Shared CLI implementation for both repository entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.batch_executor import BatchExecutor
from src.config import resolve_config
from src.coach_player import CoachPlayerSession
from src.learning.recorder import RunRecorder
from src.plan_tracker import PlanTracker, parse_requirements


PROVIDER_CHOICES = ["zai", "claude", "codex", "opencode", "kilo"]


def resolve_go_config(args):
    """Resolve config for `tero go` from CLI-like args."""
    return resolve_config(
        {
            "working_dir": args.working_dir,
            "max_turns": args.max_turns,
            "plan_file": args.plan,
            "verbose": args.verbose,
            "autonomous": args.autonomous,
            "coach_model": getattr(args, "coach_model", None),
            "player_provider": getattr(args, "player_provider", None),
            "coach_provider": getattr(args, "coach_provider", None),
            "player_model": getattr(args, "player_model", None),
            "batch_mode": getattr(args, "batch_mode", None),
            "tdd_mode": getattr(args, "tdd_mode", None),
            "test_command": getattr(args, "test_command", None),
            "test_timeout_s": getattr(args, "test_timeout_s", None),
            "code_review": getattr(args, "code_review", None),
            "review_provider": getattr(args, "review_provider", None),
            "review_model": getattr(args, "review_model", None),
            "coach_retry_max": getattr(args, "coach_retry_max", None),
            "coach_fallback_provider": getattr(args, "coach_fallback_provider", None),
            "coach_fallback_model": getattr(args, "coach_fallback_model", None),
            "context_limit": getattr(args, "context_limit", None),
            "compact_threshold": getattr(args, "compact_threshold", None),
            "max_continuation_attempts": getattr(args, "max_continuation_attempts", None),
            "batch_judge_provider": getattr(args, "batch_judge_provider", None),
            "batch_judge_model": getattr(args, "batch_judge_model", None),
            "batch_pre_provider": getattr(args, "batch_pre_provider", None),
            "batch_pre_model": getattr(args, "batch_pre_model", None),
            "batch_post_provider": getattr(args, "batch_post_provider", None),
            "batch_post_model": getattr(args, "batch_post_model", None),
            "max_review_iterations": getattr(args, "max_review_iterations", None),
            # Pre-Planner (Phase 0)
            "preplan_provider": getattr(args, "preplan_provider", None) or None,
            "preplan_model": getattr(args, "preplan_model", None) or None,
            "preplan_mode": getattr(args, "preplan_mode", None),
            # Provider fallback chain
            "player_fallback_chain": getattr(args, "player_fallback_chain", None),
            "coach_fallback_chain": getattr(args, "coach_fallback_chain", None),
            "chain_retry_wait_s": getattr(args, "chain_retry_wait_s", None),
            "chain_max_retries": getattr(args, "chain_max_retries", None),
        }
    )


def prepare_go_config(args):
    """Resolve config and, when requested, prompt for interactive settings."""
    config = resolve_go_config(args)
    no_menu = getattr(args, "no_menu", False)
    autonomous_requested = bool(getattr(args, "autonomous", False))

    if no_menu or autonomous_requested:
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
    go_parser.add_argument("--max-turns", "-n", type=int, default=None)
    go_parser.add_argument("--plan", "-p", type=str, default=None)
    go_parser.add_argument("--verbose", "-v", action="store_true", default=None)
    go_parser.add_argument("--autonomous", action="store_true", default=None)
    go_parser.add_argument("--no-menu", action="store_true", default=False)
    go_parser.add_argument("--working-dir", "-w", type=str, default=".")

    go_parser.add_argument(
        "--player-provider",
        "-pp",
        type=str,
        default=None,
        choices=PROVIDER_CHOICES,
    )
    go_parser.add_argument(
        "--coach-provider",
        "-cp",
        type=str,
        default=None,
        choices=PROVIDER_CHOICES,
    )
    go_parser.add_argument("--player-model", "-pm", type=str, default=None)
    go_parser.add_argument("--coach-model", "-cm", type=str, default=None, dest="coach_model")
    go_parser.add_argument("--batch", action="store_true", dest="batch_mode", default=None)

    go_parser.add_argument("--tdd", dest="tdd_mode", action="store_true", default=None)
    go_parser.add_argument("--test-command", type=str, default=None)
    go_parser.add_argument("--test-timeout-s", type=int, default=None, dest="test_timeout_s")

    go_parser.add_argument("--code-review", action="store_true", default=None)
    go_parser.add_argument(
        "--review-provider",
        type=str,
        default=None,
        choices=PROVIDER_CHOICES,
    )
    go_parser.add_argument("--review-model", type=str, default=None)

    go_parser.add_argument("--coach-retry-max", type=int, default=None)
    go_parser.add_argument(
        "--coach-fallback-provider",
        type=str,
        default=None,
        choices=PROVIDER_CHOICES,
    )
    go_parser.add_argument("--coach-fallback-model", type=str, default=None)

    go_parser.add_argument("--context-limit", type=int, default=None)
    go_parser.add_argument("--compact-threshold", type=float, default=None)
    go_parser.add_argument(
        "--max-continuation",
        type=int,
        default=None,
        dest="max_continuation_attempts",
    )

    go_parser.add_argument(
        "--batch-judge-provider",
        type=str,
        default=None,
        choices=PROVIDER_CHOICES,
        dest="batch_judge_provider",
    )
    go_parser.add_argument(
        "--batch-judge-model",
        type=str,
        default=None,
        dest="batch_judge_model",
    )
    go_parser.add_argument(
        "--batch-pre-provider",
        type=str,
        default=None,
        choices=PROVIDER_CHOICES,
        dest="batch_pre_provider",
    )
    go_parser.add_argument(
        "--batch-pre-model",
        type=str,
        default=None,
        dest="batch_pre_model",
    )
    go_parser.add_argument(
        "--batch-post-provider",
        type=str,
        default=None,
        choices=PROVIDER_CHOICES,
        dest="batch_post_provider",
    )
    go_parser.add_argument(
        "--batch-post-model",
        type=str,
        default=None,
        dest="batch_post_model",
    )
    go_parser.add_argument(
        "--max-review-iterations",
        type=int,
        default=None,
        dest="max_review_iterations",
    )

    # Pre-Planner (Phase 0)
    go_parser.add_argument(
        "--preplan-provider",
        type=str,
        default="",
        help="Provider for Phase 0 Pre-Planner (default: same as player)",
    )
    go_parser.add_argument(
        "--preplan-model",
        type=str,
        default="",
        help="Model override for Phase 0 Pre-Planner",
    )
    preplan_group = go_parser.add_mutually_exclusive_group()
    preplan_group.add_argument(
        "--preplan",
        dest="preplan_mode",
        action="store_true",
        default=None,
        help="Enable Phase 0 plan enrichment",
    )
    preplan_group.add_argument(
        "--no-preplan",
        dest="preplan_mode",
        action="store_false",
        default=None,
        help="Disable Phase 0 plan enrichment",
    )

    # Provider fallback chain
    go_parser.add_argument(
        "--player-fallback-chain",
        type=str,
        default=None,
        dest="player_fallback_chain",
    )
    go_parser.add_argument(
        "--coach-fallback-chain",
        type=str,
        default=None,
        dest="coach_fallback_chain",
    )
    go_parser.add_argument(
        "--chain-retry-wait",
        type=float,
        default=None,
        dest="chain_retry_wait_s",
    )
    go_parser.add_argument(
        "--chain-max-retries",
        type=int,
        default=None,
        dest="chain_max_retries",
    )

    history_parser = subparsers.add_parser("history", help="Show run history")
    history_parser.add_argument("--limit", "-l", type=int, default=10)
    history_parser.add_argument("--working-dir", "-w", type=str, default=".")

    debug_parser = subparsers.add_parser("debug", help="Run automated bug-find-test-fix loop")
    debug_parser.add_argument(
        "--working-dir", "-w", type=str, default=".",
        dest="working_dir",
    )
    debug_parser.add_argument(
        "--player-provider", type=str, choices=PROVIDER_CHOICES,
        default=None, dest="debug_player_provider",
    )
    debug_parser.add_argument(
        "--tester-provider", type=str, choices=PROVIDER_CHOICES,
        default=None, dest="debug_tester_provider",
    )
    debug_parser.add_argument(
        "--fixer-provider", type=str, choices=PROVIDER_CHOICES,
        default=None, dest="debug_fixer_provider",
    )
    debug_parser.add_argument(
        "--intensity", type=str, choices=["low", "medium", "high"],
        default=None, dest="debug_intensity",
    )
    debug_parser.add_argument(
        "--limit", type=int, default=None, dest="debug_limit_value",
    )
    debug_parser.add_argument(
        "--time", type=int, default=None,
    )
    debug_parser.add_argument(
        "--infinite", action="store_true", default=False,
    )
    debug_parser.add_argument(
        "--no-menu", action="store_true", default=False,
    )

    return parser


async def run_go(args, config=None, *, session_cls=CoachPlayerSession):
    """Run the coach-player session."""
    if config is None:
        config = resolve_go_config(args)

    plan_path = Path(config.working_dir) / config.plan_file
    if not plan_path.exists():
        print(f"Ошибка: файл плана не найден: {plan_path}")
        sys.exit(1)

    requirements = plan_path.read_text()
    print(f"\nПлан: {plan_path} ({len(requirements)} байт)")

    try:
        session = session_cls(config, requirements, str(plan_path))
        if config.batch_mode:
            items = parse_requirements(requirements)
            phases = []
            if config.preplan_mode and hasattr(session, "_run_phase_zero"):
                enriched_items, phases = await session._run_phase_zero(requirements)
                if enriched_items:
                    items = enriched_items
            tracker = PlanTracker(items)
            if phases:
                tracker.phases = phases
            executor = BatchExecutor(session, tracker)
            await executor.run()
            sys.exit(0)

        result = await session.run()
        sys.exit(0 if result.approved else 1)
    except RuntimeError as exc:
        print(f"\nОшибка: {exc}")
        sys.exit(1)


def run_debug(args) -> None:
    """Run the automated debugger loop."""
    from src.config import resolve_config
    from src.debugger import Debugger
    from src.menu import run_debugger_menu

    cli_overrides: dict = {"working_dir": args.working_dir}

    if getattr(args, "debug_player_provider", None):
        cli_overrides["debug_player_provider"] = args.debug_player_provider
    if getattr(args, "debug_tester_provider", None):
        cli_overrides["debug_tester_provider"] = args.debug_tester_provider
    if getattr(args, "debug_fixer_provider", None):
        cli_overrides["debug_fixer_provider"] = args.debug_fixer_provider
    if getattr(args, "debug_intensity", None):
        cli_overrides["debug_intensity"] = args.debug_intensity

    # Limit mode resolution: --infinite > --time > --limit
    if getattr(args, "infinite", False):
        cli_overrides["debug_limit_mode"] = "infinite"
        cli_overrides["debug_limit_value"] = 0
    elif getattr(args, "time", None):
        cli_overrides["debug_limit_mode"] = "time"
        cli_overrides["debug_limit_value"] = args.time
    elif getattr(args, "debug_limit_value", None):
        cli_overrides["debug_limit_mode"] = "iterations"
        cli_overrides["debug_limit_value"] = args.debug_limit_value

    config = resolve_config(cli_overrides)

    if not getattr(args, "no_menu", False):
        config = run_debugger_menu(config)

    debugger = Debugger(config)
    result = debugger.run_sync()
    sys.exit(0 if result.victory else 1)


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


def main():
    """Package-safe entrypoint used by the installed `tero` script."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "go":
        config = prepare_go_config(args)
        try:
            asyncio.run(run_go(args, config=config))
        except KeyboardInterrupt:
            print("\nПрервано.")
            sys.exit(130)
    elif args.command == "history":
        run_history(args)
    elif args.command == "debug":
        try:
            run_debug(args)
        except KeyboardInterrupt:
            print("\nПрервано.")
            sys.exit(130)
