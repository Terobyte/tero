"""Offset-based reader for stream.jsonl.

Reads only what is new since the last call. A trailing fragment without
``\\n`` is deferred to the next call; a broken (non-JSON) line is skipped.
If the file shrank below the stored offset (rotation/truncation), reading
restarts from zero. Reading never raises: a missing file reads as empty.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Tail:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.offset = 0

    def read_new(self) -> list[dict]:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:
            self.offset = 0
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
        except OSError:
            return []
        if not chunk:
            return []
        if chunk.endswith(b"\n"):
            raw_lines = chunk.split(b"\n")[:-1]
            consumed = len(chunk)
        else:
            *raw_lines, _rest = chunk.split(b"\n")
            consumed = len(chunk) - len(_rest)
        events: list[dict] = []
        for raw in raw_lines:
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                events.append(obj)
        self.offset += consumed
        return events
