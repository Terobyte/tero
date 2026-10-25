"""Sleep/wake core for the agent-host: `tero wait` and `tero status`.

Only reads `state.json` (written solely by the run owner) plus the
`pulse`/`watch` side files. Never writes `state.json`, never touches git.
All disk failures are swallowed: a broken side file looks like "no info",
not like death.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from src.errors import scrub_data
from src.fsio import atomic_write_json, read_json_dict
from src.hud.registry import pid_is_alive

DONE = 0
DEBT_DONE = 4
STOPPED = 2
NEED_AGENT = 3
DEAD = 5
TIMEOUT = 6

_AGENT_STATUSES = ("waiting_agent", "judge_pending")
_WAITING_STATUSES = ("waiting_human", "waiting_agent", "waiting_limit")
_WAITING_THRESHOLD_S = 120.0
_ALIVE_STALE_S = 600.0


def boot_id() -> str:
    """Stable id of the current OS boot; "" when unknown."""
    try:
        if sys.platform == "darwin":
            proc = subprocess.run(
                ["sysctl", "-n", "kern.bootsessionuuid"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
            return ""
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except Exception:
        return ""


def _read_state(run_dir: Path) -> dict[str, Any]:
    return read_json_dict(run_dir / "state.json") or {}


def _read_pulse(run_dir: Path) -> dict[str, Any] | None:
    return read_json_dict(run_dir / "pulse")


def _read_watch(run_dir: Path) -> dict[str, Any] | None:
    return read_json_dict(run_dir / "watch")


def _write_watch(
    run_dir: Path, boot: str, progress: Any, seen_mono: float
) -> None:
    """Record when this progress value was first seen. Best effort."""
    try:
        payload = {"boot_id": boot, "progress": progress, "seen_mono": seen_mono}
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(run_dir / "watch", payload, mode=0o600)
    except Exception:
        pass


def done_line(status: str, debt: list | None) -> str:
    """Строка финала для HUD/CLI: done с долгом рисуется через debt_line."""
    from src.hud.app import hud_done_text

    n = len(debt) if isinstance(debt, list) else 0
    return hud_done_text(status, n)


def wait_run(
    run_dir: str | Path,
    repo: str | Path,
    *,
    timeout_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 2,
) -> int:
    """Block until the agent is needed, the run ends, or it looks dead.

    Codes: 0 done, 4 done with debt, 2 stopped, 3 agent needed
    (waiting_agent/judge_pending), 5 dead or hung, 6 timeout expired.
    Terminal statuses win even when the owner process is gone; 5 fires
    only for unfinished statuses.
    """
    run_path = Path(run_dir)
    repo_path = Path(repo)
    boot = boot_id()
    start = clock()
    last_alive: Any = None
    alive_since = start

    while True:
        state = _read_state(run_path)
        status = state.get("status") or ""
        if status == "done":
            debt = state.get("debt") or []
            return DEBT_DONE if debt else DONE
        if status == "stopped":
            return STOPPED
        if status in _AGENT_STATUSES:
            return NEED_AGENT

        if pid_is_alive(state.get("pid"), state.get("pid_started")):
            now = clock()
            pulse = _read_pulse(run_path)
            if pulse is not None:
                progress = pulse.get("progress")
                if isinstance(progress, (int, float)):
                    watch = _read_watch(run_path)
                    if (
                        watch is not None
                        and watch.get("boot_id") == boot
                        and watch.get("progress") == progress
                        and isinstance(watch.get("seen_mono"), (int, float))
                    ):
                        seen = watch["seen_mono"]
                    else:
                        seen = now
                        _write_watch(run_path, boot, progress, seen)
                    if status == "running":
                        threshold = state.get("hang_after_s")
                    elif status in _WAITING_STATUSES:
                        threshold = _WAITING_THRESHOLD_S
                    else:
                        threshold = None
                    if (
                        isinstance(threshold, (int, float))
                        and threshold > 0
                        and now - seen >= threshold
                    ):
                        return DEAD
                alive_val = pulse.get("alive")
                if isinstance(alive_val, (int, float)):
                    if last_alive is None or alive_val != last_alive:
                        last_alive = alive_val
                        alive_since = now
                    elif now - alive_since >= _ALIVE_STALE_S:
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
            return TIMEOUT
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

    payload = {
        "phase": f"{index + 1}/{total if total else '?'}",
        "phase_title": state.get("phase") or "",
        "round": state.get("round", 0) or 0,
        "role": state.get("role") or "",
        "provider": state.get("provider") or "",
        "model": state.get("model") or "",
        "status": status,
        "doing": state.get("doing") or "",
        "done_text": done_line(status, debt),
        "question": question,
        "decisions": decisions,
        "debt": debt,
        "pulse_age_s": pulse_age,
        "lease": None,
        "push_ok": False,
    }
    return scrub_data(payload)
