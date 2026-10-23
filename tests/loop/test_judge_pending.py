"""Judge failures retry on the same ref; three in a row → judge_pending."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import Config
from src.loop import snapshot as snapshot_module
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from src.loop.state import RunState
from tests.loop import fakes as scripted

FakeAgents = scripted.FakeAgents
judge_returns = scripted.judge_returns
player_writes = scripted.player_writes
ship = scripted.ship


PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""

HELLO = "def greet():\n    return 'hello'\n"

INVALID = {
    "__invalid__": True,
    "errors": ["'verdict' is a required property"],
    "raw": "looks good to me",
}

UNAVAILABLE = {"__unavailable__": True, "error": "quota exceeded"}


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


async def _run_phase(repo: Path, agents: FakeAgents, cfg: Config | None = None):
    from src.loop.plan import load_phases, load_preamble

    cfg = cfg or _cfg()
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    outcome = await run_phase(
        phases[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=cfg,
        agents=agents,
        test_command=detect(repo),
        ledger=ledger,
    )
    return outcome, ledger


def _ledger_rows(ledger: Ledger) -> list[dict]:
    return [json.loads(line) for line in ledger.path.read_text().splitlines()]


def _stream_rows(ledger: Ledger) -> list[dict]:
    return [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]


@pytest.mark.asyncio
async def test_garbage_judge_three_times_goes_judge_pending(tmp_path: Path):
    from src.loop.run import run as loop_run

    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(dict(INVALID)),
            judge_returns(dict(INVALID)),
            judge_returns(dict(INVALID)),
        ],
    )
    outcome = await loop_run(
        _cfg(plan_file="plan.md", working_dir=str(repo)), agents=agents
    )
    assert outcome.status == "judge_pending"
    assert outcome.exit_code == 3
    assert outcome.reason == "judge_failed"
    assert outcome.status != "stuck"
    assert agents.calls == ["player", "judge", "judge", "judge"]

    state = RunState.read(Path(outcome.run_dir) / "state.json")
    assert state is not None
    assert state.get("status") == "judge_pending"
    assert state.get("reason") == "judge_failed"
    assert state.get("round") == 1
    ref = state.get("snapshot_ref")
    base = state.get("base")
    assert ref, "judge_pending must keep the snapshot ref"
    assert base
    text = state.get("text") or ""
    assert ref in text
    assert "tero review" in text
    assert "tero resume" in text

    assert scripted.git(repo, "rev-parse", "--verify", ref).strip()
    assert snapshot_module.tree_of(repo, base) == snapshot_module.ref_tree(repo, ref)
    assert (repo / "src/greet.py").read_text() == HELLO

    ledger = Ledger(Path(outcome.run_dir))
    kinds = [row.get("kind") for row in _ledger_rows(ledger)]
    assert "escalate" not in kinds
    assert "rollback" not in kinds
    failed = [row for row in _ledger_rows(ledger) if row.get("kind") == "judge_failed"]
    assert [row.get("attempt") for row in failed] == [1, 2, 3]
    assert all("verdict" in (row.get("error") or "") for row in failed)

    stream_failed = [
        row for row in _stream_rows(ledger) if row.get("type") == "judge_failed"
    ]
    assert len(stream_failed) == 3
    for row in stream_failed:
        for key in ("phase", "round", "role", "text"):
            assert row.get(key), key


@pytest.mark.asyncio
async def test_judge_retry_does_not_rerun_player(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(dict(INVALID)),
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run_phase(repo, agents)
    assert outcome.status == "ship"
    assert outcome.rounds == 1
    assert agents.calls == ["player", "judge", "judge"]
    failed = [row for row in _ledger_rows(ledger) if row.get("kind") == "judge_failed"]
    assert len(failed) == 1
    assert failed[0].get("attempt") == 1


@pytest.mark.asyncio
async def test_judge_unavailable_three_times_no_rollback(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(dict(UNAVAILABLE)),
            judge_returns(dict(UNAVAILABLE)),
            judge_returns(dict(UNAVAILABLE)),
        ],
    )
    outcome, ledger = await _run_phase(repo, agents)
    assert outcome.status == "judge_pending"
    assert outcome.reason == "judge_failed"
    assert outcome.rounds == 1
    assert agents.calls == ["player", "judge", "judge", "judge"]
    # No rollback: the player's file is still on disk.
    assert (repo / "src/greet.py").read_text() == HELLO
    kinds = [row.get("kind") for row in _ledger_rows(ledger)]
    assert "rollback" not in kinds
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    base = state.get("base")
    assert ref and base
    assert scripted.git(repo, "rev-parse", "--verify", ref).strip()
    assert snapshot_module.tree_of(repo, base) == snapshot_module.ref_tree(repo, ref)
    failed = [row for row in _ledger_rows(ledger) if row.get("kind") == "judge_failed"]
    assert [row.get("attempt") for row in failed] == [1, 2, 3]
    assert all(row.get("error") == "quota exceeded" for row in failed)


@pytest.mark.asyncio
async def test_judge_failure_does_not_escalate_player(tmp_path: Path):
    repo = _repo(tmp_path)
    cfg = _cfg()
    assert cfg.escalation_provider, "test needs escalation enabled"
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(dict(INVALID)),
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run_phase(repo, agents, cfg)
    assert outcome.status == "ship"
    assert outcome.escalated is False
    kinds = [row.get("kind") for row in _ledger_rows(ledger)]
    assert "escalate" not in kinds
    assert cfg.escalation_model not in agents.models
