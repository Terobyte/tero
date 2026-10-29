"""Sleep/wake core for the agent-host: `tero wait` and `tero status`.

Only reads `state.json` (written solely by the run owner) plus the
`pulse` side files, and writes the `lease` side file
(`<run_dir>/lease` — аренда агента). Never writes `state.json`,
never touches git. All disk failures are swallowed: a broken side file
looks like "no info", not like death.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from src.errors import scrub_data
from src.fsio import read_json_dict
from src.hud.registry import pid_is_alive
from src.loop import host_id, lease as lease_mod

DONE = 0
DEBT_DONE = 4
STOPPED = 2
NEED_AGENT = 3
DEAD = 5
TIMEOUT = 6
MESSAGE = 7
NO_HOST = 8
BUSY = 9
HOST_CHECK_S = 30.0

_AGENT_STATUSES = ("waiting_agent", "judge_pending")
_WAITING_STATUSES = ("waiting_human", "waiting_agent", "waiting_limit")
_WAITING_THRESHOLD_S = 120.0
_ALIVE_STALE_S = 600.0


def _hung_threshold(state: dict) -> float | None:
    status = (state or {}).get("status") or ""
    if status == "running":
        return (state or {}).get("hang_after_s")
    if status in _WAITING_STATUSES:
        return _WAITING_THRESHOLD_S
    return None


def is_hung(state: dict, pulse: dict | None, now: float) -> bool:
    """Shared hung-owner rule for wait_run and resume."""
    threshold = _hung_threshold(state or {})
    if not isinstance(threshold, (int, float)) or threshold <= 0:
        return False
    ts = (pulse or {}).get("progress_ts") if isinstance(pulse, dict) else None
    if not isinstance(ts, (int, float)):
        return False
    if not pid_is_alive((state or {}).get("pid"), (state or {}).get("pid_started")):
        return False
    try:
        return (float(now) - float(ts)) >= float(threshold)
    except (TypeError, ValueError):
        return False


def _read_state(run_dir: Path) -> dict[str, Any]:
    return read_json_dict(run_dir / "state.json") or {}


def _read_pulse(run_dir: Path) -> dict[str, Any] | None:
    return read_json_dict(run_dir / "pulse")


def done_line(status: str, debt: list | None) -> str:
    """Строка финала для HUD/CLI: done с долгом рисуется через debt_line."""
    from src.hud.app import hud_done_text

    n = len(debt) if isinstance(debt, list) else 0
    return hud_done_text(status, n)


def find_host(run_path: Path) -> host_id.Host:
    """The host of this `wait`: by ancestry, never a descendant of the run owner."""
    host = host_id.detect()
    if host.known and host_id.under_owner(_read_state(run_path)):
        return host_id.Host(host.kind, why="запущен из-под прогона")
    return host


def _refresh_lease(run_path: Path, host: host_id.Host, seconds: float) -> bool:
    """Write the lease for `host` unless a live lease of another host holds it."""
    if not host.known:
        return False
    try:
        if not lease_mod.may_take(run_path, host.pid):
            return False
        return lease_mod.write(run_path, until=time.time() + seconds, pid=host.pid,
                               pid_started=host.started, host=host.kind,
                               session=host.session)
    except Exception:
        return False


def _settle_lease(run_path: Path, code: int, host: host_id.Host) -> bool:
    from src.loop import autopilot as auto

    try:
        timeout = auto.agent_timeout_of(run_path)
        seconds = float(timeout) if code == NEED_AGENT else 300.0
    except Exception:
        seconds = 300.0
    return _refresh_lease(run_path, host, seconds)


def host_gone(host: host_id.Host) -> bool:
    """An orphaned `wait`: its host was known and its process is no longer there."""
    return host.known and not pid_is_alive(host.pid, host.started)


def wait_run(
    run_dir: str | Path,
    repo: str | Path,
    *,
    timeout_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 2,
    host: host_id.Host | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Block until the agent is needed, the run ends, or it looks dead.

    Codes: 0 done, 4 done with debt, 2 stopped, 3 agent needed
    (waiting_agent/judge_pending), 5 dead or hung, 6 timeout expired,
    8 the host of this `wait` is gone (checked before every other code,
    so a dead host never takes messages). Terminal statuses win even when
    the owner process is gone; 5 fires only for unfinished statuses.

    ``tero wait`` пишет аренду хозяина (pid хозяина, не оболочки): пока
    ждёт — ``now + 120`` каждые 30 с; при выходе с 3 — ``now +
    agent_timeout_s``; с 6 — ``now + 300``. Хозяин не определён — аренды
    нет и кода 8 нет.
    """
    run_path = Path(run_dir)
    repo_path = Path(repo)
    start = clock()
    last_alive: Any = None
    alive_since = start
    if host is None:
        host = find_host(run_path)
    if not host.known:
        try:
            out(f"хозяин не определён: {host.why or 'нет pid'}")
        except Exception:
            pass
    from src.loop import autopilot as auto

    if host_gone(host):
        return NO_HOST
    _refresh_lease(run_path, host, auto.LEASE_REFRESH_S)
    last_refresh = clock()

    def _exit(code: int) -> int:
        if code in (NEED_AGENT, TIMEOUT):
            _settle_lease(run_path, code, host)
        return code

    while True:
        if host_gone(host):
            return NO_HOST
        state = _read_state(run_path)
        status = state.get("status") or ""
        if status == "done":
            debt = state.get("debt") or []
            return _exit(DEBT_DONE if debt else DONE)
        if status == "stopped":
            return _exit(STOPPED)
        if status in _AGENT_STATUSES:
            return _exit(NEED_AGENT)

        if pid_is_alive(state.get("pid"), state.get("pid_started")):
            pulse = _read_pulse(run_path)
            if pulse is not None:
                progress = pulse.get("progress")
                if isinstance(progress, (int, float)):
                    try:
                        if bool(is_hung(state, pulse, time.time())):
                            return DEAD
                    except Exception:
                        pass
                alive_val = pulse.get("alive")
                if isinstance(alive_val, (int, float)):
                    now_mono = clock()
                    if last_alive is None or alive_val != last_alive:
                        last_alive = alive_val
                        alive_since = now_mono
                    elif now_mono - alive_since >= _ALIVE_STALE_S:
                        return DEAD
        else:
            try:
                from src.loop.lock import acquire

                lock = acquire(repo_path)
            except Exception:
                lock = None
            if lock is not None:
                try:
                    lock.release()
                except Exception:
                    pass
                return DEAD
            # Busy lock with a dead pid: someone else owns the repo,
            # so this run is not dead. Keep waiting.

        if timeout_s is not None and clock() - start >= timeout_s:
            return _exit(TIMEOUT)
        if clock() - last_refresh >= auto.LEASE_REFRESH_EVERY_S:
            _refresh_lease(run_path, host, auto.LEASE_REFRESH_S)
            last_refresh = clock()
        try:
            sleep(poll_s)
        except Exception:
            pass


