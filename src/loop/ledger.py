"""Append-only run journal and on-disk artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Ledger:
    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "ledger.jsonl"

    def event(self, kind: str, **data: Any) -> None:
        from src.errors import scrub_data

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **scrub_data(data),
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def artifact(self, relpath: str, content: str) -> Path:
        target = self.run_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target


def _runs_dir(repo: str | Path) -> Path:
    return Path(repo) / ".tero" / "runs"


def new_run_dir(repo: str | Path, slug: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
    path = _runs_dir(repo) / f"{stamp}-{slug}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def history(repo: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    runs = _runs_dir(repo)
    if limit <= 0 or not runs.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    try:
        entries = sorted(runs.iterdir(), reverse=True)
    except OSError:
        return []
    for directory in entries:
        if not directory.is_dir():
            continue
        ledger = directory / "ledger.jsonl"
        if not ledger.is_file():
            continue
        start: dict[str, Any] = {}
        end: dict[str, Any] = {}
        try:
            lines = ledger.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("kind") == "run_start":
                start = event
            elif event.get("kind") == "run_end":
                end = event
        if not start:
            # Несостоявшийся старт (fail_start пишет только stop): HUD видит
            # state.json=stopped, а в историю прогон не попадает.
            continue
        parts = directory.name.split("-", 2)
        if len(parts) < 3:
            continue
        slug = parts[2]
        rows.append(
            {
                "slug": slug,
                "dir": str(directory),
                "plan": start.get("plan", ""),
                "reason": end.get("reason", ""),
                "phases_shipped": end.get("phases_shipped", 0),
                "phases_total": end.get("phases_total", 0),
            }
        )
        if len(rows) >= limit:
            break
    return rows
