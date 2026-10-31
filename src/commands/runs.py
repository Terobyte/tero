"""Read-only run commands: history, stats, wait, status, review."""

from __future__ import annotations

from pathlib import Path

from src.commands import resolve_run_dir


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


def run_stats(args):
    """Aggregate stats over .tero/runs (one plan or all)."""
    from src.loop.stats import collect, render

    repo = Path(args.working_dir).resolve()
    acc = collect(repo, plan=args.plan, limit=args.limit)
    print(render(acc, args.plan or "все планы"))


def run_wait(args) -> int:
    """Sleep until the agent is needed; exit with the wait RunOutcome code."""
    from src.loop.state import RunState
    from src.loop.wait import wait_run

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = resolve_run_dir(args)
    if run_dir is None:
        return 1
    code = wait_run(run_dir, repo, timeout_s=getattr(args, "timeout", None),
                    messages=bool(getattr(args, "messages", False)))
    state = RunState.read(run_dir / "state.json") or {}
    why = {
        0: "готово",
        4: "готово с долгом",
        2: f"остановлен: {state.get('reason') or ''}".rstrip(),
        3: "нужен агент",
        5: "мёртв или завис — нужен resume",
        6: "таймаут",
        7: "сообщение хозяину",
        8: "хозяина нет: ты сирота по мнению tero; если ты жив и это читаешь — `tero status`, затем снова `tero wait`",
    }.get(code, f"код {code}")
    print(f"{state.get('status', '?')}: {why}")
    return code


def run_status(args) -> int:
    """Print run status: machine-readable with --json, lines otherwise."""
    import json as _json

    from src.loop.wait import status_payload

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = resolve_run_dir(args)
    if run_dir is None:
        return 1
    payload = status_payload(run_dir, repo, with_log=bool(getattr(args, "log", False)))
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
    if payload.get("preset"):
        print(f"пресет: {payload['preset']}")
    print(f"статус: {payload['status']}")
    done_text = payload.get("done_text") or ""
    if done_text:
        print(f"итог: {done_text}")
    print(f"doing: {payload['doing']}")
    question = payload.get("question")
    if question:
        print(f"вопрос: {question.get('id')} {question.get('text')}")
        print(f"действия: {','.join(question.get('actions') or [])}")
    else:
        print("вопрос: null")
    print(f"лимиты: {_json.dumps(payload['limits'], ensure_ascii=False)}")
    print(f"решения: {_json.dumps(payload['decisions'], ensure_ascii=False)}")
    print(f"долг: {_json.dumps(payload['debt'], ensure_ascii=False)}")
    print(f"пульс: {payload['pulse_age_s']}")
    from src.loop.wait import agent_line

    print(agent_line(payload.get("agent")))
    from src.loop.host_tail import session_line

    print(session_line(run_dir))
    unread = int((payload.get("view") or {}).get("messages_waiting") or 0)
    if unread:
        print(f"непрочитанных: {unread}")
    print(f"аренда: {_json.dumps(payload['lease'], ensure_ascii=False)}")
    print(f"push_ok: {str(payload['push_ok']).lower()}")
    if payload.get("relay_error"):
        print(payload["relay_error"])
    return 0


def run_review(args) -> int:
    """Print status, phase, round, snapshot ref, base, diff stat and full diff.

    Read-only: resolves the run dir from --run (or the latest in .tero/runs),
    reads state.json, and shows diff_for(base, ref) via a git subprocess.
    """
    from src.loop.snapshot import diff_for
    from src.loop.state import RunState

    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = resolve_run_dir(args)
    if run_dir is None:
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
