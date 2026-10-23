"""Live run event log for the HUD: append-only stream.jsonl that never breaks a run."""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Stream:
    """Append whole newline-terminated JSON lines to stream.jsonl.

    One ``emit`` call is one ``write`` call, so an offset-based reader
    never sees a torn line as long as it defers a tail without ``\\n``.
    A failed write warns once on stderr and never raises.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._warned = False

    def emit(self, type: str, **fields: Any) -> None:
        try:
            line = (
                json.dumps(
                    {"ts": datetime.now(timezone.utc).isoformat(), "type": type, **fields},
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except Exception as exc:
            with self._lock:
                if self._warned:
                    return
                self._warned = True
            try:
                print(f"HUD: не могу записать {self.path}: {exc}", file=sys.stderr)
            except Exception:
                pass
