"""Хозяин `tero wait` и его аренда (вынесено из wait.py, план 09, фаза 1).

wait.py реэкспортит оба имени.
"""

from __future__ import annotations

import time
from pathlib import Path

from src.loop import host_id, lease as lease_mod


def find_host(run_path: Path) -> host_id.Host:
    """The host of this `wait`: by ancestry, never a descendant of the run owner."""
    from src.loop.wait import _read_state

    host = host_id.detect()
    if host.known and host_id.under_owner(_read_state(run_path)):
        return host_id.Host(host.kind, why="запущен из-под прогона")
    return host


def _refresh_lease(run_path: Path, host: host_id.Host, seconds: float) -> bool:
    """Write the lease for `host` unless a live lease of another host holds it."""
    if not host.known:
        return False
    try:
        return lease_mod.write(run_path, until=time.time() + seconds, pid=host.pid,
                               pid_started=host.started, host=host.kind,
                               session=host.session, if_may_take=True)
    except Exception:
        return False
