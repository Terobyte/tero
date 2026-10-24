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


def run_wait(args) -> int:
    """Sleep until the agent is needed; exit with the wait RunOutcome code."""
    from src.loop.state import RunState
    from src.loop.wait import wait_run

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = Path(args.run).expanduser() if getattr(args, "run", None) else None
    if run_dir is None:
        run_dir = _latest_run_dir(repo)
    if run_dir is None or not (run_dir / "state.json").is_file():
        print("Прогонов нет.")
        return 1
    code = wait_run(
        run_dir, repo, timeout_s=getattr(args, "timeout", None)
    )
    state = RunState.read(run_dir / "state.json") or {}
    why = {
        0: "готово",
        4: "готово с долгом",
        2: f"остановлен: {state.get('reason') or ''}".rstrip(),
        3: "нужен агент",
        5: "мёртв или завис — нужен resume",
        6: "таймаут",
    }.get(code, f"код {code}")
    print(f"{state.get('status', '?')}: {why}")
    return code


def run_status(args) -> int:
    """Print run status: machine-readable with --json, lines otherwise."""
    import json as _json

    from src.loop.wait import status_payload

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = Path(args.run).expanduser() if getattr(args, "run", None) else None
    if run_dir is None:
        run_dir = _latest_run_dir(repo)
    if run_dir is None or not (run_dir / "state.json").is_file():
        print("Прогонов нет.")
        return 1
    payload = status_payload(run_dir, repo)
    if getattr(args, "json", False):
        print(_json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    print(f"фаза: {payload['phase']}")
    title = payload.get("phase_title") or ""
    if title:
        print(f"название: {title}")
    print(f"раунд: {payload['round']}")
    print(f"роль: {payload['role']}")
    print(f"провайдер: {payload['provider']}")
    print(f"модель: {payload['model']}")
    print(f"статус: {payload['status']}")
    print(f"doing: {payload['doing']}")
    question = payload.get("question")
    if question:
        print(f"вопрос: {question.get('id')} {question.get('text')}")
        print(f"действия: {','.join(question.get('actions') or [])}")
    else:
        print("вопрос: null")
    print(f"решения: {_json.dumps(payload['decisions'], ensure_ascii=False)}")
    print(f"долг: {_json.dumps(payload['debt'], ensure_ascii=False)}")
    print(f"пульс: {payload['pulse_age_s']}")
    print("аренда: null")
    print(f"push_ok: {str(payload['push_ok']).lower()}")
    return 0


def _answer_wait_s() -> float:
    import os as _os

    try:
        return max(0.0, float(_os.environ.get("TERO_ANSWER_WAIT_S", "60")))
    except ValueError:
        return 60.0


def _answer_poll_s() -> float:
    import os as _os

    try:
        return max(0.001, float(_os.environ.get("TERO_ANSWER_POLL_S", "0.2")))
    except ValueError:
        return 0.2


def run_answer(args) -> int:
    """Write inbox/<uuid>.json and wait for the owner's .result verdict."""
    import json
    import os
    import sys as _sys
    import time
    import uuid as _uuid

    from src.errors import scrub_data, scrub_secrets

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = Path(args.run).expanduser() if getattr(args, "run", None) else None
    if run_dir is None:
        run_dir = _latest_run_dir(repo)
    if run_dir is None or not (run_dir / "state.json").is_file():
        print("Прогонов нет.")
        return 1
    if getattr(args, "human", False) and _sys.stdin.isatty():
        who = "human"
    else:
        who = "agent"
    uid = _uuid.uuid4().hex
    payload = scrub_data(
        {
            "uuid": uid,
            "question_id": args.question_id,
            "action": args.action,
            "text": args.text or "",
            "why": args.why,
            "who": who,
            "ts": time.time(),
        }
    )
    inbox = run_dir / "inbox"
    try:
        inbox.mkdir(parents=True, exist_ok=True)
    except OSError:
        print("не могу записать inbox")
        return 1
    import tempfile

    raw = json.dumps(payload, ensure_ascii=False)
    try:
        fd, tmp_name = tempfile.mkstemp(dir=inbox, prefix=f"{uid}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(raw)
            os.replace(tmp_name, inbox / f"{uid}.json")
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception:
        print("не могу записать inbox")
        return 1
    deadline = time.monotonic() + _answer_wait_s()
    result_path = inbox / f"{uid}.result"
    while time.monotonic() < deadline:
        try:
            text = result_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text:
            print(scrub_secrets(text))
            return 0
        time.sleep(_answer_poll_s())
    print("владелец не ответил")
    return 1


def run_compact(args) -> int:
    """Request a compact: write inbox/compact.json without touching state.json.

    Read-only for state.json: only the run owner writes it.
    """
    import json
    import time

    from src.loop.state import RunState

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = Path(args.run).expanduser() if getattr(args, "run", None) else None
    if run_dir is None:
        run_dir = _latest_run_dir(repo)
    if run_dir is None or not (run_dir / "state.json").is_file():
        print("Прогонов нет.")
        return 1
    state = RunState.read(run_dir / "state.json") or {}
    if state.get("status") != "running":
        print("прогон не идёт")
        return 1
    role = getattr(args, "role", "player") or "player"
    inbox = run_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / "compact.json"
    payload = json.dumps({"role": role, "ts": time.time()}, ensure_ascii=False)
    import os

    tmp_name = ""
    try:
        import tempfile

        fd, tmp_name = tempfile.mkstemp(dir=inbox, prefix="compact.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, target)
    except Exception:
        try:
            if tmp_name:
                os.unlink(tmp_name)
        except OSError:
            pass
        print("не могу записать compact.json")
        return 1
    print(f"запрошен компакт `{role}`")
    return 0


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