def status_payload(
    run_dir: str | Path, repo: str | Path
) -> dict[str, Any]:
    """All `tero status` fields from state.json + pulse. Secrets scrubbed."""
    run_path = Path(run_dir)
    repo_path = Path(repo)
    state = _read_state(run_path)

    plan_name = state.get("plan") or ""
    config = state.get("config")
    if not plan_name and isinstance(config, dict):
        plan_name = config.get("plan_file") or ""
    total: int | None = None
    if plan_name:
        try:
            from src.loop.plan import load_phases

            total = len(load_phases(repo_path / str(plan_name)))
        except Exception:
            total = None
    try:
        index = int(state.get("phase_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0

    pulse_age: float | None = None
    pulse = _read_pulse(run_path)
    if pulse is not None:
        alive = pulse.get("alive")
        if isinstance(alive, (int, float)):
            try:
                pulse_age = max(0.0, time.time() - float(alive))
            except Exception:
                pulse_age = None

    question = state.get("question")
    if not isinstance(question, dict):
        question = None
    decisions = state.get("decisions")
    if not isinstance(decisions, list):
        decisions = []
    debt = state.get("debt")
    if not isinstance(debt, list):
        debt = []
    status = state.get("status") or ""
    from src.providers.limits import normalize_limits
    from src.relay.pusher import relay_status

    saved_limits = state.get("limits")
    seen: list[str] = []
    extra: tuple[str, ...] = ()
    try:
        extra = tuple(saved_limits.keys()) if isinstance(saved_limits, dict) else ()
    except Exception:
        extra = ()
    for name in (state.get("provider") or "", "codex", *extra):
        if name and name not in seen:
            seen.append(name)

    relay_hint = relay_status(run_path)
    agent = lease_mod.agent_view(run_path)

    payload = {
        "phase": f"{index + 1}/{total if total else '?'}",
        "phase_title": state.get("phase") or "",
        "round": state.get("round", 0) or 0,
        "role": state.get("role") or "",
        "provider": state.get("provider") or "",
        "model": state.get("model") or "",
        "preset": (config.get("preset") or "") if isinstance(config, dict) else "",
        "status": status,
        "doing": state.get("doing") or "",
        "done_text": done_line(status, debt),
        "question": question,
        "decisions": decisions,
        "debt": debt,
        "limits": normalize_limits(state.get("limits"), tuple(seen) or ("codex",)),
        "pulse_age_s": pulse_age,
        "lease": lease_mod.legacy_view(run_path),
        "push_ok": push_ok(run_path),
        "relay_error": relay_hint,
        "agent": agent,
        "view": {"agent": agent},
    }
    return scrub_data(payload)


def push_ok(run_dir: str | Path) -> bool:
    """The relay's last word on the push channel (`relay.json`); False until it spoke."""
    from src.relay.pusher import RELAY_FILE

    data = read_json_dict(Path(run_dir) / RELAY_FILE) or {}
    return bool(data.get("ok")) and data.get("push_ok") is not False


def agent_line(agent: dict | None) -> str:
    """`агент: claude · дежурит · аренда до 14:42` for `tero status`."""
    a = agent or {}
    if a.get("lease_until") is None:
        return "агент: не известен"
    host = str(a.get("host") or "?")
    until = _hhmm(a.get("lease_until"))
    if a.get("on_duty"):
        return f"агент: {host} · дежурит · аренда до {until}"
    if a.get("alive"):
        return f"агент: {host} · жив, не дежурит · аренда истекла {until}"
    return f"агент: {host} · умер · аренда истекла {until}"


def _hhmm(ts: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"
