"""HUD screen model: pure function run-files -> what to draw (phase 7, спек пульта 2 п. 6).

No window here: `build_view` turns one `state.json` snapshot plus the
`stream.jsonl` tail events into a JSON-ready dict for the HUD window and,
through `src/hud/model_view.py`, for the relay and `tero status --json`.
Texts (`light_text`, `doing`) are computed at `built_at`; next to them go
absolute times (`phase_started_at`, `gate_started_at`, `last_event_at`,
`verdict_at`), so a screen that is not rebuilt every second (the phone)
recomputes ages itself. Reads nothing, writes nothing, never raises on bad input.
"""

from __future__ import annotations

from datetime import datetime, timezone  # noqa: F401 (реэкспорт для тестов)
from typing import Any, Callable

from src.hud.app import debt_line, hud_done_text  # noqa: F401 (единые строки HUD)
from src.hud.model_parts import (  # noqa: F401 (реэкспорт)
    _age, _first_finding, _fmt_mss, _parse_ts, _verdict, _verdict_summary,
)

TAB_LIMIT = 2000  # log lines kept on the mac
LOG_TEXT_LIMIT = 300

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

NO_AGENT = {"host": "", "alive": False, "on_duty": False, "pid_started_at": None,
            "lease_until": None, "lease_written_at": None}

# every key `render()` in view.html reads; the relay and `tero status` carry the same set
VIEW_KEYS = (
    "light", "light_text", "sectors", "phase_title", "phase_index", "total_phases", "round",
    "phase_started_at", "role", "role_line", "doing", "gate_started_at", "last_event_at",
    "verdict", "verdict_at", "stop_text", "done_text", "debt_line", "agent", "messages_waiting",
    "resume", "log", "built_at", "quiet_after_s",
)


def build_view(
    state: dict | None,
    events: list[dict],
    now: float,
    alive: Callable[[dict], bool],
    agent: dict | None = None,
    *,
    log_limit: int = TAB_LIMIT,
    messages_waiting: int = 0,
    resume: dict | None = None,
) -> dict:
    """Snapshot + tail events -> screen fields. `None` state keeps the old frame.

    `agent` is `lease.agent_view(run_dir)`: whether the host is alive and on duty.
    `log_limit` caps the merged event log (the relay asks for 40).
    """
    items = [e for e in (events or []) if isinstance(e, dict)]
    agent = dict(NO_AGENT, **(agent or {}))
    base = {
        "agent": agent,
        "messages_waiting": int(messages_waiting or 0),
        "resume": resume if isinstance(resume, dict) else None,
        "log": _log(items, log_limit),
        "last_event_at": _last_ts(items),
        "built_at": float(now),
        "quiet_after_s": QUIET_AFTER_S,
    }
    if not isinstance(state, dict):
        return {
            **base,
            "sectors": [],
            "role_line": "",
            "doing": "нет данных",
            "verdict": {"verdict": None, "blocking": 0, "first": ""},
            "verdict_at": None,
            "debt_line": debt_line(0),
            "done_text": "",
            "stop_text": "",
            "light": "grey",
            "light_text": "нет данных",
            "phase_title": "",
            "phase_index": 0,
            "total_phases": None,
            "round": 0,
            "phase_started_at": None,
            "role": "",
            "gate_started_at": None,
        }
    status = state.get("status") or ""
    broken = _is_broken(state, status, alive)
    sectors = _sectors(state)
    debt_n = _debt_count(state)
    age = _age(items, now)
    light, light_text = _light(state, status, broken, age, debt_n, agent)
    role = str(state.get("role") or "")
    return {
        **base,
        "sectors": sectors,
        "role_line": _role_line(state),
        "doing": _doing(state, status, items, now, broken, agent),
        "verdict": _verdict(state, items),
        "verdict_at": _verdict_at(items),
        "debt_line": debt_line(debt_n),
        "done_text": hud_done_text(status, debt_n),
        "stop_text": stop_text(state),
        "light": light,
        "light_text": light_text,
        "phase_title": str(state.get("phase") or _current_title(state)),
        "phase_index": _int(state.get("phase_index")),
        "total_phases": _total(state),
        "round": _int(state.get("round")),
        "phase_started_at": _parse_ts(state.get("phase_started")),
        "role": role,
        "gate_started_at": _gate_started_at(items) if role == "gate" else None,
    }


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _total(state: dict) -> int | None:
    total: Any = state.get("total_phases")
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        phases = state.get("phases")
        return len(phases) if isinstance(phases, list) and phases else None
    return total


def stop_text(state: dict) -> str:
    if state.get("status") != "stopped":
        return ""
    return str(state.get("text") or state.get("reason") or "стоп")


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
        text = stop_text(state)
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


def _gate_bounds(events: list[dict]) -> tuple[int, int]:
    start_idx = end_idx = -1
    for i, ev in enumerate(events):
        if ev.get("type") == "gate_start":
            start_idx = i
        elif ev.get("type") == "gate":
            end_idx = i
    return start_idx, end_idx


def _gate_started_at(events: list[dict]) -> float | None:
    """Start of a gate that is still running (absolute): the screen ticks the timer itself."""
    start_idx, end_idx = _gate_bounds(events)
    if start_idx < 0 or start_idx < end_idx:
        return None
    return _parse_ts(events[start_idx].get("ts"))


def _gate_doing(events: list[dict], now: float) -> str | None:
    start_idx, end_idx = _gate_bounds(events)
    if start_idx < 0 and end_idx < 0:
        return None
    if start_idx > end_idx:
        started = _parse_ts(events[start_idx].get("ts"))
        elapsed = now - started if started is not None else 0
        return f"⏱ тесты идут {_fmt_mss(elapsed)}"
    text = events[end_idx].get("text")
    return str(text) if text else "тесты …"


def _verdict_at(events: list[dict]) -> float | None:
    for ev in reversed(events):
        if ev.get("type") == "verdict" and ev.get("verdict"):
            return _parse_ts(ev.get("ts"))
    return None


def _last_ts(events: list[dict]) -> float | None:
    for ev in reversed(events):
        ts = _parse_ts(ev.get("ts"))
        if ts is not None:
            return ts
    return None


def _log(events: list[dict], limit: int) -> list[dict]:
    """`{ts, role, type, text}` per event, newest last, texts cut to LOG_TEXT_LIMIT."""
    out = []
    for ev in events[-max(0, int(limit or 0)):] if limit else []:
        text = ev.get("text")
        text = str(text) if text is not None else str(ev.get("type") or "")
        out.append({
            "ts": ev.get("ts"),
            "role": str(ev.get("role") or ""),
            "type": str(ev.get("type") or ""),
            "text": text[:LOG_TEXT_LIMIT],
        })
    return out
