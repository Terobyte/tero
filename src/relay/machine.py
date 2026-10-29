"""`~/.tero/machine_id`: a random, stable id of this machine for the relay.

The hostname used to be the id: it changes with the network and shows up
in D1 as a new machine. The file is created once with `O_EXCL` under a
`flock` on `machine_id.lock`, so two first starts agree. Never raises.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def machine_id_path() -> Path:
    return Path.home() / ".tero" / "machine_id"


def _read(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value if value.isalnum() and 8 <= len(value) <= 64 else ""


def machine_id() -> str:
    """The id from the file, created on first use; "" only when ~/.tero is unwritable."""
    path = machine_id_path()
    got = _read(path)
    if got:
        return got
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix(".lock"), "a") as lock:
            try:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            got = _read(path)
            if got:
                return got
            value = secrets.token_hex(12)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(value)
            return value
    except FileExistsError:
        return _read(path)
    except OSError:
        return ""
