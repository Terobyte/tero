"""Import every src module up front for long-lived commands.

`tero` runs straight from its checkout and imports lazily inside rounds. A
refactor or a branch switch in that checkout while a run is going used to
crash it at the next lazy import (ImportError: cannot import name
'opus_decision' from 'src.loop', 2026-10-28). With everything in
sys.modules, later imports never touch the disk again. ~0.3 s.
"""

from __future__ import annotations

import importlib
import pkgutil


def preload_all() -> list[str]:
    """Import all src.* modules; return the ones that failed (never raises)."""
    import src

    failed: list[str] = []
    for info in pkgutil.walk_packages(src.__path__, "src."):
        try:
            importlib.import_module(info.name)
        except Exception:
            failed.append(info.name)
    return failed
