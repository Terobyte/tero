"""--escalate-at: эскалация ровно на одном раунде, без вечного переключения."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config, apply_cli_args
from tests.loop import fakes as scripted
from tests.loop.test_phase import _repo, _run

FakeAgents = scripted.FakeAgents
finding = scripted.finding
judge_returns = scripted.judge_returns
no_ship = scripted.no_ship
player_writes = scripted.player_writes


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 5, "test_timeout_s": 30,
            "escalation_provider": "claude", "escalation_model": "sonnet",
            "escalate_at_round": 5}
    data.update(overrides)
    return Config(**data)


def _player_models(agents: FakeAgents) -> list[str]:
    return [m for c, m in zip(agents.calls, agents.models) if c == "player"]


def _rounds(repo: Path, n: int, stuck: bool = False) -> list:
    steps = []
    for i in range(n):
        steps.append(player_writes(repo, "src/greet.py", f"def greet():\n    return {i}\n"))
        prev = [{"id": "F1", "state": "still_open"}] if stuck and i else []
        steps.append(judge_returns(no_ship([finding(problem=f"broken {i}")], previous=prev)))
    return steps


@pytest.mark.asyncio
async def test_escalation_only_on_round_five_then_fuse(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(repo, _rounds(repo, 5))
    outcome, _ledger = await _run(repo, agents, _cfg())
    models = _player_models(agents)
    assert len(models) == 5
    assert "sonnet" not in models[:4]
    assert models[4] == "sonnet"
    assert outcome.status == "stop"
    assert outcome.reason in ("fuse", "stuck")
    assert "rollback" in _ledger.path.read_text()


@pytest.mark.asyncio
async def test_no_progress_before_escalation_round_keeps_going(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(repo, _rounds(repo, 5, stuck=True))
    outcome, _ledger = await _run(repo, agents, _cfg())
    models = _player_models(agents)
    assert len(models) == 5
    assert models.count("sonnet") == 1 and models[4] == "sonnet"
    assert outcome.status == "stop"


def test_cli_escalate_at_sets_round():
    class Args:
        escalate_at = 5
    cfg = apply_cli_args(Config(), Args())
    assert cfg.escalate_at_round == 5
