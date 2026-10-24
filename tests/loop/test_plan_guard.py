"""План правит только tero: правки плана ролями откатываются до снимков."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.config import Config
from src.loop.run import run
from tests.loop import fakes as scripted

PLAN_TWO = """# Demo

## First

- [ ] first done

## Second

- [ ] second done
"""

PLAN_SECOND_LEFT = """# Demo

## First

- [x] first done

## Second

- [ ] second done
"""

PLAN_ONE = """# Demo

## Add greet

- [ ] greet() returns hello
"""


def _cfg_run(repo: Path, **overrides) -> Config:
    data = {
        "plan_file": "plan.md",
        "working_dir": str(repo),
        "max_rounds": 8,
        "test_timeout_s": 30,
    }
    data.update(overrides)
    return Config(**data)


def _ledger_events(run_dir: str | Path) -> list[dict]:
    ledger = Path(run_dir) / "ledger.jsonl"
    events = []
    for line in ledger.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _git_show(repo: Path, spec: str) -> bytes:
    return subprocess.run(
        ["git", "show", spec], cwd=repo, capture_output=True, check=True
    ).stdout


@pytest.mark.asyncio
async def test_player_ticking_future_phase_is_reverted(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN_TWO)

    def sabotage(role: str, kwargs: dict) -> dict:
        assert role == "player"
        scripted.write_file(repo, "src/first.py", "FIRST = 1\n")
        text = (repo / "plan.md").read_text().replace("- [ ]", "- [x]")
        (repo / "plan.md").write_text(text)
        return scripted.PLAYER_DONE

    def crash(role: str, kwargs: dict) -> dict:
        raise RuntimeError("player exploded")

    agents = scripted.FakeAgents(
        repo, [sabotage, scripted.judge_returns(scripted.ship()), crash, crash]
    )
    outcome = await run(_cfg_run(repo), agents=agents)
    assert outcome.reason == "provider_unavailable"
    assert outcome.phases_shipped == 1
    events = _ledger_events(outcome.run_dir)
    assert any(
        e["kind"] == "plan_restored" and e.get("role") == "player" for e in events
    )
    plan = (repo / "plan.md").read_text()
    assert "- [x] first done" in plan
    assert "- [ ] second done" in plan

    def second_player(role: str, kwargs: dict) -> dict:
        assert role == "player"
        scripted.write_file(repo, "src/second.py", "SECOND = 2\n")
        return scripted.PLAYER_DONE

    agents2 = scripted.FakeAgents(
        repo, [second_player, scripted.judge_returns(scripted.ship())]
    )
    outcome2 = await run(_cfg_run(repo), agents=agents2)
    assert outcome2.reason == "ship"
    assert outcome2.phases_shipped == 1
    assert outcome2.phases_total == 2


@pytest.mark.asyncio
async def test_player_inserting_lines_above_phase(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN_SECOND_LEFT)

    def insert_above(role: str, kwargs: dict) -> dict:
        assert role == "player"
        scripted.write_file(repo, "src/second.py", "SECOND = 2\n")
        plan = (repo / "plan.md").read_text()
        (repo / "plan.md").write_text("# inserted by player\n\n" + plan)
        return scripted.PLAYER_DONE

    agents = scripted.FakeAgents(
        repo, [insert_above, scripted.judge_returns(scripted.ship())]
    )
    outcome = await run(_cfg_run(repo), agents=agents)
    assert outcome.reason == "ship"
    plan = (repo / "plan.md").read_text()
    assert "# inserted by player" not in plan
    assert "- [x] first done" in plan
    assert "- [x] second done" in plan


@pytest.mark.asyncio
async def test_player_crash_snapshot_has_clean_plan(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN_ONE)
    original = (repo / "plan.md").read_bytes()

    def crash(role: str, kwargs: dict) -> dict:
        assert role == "player"
        (repo / "plan.md").write_text("# саботаж\n" + original.decode())
        raise RuntimeError("player exploded")

    agents = scripted.FakeAgents(repo, [crash, crash])
    outcome = await run(_cfg_run(repo), agents=agents)
    assert outcome.reason == "provider_unavailable"
    state = json.loads((Path(outcome.run_dir) / "state.json").read_text())
    ref = state.get("snapshot_ref") or ""
    assert ref
    assert _git_show(repo, f"{ref}:plan.md") == original


@pytest.mark.asyncio
async def test_tester_editing_plan_is_reverted(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN_ONE)

    def tester_sabotage(role: str, kwargs: dict) -> dict:
        assert role == "tester"
        plan = (repo / "plan.md").read_text()
        (repo / "plan.md").write_text(plan + "\n# саботаж тестера\n")
        scripted.write_file(
            repo,
            "tests/test_red_sabotage.py",
            "from src.greet import greet\n\n\ndef test_red():\n    assert greet() == 'hello'\n",
        )
        return {
            "tests": [
                {"finding_id": "F1", "path": "tests/test_red_sabotage.py", "name": "red"}
            ],
            "skipped": [],
        }

    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(repo, "src/broken.py", "BROKEN = 1\n"),
            scripted.judge_returns(
                scripted.no_ship([scripted.finding(route="tester", id="F1")])
            ),
            tester_sabotage,
            scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome = await run(_cfg_run(repo), agents=agents)
    assert outcome.reason == "ship"
    events = _ledger_events(outcome.run_dir)
    assert any(
        e["kind"] == "plan_restored" and e.get("role") == "tester" for e in events
    )
    plan = (repo / "plan.md").read_text()
    assert "# саботаж тестера" not in plan
    assert "- [x] greet() returns hello" in plan


@pytest.mark.asyncio
async def test_plan_outside_repo_untouched(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    scripted.init_repo(repo, "# пусто\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    plan_path = outside / "plan.md"
    plan_path.write_text(PLAN_ONE)

    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome = await run(_cfg_run(repo, plan_file=str(plan_path)), agents=agents)
    assert outcome.reason == "ship"
    events = _ledger_events(outcome.run_dir)
    assert not any(e["kind"] == "plan_restored" for e in events)
