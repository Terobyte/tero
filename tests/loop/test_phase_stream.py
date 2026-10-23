"""Phase stream order + state verdict from validated judge JSON (T2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from src.loop.state import RunState
from tests.loop import fakes as scripted


PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


def _player_stub(repo: Path):
    from src.providers.message_adapter import (
        AdaptedMessage,
        TextBlock,
        ToolUseBlock,
    )

    class _P:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
            yield AdaptedMessage(
                role="assistant",
                content=[ToolUseBlock(name="Read", input={"file_path": "src/greet.py"})],
            )
            yield AdaptedMessage(
                role="assistant", content=[TextBlock(text="делаю контракт")]
            )
            yield AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=json.dumps(scripted.PLAYER_DONE))],
            )

    return _P()


def _judge_stub(payload: dict, prose: str = "смотрю диф"):
    from src.providers.message_adapter import AdaptedMessage, TextBlock

    class _J:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield AdaptedMessage(role="assistant", content=[TextBlock(text=prose)])
            yield AdaptedMessage(
                role="assistant", content=[TextBlock(text=json.dumps(payload))]
            )

    return _J()


def _live(factory_map):
    from src.loop.agents import LiveAgents

    def factory(name: str):
        return factory_map[name]

    # working_dir unused by stubs; tmp path not needed
    return LiveAgents(".", factory=factory, retry_wait_s=0)


async def _run_with_live(repo: Path, agents, cfg: Config | None = None):
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
        total_phases=len(phases),
    )
    return outcome, ledger


def _stream_types(ledger: Ledger) -> list[str]:
    path = ledger.run_dir / "stream.jsonl"
    assert path.is_file(), "stream.jsonl не записан"
    return [json.loads(line)["type"] for line in path.read_text().splitlines()]


@pytest.mark.asyncio
async def test_phase_stream_has_ordered_events(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = _live(
        {
            "muse": _player_stub(repo),
            "codex": _judge_stub(scripted.ship()),
            "gemini": _player_stub(repo),
            "claude": _player_stub(repo),
        }
    )
    outcome, ledger = await _run_with_live(repo, agents)
    assert outcome.status == "ship"
    types = _stream_types(ledger)
    required = ["start_step", "tool", "gate_start", "gate", "verdict", "ship"]
    pos = -1
    for want in required:
        try:
            nxt = types.index(want, pos + 1)
        except ValueError:
            pytest.fail(f"в stream.jsonl нет {want} после позиции {pos}: {types}")
        pos = nxt


@pytest.mark.asyncio
async def test_state_verdict_comes_from_validated_json_not_prose(tmp_path: Path):
    repo = _repo(tmp_path)
    # проза говорит NO_SHIP, валидный JSON — SHIP; в state должен лечь SHIP
    agents = _live(
        {
            "muse": _player_stub(repo),
            "codex": _judge_stub(
                scripted.ship(), prose="NO_SHIP: кажется всё плохо, blocking"
            ),
            "gemini": _player_stub(repo),
            "claude": _player_stub(repo),
        }
    )
    outcome, ledger = await _run_with_live(repo, agents)
    assert outcome.status == "ship"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None, "state.json не записан"
    verdict = state.get("verdict")
    assert isinstance(verdict, dict), f"verdict не dict: {verdict!r}"
    assert verdict.get("verdict") == "SHIP", f"вердикт взят не из JSON: {verdict!r}"


TWO_PHASE_PLAN = """# Demo

Preamble.

## First

- [ ] first done

## Second

- [ ] second done
"""


@pytest.mark.asyncio
async def test_new_phase_resets_done_and_commit(tmp_path: Path):
    """F10: вход во вторую фазу не наследует phase_done/commit первой."""
    from src.loop.plan import load_phases, load_preamble

    repo = scripted.init_repo(tmp_path, TWO_PHASE_PLAN)
    cfg = _cfg()
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    preamble = load_preamble(repo / "plan.md")

    async def _ship_phase(phase, agents):
        return await run_phase(
            phase,
            repo=repo,
            plan_path=repo / "plan.md",
            preamble=preamble,
            cfg=cfg,
            agents=agents,
            test_command=detect(repo),
            ledger=ledger,
            total_phases=len(phases),
        )

    first = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            ),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome = await _ship_phase(phases[0], first)
    assert outcome.status == "ship"
    shipped = RunState.read(ledger.run_dir / "state.json")
    assert shipped is not None
    assert shipped.get("phase_done") is True
    assert shipped.get("commit"), "первая фаза должна записать commit"

    seen: dict = {}

    def player_second(role, kwargs):
        state = RunState.read(ledger.run_dir / "state.json")
        assert state is not None
        seen["phase_done"] = state.get("phase_done")
        seen["commit"] = state.get("commit")
        scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return scripted.PLAYER_DONE

    second = scripted.FakeAgents(
        repo, [player_second, scripted.judge_returns(scripted.ship())]
    )
    outcome2 = await _ship_phase(phases[1], second)
    assert outcome2.status == "ship"
    assert seen["phase_done"] in (False, None), (
        f"фаза 2 унаследовала phase_done: {seen!r}"
    )
    assert not seen["commit"], f"фаза 2 унаследовала commit: {seen!r}"
