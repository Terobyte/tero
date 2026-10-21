"""Shared CLI implementation for both repository entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.config import resolve_config


PROVIDER_CHOICES = ["muse", "claude", "codex", "gemini", "opencode", "cursor"]


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
    go_parser.add_argument("--player", dest="player_spec", default=None)
    go_parser.add_argument("--judge", dest="judge_spec", default=None)
    go_parser.add_argument("--tester", dest="tester_spec", default=None)
    go_parser.add_argument("--escalation", dest="escalation_spec", default=None)
    go_parser.add_argument("--no-escalation", action="store_true", default=False)
    go_parser.add_argument("--test-command", default=None)
    go_parser.add_argument("--test-command-scoped", default=None)
    go_parser.add_argument("--max-rounds", type=int, default=None)

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

    return parser


async def run_go(args, config=None):
    """Run the v4 player → judge loop."""
    from src.loop.agents import LiveAgents
    from src.loop.config import apply_saved_config, config_from_namespace
    from src.loop.run import run as run_loop

    loop_cfg = config_from_namespace(args)
    if config is not None:
        loop_cfg = apply_saved_config(loop_cfg, config, args)

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
    """Show recent v4 runs from .g3/runs."""
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
        config = prepare_go_config(args)
        try:
            asyncio.run(run_go(args, config=config))
        except KeyboardInterrupt:
            print("\nПрервано.")
            sys.exit(130)
    elif args.command == "history":
        run_history(args)
