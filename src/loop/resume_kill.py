"""Kill a hung run owner (process group, best effort)."""

from __future__ import annotations

import os
import signal
from typing import Any


def kill_hung_owner(pid: Any) -> bool:
    """Kill the hung owner's process group. Never raises."""
    try:
        pid = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return True
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except Exception:
        return False
