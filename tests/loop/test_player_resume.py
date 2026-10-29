"""Player crash resume: second player gets the first player's diff."""

from __future__ import annotations

import json
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
write_file = scripted.write_file


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
        "escalation_provider": "",
        "escalation_model": "",
    }
    data.update(overrides)
    return Config(**data)


async def _run(repo: Path, agents, cfg: Config | None = None):
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


def _ref_name(run_id: str, phase_index: int, round: int) -> str:
    return f"refs/tero/{run_id}/phase-{phase_index:02d}-round{round:02d}"


@pytest.mark.asyncio
async def test_second_player_gets_first_diff(tmp_path: Path):
    repo = _repo(tmp_path)
    prompts: list[str] = []

    def crashing_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        write_file(repo, "src/greet.py", "def greet():\n    return 'half'\n")
        raise RuntimeError("provider down")

    def second_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return dict(scripted.PLAYER_DONE)

    agents = FakeAgents(repo, [crashing_player, second_player, judge_returns(ship())])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    assert len(prompts) == 2
    assert "Продолжай, не переписывай готовое" in prompts[1]
    assert "return 'half'" in prompts[1]
    assert "Продолжай, не переписывай готовое" not in prompts[0]
    run_id = ledger.run_dir.name
    ref = _ref_name(run_id, 0, 1)
    assert prompts[1].count(f"git diff") >= 1
    assert "half" in _git(repo, "cat-file", "-p", f"{ref}:src/greet.py")


