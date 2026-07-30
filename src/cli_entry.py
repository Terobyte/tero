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


PROVIDER_CHOICES = ["zai", "claude", "codex", "opencode", "kilo", "gemini"]


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
            "code_review": getattr(args, "code_review", None),
            "review_provider": getattr(args, "review_provider", None),
            "review_model": getattr(args, "review_model", None),
            "coach_retry_max": getattr(args, "coach_retry_max", None),
            "coach_fallback_provider": getattr(args, "coach_fallback_provider", None),
            "coach_fallback_model": getattr(args, "coach_fallback_model", None),
            "context_limit": getattr(args, "context_limit", None),
            "compact_threshold": getattr(args, "compact_threshold", None),
            "max_continuation_attempts": getattr(
                args, "max_continuation_attempts", None
            ),
            "batch_judge_provider": getattr(args, "batch_judge_provider", None),
            "batch_judge_model": getattr(args, "batch_judge_model", None),
            "batch_pre_provider": getattr(args, "batch_pre_provider", None),
            "batch_pre_model": getattr(args, "batch_pre_model", None),
            "batch_post_provider": getattr(args, "batch_post_provider", None),
            "batch_post_model": getattr(args, "batch_post_model", None),
            "max_review_iterations": getattr(args, "max_review_iterations", None),
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
    go_parser.add_argument(
        "--coach-model", "-cm", type=str, default=None, dest="coach_model"
    )
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

    ldb_parser = subparsers.add_parser("ldb", help="Run ldb bug-finder")
    ldb_parser.add_argument(
        "--working-dir", "-w", type=str, default=".", dest="working_dir"
    )
    ldb_parser.add_argument(
        "--input-provider",
        type=str,
        choices=PROVIDER_CHOICES,
        default=None,
        dest="ldb_input_provider",
    )
    ldb_parser.add_argument(
        "--player-provider",
        type=str,
        choices=PROVIDER_CHOICES,
        default=None,
        dest="ldb_player_provider",
    )
    ldb_parser.add_argument(
        "--tester-provider",
        type=str,
        choices=PROVIDER_CHOICES,
        default=None,
        dest="ldb_tester_provider",
    )
    ldb_parser.add_argument(
        "--fixer-provider",
        type=str,
        choices=PROVIDER_CHOICES,
        default=None,
        dest="ldb_fixer_provider",
    )
    ldb_parser.add_argument(
        "--input-model", type=str, default=None, dest="ldb_input_model"
    )
    ldb_parser.add_argument(
        "--player-model", type=str, default=None, dest="ldb_player_model"
    )
    ldb_parser.add_argument(
        "--tester-model", type=str, default=None, dest="ldb_tester_model"
    )
    ldb_parser.add_argument(
        "--fixer-model", type=str, default=None, dest="ldb_fixer_model"
    )
    ldb_parser.add_argument(
        "--mode", type=int, choices=[2, 3], default=None, dest="ldb_mode"
    )
    ldb_parser.add_argument("--file", type=str, default=None, dest="ldb_target_file")
    ldb_parser.add_argument("--entry", type=str, default=None, dest="ldb_target_entry")
    ldb_parser.add_argument(
        "--all", action="store_true", default=False, dest="ldb_scope_all",
        help="scan every public function (default: only functions touched in git diff)",
    )
    ldb_parser.add_argument(
        "--diff-base", type=str, default=None, dest="ldb_diff_base",
        help="git ref the --changed scope diffs against (default: HEAD = staged + unstaged)",
    )
    ldb_parser.add_argument(
        "--max-iterations", type=int, default=None, dest="ldb_max_iterations"
    )
    ldb_parser.add_argument("--timeout", type=int, default=None, dest="ldb_timeout_s")
    ldb_parser.add_argument(
        "--test", type=str, default=None, dest="ldb_test_input",
        help="Explicit test/assert input (e.g. 'assert add(1,2)==3')",
    )
    ldb_parser.add_argument(
        "--no-menu", action="store_true", default=False,
        help="(deprecated; default) skip the LDB settings menu and run directly",
    )
    ldb_parser.add_argument(
        "--menu", action="store_true", default=False,
        help="open the LDB settings menu before running (default: run directly)",
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
        items = parse_requirements(requirements)
        tracker = PlanTracker(items)
        executor = BatchExecutor(session, tracker, session.router)
        await executor.run()
        sys.exit(0)
    except RuntimeError as exc:
        print(f"\nОшибка: {exc}")
        sys.exit(1)


def run_ldb(args) -> None:
    from src.config import resolve_config

    cli_overrides: dict = {"working_dir": args.working_dir}

    _LDB_FIELDS = [
        "ldb_input_provider",
        "ldb_player_provider",
        "ldb_tester_provider",
        "ldb_fixer_provider",
        "ldb_input_model",
        "ldb_player_model",
        "ldb_tester_model",
        "ldb_fixer_model",
        "ldb_mode",
        "ldb_target_file",
        "ldb_target_entry",
        "ldb_scope_all",
        "ldb_diff_base",
        "ldb_max_iterations",
        "ldb_timeout_s",
        "ldb_test_input",
    ]
    for field in _LDB_FIELDS:
        val = getattr(args, field, None)
        if val is not None:
            if isinstance(val, bool) and not val:
                # Skip False for store_true flags to allow config.yaml defaults
                continue
            cli_overrides[field] = val

    config = resolve_config(cli_overrides)

    # Default = run automatically. The settings menu is opt-in via --menu;
    # the legacy --no-menu flag is kept for backward compatibility but no
    # longer needed (it is now the default behaviour).
    if getattr(args, "menu", False):
        from src.menu import run_ldb_menu

        menu_result = run_ldb_menu(config)
        if menu_result is None:
            print("Выход.")
            sys.exit(0)
        config = menu_result

    _execute_ldb(config)


def _execute_ldb(config) -> None:
    """Run LDB with a fully resolved Config (no menu interaction).

    Scope is decided automatically by Config.__post_init__:
      - ldb_target_file set → single-target mode
      - otherwise          → scan every public function (--all default)
    """
    from src.ldb import LdbRunner

    if config.ldb_target_file and not config.ldb_target_entry:
        print("Error: --file requires --entry <function_name>")
        sys.exit(1)

    runner = LdbRunner(config)
    try:
        result = runner.run_sync()
    except Exception as exc:
        print(f"ldb error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(
        f"ldb done: bugs_found={result.bugs_found} "
        f"tests_written={result.tests_written} "
        f"bugs_fixed={result.bugs_fixed}"
    )
    sys.exit(0 if result.success else 1)


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
        if config.ldb_run_requested:
            try:
                _execute_ldb(config)
            except KeyboardInterrupt:
                print("\nПрервано.")
                sys.exit(130)
            return
        try:
            asyncio.run(run_go(args, config=config))
        except KeyboardInterrupt:
            print("\nПрервано.")
            sys.exit(130)
    elif args.command == "history":
        run_history(args)
    elif args.command == "ldb":
        try:
            run_ldb(args)
        except KeyboardInterrupt:
            print("\nПрервано.")
            sys.exit(130)
