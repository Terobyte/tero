"""Фаза 08-12: скип в долг без отката (красный тест контракта)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.loop.state import RunState


TWO_PHASE_PLAN = """# Demo

Preamble.

## First

- [ ] first done

## Second

- [ ] second done
"""


def _repo(tmp_path: Path) -> Path:
    from tests.loop import fakes as scripted

    return scripted.init_repo(tmp_path, TWO_PHASE_PLAN)


def _cfg(**overrides):
    from src.config import Config

    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30,
            "escalation_provider": "claude", "escalation_model": "opus"}
    data.update(overrides)
    return Config(**data)


async def _run_with_skip(tmp_path: Path, monkeypatch, cfg, gate_result=None, mole: bool = False, secret: bool = False):
    import json
    import threading
    import src.loop.decision.step as ds_mod
    from src.loop.inbox import inbox_dir
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.plan import load_phases, load_preamble
    from src.loop.gates import detect
    from src.loop.session import run_phases
    from src.loop.phase import run_phase
    from tests.loop import fakes as scripted
    from tests.loop.fakes import FakeAgents, finding, judge_returns, no_ship, player_writes, ship

    monkeypatch.setattr(ds_mod, "DECISION_POLL_S", 0.01)
    if gate_result is not None:
        import src.loop.gate_scope as gs_mod

        calls = {"n": 0}

        def _fake_full_gate(command, repo, ctx, ledger, st, **kw):
            if gate_result == "red-once":
                calls["n"] += 1
                return "red" if calls["n"] == 1 else "green"
            return gate_result

        monkeypatch.setattr(gs_mod, "full_gate", _fake_full_gate)
    repo = _repo(tmp_path)
    (repo / "plan.md").write_text(TWO_PHASE_PLAN)
    import subprocess as _git
    _git.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    try:
        _git.run(["git", "commit", "-m", "two phases"], cwd=repo, check=True, capture_output=True)
    except Exception:
        pass
    stuck = no_ship([finding(problem="still broken")],
                    previous=[{"id": "F1", "state": "still_open"}])
    if mole:
        # skip приходит на первую точку решения: stuck даёт ask_owner уже
        # на 5-м раунде (sonnet на r5, второй stuck после него) — коммит
        # хранит работу 5-го раунда (return 'e').
        steps = []
        for i in range(1, 9):
            letter = chr(ord('a') + i - 1)
            steps.append(player_writes(repo, "src/greet.py",
                                       f"def greet():\n    return '{letter}'\n"))
            steps.append(judge_returns(no_ship(
                [finding(problem="stuck one")],
                previous=[] if i == 1 else [{"id": "F1", "state": "still_open"}])))
        if secret:
            _orig_mole = steps[0]

            def _mole_first(role, kwargs, _o=_orig_mole):
                out = _o(role, kwargs)
                (repo / ".env").write_text("TYPESAFE_API_KEY=sk-test-secret\n")
                return out

            steps[0] = _mole_first
        steps += [
            player_writes(repo, "src/second.py", "x = 1\n"),
            judge_returns(ship()),
        ]
        agents = FakeAgents(repo, steps)
    else:
        first_step = player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n")
        if secret:
            _orig = first_step

            def first_step(role, kwargs, _o=_orig):
                out = _o(role, kwargs)
                (repo / ".env").write_text("TYPESAFE_API_KEY=sk-test-secret\n")
                return out

        agents = FakeAgents(
            repo,
            [
                first_step,
                judge_returns(no_ship([finding(problem="still broken")])),
                player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
                judge_returns(stuck),
                player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
                judge_returns(stuck),
                player_writes(repo, "src/second.py", "x = 1\n"),
                judge_returns(ship()),
            ],
        )
    import subprocess as _git
    ledger = Ledger(new_run_dir(repo, "demo"))
    inbox = inbox_dir(ledger.run_dir, create=True)
    assert inbox is not None
    phases = load_phases(repo / "plan.md")
    command = detect(repo)
    kwargs = dict(
        repo=repo, plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"), cfg=cfg,
        agents=agents, test_command=command,
    )

    def _answer_skip() -> None:
        import time as _t
        for _ in range(3000):
            try:
                state = RunState.read(ledger.run_dir / "state.json") or {}
                q = state.get("question") or {}
                if (state.get("status") == "waiting_human") and q.get("id"):
                    (ledger.run_dir / "inbox" / "a.json").write_text(
                        json.dumps({"uuid": "a1", "question_id": q["id"],
                                    "action": "skip", "text": "", "who": "human"}),
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
    t.join(timeout=30)
    assert not t.is_alive(), "помощник не успел подать skip за 30с"
    return outcome, ledger, repo


@pytest.mark.asyncio
async def test_skip_secret_blocks_ship(tmp_path: Path, monkeypatch):
    import subprocess as _git

    outcome, ledger, repo = await _run_with_skip(
        tmp_path, monkeypatch, _cfg(escalation="situational", decisions=True), gate_result="green",
        mole=True, secret=True)
    assert outcome.status == "ok", outcome
    secret_left = (repo / ".env").exists()
    assert secret_left or (
        _git.run(["git", "show", "HEAD:.env"], cwd=repo,
                 capture_output=True).returncode != 0
    ), "секрет не должен попасть в коммит"
    head = _git.run(["git", "rev-list", "--all", "--", ".env"], cwd=repo,
                    capture_output=True, text=True).stdout.strip()
    assert not head, ".env не должен попасть ни в один коммит"


@pytest.mark.asyncio
async def test_skip_keeps_work_situational(tmp_path: Path, monkeypatch):
    outcome, ledger, repo = await _run_with_skip(
        tmp_path, monkeypatch, _cfg(escalation="situational", decisions=True), gate_result="green",
        mole=True)
    assert outcome.status == "ok", outcome
    kept = (repo / "src/greet.py").read_text(encoding="utf-8")
    assert "return 'e'" in kept, "работа игрока должна быть закоммичена, а не откачена"
    state = RunState.read(ledger.run_dir / "state.json") or {}
    debt = state.get("debt") or []
    assert debt and all(e.get("why") == "skip" for e in debt), debt
    import json as _json
    import subprocess as _git

    ships = [e for e in (ledger.run_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
             if '"skip"' in e or '"ship"' in e]
    assert ships, "ожидаются события skip/ship пропуска первой фазы"
    ship_events = [_json.loads(e) for e in (ledger.run_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
                   if _json.loads(e).get("kind") == "ship"]
    skip_commits = []
    try:
        stream_lines = (ledger.run_dir / "stream.jsonl").read_text(encoding="utf-8").splitlines()
    except Exception:
        stream_lines = []
    for _ln in stream_lines:
        if '"skip"' not in _ln:
            continue
        try:
            skip_commits.append(_json.loads(_ln).get("commit_sha") or "")
        except Exception:
            pass
    assert ship_events, "ожидается событие ship коммита первой фазы"
    ship_by_commit = {ev.get("commit", ""): ev for ev in ship_events}
    skip_ship = next((ship_by_commit[c] for c in skip_commits if c and c in ship_by_commit), None)
    if skip_ship is None:
        skip_ship = next((ev for ev in ship_events if ev.get("phase") == "First"), None)
    assert skip_ship is not None, (ship_events, skip_commits)
    assert skip_ship.get("phase"), skip_ship
    assert isinstance(skip_ship.get("round"), int), skip_ship
    assert skip_ship.get("role"), skip_ship
    assert "rounds" not in skip_ship, skip_ship

    run_id = ledger.run_dir.name
    first_phase_commit = None
    log = _git.run(["git", "log", "--format=%H %b", "--reverse"], cwd=repo,
                   check=True, capture_output=True, text=True).stdout
    for line in log.splitlines():
        if f"Tero-Phase: {run_id}/1" in line:
            first_phase_commit = line.split()[0]
    assert first_phase_commit, f"первая фаза должна быть закоммичена со своим трейлером:\n{log}"
    shown = _git.run(["git", "show", f"{first_phase_commit}:src/greet.py"], cwd=repo,
                     capture_output=True, text=True).stdout
    assert "return 'e'" in shown, "в коммите первой фазы — работа игрока"
    assert outcome.phases_shipped == 2, outcome


@pytest.mark.asyncio
async def test_skip_red_gate_rolls_back(tmp_path: Path, monkeypatch):
    outcome, ledger, repo = await _run_with_skip(
        tmp_path, monkeypatch, _cfg(escalation="situational", decisions=True),
        gate_result="red-once", mole=True)
    assert outcome.status == "ok", outcome
    state = RunState.read(ledger.run_dir / "state.json") or {}
    debt = state.get("debt") or []
    assert debt, debt
    blob = " ".join(str(v) for e in debt for v in (e.values() if isinstance(e, dict) else [e]))
    assert "гейт красный, откат" in blob, debt
    assert any((e.get("ref") or "") for e in debt if isinstance(e, dict)), debt
    import subprocess as _git

    refs = [e.get("ref") for e in debt if isinstance(e, dict) and e.get("ref")]
    # Откат идёт к checkpoint фазы (начало), а не к snapshot из ref:
    # ref хранит работу (e), дерево после отката — состояние двух коммитов
    # init + "two phases" без greet.py.
    tracked = _git.run(["git", "ls-files"], cwd=repo,
                       check=True, capture_output=True, text=True).stdout.split()
    assert "src/greet.py" not in tracked, tracked
    status = _git.run(["git", "status", "--porcelain"], cwd=repo,
                      check=True, capture_output=True, text=True).stdout.strip()
    assert status == "", status
    got = _git.run(["git", "cat-file", "-t", refs[0]], cwd=repo, capture_output=True)
    assert got.returncode == 0, "ref из долга должен существовать"


@pytest.mark.asyncio
async def test_skip_round_mode_rolls_back(tmp_path: Path, monkeypatch):
    outcome, ledger, repo = await _run_with_skip(
        tmp_path, monkeypatch, _cfg(decisions=True), gate_result="green")
    assert outcome.status == "ok", outcome
    gp = repo / "src/greet.py"
    kept = gp.read_text(encoding="utf-8") if gp.exists() else ""
    assert "return 'a'" not in kept, "round-режим: старое поведение с откатом"
