"""Точки решения: _halt открывает вопрос вместо стопа (decisions=True)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from tests.loop import fakes as scripted

FakeAgents = scripted.FakeAgents
finding = scripted.finding
judge_returns = scripted.judge_returns
no_ship = scripted.no_ship
player_writes = scripted.player_writes
ship = scripted.ship

PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""

HELLO = "def greet():\n    return 'hello'\n"


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


async def _run(repo: Path, agents: FakeAgents, cfg: Config):
    from src.loop.plan import load_phases, load_preamble
    from src.loop.phase import run_phase

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


def _state_of(ledger: Ledger) -> dict:
    from src.loop.state import RunState

    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    return state


@pytest.mark.asyncio
async def test_halt_opens_question_tree_untouched(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(
                {"verdict": "NEEDS_USER", "previous": [], "findings": [],
                 "user_question": "which greeting?"}
            ),
        ],
    )
    outcome, ledger = await _run(repo, agents, _cfg(decisions=True))
    assert outcome.status == "waiting_human"
    state = _state_of(ledger)
    assert state.get("status") == "waiting_human"
    q = state.get("question") or {}
    assert q.get("id") == "1-1"
    assert (repo / "src/greet.py").read_text() == HELLO
    assert state.get("tree_sha")


@pytest.mark.asyncio
async def test_halt_without_decisions_unchanged(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(
                {"verdict": "NEEDS_USER", "previous": [], "findings": [],
                 "user_question": "which greeting?"}
            ),
        ],
    )
    outcome, ledger = await _run(repo, agents, _cfg(decisions=False))
    assert outcome.status == "stop"
    state = _state_of(ledger)
    assert state.get("status") == "stopped"
    assert state.get("snapshot_ref"), "откат должен оставить снимок в refs/tero"
    assert not (repo / "src/greet.py").exists(), "decisions=False: дерево откачено"


@pytest.mark.asyncio
async def test_question_actions_only_implemented(tmp_path: Path):
    from src.loop.inbox import Inbox
    from src.relay.iface import FakeRelay

    repo = _repo(tmp_path)
    stuck = no_ship([finding(problem="still broken")],
                    previous=[{"id": "F1", "state": "still_open"}])
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(no_ship([finding(problem="still broken")])),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(stuck),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(stuck),
        ],
    )
    outcome, ledger = await _run(repo, agents, _cfg(decisions=True))
    state = _state_of(ledger)
    q = state.get("question") or {}
    assert q.get("actions") == ["more", "model", "skip", "stop"]
    inbox = Inbox(ledger.run_dir)
    (ledger.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (ledger.run_dir / "inbox" / "m.json").write_text(
        json.dumps({"uuid": "m1", "question_id": q.get("id"),
                    "action": "answer", "text": "x"}),
        encoding="utf-8",
    )
    assert inbox.take(q.get("id"), FakeRelay()) is None
    assert "недопустимо" in (ledger.run_dir / "inbox" / "m1.result").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_snapshot_failed_no_question(tmp_path: Path, monkeypatch):
    import src.loop.snapshot as snapshot_module

    repo = _repo(tmp_path)

    # snapshot_failed вне ACTIONS_BY_REASON: _halt идёт прежним стопом без вопроса.
    # Реальный сбой снимка: rollback падает, DecisionStep.halt останавливается
    # в stopped без открытого вопроса.
    def _boom(*a: object, **k: object):
        raise RuntimeError("boom")

    monkeypatch.setattr(snapshot_module, "take_snapshot", _boom)
    from src.loop.decision_step import DecisionStep as _DS

    assert _DS.implemented_actions("snapshot_failed") == []
    from src.loop.ledger import Ledger as _Ledger
    from src.loop.ledger import new_run_dir as _new_run_dir
    from src.loop.phase_ctx import PhaseContext as _PC
    from src.loop.phase_ctx import PhaseState as _PS
    from src.loop.plan import load_phases as _lp
    from src.loop.state import RunState as _RS
    from src.loop.stream import Stream as _St

    fail_agents = FakeAgents(repo, [])
    fail_ledger = _Ledger(_new_run_dir(repo, "demo"))
    fail_ctx = _PC(
        phase=_lp(repo / "plan.md")[0], total_phases=1, repo=repo,
        plan_path=repo / "plan.md", preamble="", cfg=_cfg(decisions=True),
        agents=fail_agents, test_command=None, tester_enabled=False,
        ledger=fail_ledger, run_dir=fail_ledger.run_dir,
        state_io=_RS(fail_ledger.run_dir / "state.json"),
        stream=_St(fail_ledger.run_dir / "stream.jsonl"),
        pulse=None, checkpoint="x" * 40, contract="", suite_hashes={},
        guarded={}, link_targets=set(), st=_PS(),
    )
    fail_step = _DS(fail_ctx, lambda reason: snapshot_module.take_snapshot(repo, "x" * 40, "refs/tero/x"))
    outcome = fail_step.halt("snapshot_failed")
    ledger = fail_ledger
    assert outcome.status == "stop", outcome
    assert outcome.reason == "snapshot_failed"
    state = _state_of(ledger)
    assert state.get("status") == "stopped"
    assert not state.get("question")


@pytest.mark.asyncio
async def test_answer_goes_to_prompts_marked(tmp_path: Path):
    from src.loop import prompts as prompts_mod

    text = prompts_mod.player_prompt(
        preamble="p", contract="c", answer="используй sqlite"
    )
    assert "используй sqlite" in text
    assert "не меняет контракт" in text
    before = text.split("используй sqlite")[0]
    assert "Ответ на вопрос фазы" in before
    jtext = prompts_mod.judge_prompt(preamble="p", contract="c",
                                     answer="используй sqlite")
    assert "используй sqlite" in jtext
    assert "Ответ на вопрос фазы" in jtext


@pytest.mark.asyncio
async def test_answer_continues_next_round_with_block(tmp_path: Path, monkeypatch):
    import src.loop.decision_step as ds_mod
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.plan import load_phases, load_preamble
    from src.loop.gates import detect
    from src.loop.session import run_phases
    from src.loop.phase import run_phase
    from src.loop.state import RunState

    monkeypatch.setattr(ds_mod, "DECISION_POLL_S", 0.01)
    repo = _repo(tmp_path)
    prompts: list[str] = []

    def _player(role: str, kwargs: dict) -> dict:
        assert role == "player"
        prompts.append(kwargs.get("prompt") or "")
        if len(prompts) == 1:
            from tests.loop import fakes as _f
            _f.write_file(repo, "src/greet.py", HELLO)
            return dict(_f.PLAYER_DONE)
        from tests.loop import fakes as _f
        _f.write_file(repo, "src/greet.py", HELLO)
        return dict(_f.PLAYER_DONE)

    def _judge(role: str, kwargs: dict) -> dict:
        assert role == "judge"
        prompts.append(kwargs.get("prompt") or "")
        if len([p for p in prompts if "judge" in p.lower()]) <= 1 and sum(1 for _ in prompts) <= 2:
            return {"verdict": "NEEDS_USER", "previous": [], "findings": [],
                    "user_question": "which greeting?"}
        return {"verdict": "SHIP", "previous": [], "findings": [], "user_question": ""}

    class _Agents:
        async def run_role(self, role: str, **kwargs: dict):
            if role == "player":
                return _player(role, kwargs)
            return _judge(role, kwargs)

    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox_dir(ledger.run_dir, create=True)
    phases = load_phases(repo / "plan.md")
    cfg = _cfg(decisions=True, max_rounds=5)
    kwargs = dict(
        repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=_Agents(), test_command=detect(repo),
    )

    def _answer() -> None:
        import time as _t
        for _ in range(500):
            try:
                state = RunState.read(ledger.run_dir / "state.json") or {}
                q = state.get("question") or {}
                if (state.get("status") == "waiting_human") and q.get("id"):
                    (ledger.run_dir / "inbox" / "a.json").write_text(
                        json.dumps({"uuid": "a1", "question_id": q["id"],
                                    "action": "answer", "text": "используй sqlite",
                                    "who": "agent"}),
                        encoding="utf-8",
                    )
                    return
            except Exception:
                pass
            _t.sleep(0.01)

    import threading
    t = threading.Thread(target=_answer, daemon=True)
    t.start()
    outcome = await run_phases(
        run_phase, phases, ledger=lambda: ledger, phase_kwargs=kwargs,
        run_dir=ledger.run_dir, on_pending=lambda: None,
        on_stop=lambda o: None, on_done=lambda s: None,
    )
    t.join(timeout=5)
    assert outcome.exit_code == 0, outcome
    assert not t.is_alive(), "помощник не успел подать answer за 5с"
    round2 = [p for p in prompts[1:] if "используй sqlite" in p]
    assert round2, "ответ не попал в промпты следующего раунда"
    assert any("Ответ на вопрос фазы" in p.split("используй sqlite")[0] for p in round2)


TWO_PHASE_PLAN = """# Demo

