"""Две проекции одной модели (спек пульта 2, п. 6): relay_view и status_view."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from src.hud.model import LOG_TEXT_LIMIT, TAB_LIMIT, VIEW_KEYS
from src.hud.model_view import RELAY_LOG_LIMIT, relay_view, status_view
from src.hud.registry import current_pid_started

NOW = 1_700_000_000.0


def _ts(delta_s: float) -> str:
    return datetime.fromtimestamp(NOW - delta_s, tz=timezone.utc).isoformat()


def _run(tmp_path: Path, events: list[dict], **state) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    base = {"status": "running", "phase_index": 0, "total_phases": 2, "round": 1, "role": "gate",
            "model": "opus", "pid": os.getpid(), "pid_started": current_pid_started(), "phase_started": _ts(300),
            "phases": [{"title": "a", "done": False}, {"title": "b", "done": False}]}
    base.update(state)
    (run / "state.json").write_text(json.dumps(base), encoding="utf-8")
    (run / "stream.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return run


def _text(i: int, delta_s: float) -> dict:
    return {"ts": _ts(delta_s), "type": "text", "role": "player", "phase": 1, "round": 1,
            "text": f"строка {i} " + "слово " * 60}


def test_relay_view_texts_match_mac_even_when_log_is_cut(tmp_path):
    """gate_start и вердикт старше 40 событий: таймер и verdict_at на телефоне те же, что на маке."""
    events = [{"ts": _ts(500), "type": "verdict", "role": "judge", "verdict": "NO_SHIP", "blocking": 1},
              {"ts": _ts(400), "type": "gate_start", "role": "gate", "phase": 1, "round": 1}]
    events += [_text(i, 399 - i) for i in range(100)]
    run = _run(tmp_path, events)
    view = relay_view(run, NOW)
    assert view["gate_started_at"] == NOW - 400
    assert view["verdict_at"] == NOW - 500
    assert view["doing"].startswith("⏱ тесты идут 6:40")
    assert len(view["log"]) == RELAY_LOG_LIMIT == 40
    assert view["log"][-1]["text"].startswith("строка 99")
    assert all(len(row["text"]) <= LOG_TEXT_LIMIT for row in view["log"])
    assert set(view["log"][0]) == {"ts", "role", "type", "text"}


def test_relay_view_has_only_whitelisted_keys_and_no_pid(tmp_path):
    run = _run(tmp_path, [_text(0, 1)])
    view = relay_view(run, NOW)
    assert set(view) <= set(VIEW_KEYS)
    dumped = json.dumps(view, ensure_ascii=False)
    assert str(os.getpid()) not in dumped and '"pid"' not in dumped
    assert "pid" not in view["agent"] and "session" not in view["agent"]


def test_status_view_without_log_by_default(tmp_path):
    run = _run(tmp_path, [_text(0, 1)])
    assert "log" not in status_view(run, NOW)
    assert len(status_view(run, NOW, with_log=True)["log"]) == 1


def test_relay_view_is_cheap_on_a_full_tail(tmp_path):
    """Критерий спека: проекция для POST /state укладывается в 10 мс и на хвосте TAB_LIMIT."""
    run = _run(tmp_path, [_text(i, TAB_LIMIT - i) for i in range(TAB_LIMIT)])
    best = min(_timed(run) for _ in range(5))
    assert best < 0.010, f"relay_view за {best * 1000:.1f} мс"


def _timed(run: Path) -> float:
    t0 = time.perf_counter()
    relay_view(run, NOW)
    return time.perf_counter() - t0
