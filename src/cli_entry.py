"""Shared CLI implementation for both repository entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.config import Config, ConfigError, UnknownProviderError, apply_cli_args, resolve_config


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
        f"tester {loop_cfg.tester_provider}({loop_cfg.tester_model}"
        f"{' ' + loop_cfg.tester_effort if loop_cfg.tester_effort else ''})",
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


def run_history(args):
    """Show recent runs from .tero/runs."""
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


def _latest_run_dir(repo: Path) -> Path | None:
    runs = repo / ".tero" / "runs"
    if not runs.is_dir():
        return None
    try:
        entries = sorted(
            (p for p in runs.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )
    except OSError:
        return None
    return entries[-1] if entries else None


def run_review(args) -> int:
    """Print status, phase, round, snapshot ref, base, diff stat and full diff.

    Read-only: resolves the run dir from --run (or the latest in .tero/runs),
    reads state.json, and shows diff_for(base, ref) via a git subprocess.
    """
    from src.loop.snapshot import diff_for
    from src.loop.state import RunState

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = Path(args.run).expanduser() if getattr(args, "run", None) else None
    if run_dir is None:
        run_dir = _latest_run_dir(repo)
    if run_dir is None or not (run_dir / "state.json").is_file():
        print("Прогонов нет.")
        return 1
    state = RunState.read(run_dir / "state.json") or {}
    ref = state.get("snapshot_ref") or ""
    if not ref:
        print("снимка нет: в state.json нет snapshot_ref")
        return 1
    from subprocess import run as _git_run

    base = state.get("base") or ""
    print(f"статус: {state.get('status', '')}")
    print(f"фаза: {state.get('phase', '')}")
    print(f"раунд: {state.get('round', '')}")
    print(f"snapshot_ref: {ref}")
    print(f"base: {base}")
    stat = _git_run(
        ["git", "diff", "--stat", base, ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if stat.returncode == 0 and stat.stdout.strip():
        print(stat.stdout.rstrip())
    print(diff_for(repo, base, ref), end="")
    return 0


async def run_resume(args) -> int:
    """Continue a stopped run. Only judge_pending is supported in this step."""
    from src.loop.agents import LiveAgents
    from src.loop.run import resume as resume_run
    from src.loop.state import RunState

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = Path(args.run).expanduser() if getattr(args, "run", None) else None
    if run_dir is None:
        run_dir = _latest_run_dir(repo)
    if run_dir is None or not (run_dir / "state.json").is_file():
        print("Прогонов нет.")
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
    elif args.command == "resume":
        sys.exit(asyncio.run(run_resume(args)))
    elif args.command == "hud":
        from src.hud.app import main as hud_main

        sys.exit(hud_main())


if __name__ == "__main__":
    main()
