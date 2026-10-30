"""Projections of the HUD model: the relay payload and `tero status --json` (спек пульта 2, п. 6).

`build_view` stays the one model. Here it is built from run files for a
process that has no window: the pusher (log of 40 lines, `view` sub-object)
and `tero status` (no log unless asked). Never raises: a missing file is an
empty model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.fsio import read_json_dict
from src.hud.model import TAB_LIMIT, LOG_TEXT_LIMIT, VIEW_KEYS, build_view
from src.hud.registry import is_dead

RELAY_LOG_LIMIT = 40
_TAIL_BYTES = 512 * 1024  # ~2000 ordinary lines; a gate_start further back loses its timer on the phone

# the nested whitelists the relay enforces (relay/src/filter.js: VIEW_FIELDS, AGENT_FIELDS,
# LOG_FIELDS); tests/relay compares them with `node -e`
VIEW_FIELDS = tuple(VIEW_KEYS)
AGENT_FIELDS = ("host", "alive", "on_duty", "pid_started_at", "lease_until", "lease_written_at")
LOG_FIELDS = ("ts", "role", "type", "text")
SECTOR_FIELDS = ("title", "color")
VERDICT_FIELDS = ("verdict", "blocking", "first")
RESUME_FIELDS = ("mode", "why")


def read_events(run_dir: str | Path, limit: int) -> list[dict]:
    """The last `limit` events of `<run_dir>/stream.jsonl` (reads the tail only)."""
    path = Path(run_dir) / "stream.jsonl"
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            chunk = fh.read()
    except OSError:
        return []
    lines = chunk.split(b"\n")
    if size > _TAIL_BYTES and lines:
        lines = lines[1:]  # a cut first line
    out: list[dict] = []
    for raw in lines[-max(1, int(limit)) - 1:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out[-max(0, int(limit)):]


def messages_waiting(run_dir: str | Path) -> int:
    """Unread messages to the host: `inbox/msg-*.json` without `.read` (фаза 1 пишет их)."""
    from src.loop.messages import count_unread

    return count_unread(run_dir)


def build_from_files(run_dir: str | Path, now: float, *, log_limit: int,
                     state: dict | None = None) -> dict:
    """The model of a run from its files; `state` can be passed when already read.

    The tail read is always TAB_LIMIT events: texts (`doing`, gate timer, verdict)
    must match the mac, only `log` is cut to `log_limit`.
    """
    from src.loop.lease import agent_view

    run_path = Path(run_dir)
    if state is None:
        state = read_json_dict(run_path / "state.json")
    try:
        events = read_events(run_path, max(TAB_LIMIT, int(log_limit or 0)))
    except Exception:
        events = []
    try:
        agent = agent_view(run_path, now)
    except Exception:
        agent = None
    try:
        waiting = messages_waiting(run_path)
    except Exception:
        waiting = 0
    resume = state.get("resume") if isinstance(state, dict) and isinstance(state.get("resume"), dict) else None
    return build_view(state if isinstance(state, dict) else None, events, now,
                      lambda s: not is_dead(s), agent=agent, log_limit=log_limit,
                      messages_waiting=waiting, resume=resume)


def _pick(value: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(value, dict):
        return value
    return {k: value[k] for k in fields if k in value}


def project(view: dict, *, with_log: bool = True) -> dict:
    """Only VIEW_KEYS, nested objects by their whitelists, log texts cut to 300."""
    out: dict[str, Any] = {}
    for key in VIEW_FIELDS:
        if key not in view:
            continue
        value = view[key]
        if key == "agent":
            value = _pick(value, AGENT_FIELDS)
        elif key == "verdict":
            value = _pick(value, VERDICT_FIELDS)
        elif key == "resume":
            value = _pick(value, RESUME_FIELDS) if isinstance(value, dict) else None
        elif key == "sectors":
            value = [_pick(s, SECTOR_FIELDS) for s in value if isinstance(s, dict)] if isinstance(value, list) else []
        elif key == "log":
            if not with_log:
                continue
            rows = value if isinstance(value, list) else []
            value = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = _pick(row, LOG_FIELDS)
                item["text"] = str(item.get("text") or "")[:LOG_TEXT_LIMIT]
                value.append(item)
        out[key] = value
    return out


def relay_view(run_dir: str | Path, now: float, state: dict | None = None) -> dict:
    """`view` for `POST /state`: the model with a 40-line log."""
    return project(build_from_files(run_dir, now, log_limit=RELAY_LOG_LIMIT, state=state))


def status_view(run_dir: str | Path, now: float, *, with_log: bool = False) -> dict:
    """`view` for `tero status --json`: no log unless `--log`."""
    return project(build_from_files(run_dir, now, log_limit=RELAY_LOG_LIMIT), with_log=with_log)
