"""HUD screen model: pure function run-files -> what to draw (phase 7).

Red test on the contract: `src/hud/model.py` does not exist yet.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.hud.model import TAB_LIMIT, build_view

NOW = 1_700_000_000.0


def _ts(delta_s: float = 0) -> str:
    return datetime.fromtimestamp(NOW - delta_s, tz=timezone.utc).isoformat()


def _state(**kw) -> dict:
    base = {
        "status": "running",
        "phase_index": 1,
        "total_phases": 4,
        "round": 3,
        "role": "player",
        "provider": "codex",
        "model": "opus",
        "phase_started": _ts(125),
        "phases": [
            {"title": "a", "done": True},
            {"title": "b", "done": False},
            {"title": "c", "done": False},
            {"title": "d", "done": False},
        ],
        "debt": [{"phase": 4, "reason": "x"}],
        "pid": 123,
        "pid_started": 1.0,
    }
    base.update(kw)
    return base


def _ev(type_: str, role: str = "player", text: str = "", delta_s: float = 0, **kw) -> dict:
    e = {
        "ts": _ts(delta_s),
        "type": type_,
        "role": role,
        "phase": 2,
        "round": 3,
        "text": text,
    }
    e.update(kw)
    return e


ALIVE = lambda s: True  # noqa: E731
DEAD = lambda s: False  # noqa: E731


def test_sectors_colors_by_phase_status():
    view = build_view(_state(), [], NOW, ALIVE)
    # future phases are grey: red is reserved for a broken or stopped run
    assert [s["color"] for s in view["sectors"]] == ["green", "yellow", "grey", "orange"]
    assert [s["title"] for s in view["sectors"]] == ["a", "b", "c", "d"]


def test_icon_per_role_and_status():
    cases = [
        ({"role": "player", "status": "running"}, "🛠"),
        ({"role": "judge", "status": "running"}, "⚖️"),
        ({"role": "tester", "status": "running"}, "🧪"),
        ({"role": "gate", "status": "running"}, "⏱"),
        ({"status": "stopped", "reason": "stuck"}, "❓"),
        ({"status": "judge_pending"}, "⏸"),
        ({"status": "done"}, "✓"),
        ({"status": "waiting_human", "question": {"id": "q1", "text": "идём дальше?"}, "reason": ""}, "❓"),
    ]
    for kw, icon in cases:
        assert build_view(_state(**kw), [], NOW, ALIVE)["icon"] == icon, kw


def test_doing_prefers_role_text_then_tool_then_gate_timer():
    view = build_view(
        _state(),
        [_ev("tool", text="Read src/x.py", delta_s=30), _ev("text", text="пишу код", delta_s=10)],
        NOW,
        ALIVE,
    )
    assert view["doing"] == "пишу код"
    assert view["header"] == "Фаза 2/4 · раунд 3 · 02:05"
    assert view["role_line"] == "player · opus"

    view = build_view(_state(), [_ev("tool", text="Read src/x.py", delta_s=30)], NOW, ALIVE)
    assert "Read src/x.py" in view["doing"]

    view = build_view(
        _state(role="gate"),
        [{"ts": _ts(161), "type": "gate_start", "role": "gate", "phase": 2, "round": 3}],
        NOW,
        ALIVE,
    )
    assert view["doing"] == "⏱ тесты идут 2:41"


def test_shipped_last_phase_is_green_not_yellow():
    st = _state(
        status="done",
        phase_index=3,
        phase_done=True,
        debt=[],
        phases=[
            {"title": "a", "done": True},
            {"title": "b", "done": True},
            {"title": "c", "done": True},
            {"title": "d", "done": False},
        ],
    )
    assert [s["color"] for s in build_view(st, [], NOW, ALIVE)["sectors"]] == [
        "green",
        "green",
        "green",
        "green",
    ]


def test_doing_prefers_newest_tool_of_any_role():
    events = [
        _ev("tool", role="player", text="old player tool", delta_s=30),
        _ev("tool", role="judge", text="new judge tool", delta_s=5),
    ]
    assert build_view(_state(), events, NOW, ALIVE)["doing"] == "new judge tool"


def test_dead_owner_shows_broken():
    view = build_view(_state(), [_ev("text", text="пишу", delta_s=5)], NOW, DEAD)
    assert view["icon"] == "❓"
    assert view["doing"] == "прогон оборвался"

    view = build_view(_state(), [_ev("text", text="пишу", delta_s=1200)], NOW, ALIVE)
    assert view["doing"] != "прогон оборвался"
    assert view["last_event_age_s"] == 1200


def test_verdict_first_renders_line_zero():
    st = _state(
        verdict={"verdict": "NO_SHIP", "blocking": 1, "first": {"file": "src/x.py", "line": 0}}
    )
    view = build_view(st, [], NOW, ALIVE)
    assert view["verdict"]["first"] == "src/x.py:0"


def test_verdict_prefers_newer_stream_event_over_stale_saved():
    # state.json write failed after the judge: saved verdict is stale, even if a later
    # unrelated write bumped updated_at; the stream event carries the full summary.
    st = _state(
        verdict={"verdict": "SHIP", "blocking": 0, "first": {}},
        round=2,
        updated_at=_ts(1),
    )
    events = [
        _ev(
            "verdict",
            role="judge",
            delta_s=50,
            verdict="NO_SHIP",
            blocking=2,
            first={"file": "src/y.py", "line": 7, "issue": "z"},
        )
    ]
    view = build_view(st, events, NOW, ALIVE)
    assert view["verdict"] == {"verdict": "NO_SHIP", "blocking": 2, "first": "src/y.py:7"}

    st_saved = _state(
        verdict={"verdict": "NO_SHIP", "blocking": 1, "first": {"file": "src/x.py", "line": 3}}
    )
    view = build_view(st_saved, [], NOW, ALIVE)
    assert view["verdict"] == {"verdict": "NO_SHIP", "blocking": 1, "first": "src/x.py:3"}


def test_none_state_is_grey_not_crash_and_tabs_capped():
    view = build_view(None, [], NOW, ALIVE)
    assert view["sectors"] == []
    assert view["circle"] == "grey"
    assert view["doing"] == "нет данных"

    events = [_ev("text", text=f"m{i}", delta_s=5000 - i) for i in range(5000)]
    view = build_view(_state(), events, NOW, ALIVE)
    assert len(view["tabs"]["player"]) == TAB_LIMIT
    assert len(view["tabs"]["all"]) == TAB_LIMIT
    assert TAB_LIMIT == 2000


def test_light_is_run_health_not_phase_progress():
    cases = [
        (_state(), [_ev("text", text="x", delta_s=5)], ALIVE, "green", "работает"),
        (_state(), [_ev("text", text="x", delta_s=5)], DEAD, "red", "оборвался"),
        (_state(status="stopped", reason="stuck"), [], ALIVE, "red", "стоп"),
        (_state(status="waiting_human", question={"id": "q", "text": "?"}), [], ALIVE, "yellow", "ждёт тебя"),
        (_state(status="judge_pending"), [], ALIVE, "yellow", "ждёт тебя"),
        (_state(status="waiting_limit"), [], ALIVE, "yellow", "ждёт лимит"),
        (_state(status="done", debt=[]), [], ALIVE, "green", "сдано"),
        (_state(status="done"), [], ALIVE, "yellow", "сдано с долгом 1"),
    ]
    for st, evs, alive, light, text in cases:
        view = build_view(st, evs, NOW, alive)
        assert (view["light"], view["light_text"]) == (light, text), st.get("status")


def test_light_turns_yellow_when_run_goes_quiet():
    view = build_view(_state(), [_ev("text", text="x", delta_s=900)], NOW, ALIVE)
    assert view["light"] == "yellow"
    assert view["light_text"] == "тихо 15 мин"


def test_light_grey_without_state():
    view = build_view(None, [], NOW, ALIVE)
    assert (view["light"], view["light_text"]) == ("grey", "нет данных")


def test_phase_label_and_current_title():
    view = build_view(_state(), [], NOW, ALIVE)
    assert view["phase_label"] == "2/4"
    assert view["phase_title"] == "b"


def test_role_line_drops_provider_prefix_of_model():
    view = build_view(_state(model="meta-model-api/muse-spark-1.3-contributor"), [], NOW, ALIVE)
    assert view["role_line"] == "player · muse-spark-1.3-contributor"


def test_view_exposes_acting_role():
    assert build_view(_state(role="judge"), [], NOW, ALIVE)["role"] == "judge"
    assert build_view(None, [], NOW, ALIVE)["role"] == ""


def test_html_colors_log_lines_by_role():
    from pathlib import Path

    html = Path("src/hud/view.html").read_text(encoding="utf-8")
    for role in ("player", "judge", "tester", "gate"):
        assert f".r-{role}" in html
    assert "text-shadow" in html