@pytest.mark.asyncio
async def test_no_gate_on_crash_snapshot(tmp_path: Path, monkeypatch):
    import src.loop.phase as phase_module

    repo = _repo(tmp_path)
    gate_calls: list = []
    real_run_all = phase_module.run_all

    def counting_run_all(command, repo, timeout_s=900):
        gate_calls.append(repo)
        return real_run_all(command, repo=repo, timeout_s=timeout_s)

    monkeypatch.setattr(phase_module, "run_all", counting_run_all)

    def crashing_player(_role, _kwargs):
        write_file(repo, "src/greet.py", "def greet():\n    return 'half'\n")
        return {"__unavailable__": True, "error": "down"}

    agents = FakeAgents(
        repo,
        [
            crashing_player,
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert agents.calls == ["player", "player", "judge"]
    # Gate runs only in round 2 (after the second player), never on the
    # raw crash snapshot of round 1.
    assert len(gate_calls) == 1


@pytest.mark.asyncio
async def test_two_player_crashes_halt(tmp_path: Path):
    from src.loop.state import RunState

    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            lambda _r, _k: {"__unavailable__": True, "error": "down"},
            lambda _r, _k: {"__unavailable__": True, "error": "down"},
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "stop"
    assert outcome.reason == "provider_unavailable"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    assert ref
    assert _git(repo, "rev-parse", "--verify", ref).strip()
    ledger_rows = [
        json.loads(line) for line in ledger.path.read_text().splitlines()
    ]
    crashes = [row for row in ledger_rows if row.get("kind") == "provider_error"]
    assert len(crashes) == 2
    assert [row["round"] for row in crashes] == [1, 2]


@pytest.mark.asyncio
async def test_resume_block_survives_no_ship(tmp_path: Path):
    repo = _repo(tmp_path)
    prompts: list[str] = []

    def crashing_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        write_file(repo, "src/greet.py", "def greet():\n    return 'half'\n")
        return {"__unavailable__": True, "error": "down"}

    def ok_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        write_file(repo, "src/greet.py", "def greet():\n    return 'half'\n")
        return dict(scripted.PLAYER_DONE)

    agents = FakeAgents(
        repo,
        [
            crashing_player,
            ok_player,
            judge_returns(no_ship([finding(problem="still bad")])),
            ok_player,
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert len(prompts) == 3
    assert "Продолжай, не переписывай готовое" not in prompts[0]
    assert "Продолжай, не переписывай готовое" in prompts[1]
    assert "Продолжай, не переписывай готовое" in prompts[2]
    assert "return 'half'" in prompts[2]


@pytest.mark.asyncio
async def test_crash_diff_truncated_at_20000(tmp_path: Path):
    repo = _repo(tmp_path)
    prompts: list[str] = []
    big = "X" * 25_000

    def crashing_player(_role, _kwargs):
        write_file(repo, "src/big.py", big + "\n")
        return {"__unavailable__": True, "error": "down"}

    def second_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        return dict(scripted.PLAYER_DONE)

    agents = FakeAgents(
        repo,
        [
            crashing_player,
            second_player,
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert len(prompts) == 1
    assert "обрезано, полный дифф" in prompts[0]


def _crash_step(repo: Path, prompts: list[str], content: str):
    def step(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        write_file(repo, "src/greet.py", content)
        return {"__unavailable__": True, "error": "down"}

    return step


def _player_step(repo: Path, prompts: list[str], prewrite: list, content: str):
    def step(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        target = repo / "src/greet.py"
        prewrite.append(target.read_text() if target.exists() else None)
        write_file(repo, "src/greet.py", content)
        return dict(scripted.PLAYER_DONE)

    return step


@pytest.mark.asyncio
async def test_kmax_two_flat_rounds_clean_retry(tmp_path: Path):
    repo = _repo(tmp_path)
    prompts: list[str] = []
    prewrite: list = []
    agents = FakeAgents(
        repo,
        [
            _crash_step(repo, prompts, "v1-crash\n"),
            _player_step(repo, prompts, prewrite, "v2\n"),
            judge_returns(
                no_ship([finding(file="src/greet.py", line=7, problem="round2 bad")])
            ),
            _player_step(repo, prompts, prewrite, "v3\n"),
            judge_returns(
                no_ship(
                    [finding(file="src/greet.py", line=7, problem="round3 bad still")]
                )
            ),
            _player_step(repo, prompts, prewrite, "v4\n"),
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    assert outcome.reason != "stuck"
    assert len(prompts) == 4
    assert "Продолжай, не переписывай готовое" not in prompts[0]
    assert "Продолжай, не переписывай готовое" in prompts[1]
    assert "Продолжай, не переписывай готовое" in prompts[2]
    assert "Продолжай, не переписывай готовое" not in prompts[3]
    assert prewrite == ["v1-crash\n", "v2\n", None]
    # Открытые замечания судьи остаются: следующий промпт их содержит.
    assert "round3 bad still" in prompts[3]
    run_id = ledger.run_dir.name
    for rnd in (2, 3):
        ref = _ref_name(run_id, 0, rnd)
        assert _git(repo, "rev-parse", "--verify", ref).strip()
    assert "v3" in _git(
        repo, "cat-file", "-p", f"{_ref_name(run_id, 0, 3)}:src/greet.py"
    )


@pytest.mark.asyncio
async def test_kmax_progress_resets(tmp_path: Path):
    repo = _repo(tmp_path)
    prompts: list[str] = []
    prewrite: list = []
    agents = FakeAgents(
        repo,
        [
            _crash_step(repo, prompts, "v1-crash\n"),
            _player_step(repo, prompts, prewrite, "v2\n"),
            judge_returns(
                no_ship(
                    [
                        finding(file="src/greet.py", line=7, problem="bad A"),
                        finding(file="src/greet.py", line=9, problem="bad B"),
                    ]
                )
            ),
            _player_step(repo, prompts, prewrite, "v3\n"),
            judge_returns(
                no_ship([finding(file="src/greet.py", line=7, problem="bad A still")])
            ),
            _player_step(repo, prompts, prewrite, "v4\n"),
            judge_returns(
                no_ship([finding(file="src/greet.py", line=7, problem="bad A again")])
            ),
            _player_step(repo, prompts, prewrite, "v5\n"),
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert len(prompts) == 5
    # Отката не было: дерево пережило все раунды, блок продолжения на месте.
    assert prewrite == ["v1-crash\n", "v2\n", "v3\n", "v4\n"]
    for prompt in prompts[1:]:
        assert "Продолжай, не переписывай готовое" in prompt


@pytest.mark.asyncio
async def test_kmax_rephrased_findings_count_as_flat(tmp_path: Path):
    repo = _repo(tmp_path)
    prompts: list[str] = []
    prewrite: list = []
    agents = FakeAgents(
        repo,
        [
            _crash_step(repo, prompts, "v1-crash\n"),
            _player_step(repo, prompts, prewrite, "v2\n"),
            judge_returns(
                no_ship(
                    [
                        finding(
                            file="src/greet.py",
                            line=7,
                            problem="greet returns wrong string",
                        )
                    ]
                )
            ),
            _player_step(repo, prompts, prewrite, "v3\n"),
            judge_returns(
                no_ship(
                    [
                        finding(
                            file="src/greet.py",
                            line=7,
                            problem="completely different wording, same place",
                        )
                    ]
                )
            ),
            _player_step(repo, prompts, prewrite, "v4\n"),
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert len(prompts) == 4
    # Тот же (file, line) другим текстом — «не уменьшились»: чистая попытка.
    assert "Продолжай, не переписывай готовое" not in prompts[3]
    assert prewrite == ["v1-crash\n", "v2\n", None]
    assert "completely different wording" in prompts[3]


@pytest.mark.asyncio
async def test_kmax_resolved_previous_is_progress(tmp_path: Path):
    # dream 1.2b: после падения в раунде 1 каждый раунд закрывал прошлый
    # блокер и находил один новый в другом месте; K_max считал это «не
    # уменьшились» и чистой попыткой стёр пять раундов работы.
    repo = _repo(tmp_path)
    prompts: list[str] = []
    prewrite: list = []
    agents = FakeAgents(
        repo,
        [
            _crash_step(repo, prompts, "v1-crash\n"),
            _player_step(repo, prompts, prewrite, "v2\n"),
            judge_returns(
                no_ship([finding(file="src/greet.py", line=7, problem="bad A")])
            ),
            _player_step(repo, prompts, prewrite, "v3\n"),
            judge_returns(
                no_ship(
                    [finding(file="src/greet.py", line=9, problem="bad B")],
                    previous=[{"id": "F1", "state": "resolved"}],
                )
            ),
            _player_step(repo, prompts, prewrite, "v4\n"),
            judge_returns(
                no_ship(
                    [finding(file="src/greet.py", line=11, problem="bad C")],
                    previous=[{"id": "F2", "state": "resolved"}],
                )
            ),
            _player_step(repo, prompts, prewrite, "v5\n"),
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    assert len(prompts) == 5
    # Отката не было: каждый раунд начинал с дерева прошлого.
    assert prewrite == ["v1-crash\n", "v2\n", "v3\n", "v4\n"]
    rows = [json.loads(line) for line in (ledger.run_dir / "ledger.jsonl").read_text().splitlines()]
    assert not [r for r in rows if r.get("kind") == "clean_retry"]
