"""Commands that write into a run's inbox/: answer and compact."""

from __future__ import annotations

from src.commands import resolve_run_dir


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
    import sys as _sys
    import time
    import uuid as _uuid

    from src.errors import scrub_data, scrub_secrets
    from src.loop.inbox import write_inbox_file

    run_dir = resolve_run_dir(args)
    if run_dir is None:
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
    raw = json.dumps(payload, ensure_ascii=False)
    if not write_inbox_file(run_dir, f"{uid}.json", raw):
        print("не могу записать inbox")
        return 1
    deadline = time.monotonic() + _answer_wait_s()
    result_path = run_dir / "inbox" / f"{uid}.result"
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

    from src.loop.inbox import write_inbox_file
    from src.loop.state import RunState

    run_dir = resolve_run_dir(args)
    if run_dir is None:
        return 1
    state = RunState.read(run_dir / "state.json") or {}
    if state.get("status") != "running":
        print("прогон не идёт")
        return 1
    role = getattr(args, "role", "player") or "player"
    payload = json.dumps({"role": role, "ts": time.time()}, ensure_ascii=False)
    if not write_inbox_file(run_dir, "compact.json", payload):
        print("не могу записать compact.json")
        return 1
    print(f"запрошен компакт `{role}`")
    return 0
