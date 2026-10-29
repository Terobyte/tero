"""Pieces of the HUD model that read the stream tail: verdicts, ages, time parsing.

Split out of `model.py` to keep it under the structure ceiling (спек пульта 2, фаза 1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _verdict(state: dict, events: list[dict]) -> dict:
    # The stream is append-only and the verdict event is emitted before state.json is
    # written, so the newest verdict event is the last verdict; state.json is only a
    # fallback for when the event has left the tail.
    for ev in reversed(events):
        if ev.get("type") == "verdict" and ev.get("verdict"):
            return _verdict_summary(ev)
    saved = state.get("verdict")
    if isinstance(saved, dict) and saved.get("verdict"):
        return _verdict_summary(saved)
    return {"verdict": None, "blocking": 0, "first": ""}


def _verdict_summary(src: dict) -> dict:
    try:
        blocking = int(src.get("blocking", 0) or 0)
    except (TypeError, ValueError):
        blocking = 0
    return {
        "verdict": src.get("verdict"),
        "blocking": blocking,
        "first": _first_finding(src.get("first")),
    }


def _first_finding(first: Any) -> str:
    if not isinstance(first, dict):
        return ""
    path = str(first.get("file") or "")
    if path:
        line = first.get("line")
        return f"{path}:{line}" if line is not None else path
    return str(first.get("issue") or "")


def _age(events: list[dict], now: float) -> int | None:
    for ev in reversed(events):
        ts = _parse_ts(ev.get("ts"))
        if ts is not None:
            return max(0, int(now - ts))
    return None


def _parse_ts(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None
    return None


def _fmt_mss(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"