Preamble.

## First

- [ ] first done

## Second

- [ ] second done
"""


@pytest.mark.asyncio
async def test_skip_is_debt_and_exit_4(tmp_path: Path, monkeypatch):
    import json
    import threading
    import src.loop.decision_step as ds_mod
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.plan import load_phases, load_preamble
    from src.loop.gates import detect
    from src.loop.session import run_phases
    from src.loop.phase import run_phase
    from src.loop.state import RunState

    monkeypatch.setattr(ds_mod, "DECISION_POLL_S", 0.01)
    repo = _repo(tmp_path)
    (repo / "plan.md").write_text(TWO_PHASE_PLAN)
    import subprocess as _git
    _git.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    _git.run(["git", "commit", "-m", "two phases"], cwd=repo, check=True, capture_output=True)
    stuck = no_ship([finding(problem="still broken")],
                    previous=[{"id": "F1", "state": "still_open"}])
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(no_ship([finding(problem="still broken")])),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(stuck),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(stuck),
            player_writes(repo, "src/second.py", "x = 1\n"),
            judge_returns(ship()),
        ],
    )
    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox = inbox_dir(ledger.run_dir, create=True)
    assert inbox is not None
    phases = load_phases(repo / "plan.md")
    cfg = _cfg(decisions=True)
    command = detect(repo)
    kwargs = dict(
        repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=agents, test_command=command,
    )

    def _answer_skip() -> None:
        import time as _t
        for _ in range(500):
            try:
                state = RunState.read(ledger.run_dir / "state.json") or {}
                q = state.get("question") or {}
                if (state.get("status") == "waiting_human") and q.get("id"):
                    (ledger.run_dir / "inbox" / "a.json").write_text(
                        json.dumps({"uuid": "a1", "question_id": q["id"],
                                    "action": "skip", "text": "", "who": "agent"}),
                        encoding="utf-8",
                    )
                    return
            except Exception:
                pass
            _t.sleep(0.01)

    t = threading.Thread(target=_answer_skip, daemon=True)
    t.start()
    outcome = await run_phases(
        run_phase, phases, ledger=lambda: ledger, phase_kwargs=kwargs,
        run_dir=ledger.run_dir, on_pending=lambda: None,
        on_stop=lambda o: None, on_done=lambda s: None,
    )
    t.join(timeout=5)
    assert outcome.exit_code == 4, outcome
    assert not t.is_alive(), "помощник не успел подать skip за 5с"
    assert outcome.status == "ok"
    assert outcome.debt == 1
    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert state.get("status") == "done"
    debt = state.get("debt") or []
    assert len(debt) == 1 and debt[0].get("phase") == 1
    text = (repo / "plan.md").read_text()
    assert "- [ ] first done" in text, "чекбокс фазы 1 остался [ ]"
    assert "- [x] second done" in text, "фаза 2 сдана"


@pytest.mark.asyncio
async def test_stop_rolls_back_exit_2(tmp_path: Path, monkeypatch):
    import json
    import threading
    import src.loop.decision_step as ds_mod
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.plan import load_phases, load_preamble
    from src.loop.gates import detect
    from src.loop.session import run_phases
    from src.loop.phase import run_phase
    from src.loop.state import RunState

    monkeypatch.setattr(ds_mod, "DECISION_POLL_S", 0.01)

    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(
                {"verdict": "NEEDS_USER", "previous": [], "findings": [],
                 "user_question": "which greeting?"}
            ),
        ],
    )
    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox_dir(ledger.run_dir, create=True)
    phases = load_phases(repo / "plan.md")
    cfg = _cfg(decisions=True)
    kwargs = dict(
        repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=agents, test_command=detect(repo),
    )

    def _answer_stop() -> None:
        import time as _t
        for _ in range(500):
            try:
                state = RunState.read(ledger.run_dir / "state.json") or {}
                q = state.get("question") or {}
                if (state.get("status") == "waiting_human") and q.get("id"):
                    (ledger.run_dir / "inbox" / "s.json").write_text(
                        json.dumps({"uuid": "s1", "question_id": q["id"],
                                    "action": "stop", "text": "", "who": "agent"}),
                        encoding="utf-8",
                    )
                    return
            except Exception:
                pass
            _t.sleep(0.01)

    t = threading.Thread(target=_answer_stop, daemon=True)
    t.start()
    outcome = await run_phases(
        run_phase, phases, ledger=lambda: ledger, phase_kwargs=kwargs,
        run_dir=ledger.run_dir, on_pending=lambda: None,
        on_stop=lambda o: None, on_done=lambda s: None,
    )
    t.join(timeout=5)
    assert outcome.exit_code == 2, outcome
    assert not t.is_alive(), "помощник не успел подать stop за 5с"
    assert outcome.status == "stop"
    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert state.get("snapshot_ref")
    assert not (repo / "src/greet.py").exists()


@pytest.mark.asyncio
async def test_phase_trailer_and_empty_commit(tmp_path: Path):
    from src.loop import git as git_mod

    repo = _repo(tmp_path)
    base = git_mod.head(repo)
    (repo / "x.txt").write_text("x")
    git_mod.stage_and_diff(repo, base)
    msg = "ship test\n\nTero-Phase: demo/1"
    git_mod.commit(repo, msg)
    assert git_mod.phase_shipped(repo, base, "demo", 1) is True
    base2 = git_mod.head(repo)
    git_mod.commit(repo, "empty\n\nTero-Phase: demo/2", allow_empty=True)
    assert git_mod.phase_shipped(repo, base2, "demo", 2) is True


@pytest.mark.asyncio
async def test_ship_empty_diff_trailer_only_with_decisions(tmp_path: Path):
    import subprocess
    import sys as _sys
    from src.loop import git as git_mod
    from src.loop.gates import TestCommand
    from src.loop.phase import run_phase
    from src.loop.plan import load_phases, load_preamble

    repo = tmp_path / "repo"
    repo.mkdir()

    def _git_here(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    _git_here("init")
    _git_here("config", "user.email", "tero@test")
    _git_here("config", "user.name", "tero")
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    _git_here("add", "-A")
    _git_here("commit", "-m", "init")
    plan_path = tmp_path / "plan_outside.md"
    green = TestCommand(name="noop", run_all=[_sys.executable, "-c", ""], run_files=None)

    def _noop(_role: str, _kw: dict) -> dict:
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    async def _once(decisions: bool):
        plan_path.write_text(PLAN)
        base = git_mod.head(repo)
        agents = FakeAgents(repo, [_noop, judge_returns(ship())])
        ledger = Ledger(new_run_dir(repo, "demo"))
        phases = load_phases(plan_path)
        outcome = await run_phase(
            phases[0], repo=repo, plan_path=plan_path,
            preamble=load_preamble(plan_path), cfg=_cfg(decisions=decisions),
            agents=agents, test_command=green, ledger=ledger,
        )
        return base, outcome, ledger

    base0, out0, _ = await _once(False)
    assert out0.status == "ship"
    assert out0.commit_sha == base0, "без decisions пустой diff коммита не делает"

    base1, out1, ledger1 = await _once(True)
    assert out1.status == "ship"
    assert out1.commit_sha != base1, "с decisions пустой diff даёт пустой коммит"
    assert git_mod.phase_shipped(repo, base1, ledger1.run_dir.name, 1) is True


@pytest.mark.asyncio
async def test_ship_dirty_diff_carries_trailer(tmp_path: Path):
    import subprocess
    import sys as _sys
    from src.loop import git as git_mod
    from src.loop.gates import TestCommand
    from src.loop.phase import run_phase
    from src.loop.plan import load_phases, load_preamble

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    (repo / "plan.md").write_text(PLAN)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tero@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "tero"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    green = TestCommand(name="noop", run_all=[_sys.executable, "-c", ""], run_files=None)
    agents = FakeAgents(repo, [player_writes(repo, "src/greet.py", HELLO), judge_returns(ship())])
    base = git_mod.head(repo)
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    outcome = await run_phase(
        phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=_cfg(decisions=True),
        agents=agents, test_command=green, ledger=ledger,
    )
    assert outcome.status == "ship"
    msg = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout
    assert f"Tero-Phase: {ledger.run_dir.name}/1" in msg
    assert git_mod.phase_shipped(repo, base, ledger.run_dir.name, 1) is True


@pytest.mark.asyncio
async def test_phase_shipped_rejects_inline_mention(tmp_path: Path):
    from src.loop import git as git_mod

    repo = _repo(tmp_path)
    base = git_mod.head(repo)
    (repo / "x.txt").write_text("x")
    git_mod.stage_and_diff(repo, base)
    git_mod.commit(repo, "Example documentation mentions Tero-Phase: demo/7 inline.")
    assert git_mod.phase_shipped(repo, base, "demo", 7) is False


@pytest.mark.asyncio
async def test_f18_secrets_opens_question_keeps_tree(tmp_path: Path, monkeypatch):
    import src.loop.decision_step as ds_mod
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.phase import run_phase
    from src.loop.plan import load_phases, load_preamble
    from src.loop.gates import detect
    from src.loop.state import RunState

    monkeypatch.setattr(ds_mod, "DECISION_POLL_S", 0.01)
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [player_writes(repo, ".env", "SECRET=1"), judge_returns(ship())],
    )
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    outcome = await run_phase(
        phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=_cfg(decisions=True),
        agents=agents, test_command=detect(repo), ledger=ledger,
    )
    assert outcome.status == "waiting_human", outcome
    assert (repo / ".env").exists(), "F18: .env удалён до ответа"
    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert (state.get("question") or {}).get("reason") == "secrets"


@pytest.mark.asyncio
async def test_f19_answer_on_last_round_runs_new_round(tmp_path: Path, monkeypatch):
    import threading
    import src.loop.decision_step as ds_mod
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.plan import load_phases, load_preamble
    from src.loop.gates import detect
    from src.loop.session import run_phases
    from src.loop.phase import run_phase
    from src.loop.state import RunState

    monkeypatch.setattr(ds_mod, "DECISION_POLL_S", 0.01)
    repo = _repo(tmp_path)
    calls: list[str] = []

    def _player(role: str, kwargs: dict) -> dict:
        from tests.loop import fakes as _f
        calls.append(kwargs.get("prompt") or "")
        _f.write_file(repo, "src/greet.py", HELLO)
        return dict(_f.PLAYER_DONE)

    def _judge(role: str, kwargs: dict) -> dict:
        n = sum(1 for c in calls if "judge" in c.lower()) + 1
        if len(calls) == 1:
            return {"verdict": "NEEDS_USER", "previous": [], "findings": [],
                    "user_question": "which greeting?"}
        return {"verdict": "SHIP", "previous": [], "findings": [], "user_question": ""}

    class _Agents:
        async def run_role(self, role: str, **kw: dict):
            if role == "player":
                return _player(role, kw)
            return _judge(role, kw)

    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox_dir(ledger.run_dir, create=True)
    phases = load_phases(repo / "plan.md")
    cfg = _cfg(decisions=True, max_rounds=1)
    kwargs = dict(repo=repo, plan_path=repo / "plan.md",
                  preamble=load_preamble(repo / "plan.md"), cfg=cfg,
                  agents=_Agents(), test_command=detect(repo))

    def _answer() -> None:
        import time as _t
        for _ in range(500):
            try:
                state = RunState.read(ledger.run_dir / "state.json") or {}
                q = state.get("question") or {}
                if state.get("status") == "waiting_human" and q.get("id"):
                    (ledger.run_dir / "inbox" / "a.json").write_text(
                        __import__("json").dumps({"uuid": "a1", "question_id": q["id"],
                                    "action": "answer", "text": "используй sqlite",
                                    "who": "agent"}),
                        encoding="utf-8")
                    return
            except Exception:
                pass
            _t.sleep(0.01)

    t = threading.Thread(target=_answer, daemon=True)
    t.start()
    outcome = await run_phases(
        run_phase, phases, ledger=lambda: ledger, phase_kwargs=kwargs,
        run_dir=ledger.run_dir, on_pending=lambda: None,
        on_stop=lambda o: None, on_done=lambda s: None)
    t.join(timeout=5)
    assert outcome.status == "ok", outcome
    assert not t.is_alive(), "помощник не успел подать answer за 5с"
    assert len([c for c in calls if "используй sqlite" in c]) >= 1, "F19: ответ не попал в новый раунд"


@pytest.mark.asyncio
async def test_hud_debt_icon(tmp_path: Path):
    from src.hud.app import debt_line, hud_done_text
    from src.loop.wait import done_line, status_payload

    assert "сдано с долгом 2" in debt_line(2)
    assert "⚠" in debt_line(2)
    assert hud_done_text("done", 2) == "⚠ сдано с долгом 2"
    assert hud_done_text("done", 0) == "✓ сдано"
    assert done_line("done", [{"phase": 1}, {"phase": 2}]) == "⚠ сдано с долгом 2"
    run_dir = tmp_path / "run"
    (run_dir).mkdir()
    (run_dir / "state.json").write_text('{"status": "done", "debt": [{"phase": 1}, {"phase": 2}]}', encoding="utf-8")
    payload = status_payload(run_dir, tmp_path)
    assert payload["status"] == "done"
    assert payload["done_text"] == "⚠ сдано с долгом 2"

    import subprocess as _sp2
    import sys as _sys2
    proc = _sp2.run(
        [_sys2.executable, "-m", "src.cli_entry", "status",
         "--run", str(run_dir), "--working-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=Path.cwd(),
    )
    assert proc.returncode == 0, proc.stderr
    assert "⚠ сдано с долгом 2" in proc.stdout


def _decision_ctx(repo: Path, ledger, phases, cfg, agents, checkpoint: str):
    from src.loop.phase_ctx import PhaseContext, PhaseState
    from src.loop.state import RunState
    from src.loop.stream import Stream

    return PhaseContext(
        phase=phases[0], total_phases=1, repo=repo, plan_path=repo / "plan.md",
        preamble="", cfg=cfg, agents=agents, test_command=None,
        tester_enabled=False, ledger=ledger, run_dir=ledger.run_dir,
        state_io=RunState(ledger.run_dir / "state.json"),
        stream=Stream(ledger.run_dir / "stream.jsonl"), pulse=None,
        checkpoint=checkpoint, contract="", suite_hashes={}, guarded={},
        link_targets=set(), st=PhaseState(),
    )


@pytest.mark.asyncio
async def test_skip_snapshot_fail_stops_no_debt(tmp_path: Path):
    from src.loop.decision_step import DecisionStep
    from src.loop.git import head as _head
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.plan import load_phases
    from src.loop.state import RunState

    repo = _repo(tmp_path)
    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox_dir(ledger.run_dir, create=True)
    phases = load_phases(repo / "plan.md")
    cfg = _cfg(decisions=True)
    ctx = _decision_ctx(repo, ledger, phases, cfg, FakeAgents(repo, []), _head(repo))
    step = DecisionStep(ctx, lambda reason: None, poll_s=0.01)
    halted = step.halt("stuck")
    assert halted.status == "waiting_human", halted
    qid = (RunState.read(ledger.run_dir / "state.json") or {}).get("question", {}).get("id")
    (ledger.run_dir / "inbox" / "s.json").write_text(
        json.dumps({"uuid": "s1", "question_id": qid, "action": "skip",
                    "text": "", "who": "agent"}), encoding="utf-8")
    resolved = step.resolve()
    assert resolved.status == "stop", resolved
    assert not ((RunState.read(ledger.run_dir / "state.json") or {}).get("debt") or [])


@pytest.mark.asyncio
async def test_skip_rollback_raise_stops_no_debt(tmp_path: Path):
    from src.loop.decision_step import DecisionStep
    from src.loop.git import head as _head
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.plan import load_phases
    from src.loop.state import RunState

    def _boom(reason: str):
        raise RuntimeError("boom")

    repo = _repo(tmp_path)
    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox_dir(ledger.run_dir, create=True)
    phases = load_phases(repo / "plan.md")
    cfg = _cfg(decisions=True)
    ctx = _decision_ctx(repo, ledger, phases, cfg, FakeAgents(repo, []), _head(repo))
    step = DecisionStep(ctx, _boom, poll_s=0.01)
    assert step.halt("stuck").status == "waiting_human"
    qid = (RunState.read(ledger.run_dir / "state.json") or {}).get("question", {}).get("id")
    (ledger.run_dir / "inbox" / "s.json").write_text(
        json.dumps({"uuid": "s1", "question_id": qid, "action": "skip",
                    "text": "", "who": "agent"}), encoding="utf-8")
    assert step.resolve().status == "stop"
    assert not ((RunState.read(ledger.run_dir / "state.json") or {}).get("debt") or [])


@pytest.mark.asyncio
async def test_skip_debt_reason_consistent_state_stream_ledger(tmp_path: Path):
    import threading

    import src.loop.decision_step as ds_mod
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.plan import load_phases, load_preamble
    from src.loop.gates import detect
    from src.loop.session import run_phases
    from src.loop.phase import run_phase
    from src.loop.state import RunState

    monkeypatch_now = ds_mod.DECISION_POLL_S
    ds_mod.DECISION_POLL_S = 0.01
    try:
        repo = _repo(tmp_path)
        (repo / "plan.md").write_text(TWO_PHASE_PLAN)
        import subprocess as _git
        _git.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        _git.run(["git", "commit", "-m", "two phases"], cwd=repo,
                 check=True, capture_output=True)
        stuck = no_ship([finding(problem="still broken")],
                        previous=[{"id": "F1", "state": "still_open"}])
        agents = FakeAgents(
            repo,
            [
                player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
                judge_returns(no_ship([finding(problem="still broken")])),
                player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
                judge_returns(stuck),
                player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
                judge_returns(stuck),
                player_writes(repo, "src/second.py", "x = 1\n"),
                judge_returns(ship()),
            ],
        )
        ledger = Ledger(new_run_dir(repo, "demo"))
        inbox_dir(ledger.run_dir, create=True)
        phases = load_phases(repo / "plan.md")
        kwargs = dict(repo=repo, plan_path=repo / "plan.md",
                      preamble=load_preamble(repo / "plan.md"),
                      cfg=_cfg(decisions=True), agents=agents,
                      test_command=detect(repo))

        def _answer_skip() -> None:
            import time as _t
            for _ in range(500):
                try:
                    state = RunState.read(ledger.run_dir / "state.json") or {}
                    q = state.get("question") or {}
                    if state.get("status") == "waiting_human" and q.get("id"):
                        (ledger.run_dir / "inbox" / "a.json").write_text(
                            json.dumps({"uuid": "a1", "question_id": q["id"],
                                        "action": "skip", "text": "", "who": "agent"}),
                            encoding="utf-8")
                        return
                except Exception:
                    pass
                _t.sleep(0.01)

        t = threading.Thread(target=_answer_skip, daemon=True)
        t.start()
        outcome = await run_phases(
            run_phase, phases, ledger=lambda: ledger, phase_kwargs=kwargs,
            run_dir=ledger.run_dir, on_pending=lambda: None,
            on_stop=lambda o: None, on_done=lambda s: None)
        t.join(timeout=5)
        assert outcome.exit_code == 4, outcome
        assert not t.is_alive(), "помощник не успел подать skip за 5с"
        state = RunState.read(ledger.run_dir / "state.json") or {}
        assert state.get("status") == "done"
        rows = [json.loads(line) for line in
                (ledger.run_dir / "ledger.jsonl").read_text().splitlines()
                if line.strip()]
        dones = [r for r in rows if r.get("kind") == "done" or r.get("status") == "done"]
        assert dones, "нет done в ledger"
        assert state.get("reason") == dones[-1].get("reason") == "debt"
    finally:
        ds_mod.DECISION_POLL_S = monkeypatch_now


@pytest.mark.asyncio
async def test_done_text_empty_when_not_done(tmp_path: Path):
    from src.hud.app import hud_done_text
    from src.loop.wait import done_line

    assert hud_done_text("running", 0) == ""
    assert hud_done_text("waiting_human", 0) == ""
    assert hud_done_text("stopped", 5) == ""
    assert done_line("running", []) == ""


@pytest.mark.asyncio
async def test_run_phase_accepts_str_plan_path(tmp_path: Path):
    from src.loop.plan import load_phases, load_preamble
    from src.loop.phase import run_phase

    repo = _repo(tmp_path)
    agents = FakeAgents(repo, [player_writes(repo, "src/greet.py", HELLO), judge_returns(ship())])
    ledger = Ledger(new_run_dir(repo, "demo"))
    outcome = await run_phase(
        load_phases(repo / "plan.md")[0], repo=str(repo), plan_path=str(repo / "plan.md"),
        preamble=load_preamble(repo / "plan.md"), cfg=_cfg(), agents=agents,
        test_command=detect(repo), ledger=ledger,
    )
    assert outcome.status == "ship", outcome


@pytest.mark.asyncio
async def test_stop_texts_without_decisions_are_legacy(tmp_path: Path):
    from src.loop.decision_step import DecisionStep
    from src.loop.git import head as _head
    from src.loop.plan import load_phases

    repo = _repo(tmp_path)
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    ctx = _decision_ctx(repo, ledger, phases, _cfg(), FakeAgents(repo, []), _head(repo))
    step = DecisionStep(ctx, lambda reason: "refs/tero/x", round_limit=lambda: 8)
    assert step.halt_text("stuck", "r") == "нет прогресса → откат фазы, работа в `r`"
    assert step.halt_text("fuse", None) == "лимит раундов (8) исчерпан → откат фазы"
    assert step.halt_text("secrets", None) == "в дифе файлы-секреты → откат фазы"
    assert step.halt_text("provider_unavailable", None) == "провайдер недоступен → откат фазы"


@pytest.mark.asyncio
async def test_decision_git_ops_hold_owner_lock(tmp_path: Path):
    from src.loop.decision_step import DecisionStep
    from src.loop.git import head as _head
    from src.loop.plan import load_phases
    from src.loop.state import owner_lock

    repo = _repo(tmp_path)
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    ctx = _decision_ctx(repo, ledger, phases, _cfg(), FakeAgents(repo, []), _head(repo))
    lock = owner_lock(ledger.run_dir / "state.json")
    held = []

    def rollback(reason):
        held.append(lock._is_owned())
        return None

    DecisionStep(ctx, rollback).halt("stuck")
    assert held == [True]


# --- фаза 3: more, model, retry, allow, wait и бюджет ---------------------


def _answerer(run_dir: Path, plan: list[list[dict]]):
    """Поток: на каждый новый открытый вопрос кладёт свою пачку ответов."""
    import threading
    import time as _t

    from src.loop.state import RunState

    seen: list[str] = []

    def _go() -> None:
        n, stuck_since, stopped = 0, 0.0, set()
        for _ in range(6000):
            state = RunState.read(run_dir / "state.json") or {}
            q = state.get("question") or {}
            qid = q.get("id")
            waiting = state.get("status") == "waiting_human" and qid
            if waiting and (qid in seen or n >= len(plan)) and qid not in stopped:
                # Страховка: вопрос без принятого ответа → stop, тест падает, а не висит.
                stuck_since = stuck_since or _t.monotonic()
                if _t.monotonic() - stuck_since > 2:
                    stopped.add(qid)
                    (run_dir / "inbox" / f"zz-{qid}.json").write_text(json.dumps(
                        {"uuid": f"zz-{qid}", "question_id": qid, "action": "stop",
                         "who": "human"}), encoding="utf-8")
                _t.sleep(0.005)
                continue
            stuck_since = 0.0
            if n >= len(plan) and not waiting:
                if state.get("status") in ("done", "stopped"):
                    return
            if waiting and qid not in seen and n < len(plan):
                seen.append(qid)
                for i, payload in enumerate(plan[n]):
                    body = {"uuid": f"u{n}x{i}", "question_id": qid,
                            "text": "", "who": "agent", **payload}
                    (run_dir / "inbox" / f"{n:02d}{i}.json").write_text(
                        json.dumps(body), encoding="utf-8")
                n += 1
            _t.sleep(0.005)

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t, seen


async def _drive(repo: Path, agents, cfg: Config, plan: list[list[dict]], monkeypatch):
    import src.loop.decision_step as ds_mod
    from src.loop.inbox import inbox_dir
    from src.loop.phase import run_phase
    from src.loop.plan import load_phases, load_preamble
    from src.loop.session import run_phases

    monkeypatch.setattr(ds_mod, "DECISION_POLL_S", 0.005)
    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox_dir(ledger.run_dir, create=True)
    kwargs = dict(
        repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=agents, test_command=detect(repo),
    )
    t, seen = _answerer(ledger.run_dir, plan)
    outcome = await run_phases(
        run_phase, load_phases(repo / "plan.md"), ledger=lambda: ledger,
        phase_kwargs=kwargs, run_dir=ledger.run_dir, on_pending=lambda: None,
        on_stop=lambda o: None, on_done=lambda s: None,
    )
    t.join(timeout=5)
    return outcome, ledger, seen


def _stream(ledger: Ledger) -> list[dict]:
    path = ledger.run_dir / "stream.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _stuck_script(repo: Path, rounds: int) -> list:
    stuck = no_ship([finding(problem="still broken")],
                    previous=[{"id": "F1", "state": "still_open"}])
    steps = [
        player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        judge_returns(no_ship([finding(problem="still broken")])),
    ]
    for _ in range(rounds - 1):
        steps += [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(stuck),
        ]
    return steps


@pytest.mark.asyncio
async def test_more_gives_five_rounds_without_stuck(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    agents = FakeAgents(repo, _stuck_script(repo, 8))
    cfg = _cfg(decisions=True, max_rounds=4, escalation_provider="",
               escalation_model="", escalation_effort="")
    outcome, ledger, seen = await _drive(
        repo, agents, cfg, [[{"action": "more"}], [{"action": "stop"}]], monkeypatch)
    assert outcome.exit_code == 2, outcome
    questions = [e for e in _stream(ledger) if e.get("type") == "question"]
    reasons = [e.get("reason") for e in questions]
    assert reasons == ["stuck", "fuse"], reasons
    first = questions[0].get("round")
    assert first and first < cfg.max_rounds
    assert questions[1].get("round") == first + 5, "more: ровно 5 раундов, лимит сдвинут"
    assert agents.calls.count("player") == first + 5, agents.calls


@pytest.mark.asyncio
async def test_model_switches_next_round(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    used: list[tuple[str, str]] = []

    def _player(role: str, kwargs: dict) -> dict:
        used.append((kwargs.get("provider"), kwargs.get("model")))
        scripted.write_file(repo, "src/greet.py", HELLO)
        return scripted.PLAYER_DONE

    script = _stuck_script(repo, 3) + [_player, judge_returns(ship())]
    agents = FakeAgents(repo, script)
    cfg = _cfg(decisions=True, escalation_provider="", escalation_model="",
               escalation_effort="")
    plan = [[{"action": "model", "text": "player nosuch:y"},
             {"action": "model", "text": "player opencode:x"}]]
    outcome, ledger, seen = await _drive(repo, agents, cfg, plan, monkeypatch)
    assert outcome.exit_code == 0, outcome
    assert used == [("opencode", "x")]
    rejected = (ledger.run_dir / "inbox" / "u0x0.result").read_text(encoding="utf-8")
    assert "неизвестный провайдер" in rejected
    accepted = (ledger.run_dir / "inbox" / "u0x1.result").read_text(encoding="utf-8")
    assert accepted == "принято"


@pytest.mark.asyncio
async def test_secrets_allow_and_retry(tmp_path: Path, monkeypatch):
    from src.loop.state import RunState

    repo = _repo(tmp_path)

    def _player_example(role: str, kwargs: dict) -> dict:
        scripted.write_file(repo, "src/greet.py", HELLO)
        scripted.write_file(repo, ".env.example", "KEY=\n")
        return scripted.PLAYER_DONE

    agents = FakeAgents(repo, [_player_example, judge_returns(ship()), judge_returns(ship())])
    plan = [[{"action": "allow", "text": ".env.example", "who": "human"}]]
    outcome, ledger, _ = await _drive(repo, agents, _cfg(decisions=True), plan, monkeypatch)
    assert outcome.exit_code == 0, outcome
    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert ".env.example" in (state.get("allowed_secrets") or [])
    files = scripted.git(repo, "show", "--name-only", "--format=", "HEAD")
    assert ".env.example" in files.split()
    assert agents.calls == ["player", "judge", "judge"], agents.calls

    (tmp_path / "second").mkdir()
    repo2 = _repo(tmp_path / "second")
    prompts: list[str] = []

    def _player_env(role: str, kwargs: dict) -> dict:
        prompts.append(kwargs.get("prompt") or "")
        scripted.write_file(repo2, "src/greet.py", HELLO)
        scripted.write_file(repo2, ".env", "SECRET=1\n")
        return scripted.PLAYER_DONE

    def _player_clean(role: str, kwargs: dict) -> dict:
        prompts.append(kwargs.get("prompt") or "")
        assert not (repo2 / ".env").exists(), "retry после secrets: дерево откачено"
        scripted.write_file(repo2, "src/greet.py", HELLO)
        return scripted.PLAYER_DONE

    agents2 = FakeAgents(repo2, [_player_env, judge_returns(ship()),
                                 _player_clean, judge_returns(ship())])
    outcome2, ledger2, _ = await _drive(
        repo2, agents2, _cfg(decisions=True), [[{"action": "retry"}]], monkeypatch)
    assert outcome2.exit_code == 0, outcome2
    assert "не создавать" not in prompts[0]
    assert "не создавать" in prompts[1] and ".env" in prompts[1]
    snaps = scripted.git(repo2, "for-each-ref", "--format=%(refname)", "refs/tero")
    assert snaps.strip(), "retry после secrets: снимок сделан"


@pytest.mark.asyncio
async def test_wait_backoff_then_question(tmp_path: Path, monkeypatch):
    import src.loop.decision_actions as da

    monkeypatch.setattr(da, "LIMIT_WAITS_S", (0.01, 0.02, 0.03))
    pauses: list[float] = []
    real_sleep = da.time.sleep
    monkeypatch.setattr(da, "_limit_sleep", lambda s: (pauses.append(s), real_sleep(0))[1])
    repo = _repo(tmp_path)

    def _quota(role: str, kwargs: dict) -> dict:
        return {"__unavailable__": True, "error": "usage limit", "kind": "quota"}

    agents = FakeAgents(repo, [_quota] * 4)
    plan = [[{"action": "wait"}], [{"action": "stop"}]]
    outcome, ledger, seen = await _drive(repo, agents, _cfg(decisions=True), plan, monkeypatch)
    assert outcome.exit_code == 2, outcome
    events = _stream(ledger)
    waits = [e for e in events if e.get("status") == "waiting_limit"]
    assert len(waits) == 3, [e.get("type") for e in events]
    assert pauses == [0.01, 0.02, 0.03]
    assert seen == ["1-1", "1-2"]
    assert agents.calls == ["player"] * 4


def test_budget_three_per_phase(tmp_path: Path):
    from src.loop.decision_actions import question_actions
    from src.loop.inbox import Inbox
    from src.loop.state import RunState
    from src.relay.iface import FakeRelay

    run_dir = tmp_path / "run"
    (run_dir / "inbox").mkdir(parents=True)
    io = RunState(run_dir / "state.json")
    actions = ["retry", "wait", "model", "stop"]

    def _ask(qid: str, who: str) -> str:
        io.update(question={"id": qid, "reason": "provider_quota", "text": "q",
                            "actions": actions})
        uid = f"{qid}-{who}"
        (run_dir / "inbox" / f"{uid}.json").write_text(json.dumps(
            {"uuid": uid, "question_id": qid, "action": "retry", "who": who}),
            encoding="utf-8")
        Inbox(run_dir).take(qid, FakeRelay())
        return (run_dir / "inbox" / f"{uid}.result").read_text(encoding="utf-8")

    assert [_ask(f"1-{i}", "agent") for i in (1, 2, 3)] == ["принято"] * 3
    assert "бюджет исчерпан" in _ask("1-4", "agent")
    assert _ask("1-5", "human") == "принято"
    assert _ask("2-1", "agent") == "принято", "бюджет — на фазу"
    state = RunState.read(run_dir / "state.json") or {}
    assert state.get("budget", {}).get("1") == 3
    assert question_actions("stuck", state, 1) == ["skip", "stop"]
    assert question_actions("stuck", state, 2) == ["more", "model", "skip", "stop"]
    assert question_actions("judge_pending", state, 2) == []
