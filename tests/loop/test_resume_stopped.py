"""Resume a stopped run from its snapshot ref (phase 1). Red first."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.config import Config
from src.loop.session import resume_refusal
from src.loop.run import resume, run as loop_run
from src.loop.state import RunState
from src.loop import snapshot as snapshot_module
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


def _cfg(repo: Path) -> Config:
    return Config(plan_file="plan.md", working_dir=str(repo), max_rounds=2, test_timeout_s=30)


async def _fuse_run(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    import dataclasses
    cfg = dataclasses.replace(
        _cfg(repo), escalation_provider="", escalation_model="",
    )
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(scripted.no_ship([scripted.finding(problem="missing greet 1")])),
            player_writes(repo, "src/greet.py", HELLO + "# v2\n"),
            judge_returns(scripted.no_ship(
                [scripted.finding(problem="missing greet 2")],
                previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome = await loop_run(cfg, agents=agents)
    assert outcome.status == "stop", outcome.reason
    assert outcome.reason in ("fuse", "stuck"), outcome.reason
    return repo, outcome


def test_resume_refusal_contract():
    assert resume_refusal({"status": "stopped"}) != ""
    cfg = {"plan_file": "plan.md", "working_dir": "/tmp"}
    # F19: snapshot/base проверяются раньше причины
    assert "снимка нет" in resume_refusal(
        {"status": "stopped", "config": cfg, "reason": "interrupted",
         "snapshot_ref": "", "base": ""})


def test_resume_refusal_snapshot_before_reason():
    cfg = {"plan_file": "plan.md", "working_dir": "/tmp"}
    r = resume_refusal(
        {"status": "stopped", "config": cfg, "reason": "interrupted",
         "snapshot_ref": "", "base": ""})
    assert "снимка нет" in r


@pytest.mark.asyncio
async def test_resume_stopped_stuck_continues_from_ref(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    import dataclasses
    base_cfg = _cfg(repo)
    cfg = dataclasses.replace(base_cfg, escalation_provider="", escalation_model="")
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(scripted.no_ship([scripted.finding()])),
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(scripted.no_ship([scripted.finding()])),
        ],
    )
    outcome = await loop_run(cfg, agents=agents)
    assert outcome.status == "stop", outcome.reason
    # F23: именно stuck, без прогресса по находкам
    assert outcome.reason == "stuck", outcome.reason
    run_dir = Path(outcome.run_dir)
    state = RunState.read(run_dir / "state.json")
    assert state is not None
    ref, base = state["snapshot_ref"], state["base"]
    expected_diff = snapshot_module.diff_for(repo, base, ref)

    order: list[str] = []
    prompts: dict = {}

    def judge_first(role: str, kwargs: dict):
        order.append(role)
        assert role == "judge", f"первым после resume ждём судью, а не {role}"
        greet_now = (repo / "src/greet.py").read_text() if (repo / "src/greet.py").exists() else ""
        prompts["judge_tree_greet"] = greet_now
        assert snapshot_module.tree_of(repo, base) == snapshot_module.ref_tree(repo, ref), "судья не на дереве ref"
        return scripted.no_ship(
            [scripted.finding(problem="missing greet 3")],
            previous=[{"id": "F1", "state": "resolved"}])

    def player_cont(role: str, kwargs: dict):
        order.append(role)
        assert role == "player"
        prompt = kwargs.get("prompt", "")
        prompts["player"] = prompt
        assert "Продолжение после падения" in prompt
        meaningful = [ln for ln in expected_diff.splitlines()
                      if ln and "__pycache__" not in ln and "Binary files" not in ln
                      and not ln.startswith("index ")]
        assert meaningful, "diff base..ref пуст"
        missing = [ln for ln in meaningful if ln not in prompt]
        assert not missing, f"diff base..ref не в промпте целиком: {missing!r}"
        scripted.write_file(repo, "src/greet.py", HELLO)
        return dict(scripted.PLAYER_DONE)

    agents2 = FakeAgents(
        repo,
        [
            judge_first,
            player_cont,
            judge_returns(ship()),
        ],
    )
    out = await resume(repo, run_dir, agents=agents2)
    assert out.exit_code == 0, out.reason
    assert order == ["judge", "player"], order
    assert agents2.calls == ["judge", "player", "judge"], agents2.calls
    assert "Продолжение после падения" in prompts.get("player", "")
    prompt_p = prompts.get("player", "")
    meaningful = [ln for ln in expected_diff.splitlines()
                  if ln and "__pycache__" not in ln and "Binary files" not in ln
                  and not ln.startswith("index ")]
    assert meaningful and all(ln in prompt_p for ln in meaningful), "diff base..ref не в промпте целиком"
    greet = scripted.git(repo, "show", f"{ref}:src/greet.py")
    assert prompts.get("judge_tree_greet") == greet
    assert (repo / "src/greet.py").read_text() == HELLO


def _meaningful_smoke(repo: Path) -> None:
    scripted.write_file(
        repo, "src/greet_mod.py", "def shout(s):\n    return s.upper() + '!'\n",
    )
    scripted.write_file(
        repo, "tests/test_smoke.py",
        "from src.greet_mod import shout\n\ndef test_smoke():\n    assert shout('hey') == 'HEY!'\n",
    )
    scripted.git(repo, "add", "-A")
    scripted.git(repo, "commit", "-m", "meaningful smoke")


def _make_red_ref(repo: Path, old_ref: str, base: str) -> str:
    """Red tree as a new commit under old_ref without moving the branch HEAD."""
    scripted.git(repo, "checkout", old_ref, "--", ".")
    scripted.write_file(repo, "tests/test_broken.py", "def test_broken():\n    assert False\n")
    # ослабляем старый тест относительно base: было поведение shout, стало холостое
    scripted.write_file(repo, "tests/test_smoke.py",
                        "def test_smoke():\n    pass\n")
    scripted.git(repo, "add", "-A")
    tree = scripted.git(repo, "write-tree").strip()
    red_commit = scripted.git(repo, "commit-tree", tree, "-p", old_ref, "-m", "red").strip()
    scripted.git(repo, "update-ref", old_ref, red_commit)
    return old_ref


@pytest.mark.asyncio
async def test_resume_stopped_red_gate_skips_judge(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    _meaningful_smoke(repo)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(scripted.no_ship([scripted.finding(problem="missing greet 1")])),
            player_writes(repo, "src/greet.py", HELLO + "# v2\n"),
            judge_returns(scripted.no_ship(
                [scripted.finding(problem="missing greet 2")],
                previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome = await loop_run(_cfg(repo), agents=agents)
    assert outcome.status == "stop"
    run_dir = Path(outcome.run_dir)
    state = RunState.read(run_dir / "state.json")
    assert state is not None
    ref = state["snapshot_ref"]
    base = state["base"]

    _make_red_ref(repo, ref, base)
    # вернуть дерево в base, чтобы resume сам восстановил ref
    scripted.git(repo, "checkout", base, "--", ".")
    scripted.git(repo, "clean", "-fd")
    calls: list[str] = []
    judge_prompts: list[str] = []
    player_prompts: list[str] = []

    def player_sees_gate(role: str, kwargs: dict):
        assert role == "player", f"первый вызов после красного гейта — player, а не {role}"
        assert not any(c == "judge" for c in calls), f"судья вызван до гейта: {calls}"
        calls.append(role)
        prompt = kwargs.get("prompt", "")
        player_prompts.append(prompt)
        assert "FAILED" in prompt or "failed" in prompt.lower() or "assert" in prompt.lower(), (
            "у исполнителя нет вывода гейта"
        )
        scripted.write_file(repo, "tests/test_broken.py", "def test_broken():\n    assert True\n")
        scripted.write_file(repo, "src/greet.py", HELLO)
        return dict(scripted.PLAYER_DONE)

    def judge_after(role: str, kwargs: dict):
        assert role == "judge"
        calls.append(role)
        prompt = kwargs.get("prompt", "")
        judge_prompts.append(prompt)
        return ship()

    agents2 = FakeAgents(repo, [player_sees_gate, judge_after])
    out = await resume(repo, run_dir, agents=agents2)
    assert out.exit_code == 0, out.reason
    assert calls and calls[0] == "player", calls
    assert calls.count("judge") == 1 and calls.index("judge") == 1, calls
    assert "judge" in calls, "судья должен сдать после починки"
    assert judge_prompts, "судья должен быть вызван"
    delta_section = judge_prompts[0].split("## suite_delta", 1)[-1]
    assert "tests/test_smoke.py" in delta_section, (
        "ослабленный старый тест должен быть в suite_delta промпта судьи"
    )


@pytest.mark.asyncio
async def test_resume_deleted_test_in_suite_delta(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    _meaningful_smoke(repo)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(scripted.no_ship([scripted.finding(problem="missing greet 1")])),
            player_writes(repo, "src/greet.py", HELLO + "# v2\n"),
            judge_returns(scripted.no_ship(
                [scripted.finding(problem="missing greet 2")],
                previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome = await loop_run(_cfg(repo), agents=agents)
    assert outcome.status == "stop"
    run_dir = Path(outcome.run_dir)
    state = RunState.read(run_dir / "state.json")
    assert state is not None
    ref, base = state["snapshot_ref"], state["base"]
    # удалить базовый тест в ref без движения HEAD ветки
    scripted.git(repo, "checkout", ref, "--", ".")
    (repo / "tests/test_smoke.py").unlink()
    scripted.git(repo, "add", "-A")
    tree = scripted.git(repo, "write-tree").strip()
    red_commit = scripted.git(repo, "commit-tree", tree, "-p", ref, "-m", "red-del").strip()
    scripted.git(repo, "update-ref", ref, red_commit)
    scripted.git(repo, "checkout", base, "--", ".")
    scripted.git(repo, "clean", "-fd")
    judge_prompts: list[str] = []

    def judge_sees_delete(role: str, kwargs: dict):
        assert role == "judge"
        judge_prompts.append(kwargs.get("prompt", ""))
        return ship()

    agents2 = FakeAgents(repo, [judge_sees_delete])
    out = await resume(repo, run_dir, agents=agents2)
    assert out.exit_code == 0, out.reason
    assert judge_prompts, "судья должен быть вызван"
    delta_section = judge_prompts[0].split("## suite_delta", 1)[-1]
    assert "tests/test_smoke.py" in delta_section


@pytest.mark.asyncio
async def test_resume_stopped_fuse_new_round_budget(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    _meaningful_smoke(repo)
    import dataclasses
    cfg = dataclasses.replace(
        _cfg(repo), escalation_provider="", escalation_model="",
    )
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(scripted.no_ship([scripted.finding(problem="missing greet 1")])),
            player_writes(repo, "src/greet.py", HELLO + "# v2\n"),
            judge_returns(scripted.no_ship(
                [scripted.finding(problem="missing greet 2")],
                previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome = await loop_run(cfg, agents=agents)
    assert outcome.status == "stop", outcome.reason
    assert outcome.reason == "fuse", outcome.reason
    run_dir = Path(outcome.run_dir)
    state0 = RunState.read(run_dir / "state.json")
    assert state0 is not None
    round0 = state0.get("round") or 0
    assert round0 == 2, round0
    refs_before = scripted.git(repo, "for-each-ref", "refs/tero").strip()
    assert "phase-00-round01" in refs_before
    assert "phase-00-round02" in refs_before
    assert "phase-00-round03" not in refs_before
    names_before = {ln.rsplit("\t", 1)[-1] for ln in refs_before.splitlines() if ln.strip()}
    agents = FakeAgents(
        repo,
        [
            judge_returns(scripted.no_ship(
                [scripted.finding(problem="r3")],
                previous=[{"id": "F1", "state": "resolved"}, {"id": "F2", "state": "resolved"}])),
            player_writes(repo, "src/greet.py", HELLO),
            judge_returns(scripted.no_ship(
                [scripted.finding(problem="r4a")],
                previous=[{"id": "F1", "state": "resolved"}, {"id": "F2", "state": "resolved"},
                          {"id": "F3", "state": "resolved"}])),
            player_writes(repo, "src/greet.py", HELLO + "# resumed\n"),
            judge_returns(scripted.no_ship(
                [scripted.finding(problem="r4b")],
                previous=[{"id": "F1", "state": "resolved"}, {"id": "F2", "state": "resolved"},
                          {"id": "F3", "state": "resolved"}, {"id": "F4", "state": "resolved"}])),
        ],
    )
    out = await resume(repo, run_dir, agents=agents)
    assert out.status == "stop", out.reason
    assert out.reason == "fuse", out.reason
    state_after = RunState.read(run_dir / "state.json") or {}
    # новый бюджет: раунд продолжился со старого round (2) — rounds 3 и 4,
    # имена снимков 1–2 на месте, снова стоп только после раунда 4
    assert state_after.get("round") == 4, state_after
    # продолженные раунды сверх старого round: минимум один новый снимок
    refs_after = scripted.git(repo, "for-each-ref", "refs/tero").strip()
    assert "phase-00-round01" in refs_after
    assert "phase-00-round02" in refs_after
    assert "phase-00-round03" in refs_after
    assert "phase-00-round04" in refs_after
    names_after = {ln.rsplit("\t", 1)[-1] for ln in refs_after.splitlines() if ln.strip()}
    for name in names_before:
        if "phase-00-round02" in name:
            continue  # round02 перезаписывается restore'ом ref-дерева при входе — имя то же
        assert name in names_after, f"снимок пропал: {name}"
    assert any("phase-00-round02" in n for n in names_after)
    judges = [c for c in agents.calls if c == "judge"]
    players = [c for c in agents.calls if c == "player"]
    assert len(judges) == 3 and len(players) == 2, agents.calls
    assert agents.calls[0] == "judge" and agents.calls[1] == "player", agents.calls


@pytest.mark.asyncio
async def test_resume_user_needs_answer(tmp_path: Path):
    from src.loop.prompts import player_prompt, judge_prompt

    p1 = player_prompt(preamble="P", contract="C")
    p2 = player_prompt(preamble="P", contract="C", answer="")
    assert p1 == p2
    j1 = judge_prompt(preamble="P", contract="C")
    j2 = judge_prompt(preamble="P", contract="C", answer="")
    assert j1 == j2
    pa = player_prompt(preamble="P", contract="C", answer="используй sqlite")
    assert "не меняет контракт" in pa
    assert "используй sqlite" in pa
    ja = judge_prompt(preamble="P", contract="C", answer="используй sqlite")
    assert "не меняет контракт" in ja

    repo = scripted.init_repo(tmp_path, PLAN)
    run_dir = repo / ".tero" / "runs" / "20200101-000000-demo"
    run_dir.mkdir(parents=True)
    scripted.git(repo, "add", "-A")
    base = scripted.git(repo, "rev-parse", "HEAD").strip()
    RunState(run_dir / "state.json").update(
        status="stopped", reason="user", text="какой orm?",
        config={"plan_file": "plan.md", "working_dir": str(repo)},
        snapshot_ref=base, base=base, round=1, phase="Add greet", phase_index=0,
    )
    import subprocess as _sp
    import sys as _sys
    import os as _os
    _os.environ["TERO_TEST_SCRUB_KEY"] = "supersecretvalue123"
    try:
        before_bytes = (run_dir / "state.json").read_bytes()
        before = RunState.read(run_dir / "state.json")
        out = await resume(repo, run_dir, agents=FakeAgents(repo, []))
    finally:
        del _os.environ["TERO_TEST_SCRUB_KEY"]
    assert out.exit_code == 1
    assert "ждал ответа" in out.reason
    assert "какой orm?" in out.reason
    assert (run_dir / "state.json").read_bytes() == before_bytes
    assert RunState.read(run_dir / "state.json") == before
    root = Path(__file__).resolve().parent.parent.parent
    _os.environ["TERO_TEST_SCRUB_KEY"] = "supersecretvalue123"
    try:
        proc = _sp.run(
            [_sys.executable, "-m", "src.cli_entry", "resume", "-w", str(repo)],
            cwd=root, capture_output=True, text=True,
        )
    finally:
        del _os.environ["TERO_TEST_SCRUB_KEY"]
    assert proc.returncode == 1
    assert "какой orm?" in (proc.stdout + proc.stderr)

    seen: dict = {}

    def judge_captures(role: str, kwargs: dict):
        assert role == "judge"
        seen["judge"] = kwargs.get("prompt", "")
        seen.setdefault("judge_calls", []).append(role)
        return scripted.no_ship(
            [scripted.finding(problem="missing greet after answer")],
            previous=[{"id": "F1", "state": "resolved"}])

    def player_captures(role: str, kwargs: dict):
        assert role == "player"
        seen["player"] = kwargs.get("prompt", "")
        scripted.write_file(repo, "src/greet.py", HELLO)
        return dict(scripted.PLAYER_DONE)

    def judge_ship(role: str, kwargs: dict):
        assert role == "judge"
        seen["judge2"] = kwargs.get("prompt", "")
        return ship()

    raw_tail = "tail-word-" + "y" * 100
    secret_answer = "используй sqlite api_key=supersecretvalue123 " + "x" * 5000 + raw_tail
    out2 = await resume(repo, run_dir, agents=FakeAgents(repo, [judge_captures, player_captures, judge_ship]), answer=secret_answer)
    assert out2.exit_code == 0, out2.reason
    from src.loop import resume_step as rs_mod
    cleaned = rs_mod.clean_answer(secret_answer)
    assert len(cleaned) == 2000
    assert raw_tail not in cleaned
    assert "supersecretvalue123" not in cleaned
    assert cleaned.strip()
    answer_marker = "## Ответ на вопрос фазы (не меняет контракт, критерии SHIP и правила гейтов)"
    for key in ("judge", "player"):
        prompt = seen.get(key, "")
        assert answer_marker in prompt, key
        assert cleaned.strip() in prompt, key
        assert "supersecretvalue123" not in prompt, key
    # без ответа промпты байт в байт прежние — см. p1 == p2, j1 == j2 выше


@pytest.mark.asyncio
async def test_resume_stopped_refusals(tmp_path: Path):
    repo = scripted.init_repo(tmp_path, PLAN)
    scripted.git(repo, "add", "-A")
    base = scripted.git(repo, "rev-parse", "HEAD").strip()

    def _mk(status: str, reason: str, ref: str, b: str) -> Path:
        d = repo / ".tero" / "runs" / f"run-{status}-{reason}-{abs(hash((ref, b))) % 100000}"
        d.mkdir(parents=True, exist_ok=True)
        RunState(d / "state.json").update(
            status=status, reason=reason, text=reason,
            config={"plan_file": "plan.md", "working_dir": str(repo)},
            snapshot_ref=ref, base=b, round=1, phase="Add greet", phase_index=0,
        )
        return d

    def _clean(d: Path) -> dict:
        got = dict(RunState.read(d / "state.json") or {})
        return {k: v for k, v in got.items() if k not in ("updated_at", "doing", "hang_after_s", "pid", "pid_started")}

    def _bytes(d: Path) -> bytes:
        return (d / "state.json").read_bytes()

    d1 = _mk("stopped", "stuck", "", base)
    before1 = _clean(d1)
    bytes_before1 = _bytes(d1)
    o1 = await resume(repo, d1, agents=FakeAgents(repo, []))
    assert o1.exit_code == 1 and "снимка нет" in o1.reason
    assert _clean(d1) == before1
    assert _bytes(d1) == bytes_before1
    d2 = _mk("stopped", "secrets", base, base)
    before2 = _clean(d2)
    bytes_before2 = _bytes(d2)
    o2 = await resume(repo, d2, agents=FakeAgents(repo, []))
    assert o2.exit_code == 1 and "секрет" in o2.reason
    assert _clean(d2) == before2
    assert _bytes(d2) == bytes_before2
    d3 = _mk("stopped", "interrupted", base, base)
    before3 = _clean(d3)
    bytes_before3 = _bytes(d3)
    o3 = await resume(repo, d3, agents=FakeAgents(repo, []))
    assert o3.exit_code == 1 and "interrupted" in o3.reason
    assert _clean(d3) == before3
    assert _bytes(d3) == bytes_before3
    # branch ahead: дерево и состояние не тронуты
    scripted.write_file(repo, "src/ahead.py", "X=1\n")
    scripted.git(repo, "add", "-A")
    scripted.git(repo, "commit", "-m", "ahead")
    ahead_content = (repo / "src/ahead.py").read_text()
    d4 = _mk("stopped", "stuck", base, base)
    bytes_before4 = _bytes(d4)
    before4 = _clean(d4)
    o4 = await resume(repo, d4, agents=FakeAgents(repo, []))
    assert o4.exit_code == 1 and "ушла вперёд" in o4.reason
    assert _bytes(d4) == bytes_before4
    assert _clean(d4) == before4
    assert (repo / "src/ahead.py").read_text() == ahead_content
    repo2 = tmp_path / "r2"
    repo2.mkdir(parents=True, exist_ok=True)
    repo2 = scripted.init_repo(repo2, PLAN)
    agents = FakeAgents(repo2, [player_writes(repo2, "src/greet.py", HELLO), judge_returns(ship())])
    ok = await loop_run(_cfg(repo2), agents=agents)
    assert ok.status == "ok"
    done_dir = Path(ok.run_dir)
    done_bytes = (done_dir / "state.json").read_bytes()
    root = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli_entry", "resume", "-w", str(repo2)],
        cwd=root, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "resume для `done` пока не умеет" in (proc.stdout + proc.stderr)
    assert (done_dir / "state.json").read_bytes() == done_bytes


@pytest.mark.asyncio
async def test_resume_branch_ahead_leaves_state_untouched(tmp_path: Path):
    repo, outcome = await _fuse_run(tmp_path)
    d = Path(outcome.run_dir)
    saved = RunState.read(d / "state.json") or {}
    base = saved["base"]
    branch_out = scripted.git(repo, "branch", "--list", "tero/*").strip()
    assert branch_out, "нет ветки прогона"
    branch = branch_out.splitlines()[0].strip().lstrip("* ").strip()
    scripted.git(repo, "checkout", branch)
    scripted.write_file(repo, "src/ahead_strict.py", "X=2\n")
    scripted.git(repo, "add", "-A")
    scripted.git(repo, "commit", "-m", "ahead-strict")
    scripted.git(repo, "checkout", base)
    scripted.write_file(repo, "src/keep_strict.py", "KEEP=1\n")
    before_bytes = (d / "state.json").read_bytes()
    stream_path = d / "stream.jsonl"
    ledger_path = d / "ledger.jsonl"
    before_stream = stream_path.read_bytes() if stream_path.exists() else None
    before_ledger = ledger_path.read_bytes() if ledger_path.exists() else None
    out = await resume(repo, d, agents=FakeAgents(repo, []))
    assert out.exit_code == 1 and "ушла вперёд" in out.reason
    assert (d / "state.json").read_bytes() == before_bytes
    after_stream = stream_path.read_bytes() if stream_path.exists() else None
    after_ledger = ledger_path.read_bytes() if ledger_path.exists() else None
    assert after_stream == before_stream
    assert after_ledger == before_ledger


@pytest.mark.asyncio
async def test_resume_stopped_setup_failure_keeps_status(tmp_path: Path, monkeypatch):
    import pytest as _pytest

    repo, outcome = await _fuse_run(tmp_path)
    run_dir = Path(outcome.run_dir)
    before = RunState.read(run_dir / "state.json") or {}
    keep_keys = ("status", "reason", "text", "snapshot_ref", "base")
    want = {k: before.get(k) for k in keep_keys}
    import src.loop.pulse as pulse_mod

    def _boom(run_dir_arg):
        raise RuntimeError("pulse down")

    monkeypatch.setattr(pulse_mod, "Pulse", _boom)
    with _pytest.raises(RuntimeError):
        await resume(repo, run_dir, agents=FakeAgents(repo, []))
    after = RunState.read(run_dir / "state.json") or {}
    for k in keep_keys:
        assert after.get(k) == want[k], k
    from src.loop.lock import acquire

    lock = acquire(repo)
    assert lock is not None
    try:
        lock.release()
    except Exception:
        pass


@pytest.mark.asyncio
async def test_resume_branch_head_checked_not_checkout(tmp_path: Path):
    repo, outcome = await _fuse_run(tmp_path)
    run_dir = Path(outcome.run_dir)
    state = RunState.read(run_dir / "state.json")
    assert state is not None
    base = state["base"]
    branch_out = scripted.git(repo, "branch", "--list", "tero/*").strip()
    assert branch_out, "нет ветки прогона"
    branch = branch_out.splitlines()[0].strip().lstrip("* ").strip()
    scripted.git(repo, "checkout", branch)
    scripted.write_file(repo, "src/ahead_branch.py", "X=1\n")
    scripted.git(repo, "add", "-A")
    scripted.git(repo, "commit", "-m", "ahead-branch")
    scripted.git(repo, "checkout", base)
    scripted.write_file(repo, "src/keep.py", "KEEP=1\n")
    before = dict(RunState.read(run_dir / "state.json") or {})
    out = await resume(repo, run_dir, agents=FakeAgents(repo, []))
    assert out.exit_code == 1 and "ушла вперёд" in out.reason
    assert (repo / "src/keep.py").read_text() == "KEEP=1\n"
    after = dict(RunState.read(run_dir / "state.json") or {})
    for k in ("status", "reason", "text", "snapshot_ref", "base", "round"):
        assert after.get(k) == before.get(k), k


def test_parse_plan_bytes_no_headings_matches_load_phases(tmp_path: Path):
    from src.loop import plan as pm
    from src.loop import resume_step as rs_mod

    raw = b"# Demo\n\nPreamble.\n\n- [ ] greet() returns hello\n- [x] done item\n"
    preamble, phases = rs_mod._parse_plan_bytes(raw)
    plan_file = tmp_path / "plan.md"
    plan_file.write_bytes(raw)
    want_preamble = pm.load_preamble(plan_file)
    want_phases = pm.load_phases(plan_file)
    assert preamble == want_preamble
    assert [p.title for p in phases] == [p.title for p in want_phases]
    assert [p.index for p in phases] == [p.index for p in want_phases]
    assert [p.start_line for p in phases] == [p.start_line for p in want_phases]
