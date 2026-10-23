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
    assert elapsed >= 2 * timeout_s, "stub must block until the player timeout fires"
    assert outcome.status == "stop"
    assert outcome.reason == "provider_unavailable"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    assert ref == ref_name(ledger.run_dir.name, 0, 2)
    assert _git(repo, "rev-parse", "--verify", ref).strip()
    tree = set(_git(repo, "ls-tree", "-r", "--name-only", ref).splitlines())
    assert "src/greet.py" in tree
    assert "src/other.py" in tree
    assert "half" in _git(repo, "show", f"{ref}:src/greet.py")
    assert not (repo / "src/other.py").exists()
    # Two crashes halt: round-1 post-player snapshot, round-2 post-player
    # snapshot, plus the _halt snapshot sharing round 2's ref.
    ref1 = ref_name(ledger.run_dir.name, 0, 1)
    assert take_calls == [ref1, ref, ref]


@pytest.mark.asyncio
async def test_pre_judge_snapshot_failure_skips_judge(tmp_path: Path, monkeypatch):
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
            player_writes(repo, "src/greet.py", "def greet():\n    return 'two'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    # Round 1: post-player snapshot (call 1) + pre-judge snapshot (call 2, flaky).
    # Round 2: post-player snapshot (call 3) + pre-judge snapshot (call 4).
    assert calls["n"] == 4
    # The judge must not review the unverified round-1 tree: no judge call then.
    assert agents.calls == ["player", "player", "judge"]
    run_id = ledger.run_dir.name
    ref1 = ref_name(run_id, 0, 1)
    ref2 = ref_name(run_id, 0, 2)
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert state.get("snapshot_ref") == ref2
    ledger_rows = [
        json.loads(line) for line in ledger.path.read_text().splitlines()
    ]
    failed = [row for row in ledger_rows if row.get("kind") == "snapshot_failed"]
    assert len(failed) == 1
    assert failed[0]["round"] == 1
    assert failed[0]["ref"] == ref1
    stream_rows = [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]
    stream_failed = [row for row in stream_rows if row.get("type") == "snapshot_failed"]
    assert len(stream_failed) == 1
    snap = stream_failed[0]
    assert snap["ref"] == ref1
    assert snap["round"] == 1
    for key in ("phase", "role", "text"):
        assert key in snap, key
    assert snap["text"]
    judge_paths = list((ledger.run_dir / "phase-00").glob("round-*/judge.json"))
    assert [path.parent.name for path in judge_paths] == ["round-02"]


@pytest.mark.asyncio
async def test_failed_post_player_snapshot_healed_by_pre_judge(tmp_path: Path, monkeypatch):
    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)
    real_take = snapshot_module.take_snapshot
    calls = {"n": 0}

    def flaky(repo_path, base, ref, deny=None):
        calls["n"] += 1
        if calls["n"] == 3:
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
    # Round 1: post-player snapshot (call 1) + pre-judge snapshot (call 2).
    # Round 2: post-player snapshot (call 3, flaky) + pre-judge (call 4, heals).
    assert calls["n"] == 4
    run_id = ledger.run_dir.name
    ref2 = ref_name(ledger.run_dir.name, 0, 2)
    assert _git(repo, "rev-parse", "--verify", ref2).strip()
    assert "two" in _git(repo, "show", f"{ref2}:src/greet.py")
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert state.get("snapshot_ref") == ref2
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


@pytest.mark.asyncio
async def test_snapshot_ref_persisted_before_pre_judge_restore(tmp_path: Path, monkeypatch):
    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    real_take = snapshot_module.take_snapshot
    take_calls = {"n": 0}

    def flaky_take(repo_path, base_arg, ref, deny=None):
        take_calls["n"] += 1
        if take_calls["n"] == 1:
            raise RuntimeError("post-player snapshot exploded")
        return real_take(repo_path, base_arg, ref, deny=deny)

    monkeypatch.setattr(snapshot_module, "take_snapshot", flaky_take)

    tree_calls = {"n": 0}
    real_tree_of = snapshot_module.tree_of

    def lying_tree_of(repo_path, base_arg, deny=None):
        tree_calls["n"] += 1
        # Call 1 = pre-judge take_snapshot's own tree_of; call 2 = the compare.
        if tree_calls["n"] == 2:
            return "0" * 40
        return real_tree_of(repo_path, base_arg, deny=deny)

    monkeypatch.setattr(snapshot_module, "tree_of", lying_tree_of)

    def boom_restore(repo_path, ref):
        raise RuntimeError("restore exploded")

    monkeypatch.setattr(snapshot_module, "restore_snapshot", boom_restore)

    agents = FakeAgents(
        repo,
        [player_writes(repo, "src/greet.py", "def greet():\n    return 'one'\n")],
    )
    with pytest.raises(RuntimeError, match="restore exploded"):
        await _run(repo, agents)
    # take 1 (post-player) failed, take 2 (pre-judge) verified the snapshot,
    # then the compare forced a restore that blew up: state must already
    # point at the verified snapshot.
    assert take_calls["n"] == 2
    state = RunState.read(_only_run_dir(repo) / "state.json")
    assert state is not None
    run_id = _only_run_dir(repo).name
    assert state.get("snapshot_ref") == ref_name(run_id, 0, 1)
    assert state.get("base") == base


