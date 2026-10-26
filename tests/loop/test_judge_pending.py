"""Judge failures retry on the same ref; three in a row → judge_pending."""

from __future__ import annotations

import json
import time
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


def _wait_question(ledger, timeout=30.0):
    import time as _t

    deadline = _t.time() + timeout
    while _t.time() < deadline:
        state = RunState.read(ledger.run_dir / "state.json") or {}
        if (state.get("question") or {}).get("id"):
            return state
        _t.sleep(0.01)
    raise AssertionError("no question opened")


@pytest.mark.asyncio
async def test_judge_pending_waits_holding_lock(tmp_path: Path, monkeypatch):
    import threading as _t

    from src.loop import autopilot as _auto
    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.lock import acquire
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases
    from src.loop.wait import wait_run

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
    monkeypatch.setattr(_auto, "NO_RELAY_GRACE_S", 3600)
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
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["done"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        try:
            _aio.run(_go())
        except Exception as exc:  # noqa: BLE001
            holder["e"] = exc

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        assert state.get("status") == "judge_pending"
        q = state.get("question") or {}
        assert q.get("reason") == "judge_pending"
        assert q.get("actions") == ["retry", "skip", "stop"]
        assert th.is_alive(), "owner exited instead of waiting"
        assert wait_run(ledger.run_dir, repo) == 3
        import time as _t2

        _t2.sleep(0.2)
        assert th.is_alive(), "owner exited before stop answer"
        assert "e" not in holder, holder.get("e")
        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        import json as _j

        (ledger.run_dir / "inbox" / "s.json").write_text(_j.dumps(
            {"uuid": "s1", "question_id": q.get("id"),
             "action": "skip", "who": "human"}))
    finally:
        th.join(timeout=30)
    assert "e" not in holder, holder.get("e")
    assert not th.is_alive(), "owner hung after skip"
    assert holder.get("done") is not None, "run_phases never returned"
    assert holder["done"].status == "ok"
    assert holder["done"].exit_code == 4, "skip judge_pending → долг, код 4"


@pytest.mark.asyncio
async def test_judge_pending_retry_calls_judge_only(tmp_path: Path, monkeypatch):
    import threading as _t

    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(dict(INVALID)),
            judge_returns(dict(INVALID)),
            judge_returns(dict(INVALID)),
            judge_returns(ship()),
        ],
    )
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["r"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        _aio.run(_go())

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        ref = state.get("snapshot_ref")
        assert ref, "retry must keep snapshot_ref"
        qid = (state.get("question") or {}).get("id")
        import json as _j

        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (ledger.run_dir / "inbox" / "r.json").write_text(_j.dumps(
            {"uuid": "r1", "question_id": qid,
             "action": "retry", "who": "human"}))
    finally:
        th.join(timeout=30)
    assert not th.is_alive(), "phase hung after retry"
    outcome = holder["r"]
    assert outcome.status == "ok", outcome
    assert agents.calls == ["player", "judge", "judge", "judge", "judge"]
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert state.get("snapshot_ref") == ref, "retry must reuse the same ref"


@pytest.mark.asyncio
async def test_accept_reject_human_only(tmp_path: Path, monkeypatch):
    import threading as _t

    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.git import phase_shipped
    from src.loop.inbox import Inbox
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases
    from src.relay.iface import FakeRelay

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
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
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["r"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        _aio.run(_go())

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        qid = (state.get("question") or {}).get("id")
        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        import json as _j

        (ledger.run_dir / "inbox" / "a.json").write_text(_j.dumps(
            {"uuid": "a1", "question_id": qid,
             "action": "accept", "who": "agent"}))
        assert Inbox(ledger.run_dir).take(qid, FakeRelay()) is None
        rej = (ledger.run_dir / "inbox" / "a1.result").read_text()
        assert "отклонено" in rej
        (ledger.run_dir / "inbox" / "a.json").write_text(_j.dumps(
            {"uuid": "a2", "question_id": qid,
             "action": "accept", "who": "human"}))
    finally:
        th.join(timeout=30)
    assert not th.is_alive(), "phase hung after human accept"
    assert holder["r"].status == "ok", holder["r"]
    st = RunState.read(ledger.run_dir / "state.json") or {}
    base = st.get("base") or ""
    assert base, "ship must keep base"
    assert phase_shipped(repo, base, ledger.run_dir.name, 1), "accept ships with trailer"


@pytest.mark.asyncio
async def test_judge_pending_records_agree_and_holds_repo_lock(tmp_path: Path, monkeypatch):
    """F1+F4: judge_pending пишется одним переходом в state/stream/ledger;
    владелец держит лок репо, пока ждёт, и отпускает после решения."""
    import threading as _t

    from src.loop import autopilot as _auto
    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.lock import acquire
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
    monkeypatch.setattr(_auto, "NO_RELAY_GRACE_S", 3600)
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
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["done"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        lock = acquire(repo)
        holder["owner_locked"] = lock is not None
        try:
            _aio.run(_go())
        except Exception as exc:  # noqa: BLE001
            holder["e"] = exc
        finally:
            try:
                if lock is not None:
                    lock.release()
            except Exception:  # noqa: BLE001
                pass

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        assert state.get("status") == "judge_pending"
        # phase_transition пишет stream.jsonl после state.json — ждём строку
        deadline = time.time() + 30
        while True:
            stream_rows = [
                json.loads(line)
                for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
            ]
            asked = [r for r in stream_rows if r.get("type") == "question"]
            if asked or time.time() > deadline:
                break
            time.sleep(0.01)
        assert asked, "вопрос должен попасть в stream.jsonl"
        assert all(r.get("status") == "judge_pending" for r in asked)
        ledger_rows = _ledger_rows(ledger)
        lasked = [r for r in ledger_rows if r.get("kind") == "question"]
        assert lasked, "вопрос должен попасть в ledger"
        assert all(r.get("status") == "judge_pending" for r in lasked)
        assert holder.get("owner_locked") is True, "владелец держит лок репо"
        assert th.is_alive(), "owner exited instead of waiting"
        assert acquire(repo) is None, "лок репо занят, пока владелец ждёт"
        qid = (state.get("question") or {}).get("id")
        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (ledger.run_dir / "inbox" / "s.json").write_text(json.dumps(
            {"uuid": "s-lock", "question_id": qid,
             "action": "skip", "who": "human"}))
    finally:
        th.join(timeout=30)
    assert "e" not in holder, holder.get("e")
    assert not th.is_alive(), "owner hung after skip"
    assert holder.get("done") is not None, "run_phases never returned"
    got = acquire(repo)
    assert got is not None, "лок репо свободен после решения"
    got.release()


@pytest.mark.asyncio
async def test_autopilot_judge_pending_retry_then_skip_debt(tmp_path: Path, monkeypatch):
    """F5: автопилот трижды retry в бюджет, затем skip в долг."""
    import threading as _t

    from src.loop import autopilot as _auto
    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
    monkeypatch.setattr(_auto, "NO_RELAY_GRACE_S", 0)
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [player_writes(repo, "src/greet.py", HELLO)]
        + [judge_returns(dict(INVALID)) for _ in range(12)],
    )
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["done"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        try:
            _aio.run(_go())
        except Exception as exc:  # noqa: BLE001
            holder["e"] = exc

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=30)
    assert "e" not in holder, holder.get("e")
    assert not th.is_alive(), "autopilot flow hung"
    assert holder.get("done") is not None, "run_phases never returned"
    assert holder["done"].status == "ok", holder["done"]
    assert holder["done"].exit_code == 4, "skip judge_pending → долг, код 4"
    assert agents.calls == ["player"] + ["judge"] * 12, agents.calls
    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert state.get("budget") == {"1": 3}, state.get("budget")
    debt = state.get("debt") or []
    assert len(debt) == 1, debt
    assert debt[0].get("reason") == "judge_pending", debt[0]
    assert debt[0].get("who") == "autopilot", debt[0]
    assert debt[0].get("phase") == 1, debt[0]
    assert debt[0].get("ref") == state.get("snapshot_ref"), debt[0]
    kinds = [row.get("kind") for row in _ledger_rows(ledger)]
    assert "rollback" in kinds, "skip уходит в долг через откат"


@pytest.mark.asyncio
async def test_judge_pending_retry_reviews_recorded_ref(tmp_path: Path, monkeypatch):
    """F6: грязь в дереве во время ожидания — retry судит записанный ref."""
    import threading as _t

    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(dict(INVALID)),
            judge_returns(dict(INVALID)),
            judge_returns(dict(INVALID)),
            judge_returns(ship()),
        ],
    )
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["r"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        _aio.run(_go())

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        ref = state.get("snapshot_ref")
        assert ref, "retry must keep snapshot_ref"
        qid = (state.get("question") or {}).get("id")
        (repo / "src/greet.py").write_text("def greet():\n    return 'junk'\n")
        import json as _j

        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (ledger.run_dir / "inbox" / "r.json").write_text(_j.dumps(
            {"uuid": "r-f6", "question_id": qid,
             "action": "retry", "who": "human"}))
    finally:
        th.join(timeout=30)
    assert not th.is_alive(), "phase hung after retry"
    outcome = holder["r"]
    assert outcome.status == "ok", outcome
    assert agents.calls == ["player", "judge", "judge", "judge", "judge"]
    shown = scripted.git(repo, "show", f"{ref}:src/greet.py")
    assert shown == HELLO, "retry перезаписал записанный ref грязью из ожидания"
    assert (repo / "src/greet.py").read_text() == HELLO


@pytest.mark.asyncio
async def test_accept_ships_recorded_ref_and_marks_plan(tmp_path: Path, monkeypatch):
    """F3: accept сдаёт код из snapshot_ref; план отмечен сданным."""
    import threading as _t

    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.git import phase_shipped
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
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
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["r"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        _aio.run(_go())

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        ref = state.get("snapshot_ref")
        base = state.get("base")
        assert ref and base, "accept needs snapshot_ref and base"
        qid = (state.get("question") or {}).get("id")
        (repo / "src/greet.py").write_text("def greet():\n    return 'junk'\n")
        import json as _j

        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (ledger.run_dir / "inbox" / "a.json").write_text(_j.dumps(
            {"uuid": "a-f3", "question_id": qid,
             "action": "accept", "who": "human"}))
    finally:
        th.join(timeout=30)
    assert not th.is_alive(), "phase hung after human accept"
    assert holder["r"].status == "ok", holder["r"]
    assert (repo / "src/greet.py").read_text() == HELLO
    assert phase_shipped(repo, base, ledger.run_dir.name, 1), "accept ships with trailer"
    assert "[ ]" not in (repo / "plan.md").read_text(), "accept must mark the phase done"


@pytest.mark.asyncio
async def test_judge_retry_restore_failure_fails_closed(tmp_path, monkeypatch):
    """F7: записанный ref бит — retry не снимает свежий снимок и не зовёт игрока/судью заново."""
    import threading as _t

    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
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
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["r"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        _aio.run(_go())

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        ref = state.get("snapshot_ref")
        assert ref, "retry must keep snapshot_ref"
        qid = (state.get("question") or {}).get("id")
        scripted.git(repo, "update-ref", "-d", ref)
        assert not snapshot_module.verify(repo, ref)
        import json as _j

        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (ledger.run_dir / "inbox" / "r.json").write_text(_j.dumps(
            {"uuid": "r-f7", "question_id": qid,
             "action": "retry", "who": "human"}))
    finally:
        th.join(timeout=30)
    assert not th.is_alive(), "phase hung after retry on unrestorable ref"
    outcome = holder["r"]
    assert outcome.status == "stop", outcome
    assert agents.calls == ["player", "judge", "judge", "judge"], agents.calls


@pytest.mark.asyncio
async def test_accept_without_ref_stops(tmp_path, monkeypatch):
    """F8: snapshot_ref пуст — accept останавливает, а не шипает живое дерево."""
    import threading as _t

    from src.loop import decision_step as _ds
    from src.loop.gates import detect as _detect
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.phase import run_phase as _run_phase_fn
    from src.loop.plan import load_phases as _lp
    from src.loop.plan import load_preamble as _lpre
    from src.loop.session import run_phases as _run_phases

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
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
    cfg = _cfg(decisions=True)
    ledger = _Ledger(_new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["r"] = await _run_phases(
            _run_phase_fn, _lp(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(
                repo=repo, plan_path=repo / "plan.md",
                preamble=_lpre(repo / "plan.md"), cfg=cfg,
                agents=agents, test_command=_detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio as _aio

        try:
            _aio.run(_go())
        except Exception as exc:  # noqa: BLE001
            holder["e"] = exc

    th = _t.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        qid = (state.get("question") or {}).get("id")
        RunState(ledger.run_dir / "state.json").update(snapshot_ref="")
        import json as _j

        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (ledger.run_dir / "inbox" / "a.json").write_text(_j.dumps(
            {"uuid": "a-f8", "question_id": qid,
             "action": "accept", "who": "human"}))
    finally:
        th.join(timeout=30)
    assert not th.is_alive(), "phase hung after accept"
    assert "e" not in holder, holder.get("e")
    assert holder["r"].status == "stop", holder["r"]
    assert "[ ]" in (repo / "plan.md").read_text(), "accept без ref не сдаёт фазу"
