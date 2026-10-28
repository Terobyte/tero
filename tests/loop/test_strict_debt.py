"""Строгий долг: major/blocker не в долг, minor — только после правки, кэп, jev, decider."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.loop import jev as jev_mod
from src.loop import opus_decision as opus_mod
from src.loop import situation as situation_mod
from src.loop import situation_step
from src.loop import verdict_step
from src.loop.phase_ctx import OpenFinding, _Next
from src.loop.state import RunState


def J(n, levels, so=0, open_level=None, sev=None):
    """Раунд истории: judge_level новых находок, open_level — пережившие правку."""
    row = {"round": n, "n_findings": len(levels), "resolved": 0, "still_open": so,
           "sev": sev if sev is not None else [], "judge_level": list(levels), "player": "muse"}
    if open_level is not None:
        row["open_level"] = open_level
    return row


def _act(rounds, **cfg):
    return situation_mod.diagnose(rounds, situation_mod.Cfg(**cfg))["action"]


# --- 1-2: долг только для minor/nit и только после правки ---

def test_meta_case_major_minor_not_debt():
    # meta: судья [major, minor], still_open 0 — раньше сразу ship_with_debt
    rs = [J(1, ["blocker"]), J(2, ["major", "minor"])]
    assert _act(rs) != "ship_with_debt"


def test_fresh_minor_gets_one_more_round():
    rs = [J(1, ["blocker", "blocker"]), J(2, ["minor"])]
    assert _act(rs) == "continue"


def test_survived_minor_goes_to_debt():
    rs = [J(1, ["minor"]), J(2, ["nit"], so=1, open_level=[[None, "minor"]])]
    assert _act(rs) == "ship_with_debt"


def test_fresh_nit_goes_to_debt():
    rs = [J(1, ["blocker"]), J(2, ["nit"])]
    assert _act(rs) == "ship_with_debt"


def test_survived_major_not_debt():
    rs = [J(1, ["major"]), J(2, ["nit"], so=1, open_level=[[None, "major"]])]
    assert _act(rs) != "ship_with_debt"


def test_still_open_without_levels_not_debt():
    rs = [J(1, ["minor"]), J(2, ["nit"], so=1)]
    assert _act(rs) != "ship_with_debt"


def test_unknown_level_not_debt():
    assert _act([J(1, ["blocker"]), J(2, [None])]) != "ship_with_debt"
    assert _act([J(1, ["blocker"]), J(2, ["critical"])]) != "ship_with_debt"


def test_confident_jev_raises_severity():
    rs = [J(1, ["blocker"]), J(2, ["nit"], sev=[["major", 0.9]])]
    assert _act(rs) != "ship_with_debt"
    rs = [J(1, ["blocker"]), J(2, ["nit"], sev=[["major", 0.1]])]
    assert _act(rs) == "ship_with_debt"


# --- 5: jev включён, но второго мнения нет — долга нет ---

def test_jev_error_blocks_polish():
    rs = [J(1, ["blocker"]), J(2, ["nit"], sev=[])]
    assert _act(rs, jev_required=True) != "ship_with_debt"
    rs = [J(1, ["blocker"]), J(2, ["nit"], sev=[["nit", 0.9]])]
    assert _act(rs, jev_required=True) == "ship_with_debt"


def test_record_situation_marks_jev_and_open_levels(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(situation_step, "maybe_query_jev", lambda *a, **k: {"error": "network"})
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="running")

    class _L:
        def event(self, *a, **k):
            pass

    old = OpenFinding(id="F1", severity="blocking", route="player", file="a.py", line=1,
                      problem="p1", expected="e1", level="minor")
    ctx = SimpleNamespace(
        cfg=SimpleNamespace(jev_enabled=True, escalation_provider="", escalation_model=""),
        phase=SimpleNamespace(index=0, title="P"), run_dir=run_dir, repo="",
        st=SimpleNamespace(round=2, next_id=2, open_findings=[old]),
        ledger=_L(), emit=lambda *a, **k: None, player_identity=lambda: ("m", "muse", ""),
    )
    nit = {"severity": "blocking", "level": "nit", "route": "player", "file": "a.py",
           "line": 2, "problem": "p2", "expected": "e2"}
    res = situation_step.record_situation(ctx, [nit], {"F1": "still_open"}, {})
    row = res["rounds"][-1]
    assert row["open_level"] == [[None, "minor"]]
    # jev включён и упал — polish запрещён в этом раунде
    assert res["action"] != "ship_with_debt"


# --- 3: кэп долга на прогон ---

def _step(tmp_path: Path, debt_n: int, max_debt: int = 3):
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    RunState(run_dir / "state.json").update(
        status="running", debt=[{"phase": 0, "round": 1, "finding_id": f"X{i}", "level": "nit",
                                 "problem": f"old {i}", "why": "polish"} for i in range(debt_n)])
    nit = OpenFinding(id="F1", severity="blocking", route="player", file="a.py", line=1,
                      problem="tiny", expected="e", level="nit")

    class _L:
        def event(self, *a, **k):
            pass

    ctx = SimpleNamespace(
        st=SimpleNamespace(round=3, open_findings=[nit], debt_pending=[]),
        run_dir=run_dir, cfg=SimpleNamespace(max_debt=max_debt),
        phase=SimpleNamespace(index=0, title="P"), ledger=_L(), emit=lambda *a, **k: None,
    )
    return SimpleNamespace(ctx=ctx)


def test_debt_cap_exceeded_asks_owner(tmp_path: Path):
    step = _step(tmp_path, debt_n=3)
    out = asyncio.run(verdict_step.situational_action(step, {"action": "ship_with_debt"}, {}))
    assert out == _Next.STOP
    assert step.ctx.st.debt_pending == []


def test_debt_cap_not_reached_ships(tmp_path: Path):
    step = _step(tmp_path, debt_n=2)
    out = asyncio.run(verdict_step.situational_action(step, {"action": "ship_with_debt"}, {}))
    assert out == _Next.SHIP


# --- 4: decider ---

class _Agents:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    async def run_role(self, role: str, **kw):
        assert role == "decider"
        self.calls.append(kw)
        return dict(self.answer)


def _ctx(tmp_path: Path, level: str, agents, provider="claude", model="sonnet", effort="high"):
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    RunState(run_dir / "state.json").update(status="running")

    class _L:
        def event(self, *a, **k):
            pass

    found = OpenFinding(id="F1", severity="blocking", route="player", file="a.py", line=1,
                        problem="p", expected="e", level=level)
    return SimpleNamespace(
        st=SimpleNamespace(round=4, open_findings=[found], debt_pending=[]),
        phase=SimpleNamespace(index=0, title="P"), run_dir=run_dir, agents=agents,
        ledger=_L(), emit=lambda *a, **k: None,
        cfg=SimpleNamespace(escalation_provider=provider, escalation_model=model,
                            escalation_effort=effort),
    )


def test_decider_debt_with_open_major_is_more(tmp_path: Path):
    agents = _Agents({"decision": "ship_with_debt", "why": "в долг"})
    ctx = _ctx(tmp_path, "major", agents)
    res = asyncio.run(opus_mod.resolve(ctx, {"situation": "mole", "reason": "x"}, {}))
    assert res is None
    assert ctx.st.debt_pending == []
    hist = (RunState.read(ctx.run_dir / "state.json") or {}).get("situation[0]") or {}
    assert hist.get("opus_round") == 4


def test_decider_uses_escalation_provider(tmp_path: Path):
    agents = _Agents({"decision": "ship_with_debt", "why": "мелочь"})
    ctx = _ctx(tmp_path, "minor", agents)
    res = asyncio.run(opus_mod.resolve(ctx, {"situation": "stuck_strong", "reason": "x"}, {}))
    assert res == "ship"
    kw = agents.calls[0]
    assert (kw["provider"], kw["model"], kw["effort"]) == ("claude", "sonnet", "high")


def test_decider_without_escalation_asks_owner(tmp_path: Path):
    agents = _Agents({"decision": "ship_with_debt", "why": "мелочь"})
    ctx = _ctx(tmp_path, "minor", agents, provider="", model="", effort="")
    res = asyncio.run(opus_mod.resolve(ctx, {"situation": "mole", "reason": "x"}, {}))
    assert res == "stop"
    assert agents.calls == []


# --- 5: ключ jev из secrets.env ---

def test_jev_key_from_secrets_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    sec = tmp_path / "secrets.env"
    sec.write_text("OTHER=1\nexport TYPESAFE_API_KEY=\"k-test\"\n")
    assert jev_mod.load_api_key(str(sec)) == "k-test"
    assert jev_mod.load_api_key(str(tmp_path / "missing.env")) == ""
    monkeypatch.setenv("TYPESAFE_API_KEY", "k-env")
    assert jev_mod.load_api_key(str(sec)) == "k-env"


def test_maybe_query_jev_uses_secrets_file(tmp_path: Path, monkeypatch):
    import src.loop.git as git_mod

    monkeypatch.setattr(git_mod, "secret_paths", lambda repo: [])
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    sec = tmp_path / "secrets.env"
    sec.write_text("TYPESAFE_API_KEY=k-file\n")
    monkeypatch.setattr(jev_mod, "SECRETS_FILE", str(sec))
    seen: list[dict] = []

    def post(url, headers, body, timeout):
        seen.append(headers)
        return 200, {"answers": {"s0": {"choice": "nit", "confidence": 0.9}},
                     "usage": {"input_tokens": 1}}

    class _L:
        def event(self, *a, **k):
            pass

    ctx = SimpleNamespace(cfg=SimpleNamespace(jev_enabled=True), phase=SimpleNamespace(title="P"),
                          st=SimpleNamespace(round=2, next_id=1, open_findings=[]), repo="",
                          ledger=_L(), emit=lambda *a, **k: None)
    f = {"severity": "blocking", "route": "player", "file": "a.py", "line": 1,
         "problem": "p", "expected": "e"}
    res = situation_step.maybe_query_jev(ctx, [f], post=post)
    assert res.get("sev") == [["nit", 0.9]]
    assert seen and seen[0]["Authorization"] == "Bearer k-file"


@pytest.fixture(autouse=True)
def _no_real_secrets(monkeypatch, tmp_path):
    # тесты не читают настоящий ~/.tero/secrets.env
    monkeypatch.setattr(jev_mod, "SECRETS_FILE", str(tmp_path / "none.env"), raising=False)


def test_jev_major_survives_repair(tmp_path: Path, monkeypatch):
    # судья minor, jev уверенно major: после правки находка всё ещё major, в долг нельзя
    monkeypatch.setattr(situation_step, "maybe_query_jev", lambda *a, **k: {"sev": [["major", 0.9]]})
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    RunState(run_dir / "state.json").update(status="running")

    class _L:
        def event(self, *a, **k):
            pass

    ctx = SimpleNamespace(
        cfg=SimpleNamespace(jev_enabled=True, escalation_provider="", escalation_model=""),
        phase=SimpleNamespace(index=0, title="P"), run_dir=run_dir, repo="",
        st=SimpleNamespace(round=2, next_id=1, open_findings=[]),
        ledger=_L(), emit=lambda *a, **k: None, player_identity=lambda: ("m", "muse", ""),
    )
    item = {"severity": "blocking", "level": "minor", "route": "player", "file": "a.py",
            "line": 1, "problem": "p", "expected": "e"}
    situation_step.record_situation(ctx, [item], {}, {})
    assert item["level"] == "major"
    found = OpenFinding(id="F1", severity="blocking", route="player", file="a.py", line=1,
                        problem="p", expected="e", level=item["level"])
    ctx.st = SimpleNamespace(round=3, next_id=2, open_findings=[found])
    monkeypatch.setattr(situation_step, "maybe_query_jev", lambda *a, **k: {"sev": []})
    res = situation_step.record_situation(ctx, [], {"F1": "still_open"}, {})
    assert res["rounds"][-1]["open_level"] == [[None, "major"]]
    assert res["action"] != "ship_with_debt"