def _only_run_dir(repo: Path) -> Path:
    (run_dir,) = [p for p in (repo / ".tero" / "runs").iterdir() if p.is_dir()]
    return run_dir


@pytest.mark.asyncio
async def test_judge_sees_snapshot_tree(tmp_path: Path):
    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)

    def checking_judge(_role, _kw):
        state = RunState.read(_only_run_dir(repo) / "state.json")
        assert state is not None
        ref = state.get("snapshot_ref")
        base = state.get("base")
        assert ref
        current = snapshot_module.tree_of(repo, base)
        wanted = snapshot_module.ref_tree(repo, ref)
        return ship(tree_match=(current == wanted), ref=ref)

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            checking_judge,
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    data = json.loads(
        (ledger.run_dir / "phase-00" / "round-01" / "judge.json").read_text()
    )
    assert data["tree_match"] is True
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert data["ref"] == state.get("snapshot_ref")


@pytest.mark.asyncio
async def test_judge_tamper_restores_snapshot(tmp_path: Path, monkeypatch):
    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)
    real_restore = snapshot_module.restore_snapshot
    restores: list = []

    def checking_restore(repo_path, ref):
        restores.append(ref)
        result = real_restore(repo_path, ref)
        state = RunState.read(_only_run_dir(repo) / "state.json")
        assert state is not None
        base = state.get("base")
        assert snapshot_module.tree_of(repo, base) == snapshot_module.ref_tree(
            repo, ref
        )
        return result

    monkeypatch.setattr(snapshot_module, "restore_snapshot", checking_restore)

    def dirty_judge(_role, _kw):
        write_file(repo, "src/greet.py", "def greet():\n    return 'TAMPER'\n")
        write_file(repo, "junk-from-judge.txt", "junk\n")
        (repo / "src/__init__.py").unlink()
        return ship()

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            dirty_judge,
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    ref = state.get("snapshot_ref")
    assert ref
    # Tamper was rolled back: ref holds the player's code, disk matches it.
    assert "hello" in _git(repo, "show", f"{ref}:src/greet.py")
    assert "TAMPER" not in _git(repo, "show", f"{ref}:src/greet.py")
    assert "hello" in (repo / "src/greet.py").read_text()
    assert "TAMPER" not in (repo / "src/greet.py").read_text()
    assert not (repo / "junk-from-judge.txt").exists()
    assert (repo / "src/__init__.py").exists()
    assert restores == [ref]
    ledger_rows = [
        json.loads(line) for line in ledger.path.read_text().splitlines()
    ]
    tampered = [row for row in ledger_rows if row.get("kind") == "judge_tamper"]
    assert len(tampered) == 1
    assert tampered[0]["round"] == 1
    assert tampered[0]["ref"] == ref


@pytest.mark.asyncio
async def test_untracked_secret_is_not_tamper(tmp_path: Path, monkeypatch):
    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)
    restore_calls: list = []
    real_restore = snapshot_module.restore_snapshot

    def counting_restore(repo_path, ref):
        restore_calls.append(ref)
        return real_restore(repo_path, ref)

    monkeypatch.setattr(snapshot_module, "restore_snapshot", counting_restore)

    def player_with_env(_role, _kw):
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, ".env", "SECRET=1\n")
        return dict(scripted.PLAYER_DONE)

    def ship_with_env_check(_role, _kw):
        state = RunState.read(_only_run_dir(repo) / "state.json")
        assert state is not None
        ref = state.get("snapshot_ref")
        base = state.get("base")
        match = snapshot_module.tree_of(repo, base) == snapshot_module.ref_tree(
            repo, ref
        )
        return ship(env_on_disk=(repo / ".env").exists(), tree_match=match)

    agents = FakeAgents(repo, [player_with_env, ship_with_env_check])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "stop"
    assert outcome.reason == "secrets"
    assert restore_calls == []
    kinds = [
        json.loads(line)["kind"] for line in ledger.path.read_text().splitlines()
    ]
    assert "judge_tamper" not in kinds
    data = json.loads(
        (ledger.run_dir / "phase-00" / "round-01" / "judge.json").read_text()
    )
    assert data["env_on_disk"] is True
    assert data["tree_match"] is True
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    tree = set(_git(repo, "ls-tree", "-r", "--name-only", state["snapshot_ref"]).splitlines())
    assert "src/greet.py" in tree
    assert ".env" not in tree


@pytest.mark.asyncio
async def test_state_before_judge_call(tmp_path: Path):
    repo = _repo(tmp_path)

    def observing_judge(_role, _kw):
        state = RunState.read(_only_run_dir(repo) / "state.json")
        assert state is not None
        return ship(
            observed={
                "role": state.get("role"),
                "provider": state.get("provider"),
                "model": state.get("model"),
                "step_started": state.get("step_started"),
                "snapshot_ref": state.get("snapshot_ref"),
            }
        )

    cfg = _cfg(judge_provider="codex", judge_model="judge-test-model")
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            observing_judge,
        ],
    )
    outcome, ledger = await _run(repo, agents, cfg)
    assert outcome.status == "ship"
    data = json.loads(
        (ledger.run_dir / "phase-00" / "round-01" / "judge.json").read_text()
    )
    observed = data["observed"]
    assert observed["role"] == "judge"
    assert observed["provider"] == "codex"
    assert observed["model"] == "judge-test-model"
    assert observed["step_started"]
    assert observed["snapshot_ref"] == ref_name(ledger.run_dir.name, 0, 1)
