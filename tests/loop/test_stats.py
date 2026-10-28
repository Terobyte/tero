"""tero stats: aggregates ledger + round artifacts, survives broken files."""

from __future__ import annotations

import json
from pathlib import Path

from src.loop.stats import collect, render


def _run(root: Path, name: str, rows: list[dict], rounds: dict[str, tuple[dict, dict]]) -> None:
    run = root / ".tero" / "runs" / name
    run.mkdir(parents=True)
    (run / "ledger.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    for rel, (judge, player) in rounds.items():
        d = run / rel
        d.mkdir(parents=True)
        # judge.json is stored as a JSON string in real runs
        (d / "judge.json").write_text(json.dumps(json.dumps(judge)), encoding="utf-8")
        (d / "player.json").write_text(json.dumps(player), encoding="utf-8")


def test_collect_counts_verdicts_time_and_findings(tmp_path):
    rows = [
        {"ts": "2026-10-27T10:00:00+00:00", "kind": "phase_start"},
        {"ts": "2026-10-27T10:10:00+00:00", "kind": "player", "round": 1},
        {"ts": "2026-10-27T10:12:00+00:00", "kind": "gate", "ok": True},
        {"ts": "2026-10-27T10:17:00+00:00", "kind": "judge", "verdict": "NO_SHIP"},
        {"ts": "2026-10-27T10:17:01+00:00", "kind": "situation",
         "situation": "stuck", "action": "sonnet_player"},
        {"ts": "2026-10-27T10:30:00+00:00", "kind": "player", "round": 2},
        {"ts": "2026-10-27T10:35:00+00:00", "kind": "judge", "verdict": "SHIP"},
        {"ts": "2026-10-27T10:35:01+00:00", "kind": "ship"},
    ]
    rounds = {
        "phase-00/round-01": ({"findings": [
            {"level": "blocker", "route": "tester", "file": "a.py"}]}, {}),
        "phase-00/round-02": ({"previous": [{"id": "F1", "state": "resolved"}], "findings": [
            {"level": "minor", "route": "player", "file": "b.py"},
            {"level": "major", "route": "tester", "file": "c.py"}]},
            {"changes": [{"file": "b.py"}]}),
    }
    _run(tmp_path, "20261027-1-08-x", rows, rounds)
    _run(tmp_path, "20261027-2-07-other", rows[:1], {})

    acc = collect(tmp_path, plan="08-x")
    assert acc["runs"] == 1
    assert acc["verdict"] == {"NO_SHIP": 1, "SHIP": 1}
    assert acc["seconds"]["player"] == 600 + 13 * 60 - 1
    assert acc["count"]["judge"] == 2
    assert acc["level"] == {"blocker": 1, "minor": 1, "major": 1}
    assert acc["previous"]["resolved"] == 1
    assert (acc["late"], acc["late_in_changed"]) == (2, 1)
    assert acc["situation"] == {"stuck→sonnet_player": 1}

    text = render(acc, "08-x")
    assert "NO_SHIP 1" in text and "шип 1" in text and "(50%)" in text


def test_no_runs(tmp_path):
    assert render(collect(tmp_path), "все") == "Прогонов нет."
