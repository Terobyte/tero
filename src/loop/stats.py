"""Run statistics over .tero/runs: verdicts, time per role, findings, situations.

Reads only what runs already write: ledger.jsonl and phase-NN/round-NN/{judge,player}.json.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

TIMED = ("player", "gate", "judge", "tester")


def _rows(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _json(path: Path) -> dict[str, Any]:
    """Round artifact; some are stored as a JSON string holding JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, str):
            data = json.loads(data)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ts(row: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(row.get("ts")))
    except ValueError:
        return None


def run_dirs(repo: Path, plan: str | None = None, limit: int | None = None) -> list[Path]:
    """Run dirs oldest first; `plan` matches the slug suffix of the dir name."""
    root = repo / ".tero" / "runs"
    if not root.is_dir():
        return []
    dirs = sorted(p for p in root.iterdir() if (p / "ledger.jsonl").is_file())
    if plan:
        dirs = [p for p in dirs if p.name.endswith(plan)]
    return dirs[-limit:] if limit else dirs


def _changed_files(player: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for item in player.get("changes") or []:
        name = item.get("file") if isinstance(item, dict) else str(item).split(":")[0]
        if name:
            out.add(str(name))
    return out


def _findings(run: Path, acc: dict[str, Any]) -> None:
    for judge_path in sorted(run.glob("phase-*/round-*/judge.json")):
        judge = _json(judge_path)
        try:
            round_no = int(judge_path.parent.name.split("-")[1])
        except (IndexError, ValueError):
            round_no = 1
        changed = _changed_files(_json(judge_path.parent / "player.json"))
        for prev in judge.get("previous") or []:
            if isinstance(prev, dict):
                acc["previous"][str(prev.get("state"))] += 1
        for item in judge.get("findings") or []:
            if not isinstance(item, dict):
                continue
            acc["level"][str(item.get("level") or "-")] += 1
            acc["route"][str(item.get("route") or "-")] += 1
            if round_no >= 2:
                acc["late"] += 1
                if item.get("file") in changed:
                    acc["late_in_changed"] += 1


def collect(repo: Path, plan: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """Aggregate over the selected runs. Never raises on a broken artifact."""
    acc: dict[str, Any] = {
        "runs": 0, "verdict": Counter(), "kind": Counter(), "stop": Counter(),
        "seconds": Counter(), "count": Counter(), "situation": Counter(),
        "level": Counter(), "route": Counter(), "previous": Counter(),
        "late": 0, "late_in_changed": 0,
    }
    for run in run_dirs(repo, plan, limit):
        acc["runs"] += 1
        prev_ts = None
        for row in _rows(run / "ledger.jsonl"):
            kind = str(row.get("kind"))
            acc["kind"][kind] += 1
            ts = _ts(row)
            if kind in TIMED and ts and prev_ts:
                acc["seconds"][kind] += (ts - prev_ts).total_seconds()
                acc["count"][kind] += 1
            prev_ts = ts or prev_ts
            if kind == "judge" and row.get("verdict"):
                acc["verdict"][str(row["verdict"])] += 1
            elif kind == "stop":
                acc["stop"][str(row.get("reason") or "-")] += 1
            elif kind == "situation":
                acc["situation"][f"{row.get('situation')}→{row.get('action')}"] += 1
        _findings(run, acc)
    return acc


def _counts(counter: Counter) -> str:
    return ", ".join(f"{k} {v}" for k, v in counter.most_common()) or "—"


def render(acc: dict[str, Any], title: str) -> str:
    """Human text for `tero stats`."""
    if not acc["runs"]:
        return "Прогонов нет."
    kind, verdict = acc["kind"], acc["verdict"]
    lines = [f"--- Статистика: {title} ({acc['runs']} прогонов) ---", ""]
    lines.append(f"фазы: начато {kind['phase_start']}, шип {kind['ship']}, "
                 f"стопов {kind['stop']} ({_counts(acc['stop'])})")
    lines.append(f"вердикты: {sum(verdict.values())} — {_counts(verdict)}")
    parts = []
    for role in TIMED:
        n = acc["count"][role]
        if n:
            sec = acc["seconds"][role]
            parts.append(f"{role} {sec / 3600:.1f} ч ({sec / n / 60:.1f} мин × {n})")
    lines.append("время: " + (", ".join(parts) or "—"))
    total = sum(acc["level"].values())
    lines.append(f"находки: {total} — {_counts(acc['level'])}")
    lines.append(f"  маршрут: {_counts(acc['route'])}")
    prev = acc["previous"]
    closed = prev["resolved"] + prev["still_open"]
    if closed:
        lines.append(f"  старые: закрыто {prev['resolved']}, висит {prev['still_open']} "
                     f"({100 * prev['resolved'] // closed}% закрытия)")
    if acc["late"]:
        lines.append(f"  новые с раунда 2: {acc['late']}, из них в файлах, "
                     f"что игрок только что правил: {acc['late_in_changed']} "
                     f"({100 * acc['late_in_changed'] // acc['late']}%)")
    lines.append(f"ситуации: {_counts(acc['situation'])}")
    return "\n".join(lines)
