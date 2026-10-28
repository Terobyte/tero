"""player_schedule: игроки по раундам и ship хозяина на fuse."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from src.config import Config
from src.loop.phase_ctx import PhaseContext
from src.schedule import apply_schedule, parse_schedule, schedule_player
from tests.loop import fakes as scripted
from tests.loop.test_judge_pending import HELLO, _cfg, _repo, _wait_question

SCHED = "opencode:meta-model-api/muse-spark-1.3-contributor×3,claude:sonnet×2,claude:opus×2"


def test_parse_schedule():
    assert parse_schedule(SCHED) == [
        ("opencode", "meta-model-api/muse-spark-1.3-contributor", 3),
        ("claude", "sonnet", 2),
        ("claude", "opus", 2),
    ]
    assert parse_schedule("claude:opus*2, codex x1") == [("claude", "opus", 2), ("codex", "", 1)]
    with pytest.raises(ValueError):
        parse_schedule("claude:opus×0")


@pytest.mark.parametrize("rnd,want", [
    (1, "opencode"), (3, "opencode"), (4, "sonnet"), (5, "sonnet"), (6, "opus"), (7, "opus"), (9, "opus"),
])
def test_schedule_player_by_round(rnd, want):
    provider, model = schedule_player(SCHED, rnd)
    assert want in (provider, model)


def test_apply_schedule_sets_limit_and_round_mode():
    cfg = apply_schedule(Config(escalation="situational", escalate_at_round=3, max_rounds=15), SCHED)
    assert cfg.max_rounds == 7
    assert cfg.escalation == "round"
    assert cfg.escalate_at_round == 0
    assert cfg.player_schedule == SCHED


def test_player_identity_follows_schedule():
    cfg = apply_schedule(Config(escalation_provider="claude", escalation_model="opus"), SCHED)
    fake = SimpleNamespace(cfg=cfg, st=SimpleNamespace(round=4, escalated=True))
    assert PhaseContext.player_identity(fake) == ("sonnet", "claude", "")
    fake.st.round = 1
    assert PhaseContext.player_identity(fake)[1] == "opencode"


def test_no_stuck_with_schedule():
    from src.loop.progress_step import check_no_progress

    cfg = apply_schedule(Config(), SCHED)
    st = SimpleNamespace(round=5, escalated=False)
    assert check_no_progress(st, cfg, ledger=None) is False
    assert st.escalated is False


def test_fuse_offers_ship():
    from src.loop.decision.actions import question_actions

    assert "ship" in question_actions("fuse", {}, 1)
    assert "ship" in question_actions("fuse", {"budget": {"1": 99}}, 1)
    assert "ship" not in question_actions("stuck", {}, 1)


@pytest.mark.asyncio
async def test_fuse_ship_commits_host_tree(tmp_path, monkeypatch):
    """fuse → вопрос; хозяин правит дерево и отвечает ship → коммит его дерева, план отмечен."""
    from src.loop.decision import step as _ds
    from src.loop.gates import detect
    from src.loop.git import phase_shipped
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.phase import run_phase
    from src.loop.plan import load_phases, load_preamble
    from src.loop.session import run_phases

    monkeypatch.setattr(_ds, "DECISION_POLL_S", 0.01)
    repo = _repo(tmp_path)
    bad = "def greet():\n    return 'nope'\n"
    agents = scripted.FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", bad),
        scripted.judge_returns(scripted.no_ship([scripted.finding()])),
    ])
    cfg = _cfg(decisions=True, max_rounds=1)
    ledger = Ledger(new_run_dir(repo, "demo"))
    holder: dict = {}

    async def _go() -> None:
        holder["r"] = await run_phases(
            run_phase, load_phases(repo / "plan.md"),
            ledger=lambda: ledger,
            phase_kwargs=dict(repo=repo, plan_path=repo / "plan.md",
                              preamble=load_preamble(repo / "plan.md"), cfg=cfg,
                              agents=agents, test_command=detect(repo)),
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)

    def _run() -> None:
        import asyncio

        asyncio.run(_go())

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    try:
        state = _wait_question(ledger)
        q = state.get("question") or {}
        assert "ship" in (q.get("actions") or []), q
        assert (repo / "src/greet.py").read_text() == bad, "tree must not be rolled back at fuse"
        base = state.get("base")
        (repo / "src/greet.py").write_text(HELLO)  # хозяин доделал
        (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (ledger.run_dir / "inbox" / "s.json").write_text(json.dumps(
            {"uuid": "s-1", "question_id": q.get("id"), "action": "ship",
             "who": "agent", "why": "host finished"}))
    finally:
        th.join(timeout=30)
    assert not th.is_alive(), "phase hung after host ship"
    assert holder["r"].status == "ok", holder["r"]
    assert (repo / "src/greet.py").read_text() == HELLO
    if base:
        assert phase_shipped(repo, base, ledger.run_dir.name, 1)
    assert "[ ]" not in (repo / "plan.md").read_text()
