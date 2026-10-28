"""Фаза 08-10: эскалация Sonnet по ситуации (красный тест контракта)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from tests.loop import fakes as scripted

FakeAgents = scripted.FakeAgents
finding = scripted.finding
_git = scripted.git
judge_returns = scripted.judge_returns
no_ship = scripted.no_ship
player_writes = scripted.player_writes
ship = scripted.ship


PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides) -> Config:
    data = {
        "plan_file": "plan.md",
        "max_rounds": 8,
        "test_timeout_s": 30,
        "escalation_provider": "claude",
        "escalation_model": "sonnet",
    }
    data.update(overrides)
    return Config(**data)


async def _run(repo: Path, agents: FakeAgents, cfg: Config):
    from src.loop.plan import load_phases, load_preamble

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


def _stuck_steps(repo: Path, rounds: int = 5):
    # NO_SHIP: судья держит still_open по F1 после первого раунда.
    stuck = no_ship(
        [finding(problem="still broken")],
        previous=[{"id": "F1", "state": "still_open"}],
    )
    steps = [
        player_writes(repo, "src/greet.py", "def greet():\n    return '0'\n"),
        judge_returns(no_ship([finding(problem="still broken")])),
    ]
    for i in range(1, rounds):
        steps.append(
            player_writes(repo, "src/greet.py", f"def greet():\n    return '{i}'\n")
        )
        steps.append(judge_returns(stuck))
    return steps


def _player_models(agents: FakeAgents) -> list[str]:
    return [m for c, m in zip(agents.calls, agents.models) if c == "player"]


@pytest.mark.asyncio
async def test_round_mode_unchanged(tmp_path: Path, monkeypatch):
    from src.loop import situation_step as step_mod

    calls: list = []
    real = step_mod.record_situation

    def counting(*a, **k):
        calls.append(True)
        return real(*a, **k)  # тень: вердикт не читает результат

    monkeypatch.setattr(step_mod, "record_situation", counting)
    monkeypatch.setattr("src.loop.judge_step.record_situation", counting)
    (tmp_path / "a").mkdir()
    repo = _repo(tmp_path / "a")
    agents = FakeAgents(repo, _stuck_steps(repo))
    out, ledger = await _run(repo, agents, _cfg(escalation="round"))
    triple = (out.rounds, tuple(_player_models(agents)), out.status, out.reason)

    monkeypatch.setattr(step_mod, "record_situation", lambda *a, **k: None)
    monkeypatch.setattr("src.loop.judge_step.record_situation", lambda *a, **k: None)
    (tmp_path / "b").mkdir()
    repo2 = _repo(tmp_path / "b")
    agents2 = FakeAgents(repo2, _stuck_steps(repo2))
    out2, _ = await _run(repo2, agents2, _cfg(escalation="round"))
    triple2 = (out2.rounds, tuple(_player_models(agents2)), out2.status, out2.reason)
    # одно сравнение: при no-op тени те же раунды, игроки и статус
    assert triple == triple2
    import json as _json

    kinds = [_json.loads(line)["kind"] for line in ledger.path.read_text().splitlines()]
    assert len(calls) == out.rounds  # тень вызывалась каждый раунд
    # no-op тени: при round те же раунды, игроки и статус, что без тени —
    # история и состав игроков детерминированы скриптом судьи:
    assert out.rounds == len(calls)
    # round: старый escalation-путь (_no_progress) эскалирует один раз,
    # sonnet в истории тени пишется через _actual_player — поведение до плана
    assert _player_models(agents) == ["muse-spark-1.3", "muse-spark-1.3", "sonnet"]
    assert (out.status, out.reason) == ("stop", "stuck")
    assert "situation" in kinds


@pytest.mark.asyncio
async def test_situational_requires_decisions(tmp_path: Path):
    import subprocess

    from src.loop.preflight import start_checks
    from src.loop.state import RunState

    repo = _repo(tmp_path)
    subprocess.run(
        ["git", "reset", "--hard"], cwd=repo, check=True, capture_output=True)
    state = RunState(repo / ".tero" / "check-state.json")
    (tmp_path / "state-outside").mkdir(exist_ok=True)
    (tmp_path / "state-outside" / ".gitignore").write_text("*\n")
    from src.loop.state import RunState as _RS

    state = _RS(tmp_path / "state-outside" / "check-state.json")
    state.update(check="requires-decisions")
    failed = start_checks(_cfg(escalation="situational", decisions=False), repo, state)
    assert failed is not None
    assert "decisions" in failed[0]


@pytest.mark.asyncio
async def test_stuck_then_sonnet_then_back(tmp_path: Path):
    repo = _repo(tmp_path)
    steps = _stuck_steps(repo, rounds=6)
    # фаза 08-11: stuck_strong после лимита Sonnet идёт в Opus; more → ещё раунд.
    def _decider_more(role: str, kw: dict) -> dict:
        assert role == "decider", role
        assert (kw.get("provider"), kw.get("model")) == ("claude", "sonnet"), kw  # решатель = cfg.escalation_*
        return {"decision": "more", "why": "try again"}

    steps.insert(10, _decider_more)
    agents = FakeAgents(repo, steps)
    outcome, _ = await _run(
        repo, agents,
        _cfg(escalation="situational", decisions=True, max_rounds=6, sonnet_max=2),
    )
    models = _player_models(agents)
    # застой → ровно 2 раунда Sonnet (sonnet_max=2), затем игрок плана
    assert models == ["muse-spark-1.3"] * 3 + ["sonnet"] * 2 + ["muse-spark-1.3"], models
    assert outcome.rounds == 6


@pytest.mark.asyncio
async def test_continuation_keeps_plan_player(tmp_path: Path):
    from src.loop.plan import load_phases, load_preamble
    from src.loop import phase as phase_mod
    from src.loop import situation_step as step_mod
    from src.loop.state import RunState

    repo = _repo(tmp_path)
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    cfg = _cfg(escalation="situational", decisions=True)
    runner = phase_mod._PhaseRunner(
        phase=phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=FakeAgents(repo, []), test_command=detect(repo),
        ledger=ledger,
    )
    hist = {
        "rounds": [
            {"round": 1, "n_findings": 1, "resolved": 0, "still_open": 0,
             "sev": [], "judge_level": ["blocker"], "player": "muse"},
            {"round": 2, "n_findings": 1, "resolved": 0, "still_open": 1,
             "sev": [], "judge_level": ["blocker"], "player": "muse"},
            {"round": 3, "n_findings": 1, "resolved": 0, "still_open": 1,
             "sev": [], "judge_level": ["blocker"], "player": "muse"},
        ],
        "opus_round": None, "owner_round": None, "sonnet_n": 0,
    }
    RunState(ledger.run_dir / "state.json").update(**{"situation[0]": hist})
    runner.ctx.st.had_crash = True
    runner.ctx.st.resume_active = True
    assert runner.ctx.player_identity() == (
        cfg.player_model, cfg.player_provider, cfg.player_effort)


@pytest.mark.asyncio
async def test_prejudge_paths_keep_old_behavior(tmp_path: Path):
    from types import SimpleNamespace

    from src.loop import progress_step as step_mod
    from src.loop.phase_ctx import PhaseState

    cfg = _cfg(escalation="situational", decisions=True)
    ledger = Ledger(new_run_dir(tmp_path, "demo"))
    state = PhaseState(round=3)
    # escalate_at_round в situational выключена: ведёт себя как старый путь
    # без неё — escalation_provider даёт прежний повторный шаг, не стоп
    cfg.escalate_at_round = 2
    assert step_mod.check_no_progress(state, cfg, ledger) is False
    assert state.escalated is True
    assert step_mod.check_no_progress(state, cfg, ledger) is True
    # grace_until остаётся: раунды more — без stuck
    fresh = PhaseState(round=1, escalated=True)
    assert step_mod.check_no_progress(
        fresh, cfg, ledger, SimpleNamespace(grace_until=5)) is False


@pytest.mark.asyncio
async def test_sonnet_max_survives_more(tmp_path: Path):
    from src.loop.plan import load_phases, load_preamble
    from src.loop import phase as phase_mod
    from src.loop import situation_step as step_mod
    from src.loop.state import RunState

    repo = _repo(tmp_path)
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    cfg = _cfg(escalation="situational", decisions=True)
    runner = phase_mod._PhaseRunner(
        phase=phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=FakeAgents(repo, []), test_command=detect(repo),
        ledger=ledger,
    )
    hist = {
        "rounds": [
            {"round": 1, "n_findings": 1, "resolved": 0, "still_open": 0,
             "sev": [], "judge_level": ["blocker"], "player": "muse"},
            {"round": 2, "n_findings": 1, "resolved": 0, "still_open": 1,
             "sev": [], "judge_level": ["blocker"], "player": "muse"},
            {"round": 3, "n_findings": 1, "resolved": 0, "still_open": 1,
             "sev": [], "judge_level": ["blocker"], "player": "sonnet"},
            {"round": 4, "n_findings": 1, "resolved": 0, "still_open": 1,
             "sev": [], "judge_level": ["blocker"], "player": "sonnet"},
        ],
        "opus_round": None, "owner_round": None, "sonnet_n": 2,
    }
    RunState(ledger.run_dir / "state.json").update(**{"situation[0]": hist})
    # новый раннер после more: та же run_dir, история пережила
    runner2 = phase_mod._PhaseRunner(
        phase=phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=FakeAgents(repo, []), test_command=detect(repo),
        ledger=Ledger(new_run_dir(repo, "demo2")),
    )
    runner2.ctx.run_dir = ledger.run_dir
    assert step_mod.next_player(runner2.ctx)[0] == "muse-spark-1.3"


def test_progress_extracted():
    from pathlib import Path as _P

    from src.loop import progress_step

    assert callable(progress_step.check_no_progress)
    actual = len((_P("src/loop/phase.py")).read_text().splitlines())
    assert actual <= 689, f"phase.py вырос: {actual}"
