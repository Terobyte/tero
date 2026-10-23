"""Phase halt rolls back only after a verified work snapshot (step 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from src.loop.snapshot import ref_name
from src.loop.state import RunState
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


async def _run(repo: Path, agents: FakeAgents, cfg: Config | None = None):
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


def _fuse_steps(repo: Path, rounds: int) -> list:
    steps = []
    for i in range(rounds):
        steps.append(
            player_writes(repo, "src/greet.py", f"def greet():\n    return '{i}'\n")
        )
        previous = [{"id": f"F{i}", "state": "resolved"}] if i else []
        steps.append(
            judge_returns(no_ship([finding(problem=f"new {i}")], previous=previous))
        )
    return steps


@pytest.mark.asyncio
async def test_halt_takes_snapshot_before_rollback(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(repo, _fuse_steps(repo, 2))
    outcome, ledger = await _run(repo, agents, _cfg(max_rounds=2))
    assert outcome.status == "stop"
    assert outcome.reason == "fuse"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    assert ref == ref_name(ledger.run_dir.name, 0, state.get("round"))
    assert _git(repo, "rev-parse", "--verify", ref).strip()
    assert "return '1'" in _git(repo, "show", f"{ref}:src/greet.py")
    assert not (repo / "src/greet.py").exists()
    assert ref in (outcome.stop_text or "")


@pytest.mark.asyncio
async def test_no_rollback_without_snapshot(tmp_path: Path, monkeypatch):
    import src.loop.phase as phase_module
    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)

    def boom(repo_path, base, ref, deny=None):
        raise RuntimeError("snapshot exploded")

    monkeypatch.setattr(snapshot_module, "take_snapshot", boom)
    rollback_calls: list = []
    monkeypatch.setattr(
        phase_module, "rollback", lambda r, b: rollback_calls.append((str(r), b))
    )

    agents = FakeAgents(repo, _fuse_steps(repo, 1))
    outcome, ledger = await _run(repo, agents, _cfg(max_rounds=1))
    assert outcome.status == "stop"
    assert outcome.reason == "fuse"
    assert rollback_calls == []
    assert (repo / "src/greet.py").read_text() == "def greet():\n    return '0'\n"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert state.get("status") == "stopped"
    assert "снимок не сделан, дерево не тронуто" in (state.get("text") or "")
    assert not state.get("snapshot_ref")


@pytest.mark.asyncio
async def test_secrets_stop_snapshot_has_no_secrets(tmp_path: Path):
    repo = _repo(tmp_path)

    def player_with_env(_role, _kw):
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, ".env", "SECRET=1\n")
        return {
            "status": "done",
            "changes": [{"file": "src/greet.py", "what": "greet", "why": "c"}],
            "start_here": [],
            "blocked_reason": "",
        }

    agents = FakeAgents(repo, [player_with_env, judge_returns(ship())])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "stop"
    assert outcome.reason == "secrets"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    assert ref
    tree = set(_git(repo, "ls-tree", "-r", "--name-only", ref).splitlines())
    assert "src/greet.py" in tree
    assert ".env" not in tree
    assert not (repo / ".env").exists()
    assert ".env" in (state.get("text") or "")


@pytest.mark.asyncio
async def test_snapshot_after_player_ok(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    assert ref == ref_name(ledger.run_dir.name, 0, 1)
    assert _git(repo, "rev-parse", "--verify", ref).strip()
    assert "hello" in _git(repo, "show", f"{ref}:src/greet.py")


@pytest.mark.asyncio
async def test_snapshot_after_player_timeout(tmp_path: Path, monkeypatch):
    import asyncio
    import time

    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)
    real_take = snapshot_module.take_snapshot
    take_calls: list = []

    def counting_take(repo_path, base, ref, deny=None):
        take_calls.append(ref)
        return real_take(repo_path, base, ref, deny=deny)

    monkeypatch.setattr(snapshot_module, "take_snapshot", counting_take)

    class BlockingPlayer:
        """Stub blocks past the configured player timeout: wait_for cancels
        it, TimeoutError escapes run_role — like a hung LiveAgents provider."""

        def __init__(self, repo_dir: Path):
            self.repo_dir = repo_dir

        async def run_role(self, role: str, **kwargs):
            assert role == "player"
            timeout = kwargs.get("timeout") or 60

            async def work():
                write_file(self.repo_dir, "src/greet.py", "def greet():\n    return 'half'\n")
                write_file(self.repo_dir, "src/other.py", "X = 1\n")
                await asyncio.sleep(timeout + 30)

            await asyncio.wait_for(asyncio.ensure_future(work()), timeout)
            raise AssertionError("unreachable: wait_for times out first")

    timeout_s = 2
    started = time.monotonic()
    outcome, ledger = await _run(
        repo, BlockingPlayer(repo), _cfg(player_timeout_s=timeout_s)
    )
    elapsed = time.monotonic() - started
    assert elapsed >= timeout_s, "stub must block until the player timeout fires"
    assert outcome.status == "stop"
    assert outcome.reason == "provider_unavailable"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    assert ref == ref_name(ledger.run_dir.name, 0, 1)
    assert _git(repo, "rev-parse", "--verify", ref).strip()
    tree = set(_git(repo, "ls-tree", "-r", "--name-only", ref).splitlines())
    assert "src/greet.py" in tree
    assert "src/other.py" in tree
    assert "half" in _git(repo, "show", f"{ref}:src/greet.py")
    assert not (repo / "src/other.py").exists()
    # Post-player snapshot plus the _halt snapshot share this round's ref:
    # removing the post-player snapshot leaves a single take_snapshot call.
    assert take_calls == [ref, ref]


@pytest.mark.asyncio
async def test_failed_player_snapshot_clears_ref(tmp_path: Path, monkeypatch):
    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)
    real_take = snapshot_module.take_snapshot
    calls = {"n": 0}

    def flaky(repo_path, base, ref, deny=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("snapshot exploded")
        return real_take(repo_path, base, ref, deny=deny)

    monkeypatch.setattr(snapshot_module, "take_snapshot", flaky)

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'one'\n"),
            judge_returns(no_ship([finding(problem="still bad")])),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'two'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    assert calls["n"] == 2
    run_id = ledger.run_dir.name
    ref1 = ref_name(run_id, 0, 1)
    ref2 = ref_name(run_id, 0, 2)
    assert _git(repo, "rev-parse", "--verify", ref1).strip()
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert not state.get("snapshot_ref")
    assert ref1 not in (state.get("snapshot_ref") or "")
    ledger_rows = [
        json.loads(line) for line in ledger.path.read_text().splitlines()
    ]
    failed = [row for row in ledger_rows if row.get("kind") == "snapshot_failed"]
    assert len(failed) == 1
    assert failed[0]["round"] == 2
    assert failed[0]["ref"] == ref2
    stream_rows = [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]
    stream_failed = [row for row in stream_rows if row.get("type") == "snapshot_failed"]
    assert len(stream_failed) == 1
    snap = stream_failed[0]
    assert snap["ref"] == ref2
    assert snap["round"] == 2
    for key in ("phase", "role", "text"):
        assert key in snap, key
    assert snap["text"]


@pytest.mark.asyncio
async def test_snapshot_ref_in_state_stream_ledger(tmp_path: Path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    agents = FakeAgents(repo, _fuse_steps(repo, 1))
    outcome, ledger = await _run(repo, agents, _cfg(max_rounds=1))
    assert outcome.reason == "fuse"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    assert ref and ref.startswith("refs/tero/")
    assert state.get("base") == base
    stream_rows = [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]
    snaps = [row for row in stream_rows if row.get("type") == "snapshot"]
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap["ref"] == ref
    assert snap["base"] == base
    assert snap["reason"] == "fuse"
    for key in ("phase", "round", "role", "text"):
        assert key in snap, key
    assert snap["text"]
    ledger_rows = [
        json.loads(line) for line in ledger.path.read_text().splitlines()
    ]
    logged = [row for row in ledger_rows if row.get("kind") == "snapshot"]
    assert len(logged) == 1
    assert logged[0]["ref"] == ref
    assert logged[0]["base"] == base
    assert logged[0]["reason"] == "fuse"
