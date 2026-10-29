"""HUD screen model: pure function run-files -> what to draw (phase 7).

No window here: `build_view` turns one `state.json` snapshot plus the
`stream.jsonl` tail events into a JSON-ready dict for the HUD window
(phase 8). Reads nothing, writes nothing, never raises on bad input.
"""

from __future__ import annotations

from datetime import datetime, timezone  # noqa: F401 (реэкспорт для тестов)
from typing import Any, Callable

from src.hud.app import debt_line, hud_done_text  # noqa: F401 (единые строки HUD)
from src.hud.model_parts import (  # noqa: F401 (реэкспорт)
    _age, _first_finding, _fmt_mmss, _fmt_mss, _parse_ts, _verdict, _verdict_age, _verdict_summary,
)

TAB_LIMIT = 2000

_UNFINISHED = (
    "starting",
    "running",
    "waiting_human",
    "waiting_agent",
    "waiting_limit",
    "judge_pending",
)

QUIET_AFTER_S = 600
_AGENT_STATUSES = ("waiting_agent", "judge_pending")

_WAITING_TEXT = {
    "waiting_human": "ждёт тебя",
    "judge_pending": "ждёт тебя",
    "waiting_agent": "ждёт агента",
    "waiting_limit": "ждёт лимит",
}

_ROLE_ICONS = {"player": "🛠", "judge": "⚖️", "tester": "🧪", "gate": "⏱"}


NO_AGENT = {"host": "", "alive": False, "on_duty": False, "pid_started_at": None,
            "lease_until": None, "lease_written_at": None}


def build_view(
    state: dict | None,
    events: list[dict],
    now: float,
    alive: Callable[[dict], bool],
    agent: dict | None = None,
) -> dict:
    """Snapshot + tail events -> screen fields. `None` state keeps the old frame.

    `agent` is `lease.agent_view(run_dir)`: whether the host is alive and on duty.
    """
    items = [e for e in (events or []) if isinstance(e, dict)]
    tabs = _tabs(items)
    agent = dict(NO_AGENT, **(agent or {}))
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
            "verdict_age_s": None,
            "debt_line": debt_line(0),
            "done_text": "",
            "tabs": tabs,
            "light": "grey",
            "light_text": "нет данных",
            "phase_label": "",
            "phase_title": "",
            "role": "",
            "agent": agent,
        }
    status = state.get("status") or ""
    broken = _is_broken(state, status, alive)
    sectors = _sectors(state)
    debt_n = _debt_count(state)
    age = _age(items, now)
    light, light_text = _light(state, status, broken, age, debt_n, agent)
    return {
        "sectors": sectors,
        "circle": _circle(status, sectors, broken),
        "icon": _icon(state, status, broken),
        "header": _header(state, now),
        "role_line": _role_line(state),
        "doing": _doing(state, status, items, now, broken, agent),
        "last_event_age_s": age,
        "verdict": _verdict(state, items),
        "verdict_age_s": _verdict_age(items, now),
        "debt_line": debt_line(debt_n),
        "done_text": hud_done_text(status, debt_n),
        "tabs": tabs,
        "light": light,
        "light_text": light_text,
        "phase_label": _phase_label(state),
        "phase_title": str(state.get("phase") or _current_title(state)),
        "role": str(state.get("role") or ""),
        "agent": agent,
    }


def _light(state: dict, status: str, broken: bool, age: int | None, debt_n: int,
           agent: dict | None = None) -> tuple[str, str]:
    """Run health for the status light: green goes, yellow needs a look, red is dead,
    ready is a finished run (debt or not: the debt is text, not a colour)."""
    if broken:
        return "red", "оборвался"
    if status == "stopped":
        return "red", "стоп"
    if status == "done":
        return "ready", f"сдано с долгом {debt_n}" if debt_n else "сдано"
    question = state.get("question")
    if status in _AGENT_STATUSES and not (agent or {}).get("on_duty"):
        return "yellow", "агента нет"
    if status in _WAITING_TEXT:
        return "yellow", _WAITING_TEXT[status]
    if isinstance(question, dict) and question.get("text"):
        return "yellow", "ждёт тебя"
    if age is not None and age >= QUIET_AFTER_S:
        return "yellow", f"тихо {age // 60} мин"
    return "green", "работает"


def _phase_label(state: dict) -> str:
    try:
        index = int(state.get("phase_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    phases = state.get("phases")
    total: Any = state.get("total_phases")
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        total = len(phases) if isinstance(phases, list) and phases else "?"
    return f"{index + 1}/{total}"


def _current_title(state: dict) -> str:
    phases = state.get("phases")
    try:
        index = int(state.get("phase_index", 0) or 0)
        info = phases[index]
    except (TypeError, ValueError, IndexError, KeyError):
        return ""
    return str(info.get("title") or "") if isinstance(info, dict) else ""


def _is_broken(state: dict, status: str, alive: Callable[[dict], bool]) -> bool:
    if status not in _UNFINISHED:
        return False
    try:
        return not alive(state)
    except Exception:
        return False


def _sectors(state: dict) -> list[dict]:
    """One sector per plan phase: done green, current yellow, rest grey, debt orange."""
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
            color = "grey"
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
    model = str(state.get("model") or "").rsplit("/", 1)[-1]
    if role and model:
        return f"{role} · {model}"
    return role or model


def agent_note(agent: dict | None) -> str:
    """`хозяин жив, не дежурит` / `агента нет` for a stopped run's plaque."""
    a = agent or {}
    if a.get("alive"):
        return "" if a.get("on_duty") else "хозяин жив, не дежурит"
    return "агента нет"


def _doing(state: dict, status: str, events: list[dict], now: float, broken: bool,
           agent: dict | None = None) -> str:
    if broken:
        return "прогон оборвался"
    if status == "stopped":
        text = str(state.get("text") or state.get("reason") or "стоп")
        note = agent_note(agent)
        return f"{text} · {note}" if note else text
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
    turn = _current_turn(events)
    for ev in reversed(turn):
        if ev.get("role") == role and ev.get("type") == "text" and ev.get("text"):
            return str(ev["text"])
    for ev in reversed(turn):
        if ev.get("type") == "tool" and ev.get("text"):
            return str(ev["text"])
    if not turn and role in ROLE_WAIT:
        return ROLE_WAIT[role]
    return str(state.get("doing") or "")


# a step boundary: what came before belongs to an earlier step (a judge that
# has not spoken yet must not show the last round's verdict json)
TURN_BOUNDARY = {"start_step", "gate_start", "gate", "verdict"}
ROLE_WAIT = {"judge": "судья читает…", "player": "игрок начинает…", "tester": "тестер начинает…"}


def _current_turn(events: list[dict]) -> list[dict]:
    """Events after the newest step boundary (all of them when there is none)."""
    for i in range(len(events) - 1, -1, -1):
        if events[i].get("type") in TURN_BOUNDARY:
            return events[i + 1:]
    return events


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


