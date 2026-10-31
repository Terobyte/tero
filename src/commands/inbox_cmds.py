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


def _trusted_session_pid(want: "int | set[int]") -> int | None:
    """Доверенный pid сессии answer при совпадении с разбуженной (F18).

    Возвращает `want`, если сам процесс answer — ppid либо потомок
    разбуженной сессии (цепочка предков через psutil). Иначе None.
    В тестах _FakeProc без метода parents — только прямой ppid.
    """
    import os as _os

    wants = {int(want)} if isinstance(want, int) else {int(w) for w in want}
    wants.discard(0)
    if not wants:
        return None
    try:
        me = int(_os.getppid())
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if me in wants:
        return me
    try:
        from psutil import Process as _Proc
    except Exception:
        return None
    pid = me
    seen = {pid}
    try:
        proc = _Proc(pid)
    except Exception:
        return None
    for _ in range(32):
        try:
            parents = proc.parents()
        except Exception:
            return None
        chain = parents() if callable(parents) else parents
        try:
            chain = list(chain or [])
        except TypeError:
            return None
        found = False
        for parent in chain:
            try:
                num = int(getattr(parent, "pid", 0) or 0)
            except (TypeError, ValueError):
                continue
            if num in wants:
                return num
            if num > 0 and num not in seen:
                seen.add(num)
                try:
                    proc = _Proc(num)
                except Exception:
                    continue
                found = True
                break
        if not found:
            return None
    return None


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
    # pid хозяина — по предкам (host_id), не по --agent-pid / TERO_AGENT_PID
    # и не ppid (это временный bash). Из-под владельца прогона хозяина нет.
    agent_pid: int | None = None
    pid_started: float | None = None
    host_kind = ""
    if who == "agent":
        from src.loop.host_id import detect, under_owner
        from src.loop.state import RunState as _RS

        host = detect()
        if host.known and not under_owner(_RS.read(run_dir / "state.json") or {}):
            agent_pid, pid_started, host_kind = host.pid, host.started, host.kind
    # F6+F17+F18: woken — только по доверенной идентичности сессии.
    # Заявленные --agent-pid / TERO_AGENT_PID игнорируются (их задаёт сам
    # отвечающий): доверенным считается только сам процесс answer — ppid
    # либо любой предок через дочерний shell/tool-процесс. Совпадение с
    # state.woken[question_id] означает: отвечает именно разбуженная сессия.
    woken = False
    if who == "agent":
        import os as _os

        from src.loop import wake as _wake

        want = _wake.run_woken_pids(run_dir) | _wake.global_woken_pids()
        trusted = _trusted_session_pid(want)
        if trusted is not None:
            agent_pid = trusted
        # TERO_WOKEN=1 сужает права и без совпадения предков (список пробуждений мог устареть).
        woken = trusted is not None or _os.environ.get("TERO_WOKEN") == "1"
    payload = scrub_data(
        {
            "uuid": uid,
            "question_id": args.question_id,
            "action": args.action,
            "text": args.text or "",
            "why": args.why,
            "who": who,
            "ts": time.time(),
            **({"agent_pid": agent_pid, "agent_pid_started": pid_started,
                "host": host_kind} if agent_pid else {}),
            **({"woken": True} if woken else {}),
        }
    )
    if who == "human":
        from pathlib import Path

        from src.loop.human_key import sign_answer

        sign_answer(Path(run_dir).name, payload)
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


def run_inbox(args) -> int:
    """Print the unread signed messages to the host and mark them read; `--all` keeps them."""
    from src.loop import messages

    run_dir = resolve_run_dir(args)
    if run_dir is None:
        return 1
    texts = messages.history(run_dir) if getattr(args, "all", False) else messages.take_unread(run_dir)
    if not texts:
        print("сообщений нет")
    for text in texts:
        from src.errors import scrub_secrets

        print(scrub_secrets(text))
    return 0
