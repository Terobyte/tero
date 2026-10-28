"""Фаза 08-11: шип с долгом и Opus-решение (красный тест контракта)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.loop.state import RunState


PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir(parents=True, exist_ok=True)
    RunState(d / "state.json").update(status="running")
    return d


def _repo(tmp_path: Path) -> Path:
    from tests.loop import fakes as scripted

    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides):
    from src.config import Config

    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30,
            "escalation_provider": "claude", "escalation_model": "opus"}
    data.update(overrides)
    return Config(**data)


async def _run(repo: Path, agents, cfg=None):
    from src.loop.gates import detect
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.phase import run_phase
    from src.loop.plan import load_phases, load_preamble

    cfg = cfg or _cfg(escalation="situational", decisions=True)
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    outcome = await run_phase(
        phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=agents, test_command=detect(repo), ledger=ledger,
    )
    return outcome, ledger


def _finding(i: int = 1, level: str | None = "minor"):
    return {"id": None, "severity": "blocking", "level": level, "route": "player",
            "file": "src/greet.py", "line": i, "problem": f"nit {i}", "expected": "polish it"}


def test_polish_build_entries_unit(tmp_path: Path):
    from src.loop import debt as debt_mod

    run_dir = _run_dir(tmp_path)
    entries = debt_mod.build_entries(phase=0, round_no=3, findings=[_finding(1), _finding(2)], why="polish")
    assert len(entries) == 2 and all(e["why"] == "polish" for e in entries)
    debt_mod.append_debt(run_dir, entries)
    state = RunState.read(run_dir / "state.json") or {}
    assert state.get("debt") == entries
    md = (run_dir / "debt.md").read_text(encoding="utf-8")
    assert "nit 1" in md and "nit 2" in md
    # путь шипа: зелёный гейт — можно шипать, красный — раунд продолжается
    assert debt_mod.should_ship(gate_ok=True) is True
    assert debt_mod.should_ship(gate_ok=False) is False


def test_opus_decide_ship_unit(tmp_path: Path):
    import asyncio

    from src.loop import debt as debt_mod
    from src.loop.decision import opus as opus_mod

    async def fake_run_role(role: str, **kw):
        assert role == "decider"
        assert kw.get("provider") == "claude" and kw.get("model") == "opus"
        return {"decision": "ship_with_debt", "why": "m minor"}

    class FakeAgents:
        async def run_role(self, role: str, **kw):
            return await fake_run_role(role, **kw)

    res = asyncio.run(opus_mod.decide(run_dir=_run_dir(tmp_path), agents=FakeAgents(), prompt="x"))
    assert res == {"decision": "ship_with_debt", "why": "m minor"}
    run_dir = _run_dir(tmp_path)
    entries = debt_mod.build_entries(phase=0, round_no=4, findings=[_finding(1)], why="opus")
    debt_mod.append_debt(run_dir, entries)
    assert (RunState.read(run_dir / "state.json") or {}).get("debt")[0]["why"] == "opus"


def test_opus_more_note_unit(tmp_path: Path):
    import asyncio

    from src.loop.decision import opus as opus_mod
    from src.loop import situation_step as step_mod

    async def fake_more(role: str, **kw):
        return {"decision": "more", "why": "try again"}

    class FakeAgents:
        async def run_role(self, role: str, **kw):
            return await fake_more(role, **kw)

    run_dir = _run_dir(tmp_path)
    res = asyncio.run(opus_mod.decide(run_dir=run_dir, agents=FakeAgents(), prompt="x"))
    assert res["decision"] == "more"
    # more пишет opus_round в историю situation[N]
    hist = step_mod.note_opus_round(run_dir, phase_index=0, round_no=5)
    assert hist.get("opus_round") == 5
    # повторный mole после opus_round -> ask_owner (решает diagnose)
    from src.loop import situation as sit_mod

    def blk(n):
        return {"round": n, "n_findings": 1, "resolved": 0, "still_open": 0,
                "sev": [["blocker", 0.9]], "player": "muse"}
    rounds = [blk(n) for n in range(6, 11)]
    d = sit_mod.diagnose(rounds, sit_mod.Cfg(), opus_round=5)
    assert d["action"] == "ask_owner"
    # ответ more хозяина пишет owner_round
    hist2 = step_mod.note_owner_round(run_dir, phase_index=0, round_no=8)
    assert hist2.get("owner_round") == 8


def test_opus_failure_unit(tmp_path: Path):
    import asyncio

    from src.loop.decision import opus as opus_mod

    class Boom:
        async def run_role(self, role: str, **kw):
            raise RuntimeError("no CLI")

    run_dir = _run_dir(tmp_path)
    res = asyncio.run(opus_mod.decide(run_dir=run_dir, agents=Boom(), prompt="x"))
    assert res["decision"] == "ask_owner"

    class Garbage:
        async def run_role(self, role: str, **kw):
            return "not json at all {{{"

    res2 = asyncio.run(opus_mod.decide(run_dir=run_dir, agents=Garbage(), prompt="x"))
    assert res2["decision"] == "ask_owner"


def test_stop_during_opus(tmp_path: Path):
    import asyncio

    from src.loop.decision import opus as opus_mod

    async def _main():
        started = asyncio.Event()
        release = asyncio.Event()
        saw_cancel = asyncio.Event()
        flag = {"stop": False}

        class Slow:
            async def run_role(self, role: str, **kw):
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    saw_cancel.set()
                    raise
                return {"decision": "more", "why": "late"}

        async def _watch():
            await started.wait()
            flag["stop"] = True

        watcher = asyncio.ensure_future(_watch())
        try:
            res = await asyncio.wait_for(
                opus_mod.decide(run_dir=_run_dir(tmp_path), agents=Slow(),
                                prompt="x", stop_checker=lambda: flag["stop"]),
                timeout=10)
        finally:
            watcher.cancel()
        assert res is None
        # stop пришёл пока Opus висел: вызов отменён до освобождения события
        assert saw_cancel.is_set()
        assert not release.is_set()

    asyncio.run(_main())


def test_debt_write_failure_rolls_back(tmp_path: Path):
    import src.loop.debt as debt_mod

    run_dir = _run_dir(tmp_path)
    entries = debt_mod.build_entries(
        phase=0, round_no=3, findings=[_finding(1)], why="polish")

    def _boom(path, text, **kw):
        raise IOError("disk full")

    real = debt_mod.atomic_write_text
    debt_mod.atomic_write_text = _boom
    try:
        try:
            debt_mod.append_debt(run_dir, entries)
        except IOError:
            pass
        else:
            raise AssertionError("expected IOError")
    finally:
        debt_mod.atomic_write_text = real
    state = RunState.read(run_dir / "state.json") or {}
    assert (state.get("debt") or []) == []
    assert not (run_dir / "debt.md").exists()


def test_ordinary_ship_after_skip_debt_not_blocked(tmp_path: Path):
    # Регресс раунда 5: долг skip есть только в state.json — обычный SHIP
    # следующей фазы без своего долга проходит, а не крутит раунды вечно.
    from types import SimpleNamespace

    from src.loop import debt as debt_mod
    from src.loop import ship_debt as ship_mod

    run_dir = _run_dir(tmp_path)
    RunState(run_dir / "state.json").update(
        debt=[{"phase": 1, "reason": "stuck", "problem": "skip"}])
    assert debt_mod.verify_debt(run_dir, []) is True
    ctx = SimpleNamespace(run_dir=run_dir, ledger=None, emit=lambda *a, **k: None,
                          phase=SimpleNamespace(index=1, title="Second"))
    st = SimpleNamespace(round=1, debt_pending=[])
    assert ship_mod.commit_before_ship(SimpleNamespace(ctx=ctx, state=st)) is True


def test_commit_pending_keeps_item_when_write_silently_diverges(tmp_path: Path, monkeypatch):
    # F10/F15: append_debt can return normally (no exception) while the
    # persisted content diverges from what was actually owed (e.g. a
    # partial/corrupted write that doesn't raise). commit_pending must not
    # clear debt_pending on a bare absence-of-exception — it must also
    # confirm via verify_debt that what was owed is what landed, and keep
    # the item pending (so a later ordinary SHIP with debt_pending == []
    # can't be let through on the strength of a mismatch nobody caught).
    from types import SimpleNamespace

    from src.loop import debt as debt_mod
    from src.loop import ship_debt as ship_mod

    run_dir = _run_dir(tmp_path)
    entries = debt_mod.build_entries(
        phase=0, round_no=3, findings=[_finding(1)], why="polish")

    def _silent_divergence(run_dir, entries):
        # Persists state.json honestly, but never writes debt.md — and,
        # unlike the real append_debt, does not raise for it.
        current = RunState.read(Path(run_dir) / "state.json") or {}
        prev = list(current.get("debt") or [])
        RunState(Path(run_dir) / "state.json").update(debt=prev + list(entries))
        return prev + list(entries)

    monkeypatch.setattr(debt_mod, "append_debt", _silent_divergence)

    class _Ledger:
        def event(self, *args, **kwargs) -> None:
            pass

    ctx = SimpleNamespace(run_dir=run_dir, ledger=_Ledger(),
                          emit=lambda *a, **k: None,
                          phase=SimpleNamespace(index=0, title="Add greet"))
    st = SimpleNamespace(round=3, debt_pending=[
        {"why": "polish", "entries": [dict(e) for e in entries]}])
    runner = SimpleNamespace(ctx=ctx, state=st)
    # first attempt: debt.md never got the entries -> must be blocked, and
    # must NOT silently drop the debt from debt_pending.
    assert ship_mod.commit_before_ship(runner) is False
    assert st.debt_pending, "debt must stay pending after a failed verify"
    # the next SHIP of the same phase retries the pending debt, not skips it
    assert ship_mod.commit_before_ship(runner) is False
    assert st.debt_pending


def _minor(level: str | None = "minor"):
    from tests.loop import fakes as scripted

    return scripted.finding(level=level)


@pytest.mark.asyncio
async def test_polish_ships_through_gate(tmp_path: Path):
    import subprocess
    import sys

    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    n1 = scripted.finding(problem="nit a", level="minor")
    n2 = scripted.finding(problem="nit b", level="nit")
    agents = scripted.FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        scripted.judge_returns(scripted.no_ship([n1])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'b'\n"),
        scripted.judge_returns(scripted.no_ship([n2], previous=[{"id": "F1", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
        scripted.judge_returns(scripted.no_ship([scripted.finding(problem="nit c", level="nit")],
                                               previous=[{"id": "F2", "state": "resolved"}])),
    ])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    trailer = subprocess.run(["git", "log", "-1", "--format=%(trailers)"],
                             cwd=repo, capture_output=True, text=True).stdout
    assert "Tero-Phase" in trailer
    state = RunState.read(ledger.run_dir / "state.json") or {}
    debt = state.get("debt") or []
    assert len(debt) >= 1 and all(e.get("why") == "polish" for e in debt)
    md = (ledger.run_dir / "debt.md").read_text(encoding="utf-8")
    # строгий долг: свежий minor (r1) — ещё раунд, свежий nit (r2) — в долг
    assert "nit b" in md and "nit a" not in md
    # при красном гейте — не шип: та же польская ситуация, но гейт красный
    (tmp_path / "red").mkdir(exist_ok=True)
    repo2 = _repo(tmp_path / "red")
    (repo2 / "tests/test_hold.py").write_text("def test_hold():\n    assert False\n")
    subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_hold.py"],
                   cwd=repo2, capture_output=True)
    agents2 = scripted.FakeAgents(repo2, [
        scripted.player_writes(repo2, "src/greet.py", "def greet():\n    return 'a'\n"),
        scripted.judge_returns(scripted.no_ship([scripted.finding(problem="nit a", level="minor")])),
        scripted.player_writes(repo2, "src/greet.py", "def greet():\n    return 'b'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="nit b", level="nit")],
            previous=[{"id": "F1", "state": "resolved"}])),
        scripted.player_writes(repo2, "src/greet.py", "def greet():\n    return 'hello'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="nit c", level="nit")],
            previous=[{"id": "F2", "state": "resolved"}])),
        scripted.player_writes(repo2, "src/greet.py", "def greet():\n    return 'hello'\n"),
        scripted.judge_returns(scripted.ship()),
    ])
    outcome2, ledger2 = await _run(repo2, agents2)
    assert outcome2.status != "ship", outcome2
    state2 = RunState.read(ledger2.run_dir / "state.json") or {}
    assert not (state2.get("commit") or "")
    # F13: красный гейт не оставил долга ни в state.json, ни в debt.md.
    assert (state2.get("debt") or []) == []
    debt_md2 = ledger2.run_dir / "debt.md"
    assert not debt_md2.exists() or "nit" not in debt_md2.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_opus_ship_with_debt(tmp_path: Path):
    import subprocess

    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    agents = scripted.FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        scripted.judge_returns(scripted.no_ship([scripted.finding(problem="mole one")], previous=[])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'b'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole two")],
            previous=[{"id": "F1", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'c'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole three")],
            previous=[{"id": "F2", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'd'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole four")],
            previous=[{"id": "F3", "state": "resolved"}])),
    ])
    opus = {"decision": "ship_with_debt", "why": "мелочи в долг"}
    real_run_role = agents.run_role

    async def routed(role: str, **kw):
        if role == "decider":
            assert kw.get("provider") == "claude" and kw.get("model") == "opus"
            return dict(opus)
        return await real_run_role(role, **kw)

    agents.run_role = routed
    outcome, ledger = await _run(repo, agents)
    # строгий долг: находка без уровня в долг не идёт даже по слову решателя → more
    assert outcome.status != "ship", outcome
    _ = subprocess
    state = RunState.read(ledger.run_dir / "state.json") or {}
    assert (state.get("debt") or []) == []
    kinds = [__import__("json").loads(l)["kind"]
             for l in ledger.path.read_text().splitlines()]
    assert "decider" in kinds


@pytest.mark.asyncio
async def test_opus_more_then_owner(tmp_path: Path):
    # Opus more: ещё раунд игрока плана (через run_phase), opus_round в истории.
    # Второй mole после opus_round -> diagnose даёт ask_owner.
    from src.loop import situation as sit_mod
    from src.loop import situation_step as step_mod
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    opus_answers = iter([{"decision": "more", "why": "try again"}])
    agents = scripted.FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole one")], previous=[])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'b'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 2")],
            previous=[{"id": "F1", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'c'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 3")],
            previous=[{"id": "F2", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'd'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 4")],
            previous=[{"id": "F3", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'e'\n"),
        scripted.judge_returns(scripted.ship()),
    ])
    real_run_role = agents.run_role

    async def routed(role: str, **kw):
        if role == "decider":
            return dict(next(opus_answers))
        return await real_run_role(role, **kw)

    agents.run_role = routed
    outcome, ledger = await _run(repo, agents, _cfg(
        escalation="situational", decisions=True, max_rounds=8))
    # more на r4 (первый mole): ещё раунд, opus_round=4, затем SHIP
    assert outcome.status == "ship", outcome
    state = RunState.read(ledger.run_dir / "state.json") or {}
    hist = state.get("situation[0]") or {}
    assert hist.get("opus_round") == 4, hist
    # второй mole после opus_round (раунды 5..8) -> ask_owner по diagnose
    def blk(n):
        return {"round": n, "n_findings": 1, "resolved": 0, "still_open": 0,
                "sev": [["blocker", 0.9]], "player": "muse"}
    d = sit_mod.diagnose([blk(n) for n in range(5, 10)], sit_mod.Cfg(), opus_round=4)
    assert d["action"] == "ask_owner", d
    # ответ more хозяина пишет owner_round
    hist2 = step_mod.note_owner_round(ledger.run_dir, phase_index=0, round_no=8)
    assert hist2.get("owner_round") == 8
    # точка решения stuck + ответ more хозяина -> owner_round в истории
    from unittest.mock import MagicMock as _MM

    from src.loop.decision import actions as _da
    run_dir = ledger.run_dir
    step = _MM()
    step.ctx.run_dir = run_dir
    step.ctx.st.round = 8
    step.ctx.phase.index = 0
    out = _da._more(step, "stuck", "1-1", _MM(text="", who="human"))
    assert out.status == "continue", out
    state3 = RunState.read(run_dir / "state.json") or {}
    assert (state3.get("situation[0]") or {}).get("owner_round") == 8


@pytest.mark.asyncio
async def test_opus_more_then_owner_second_mole(tmp_path: Path):
    # F16/F17: второй mole после Opus more — та же фаза доходит до точки
    # решения stuck без второго decider; more хозяина пишет owner_round.
    from unittest.mock import MagicMock as _MM

    from src.loop.decision import actions as _da
    from src.loop import situation_step as step_mod
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    opus_answers = iter([{"decision": "more", "why": "try again"}])
    agents = scripted.FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole one")], previous=[])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'b'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 2")],
            previous=[{"id": "F1", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'c'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 3")],
            previous=[{"id": "F2", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'd'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 4")],
            previous=[{"id": "F3", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'e'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 5")],
            previous=[{"id": "F4", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'f'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 6")],
            previous=[{"id": "F5", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'g'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 7")],
            previous=[{"id": "F6", "state": "resolved"}])),
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'h'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole 8")],
            previous=[{"id": "F7", "state": "resolved"}])),
    ])
    real_run_role = agents.run_role
    decider_calls = {"n": 0}

    async def routed(role: str, **kw):
        if role == "decider":
            decider_calls["n"] += 1
            return dict(next(opus_answers))
        return await real_run_role(role, **kw)

    agents.run_role = routed
    outcome, ledger = await _run(repo, agents, _cfg(
        escalation="situational", decisions=True, max_rounds=12))
    # второй mole той же фазы после opus_round=4: ask_owner → точка stuck,
    # второго вызова decider нет.
    assert outcome.status == "waiting_human", outcome
    assert outcome.reason == "stuck", outcome
    assert decider_calls["n"] == 1
    hist = (RunState.read(ledger.run_dir / "state.json") or {}).get("situation[0]") or {}
    assert hist.get("opus_round") == 4, hist
    assert hist.get("owner_round") is None, hist
    # ответ more хозяина → owner_round в истории.
    step = _MM()
    step.ctx.run_dir = ledger.run_dir
    step.ctx.st.round = outcome.rounds
    step.ctx.phase.index = 0
    out = _da._more(step, "stuck", "1-1", _MM(text="", who="human"))
    assert out.status == "continue", out
    state2 = RunState.read(ledger.run_dir / "state.json") or {}
    owner_round = (state2.get("situation[0]") or {}).get("owner_round")
    assert owner_round == outcome.rounds, state2.get("situation[0]")


@pytest.mark.asyncio
async def test_opus_failure_asks_owner_e2e(tmp_path: Path):
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    agents = scripted.FakeAgents(repo, [
        scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem="mole one")], previous=[])),
    ])
    for i, prev in [(2, "F1"), (3, "F2"), (4, "F3"), (5, "F4")]:
        agents.steps.append(scripted.player_writes(
            repo, "src/greet.py", f"def greet():\n    return '{i}'\n"))
        agents.steps.append(scripted.judge_returns(scripted.no_ship(
            [scripted.finding(problem=f"mole {i}")],
            previous=[{"id": prev, "state": "resolved"}])))

    async def boom(role: str, **kw):
        if role == "decider":
            raise RuntimeError("no CLI")
        return await scripted.FakeAgents.run_role(agents, role, **kw)

    agents.run_role = boom
    outcome, ledger = await _run(repo, agents)
    assert outcome.status in ("waiting_human", "stop"), outcome
    if outcome.status == "stop":
        assert outcome.reason == "stuck", outcome
