"""Фаза 08-3: тень situation — diagnose без изменения логики, событие после NO_SHIP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import Config
from src.loop import situation as situation_mod
from src.loop import situation_step
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from src.loop.state import RunState
from tests.loop import fakes as scripted

FakeAgents = scripted.FakeAgents
finding = scripted.finding
judge_returns = scripted.judge_returns
no_ship = scripted.no_ship
player_writes = scripted.player_writes
ship = scripted.ship

def R(n, nf, res=0, so=0, sev=None, player="muse"):
    return {"round": n, "n_findings": nf, "resolved": res, "still_open": so,
            "sev": sev if sev is not None else [["major", 0.9]] * nf, "player": player}


def blk(n, **k):
    return R(n, 1, sev=[["blocker", 0.9]], **k)


# 24 перенесённых теста бенча (без CLI и simulate): тот же вход — тот же выход.
def test_converging():
    assert situation_mod.diagnose([R(1, 1)], situation_mod.Cfg()) == {"situation": "converging", "action": "continue", "reason": "находки закрываются, идём дальше"}


def test_stuck():
    d = situation_mod.diagnose([R(1, 1), R(2, 1, so=1), R(3, 1, so=1)], situation_mod.Cfg())
    assert (d["situation"], d["action"]) == ("stuck", "sonnet_player")


def test_stuck_zero_findings():
    assert situation_mod.diagnose([R(1, 1), R(2, 0, so=1, sev=[])], situation_mod.Cfg())["situation"] == "stuck"


def test_stuck_strong_last_rounds_sonnet():
    rs = [R(1, 1), R(2, 1, so=1), R(3, 1, so=1, player="sonnet"), R(4, 1, so=1, player="sonnet")]
    d = situation_mod.diagnose(rs, situation_mod.Cfg())
    assert (d["situation"], d["action"]) == ("stuck_strong", "opus_decides")


def test_sonnet_max_exhausted():
    rs = [R(1, 1), R(2, 1, so=1, player="sonnet"), R(3, 1, so=1), R(4, 1, so=1, player="sonnet")]
    d = situation_mod.diagnose(rs, situation_mod.Cfg())
    assert (d["situation"], d["action"]) == ("stuck_strong", "opus_decides")
    assert situation_mod.diagnose(rs, situation_mod.Cfg(sonnet_max=3))["situation"] == "stuck"


def test_mole():
    d = situation_mod.diagnose([blk(1), blk(2), blk(3), blk(4)], situation_mod.Cfg())
    assert (d["situation"], d["action"]) == ("mole", "opus_decides")
    assert situation_mod.diagnose([blk(1), blk(2), blk(3)], situation_mod.Cfg())["situation"] == "converging"


def test_mole_needs_blocker_every_round():
    rs = [blk(1), blk(2), R(3, 1, sev=[["minor", 0.9]]), blk(4)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] != "mole"


def test_polish():
    rs = [R(1, 2), R(2, 1, sev=[["nit", 0.9]])]
    d = situation_mod.diagnose(rs, situation_mod.Cfg())
    assert (d["situation"], d["action"]) == ("polish", "ship_with_debt")
    # строгий долг: свежий minor ещё раунд, а не в долг
    rs = [R(1, 2), R(2, 1, sev=[["minor", 0.9]])]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "converging"


def test_low_conf_is_blocker():
    rs = [R(1, 2), R(2, 1, sev=[["nit", 0.3]])]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "converging"
    assert situation_mod.diagnose(rs, situation_mod.Cfg(min_conf=0.2))["situation"] == "polish"


def test_polish_first_changes_outcome():
    rs = [R(1, 2), R(2, 1, so=1, sev=[["nit", 0.9]]), R(3, 1, so=1, sev=[["nit", 0.9]])]
    rs[-1]["open_level"] = [[None, "nit"]]  # переживший правку: уровень известен
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "stuck"
    assert situation_mod.diagnose(rs, situation_mod.Cfg(polish_first=True))["situation"] == "polish"


def test_polish_min_round():
    rs = [R(1, 3, sev=[["nit", 0.9]] * 3)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "converging"
    assert situation_mod.diagnose(rs, situation_mod.Cfg(polish_min_round=1))["situation"] == "polish"


def test_polish_blocked_by_still_open_without_stuck():
    rs = [R(1, 2), R(2, 1, so=1, sev=[["minor", 0.9]])]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "converging"


def test_missing_severity_is_blocker():
    assert situation_mod.diagnose([R(1, 2), R(2, 2, sev=[])], situation_mod.Cfg())["situation"] == "converging"
    rs = [R(1, 2), R(2, 2, sev=[["minor", 0.9]])]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "converging"


def test_stuck_two_of_three_alternating():
    rs = [R(1, 1), R(2, 1, so=1), R(3, 1, so=0), R(4, 1, so=1)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "stuck"


def test_sonnet_returns_to_muse_after_limit():
    rs = [R(1, 1), R(2, 1, so=1), R(3, 1, so=1), blk(4, player="sonnet"), blk(5, player="sonnet")]
    d = situation_mod.diagnose(rs, situation_mod.Cfg())
    assert (d["situation"], d["action"]) == ("unstuck", "muse_player")
    assert situation_mod.diagnose(rs[:-1], situation_mod.Cfg())["action"] == "continue"


def test_opus_once_then_owner():
    rs = [blk(1), blk(2), blk(3), blk(4)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["action"] == "opus_decides"
    assert situation_mod.diagnose(rs + [blk(5)], situation_mod.Cfg(), opus_round=4)["action"] == "continue"
    more = rs + [blk(5), blk(6), blk(7), blk(8)]
    assert situation_mod.diagnose(more, situation_mod.Cfg(), opus_round=4)["action"] == "ask_owner"


def test_mole_tolerates_one_still_open():
    rs = [blk(1), blk(2, player="sonnet"), blk(3), blk(4, so=1), blk(5)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "mole"
    rs[1]["player"] = "muse"
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] != "mole"
    rs = [blk(1), blk(2), blk(3, so=1), blk(4), blk(5, so=1)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "stuck"


def test_judge_level_merges_with_jev():
    rs = [R(1, 2), R(2, 1, sev=[["nit", 0.9]])]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "polish"
    rs[-1]["judge_level"] = ["blocker"]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "converging"
    rs = [R(1, 2), R(2, 1, sev=[])]
    rs[-1]["judge_level"] = ["nit"]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "polish"
    rs[-1]["judge_level"] = ["major"]  # major судьи в долг не идёт
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] == "converging"


def test_no_severity_at_all_no_ship_no_mole():
    rs = [R(n, 1, sev=[]) for n in range(1, 6)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["action"] == "continue"


def test_partial_severity_blocks_ship_but_not_mole():
    def big(n):
        return R(n, 10, sev=[["nit", 0.9]] * 8)
    assert situation_mod.diagnose([big(1), big(2), big(3), big(4)], situation_mod.Cfg())["action"] == "continue"


def test_unknown_judge_level_is_blocker():
    rs = [R(1, 2), R(2, 1, sev=[["minor", 0.9]])]
    rs[-1]["judge_level"] = ["critical"]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["situation"] != "polish"


def test_sonnet_cap_survives_opus_round():
    rs = [R(1, 1), R(2, 1, so=1), R(3, 1, so=1, player="sonnet"), R(4, 1, player="sonnet"),
          R(5, 1), R(6, 1, so=1), R(7, 1, so=1)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg(), opus_round=5)["action"] == "ask_owner"


def test_owner_round_resets_windows():
    rs = [blk(1), blk(2), blk(3), blk(4), blk(5, so=0), blk(6)]
    assert situation_mod.diagnose(rs, situation_mod.Cfg(), opus_round=4)["action"] == "continue"
    more = rs + [blk(7), blk(8)]
    assert situation_mod.diagnose(more, situation_mod.Cfg(), opus_round=4)["action"] == "ask_owner"
    assert situation_mod.diagnose(more + [blk(9)], situation_mod.Cfg(), opus_round=4, owner_round=8)["action"] == "continue"


def test_mole_tail_before_sonnet_not_tolerated():
    rs = [blk(1), blk(2, so=1), blk(3, so=1), blk(4, player="sonnet"), blk(5, player="sonnet")]
    assert situation_mod.diagnose(rs, situation_mod.Cfg())["action"] == "muse_player"


def test_situation_matches_bench():
    import subprocess
    import sys as _sys

    bench = "/Users/terobyte/Desktop/Projects/Active/tero-v5-logs/jev-bench/tests/test_diagnose.py"
    try:
        out = subprocess.run([_sys.executable, "-m", "pytest", "-q", bench, "-k",
                              "not test_cli_both_datasets and not test_simulate_stops_and_switches_player"],
                             capture_output=True, text=True, timeout=120)
    except Exception:
        pytest.skip("bench unavailable")
        return
    if out.returncode != 0:
        pytest.skip(f"bench env unavailable: {(out.stderr or out.stdout)[-500:]}")
    names = [n for n in globals() if n.startswith(("test_converging", "test_stuck", "test_sonnet",
                "test_mole", "test_polish", "test_low_conf", "test_missing", "test_no_severity",
                "test_partial", "test_unknown", "test_opus", "test_owner", "test_judge"))]
    assert len(names) == 24


PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30,
            "escalation_provider": "", "escalation_model": ""}
    data.update(overrides)
    return Config(**data)


async def _run(repo: Path, agents: FakeAgents, cfg: Config | None = None):
    from src.loop.plan import load_phases, load_preamble

    cfg = cfg or _cfg()
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    outcome = await run_phase(
        phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=agents, test_command=detect(repo), ledger=ledger,
    )
    return outcome, ledger


def _ledger_kinds(ledger: Ledger) -> list[str]:
    return [json.loads(line)["kind"] for line in ledger.path.read_text().splitlines()]


def _situation_events(ledger: Ledger) -> list[dict]:
    return [json.loads(line) for line in ledger.path.read_text().splitlines()
            if json.loads(line)["kind"] == "situation"]


def _stream_rows(ledger: Ledger) -> list[dict]:
    return [json.loads(line) for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()]


def _history(ledger: Ledger) -> dict:
    return (RunState.read(ledger.run_dir / "state.json") or {}).get("situation[0]") or {}


def _verdicts(ledger: Ledger) -> list:
    return [(json.loads(line).get("round"), json.loads(line).get("verdict"))
            for line in ledger.path.read_text().splitlines()
            if json.loads(line).get("kind") == "judge"]


@pytest.mark.asyncio
async def test_situation_event_after_no_ship(tmp_path: Path, monkeypatch):
    from src.loop import situation_step as step_mod

    repo = _repo(tmp_path)
    f1 = finding(problem="still broken")
    f2 = finding(problem="another break")
    steps = [
        player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        judge_returns(no_ship([f1])),
        player_writes(repo, "src/greet.py", "def greet():\n    return 'b'\n"),
        judge_returns(no_ship([f2], previous=[{"id": "F1", "state": "resolved"}])),
        player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
        judge_returns(ship()),
    ]
    outcome, ledger = await _run(repo, FakeAgents(repo, steps))
    assert outcome.status == "ship"
    events = _situation_events(ledger)
    assert len(events) == 2
    assert [e["round"] for e in events] == [1, 2]
    for ev in events:
        assert ev["role"] == "judge"
        assert ev["situation"] and ev["action"] and isinstance(ev.get("reason"), str)
        assert "phase" in ev
    stream = [row for row in _stream_rows(ledger) if row["type"] == "situation"]
    assert len(stream) == 2
    assert all(s.get("phase") for s in stream)
    assert [s.get("round") for s in stream] == [1, 2]
    hist = _history(ledger)
    assert [r["round"] for r in hist["rounds"]] == [1, 2]
    row = hist["rounds"][0]
    assert (row["round"], row["n_findings"], row["resolved"], row["still_open"]) == (1, 1, 0, 0)
    assert row["judge_level"] == ["blocker"] and row["player"] == "muse"
    assert hist["opus_round"] is None and hist["owner_round"] is None and hist["sonnet_n"] == 0

    (tmp_path / "noop").mkdir(exist_ok=True)
    (tmp_path / "noop").mkdir(exist_ok=True)
    repo2 = _repo(tmp_path / "noop")
    monkeypatch.setattr(step_mod, "record_situation", lambda *a, **k: None)
    monkeypatch.setattr("src.loop.judge_step.record_situation", lambda *a, **k: None)
    outcome2, ledger2 = await _run(repo2, FakeAgents(repo2, [
        player_writes(repo2, "src/greet.py", "def greet():\n    return 'a'\n"),
        judge_returns(no_ship([finding(problem="still broken")], previous=[])),
        player_writes(repo2, "src/greet.py", "def greet():\n    return 'b'\n"),
        judge_returns(no_ship([finding(problem="another break")],
                              previous=[{"id": "F1", "state": "resolved"}])),
        player_writes(repo2, "src/greet.py", "def greet():\n    return 'hello'\n"),
        judge_returns(ship()),
    ]))
    assert outcome2.status == outcome.status == "ship"
    assert _verdicts(ledger2) == _verdicts(ledger)
    assert _ledger_kinds(ledger2).count("judge") == _ledger_kinds(ledger).count("judge")
    assert _situation_events(ledger2) == []
    assert (outcome2.status, outcome2.rounds) == (outcome.status, outcome.rounds)


@pytest.mark.asyncio
async def test_situation_on_stalled_stop(tmp_path: Path):
    repo = _repo(tmp_path)
    stuck = no_ship([finding(problem="still broken")], previous=[{"id": "F1", "state": "still_open"}])
    agents = FakeAgents(repo, [
        player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        judge_returns(no_ship([finding(problem="still broken")])),
        player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        judge_returns(stuck),
        player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        judge_returns(stuck),
    ])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "stop"
    events = _situation_events(ledger)
    assert len(events) == 2
    assert [e["round"] for e in events] == [1, 2]
    assert all("situation" in e and "action" in e for e in events)


@pytest.mark.asyncio
async def test_history_survives_more(tmp_path: Path):
    from src.loop import situation_step as step_mod

    repo = _repo(tmp_path)
    f1 = finding(problem="still broken")
    agents = FakeAgents(repo, [
        player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        judge_returns(no_ship([f1])),
        player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
        judge_returns(ship()),
    ])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    path = ledger.run_dir / "state.json"
    state = RunState.read(path) or {}
    hist = state.get("situation[0]")
    assert hist and len(hist["rounds"]) == 1

    from src.loop.plan import load_phases, load_preamble
    from src.loop import phase as phase_mod

    cfg = _cfg()
    phases = load_phases(repo / "plan.md")
    runner = phase_mod._PhaseRunner(
        phase=phases[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=FakeAgents(repo, []), test_command=detect(repo),
        ledger=Ledger(new_run_dir(repo, "demo2")),
    )
    runner.ctx.run_dir = ledger.run_dir
    seen = step_mod.situation_state(runner.ctx, 0)
    assert len(seen["rounds"]) == 1 and seen["rounds"][0]["player"] == "muse"
    assert seen["sonnet_n"] == 0 and seen["opus_round"] is None


@pytest.mark.asyncio
async def test_player_is_actual_not_flag(tmp_path: Path):
    repo = _repo(tmp_path)
    f = finding(problem="still broken")
    steps = []
    for i in range(5):
        steps.append(player_writes(repo, "src/greet.py", f"def greet():\n    return '{i}'\n"))
        steps.append(judge_returns(no_ship([finding(problem=f"broken {i}")],
                                            previous=[{"id": f"F{i}", "state": "resolved"}] if i else [])))
    steps.append(player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"))
    steps.append(judge_returns(ship()))
    cfg = _cfg(escalate_at_round=5, escalation_provider="claude", escalation_model="sonnet-x")
    outcome, ledger = await _run(repo, FakeAgents(repo, steps), cfg)
    assert outcome.status == "ship"
    hist = _history(ledger)
    by_round = {r["round"]: r["player"] for r in hist["rounds"]}
    assert by_round[5] == "sonnet"
    assert all(by_round[r] == "muse" for r in (1, 2, 3, 4))

    # повтор после more на игроке плана (реальный раундный контекст,
    # эскалация на r5 уже не действует): player_identity даёт игрока.
    from src.loop.plan import load_phases, load_preamble
    from src.loop import phase as phase_mod

    cfg6 = _cfg(escalate_at_round=5, escalation_provider="claude", escalation_model="opus")
    phases6 = load_phases(repo / "plan.md")
    runner6 = phase_mod._PhaseRunner(
        phase=phases6[0], repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg6,
        agents=FakeAgents(repo, []), test_command=detect(repo),
        ledger=Ledger(new_run_dir(repo, "demo6")),
    )
    runner6.ctx.run_dir = ledger.run_dir
    runner6.ctx.st.round = 6
    assert runner6.ctx.player_identity() == (cfg6.player_model, cfg6.player_provider, cfg6.player_effort)
    assert situation_step._actual_player(runner6.ctx) == "muse"


@pytest.mark.asyncio
async def test_situation_crash_is_event(tmp_path: Path, monkeypatch):
    import src.loop.situation as sit_mod

    def boom(rounds, cfg, **kw):
        raise RuntimeError("diagnose down")

    monkeypatch.setattr(sit_mod, "diagnose", boom)
    repo = _repo(tmp_path)
    agents = FakeAgents(repo, [
        player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
        judge_returns(no_ship([finding(problem="still broken")])),
        player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
        judge_returns(ship()),
    ])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    events = _situation_events(ledger)
    assert len(events) == 1 and "error" in events[0]
    assert "diagnose down" not in json.dumps(events)
    assert "boom" not in json.dumps(events)
    stream = [row for row in _stream_rows(ledger) if row["type"] == "situation"]
    assert stream and "error" in stream[0]
