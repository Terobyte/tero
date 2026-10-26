"""HUD screen model: pure function run-files -> what to draw (phase 7).

No window here: `build_view` turns one `state.json` snapshot plus the
`stream.jsonl` tail events into a JSON-ready dict for the HUD window
(phase 8). Reads nothing, writes nothing, never raises on bad input.
"""

from __future__ import annotations

from datetime import datetime, timezone  # noqa: F401 (реэкспорт для тестов)
from typing import Any, Callable

from src.hud.app import debt_line, hud_done_text  # noqa: F401 (единые строки HUD)

TAB_LIMIT = 2000

_UNFINISHED = (
    "starting",
    "running",
    "waiting_human",
    "waiting_agent",
    "waiting_limit",
    "judge_pending",
)

_ROLE_ICONS = {"player": "🛠", "judge": "⚖️", "tester": "🧪", "gate": "⏱"}


def build_view(
    state: dict | None,
    events: list[dict],
    now: float,
    alive: Callable[[dict], bool],
) -> dict:
    """Snapshot + tail events -> screen fields. `None` state keeps the old frame."""
    items = [e for e in (events or []) if isinstance(e, dict)]
    tabs = _tabs(items)
    if not isinstance(state, dict):
        return {
            "sectors": [],
            "circle": "grey",
            "icon": "❓",
            "header": "",
            "role_line": "",
            "doing": "нет данных",
            "last_event_age_s": _age(items, now),
            "verdict": {"verdict": None, "blocking": 0, "first": ""},
            "debt_line": debt_line(0),
            "done_text": "",
            "tabs": tabs,
        }
    status = state.get("status") or ""
    broken = _is_broken(state, status, alive)
    sectors = _sectors(state)
    debt_n = _debt_count(state)
    return {
        "sectors": sectors,
        "circle": _circle(status, sectors, broken),
        "icon": _icon(state, status, broken),
        "header": _header(state, now),
        "role_line": _role_line(state),
        "doing": _doing(state, status, items, now, broken),
        "last_event_age_s": _age(items, now),
        "verdict": _verdict(state, items),
        "debt_line": debt_line(debt_n),
        "done_text": hud_done_text(status, debt_n),
        "tabs": tabs,
    }


def _is_broken(state: dict, status: str, alive: Callable[[dict], bool]) -> bool:
    if status not in _UNFINISHED:
        return False
    try:
        return not alive(state)
    except Exception:
        return False


def _sectors(state: dict) -> list[dict]:
    """One sector per plan phase: done green, current yellow, rest red, debt orange."""
    phases = state.get("phases")
    if not isinstance(phases, list) or not phases:
        return []
    try:
        index = int(state.get("phase_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    debt_nos = _debt_phases(state.get("debt"))
    shipped = bool(state.get("phase_done"))
    out = []
    for i, item in enumerate(phases):
        info = item if isinstance(item, dict) else {}
        if info.get("done") or i < index or (i == index and shipped):
            color = "green"
        elif i == index:
            color = "yellow"
        else:
            color = "red"
        if (i + 1) in debt_nos:
            color = "orange"
        out.append({"title": info.get("title", ""), "color": color})
    return out


def _debt_phases(debt: Any) -> set[int]:
    nos: set[int] = set()
    if not isinstance(debt, list):
        return nos
    for entry in debt:
        if not isinstance(entry, dict):
            continue
        try:
            nos.add(int(entry.get("phase")))
        except (TypeError, ValueError):
            continue
    return nos


def _debt_count(state: dict) -> int:
    debt = state.get("debt")
    return len(debt) if isinstance(debt, list) else 0


def _circle(status: str, sectors: list[dict], broken: bool) -> str:
    if not sectors:
        return "grey"
    if broken or status == "stopped":
        return "red"
    if status == "done":
        return "green"
    colors = {s["color"] for s in sectors}
    if "yellow" in colors:
        return "yellow"
    if colors == {"green"}:
        return "green"
    return "grey"


def _icon(state: dict, status: str, broken: bool) -> str:
    if status == "done":
        return "✓"
    if broken or status == "stopped":
        return "❓"
    if status == "judge_pending":
        return "⏸"
    if status in ("waiting_human", "waiting_agent", "waiting_limit"):
        return "❓"
    question = state.get("question")
    if isinstance(question, dict) and question.get("text"):
        return "❓"
    return _ROLE_ICONS.get(state.get("role") or "", "❓")


def _header(state: dict, now: float) -> str:
    try:
        index = int(state.get("phase_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    total: Any = state.get("total_phases")
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        phases = state.get("phases")
        total = len(phases) if isinstance(phases, list) and phases else "?"
    try:
        rnd = int(state.get("round", 0) or 0)
    except (TypeError, ValueError):
        rnd = 0
    started = _parse_ts(state.get("phase_started"))
    elapsed = now - started if started is not None else 0
    return f"Фаза {index + 1}/{total} · раунд {rnd} · {_fmt_mmss(elapsed)}"


def _role_line(state: dict) -> str:
    role = str(state.get("role") or "")
    model = str(state.get("model") or "")
    if role and model:
        return f"{role} · {model}"
    return role or model


def _doing(state: dict, status: str, events: list[dict], now: float, broken: bool) -> str:
    if broken:
        return "прогон оборвался"
    if status == "stopped":
        return str(state.get("text") or state.get("reason") or "стоп")
    if status == "done":
        return hud_done_text(status, _debt_count(state))
    question = state.get("question")
    if isinstance(question, dict):
        text = str(question.get("text") or "").strip()
        if text:
            return text
    role = str(state.get("role") or "")
    if role == "gate":
        gate = _gate_doing(events, now)
        if gate is not None:
            return gate
    for ev in reversed(events):
        if ev.get("role") == role and ev.get("type") == "text" and ev.get("text"):
            return str(ev["text"])
    for ev in reversed(events):
        if ev.get("type") == "tool" and ev.get("text"):
            return str(ev["text"])
    return str(state.get("doing") or "")


def _gate_doing(events: list[dict], now: float) -> str | None:
    start_idx = end_idx = -1
    for i, ev in enumerate(events):
        if ev.get("type") == "gate_start":
            start_idx = i
        elif ev.get("type") == "gate":
            end_idx = i
    if start_idx < 0 and end_idx < 0:
        return None
    if start_idx > end_idx:
        started = _parse_ts(events[start_idx].get("ts"))
        elapsed = now - started if started is not None else 0
        return f"⏱ тесты идут {_fmt_mss(elapsed)}"
    text = events[end_idx].get("text")
    return str(text) if text else "тесты …"


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


def _tabs(events: list[dict]) -> dict[str, list[dict]]:
    tabs: dict[str, list[dict]] = {
        "all": [],
        "player": [],
        "judge": [],
        "tester": [],
        "gate": [],
    }
    for ev in events:
        tabs["all"].append(ev)
        role = ev.get("role")
        if role in tabs and role != "all":
            tabs[role].append(ev)
    for key, items in tabs.items():
        tabs[key] = items[-TAB_LIMIT:]
    return tabs


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


def _fmt_mmss(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    return f"{total // 60:02d}:{total % 60:02d}"


def _fmt_mss(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"
