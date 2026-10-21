"""Run ledger events, artifacts, and history table."""

import json
from pathlib import Path

from src.loop.ledger import Ledger, history, new_run_dir


def test_new_run_dir_uses_timestamp_and_slug(tmp_path: Path):
    path = new_run_dir(tmp_path, "parse-empty")
    assert path.parent == tmp_path / ".tero" / "runs"
    assert path.name.endswith("-parse-empty")
    assert path.is_dir()


def test_event_appends_jsonl(tmp_path: Path):
    run = new_run_dir(tmp_path, "x")
    ledger = Ledger(run)
    ledger.event("run_start", plan="plan.md", slug="x")
    ledger.event("ship", phase=2, round=1, verdict="SHIP")
    lines = (run / "ledger.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "run_start"
    assert first["plan"] == "plan.md"
    assert "ts" in first


def test_artifact_writes_relative_path(tmp_path: Path):
    run = new_run_dir(tmp_path, "x")
    ledger = Ledger(run)
    written = ledger.artifact("phase-02/round-03/player.json", '{"status":"done"}')
    assert written.exists()
    assert written.read_text() == '{"status":"done"}'


def test_history_lists_recent_runs(tmp_path: Path):
    first = new_run_dir(tmp_path, "one")
    Ledger(first).event("run_start", plan="a.md")
    Ledger(first).event("run_end", reason="ship", phases_shipped=1, phases_total=1)
    second = new_run_dir(tmp_path, "two")
    Ledger(second).event("run_start", plan="b.md")
    Ledger(second).event("run_end", reason="stuck", phases_shipped=0, phases_total=2)
    rows = history(tmp_path, limit=10)
    assert [row["slug"] for row in rows] == ["two", "one"]
    assert rows[0]["reason"] == "stuck"
    assert rows[1]["reason"] == "ship"
