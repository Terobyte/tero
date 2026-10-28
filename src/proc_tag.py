"""Step process tag in the environment: orphans keep it after setsid and reparenting (B21).

A process group and a psutil descendant walk both miss a grandchild that
started its own session (agent shells, xdist workers) or whose parent died:
it is reparented to PID 1. The environment is inherited through both, so a
per-spawn tag finds such processes after the step and kills them.
Platform binaries (/bin/sleep) hide their environment on macOS; python and
node, which is what agents and test runners leave behind, do not.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import os
import time
import uuid

import psutil

TAG_ENV = "TERO_PROC_TAG"
SWEEP_GRACE_S = 2.0


def tagged_env(env: dict[str, str] | None = None) -> tuple[dict[str, str], str]:
    """A copy of env (default: os.environ) with a fresh tag, and the tag."""
    tag = uuid.uuid4().hex
    out = dict(os.environ if env is None else env)
    out[TAG_ENV] = tag
    return out, tag


def find_tagged(tag: str, since: float | None = None) -> list[psutil.Process]:
    """Live processes carrying tag; since (epoch s) skips older ones cheaply."""
    me = os.getpid()
    found: list[psutil.Process] = []
    for proc in psutil.process_iter(["create_time"]):
        if proc.pid == me:
            continue
        created = proc.info.get("create_time") or 0.0
        if since is not None and created < since - 1.0:
            continue
        try:
            if proc.environ().get(TAG_ENV) == tag:
                found.append(proc)
        except (psutil.Error, OSError):
            continue
    return found


def sweep(tag: str, since: float | None = None, grace_s: float = SWEEP_GRACE_S) -> list[int]:
    """SIGTERM every process with tag, SIGKILL what is left after grace_s. Never raises."""
    try:
        victims = find_tagged(tag, since)
    except Exception:
        return []
    for proc in victims:
        try:
            proc.terminate()
        except (psutil.Error, OSError):
            pass
    try:
        _, alive = psutil.wait_procs(victims, timeout=grace_s)
    except Exception:
        alive = victims
    for proc in alive:
        try:
            proc.kill()
        except (psutil.Error, OSError):
            pass
    return [p.pid for p in victims]


def spawn_time() -> float:
    """Epoch timestamp to pass as `since` for processes spawned after now."""
    return time.time()


def swept_env(agen_fn):
    """Async-generator decorator: tag the `env` argument, sweep the tag when the generator ends."""
    sig = inspect.signature(agen_fn)

    @functools.wraps(agen_fn)
    async def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        bound.arguments["env"], tag = tagged_env(bound.arguments.get("env"))
        started = spawn_time()
        try:
            async with contextlib.aclosing(agen_fn(*bound.args, **bound.kwargs)) as events:
                async for event in events:
                    yield event
        finally:
            sweep(tag, since=started)

    return wrapper
