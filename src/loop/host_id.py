"""Who is the agent-host of `tero wait` / `tero answer`, by process ancestry.

The lease used to carry `os.getppid()`: under Claude Code that is the
temporary `bash -c`, dead seconds after `wait` exits, so the owner saw a
dead agent exactly while the agent was thinking. Now the host pid is the
process of the host itself: `CLAUDE_PID` from the environment when it is
among our ancestors, else the first ancestor that is not a shell or a
wrapper. A pid is accepted only when it is an ancestor, not pid 1, not a
subreaper and its name matches the host kind. Nothing here raises.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

WRAPPERS = frozenset({
    "bash", "sh", "zsh", "fish", "dash", "ksh", "uv", "uvx", "env", "timeout",
    "bwrap", "caffeinate", "nohup", "setsid", "script", "sudo", "doas",
})
SUBREAPERS = frozenset({"launchd", "systemd", "init", "bwrap", "tini", "dumb-init"})
HOST_NAMES = {"claude": ("claude", "node"), "opencode": ("opencode", "node", "bun")}

# session identity Claude Code passes to its commands; the owner and a woken
# agent must not hand it further down (a player would look like the host)
HOST_ENV_VARS = (
    "CLAUDE_PID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_AFTER_LAST_COMPACT", "AI_AGENT", "TERO_AGENT_PID", "TERO_HOST",
)


@dataclass(frozen=True)
class Proc:
    pid: int
    name: str = ""
    cmdline: tuple[str, ...] = ()
    started: float | None = None


@dataclass(frozen=True)
class Host:
    kind: str
    pid: int = 0
    started: float | None = None
    session: str = ""
    why: str = ""

    @property
    def known(self) -> bool:
        return self.pid > 0 and self.started is not None


def host_kind(env: Mapping[str, str] | None = None) -> str:
    e = os.environ if env is None else env
    if str(e.get("CLAUDECODE") or "") == "1":
        return "claude"
    return str(e.get("TERO_HOST") or "").strip()


def ancestors(pid: int | None = None) -> list[Proc]:
    """Parents of `pid` (ourselves by default), nearest first. [] when unknown."""
    try:
        import psutil

        me = psutil.Process(pid) if pid is not None else psutil.Process()
        out: list[Proc] = []
        for parent in me.parents():
            try:
                out.append(Proc(int(parent.pid), str(parent.name() or ""),
                                tuple(parent.cmdline() or ()), float(parent.create_time())))
            except Exception:
                out.append(Proc(int(getattr(parent, "pid", 0) or 0)))
        return out
    except Exception:
        return []


def _base(name: str) -> str:
    return os.path.basename(str(name or "")).lower()


def _is_wrapper(proc: Proc) -> bool:
    base = _base(proc.name)
    if base in WRAPPERS or base.startswith("python"):
        return True
    return bool(proc.cmdline) and _base(proc.cmdline[0]) in WRAPPERS


def _name_matches(kind: str, proc: Proc) -> bool:
    wanted = HOST_NAMES.get(kind)
    if not wanted:
        return True
    base = _base(proc.name)
    argv = " ".join(proc.cmdline).lower()
    if base in wanted and (base == kind or kind in argv or not proc.cmdline):
        return True
    return kind in base


def _reject(kind: str, proc: Proc) -> str:
    if proc.pid <= 1:
        return "pid 1"
    if _base(proc.name) in SUBREAPERS:
        return f"subreaper {proc.name}"
    if proc.started is None:
        return "нет времени старта"
    if not _name_matches(kind, proc):
        return f"имя {proc.name or '?'} не похоже на {kind or 'хозяина'}"
    return ""


def detect(env: Mapping[str, str] | None = None,
           chain: Iterable[Proc] | None = None) -> Host:
    """The host of this process: kind from the environment, pid from ancestry."""
    e = os.environ if env is None else env
    kind = host_kind(e)
    parents = list(ancestors() if chain is None else chain)
    by_pid = {p.pid: p for p in parents}
    candidate: Proc | None = None
    try:
        claimed = int(str(e.get("CLAUDE_PID") or "0").strip() or 0)
    except ValueError:
        claimed = 0
    if claimed > 0:
        candidate = by_pid.get(claimed)
        if candidate is None:
            return Host(kind, why=f"CLAUDE_PID {claimed} не среди предков")
    if candidate is None:
        candidate = next((p for p in parents if not _is_wrapper(p)), None)
    if candidate is None:
        return Host(kind, why="предков-хозяев нет")
    why = _reject(kind, candidate)
    if why:
        return Host(kind, why=why)
    return Host(kind, candidate.pid, candidate.started,
                str(e.get("CLAUDE_CODE_SESSION_ID") or ""))


def under_owner(state: Mapping[str, Any] | None,
                chain: Iterable[Proc] | None = None) -> bool:
    """True when the run owner (pid + pid_started from state.json) is our ancestor."""
    try:
        pid = int((state or {}).get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    started = (state or {}).get("pid_started")
    for proc in (ancestors() if chain is None else chain):
        if proc.pid != pid:
            continue
        if proc.started is None or not isinstance(started, (int, float)):
            return True
        return abs(float(proc.started) - float(started)) < 1.0
    return False


def strip_host_env(environ: Any = None) -> list[str]:
    """Drop the host's session identity from `environ` (os.environ by default)."""
    target = os.environ if environ is None else environ
    gone = []
    for name in HOST_ENV_VARS:
        if name in target:
            try:
                del target[name]
                gone.append(name)
            except Exception:
                pass
    return gone
