"""Red proof tests for hunt bugs B11,B17a,B23,B24,B25,B27,B28 (writer loop2).

B17b omitted: green on current code (CRLF file round-trips byte-identical,
no protected_violation) — see report.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import TestCommand, detect
from src.loop.git import head
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from src.loop.plan import load_phases, load_preamble, mark_phase_done
from src.loop.run import run
from tests.loop.fakes import FakeAgents, finding, judge_returns, no_ship, player_writes, ship, write_file

PLAN = """# Demo

## Add greet

- [ ] greet() returns hello
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path, plan: str = PLAN) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tero@test")
    _git(tmp_path, "config", "user.name", "tero")
    write_file(tmp_path, "plan.md", plan)
    write_file(tmp_path, "src/__init__.py", "")
    write_file(tmp_path, "tests/test_smoke.py", "def test_smoke():\n    assert True\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


async def _run_phase(repo: Path, agents: FakeAgents, cfg: Config | None = None, plan_path: Path | None = None):
    cfg = cfg or _cfg()
    plan_path = plan_path or (repo / "plan.md")
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(plan_path)
    outcome = await run_phase(
        phases[0],
        repo=repo,
        plan_path=plan_path,
        preamble=load_preamble(plan_path),
        cfg=cfg,
        agents=agents,
        test_command=detect(repo),
        ledger=ledger,
    )
    return outcome, ledger


@pytest.mark.asyncio
async def test_B11_ship_empty_diff_no_commit(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tero@test")
    _git(repo, "config", "user.name", "tero")
    write_file(repo, "src/__init__.py", "")
    write_file(repo, "tests/test_smoke.py", "def test_smoke():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    plan_path = tmp_path / "plan_outside.md"
    plan_path.write_text(PLAN)
    base = head(repo)

    def noop(_role, _kw):
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    agents = FakeAgents(repo, [noop, judge_returns(ship())])
    green = TestCommand(name="noop", run_all=[sys.executable, "-c", ""], run_files=None)
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(plan_path)
    try:
        outcome = await run_phase(
            phases[0],
            repo=repo,
            plan_path=plan_path,
            preamble=load_preamble(plan_path),
            cfg=_cfg(),
            agents=agents,
            test_command=green,
            ledger=ledger,
        )
    except Exception as exc:
        pytest.fail(f"B11: run_phase бросил {type(exc).__name__} на SHIP с пустым diff: {exc}")
    assert outcome.status == "ship", f"B11: ожидался ship без исключений, got {outcome!r}"
    assert outcome.commit_sha == base, (
        f"B11: коммита нет, commit_sha должен остаться HEAD {base}, got {outcome.commit_sha!r}"
    )
    assert "- [x]" in plan_path.read_text(), "B11: галочка не отмечена [x] в плане вне репо"


@pytest.mark.asyncio
async def test_B17a_ghost_tester_path_not_protected(tmp_path: Path):
    repo = _repo(tmp_path)

    def tester_ghost(role, kwargs):
        assert role == "tester"
        return {
            "tests": [{"finding_id": "F1", "path": "tests/test_ghost.py", "name": "test_ghost"}],
            "skipped": [],
        }

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester", problem="returns nope")])),
            tester_ghost,
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, ledger = await _run_phase(repo, agents)
    kinds = [json.loads(line)["kind"] for line in ledger.path.read_text().splitlines()]
    assert outcome.status == "ship", f"B17a: ожидался ship, фаза ушла в {outcome!r}"
    assert "protected_violation" not in kinds, (
        f"B17a: несуществующий tests/test_ghost.py попал в защищённые: {kinds}"
    )


@pytest.mark.asyncio
async def test_B23_cancel_writes_interrupted_run_end(tmp_path: Path):
    repo = _repo(tmp_path)

    class _SleepAgents:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.player_started = asyncio.Event()

        async def run_role(self, role: str, **kwargs):
            self.calls.append(role)
            if role == "player":
                self.player_started.set()
                await asyncio.sleep(10)
                return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}
            if role == "judge":
                return ship()
            raise AssertionError(f"unexpected {role} call")

    agents = _SleepAgents()
    cfg = Config(plan_file="plan.md", working_dir=str(repo), max_rounds=8, test_timeout_s=30)
    task = asyncio.create_task(run(cfg, agents=agents))
    try:
        await asyncio.wait_for(agents.player_started.wait(), timeout=15)
    except TimeoutError:
        task.cancel()
        pytest.fail("B23: player так и не стартовал за 15 с")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    ledgers = sorted((repo / ".tero" / "runs").glob("*/ledger.jsonl"))
    assert len(ledgers) >= 1, "B23: нет ledger текущего рана"
    lines = ledgers[-1].read_text().splitlines()
    assert len(lines) >= 1, "B23: ledger пуст"
    last = json.loads(lines[-1])
    assert last.get("kind") == "run_end", f"B23: последняя строка ledger не run_end: {last!r}"
    assert last.get("reason") == "interrupted", f"B23: run_end без reason interrupted: {last!r}"


@pytest.mark.asyncio
async def test_B24_tester_skips_finding_with_protected_test(tmp_path: Path):
    repo = _repo(tmp_path)
    tester_prompts: list[str] = []

    def tester_first(role, kwargs):
        assert role == "tester"
        tester_prompts.append(kwargs.get("prompt") or "")
        write_file(
            repo,
            "tests/test_greet.py",
            "from src.greet import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
        )
        return {
            "tests": [{"finding_id": "F1", "path": "tests/test_greet.py", "name": "test_greet"}],
            "skipped": [],
        }

    def tester_second(role, kwargs):
        assert role == "tester"
        tester_prompts.append(kwargs.get("prompt") or "")
        return {"tests": [], "skipped": []}

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester", problem="returns nope")])),
            tester_first,
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(
                no_ship(
                    [finding(route="tester", problem="still nope")],
                    previous=[{"id": "F1", "state": "still_open"}],
                )
            ),
            tester_second,
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, _ = await _run_phase(repo, agents)
    assert outcome.status == "ship", f"B24: setup failed, ожидался ship: {outcome!r}"
    second_clean = len(tester_prompts) <= 1 or all(
        '"F1"' not in prompt for prompt in tester_prompts[1:]
    )
    assert second_clean, (
        f"B24: tester получил F1 повторно: вызовов {len(tester_prompts)}"
    )


@pytest.mark.asyncio
async def test_B25_both_deleted_suite_tests_restored_in_one_round(tmp_path: Path):
    repo = tmp_path
    _git(repo, "init")
    _git(repo, "config", "user.email", "tero@test")
    _git(repo, "config", "user.name", "tero")
    write_file(repo, "plan.md", PLAN)
    write_file(repo, "src/__init__.py", "")
    write_file(repo, "tests/test_a.py", "def test_a():\n    assert True\n")
    write_file(repo, "tests/test_b.py", "def test_b():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    seen: dict[str, bool] = {}

    def deleter(role, kwargs):
        assert role == "player"
        (repo / "tests/test_a.py").unlink()
        (repo / "tests/test_b.py").unlink()
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    def checker(role, kwargs):
        assert role == "player"
        seen["a"] = (repo / "tests/test_a.py").exists()
        seen["b"] = (repo / "tests/test_b.py").exists()
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    agents = FakeAgents(repo, [deleter, checker, judge_returns(ship())])
    await _run_phase(repo, agents)
    assert seen.get("a") is True and seen.get("b") is True, (
        f"B25: после одного раунда восстановлен не весь сюит: {seen}"
    )


def test_B27_missing_binary_returns_gate_result(tmp_path: Path):
    from src.loop.gates import _run

    try:
        result = _run(["no-such-binary-xyz"], "pytest", tmp_path, 1)
    except FileNotFoundError as exc:
        pytest.fail(f"B27: FileNotFoundError вместо GateResult: {exc}")
    assert result.ok is False, f"B27: ожидался ok=False, got {result!r}"
    assert result.exit_code == 127, f"B27: ожидался exit_code 127, got {result!r}"
    assert "no-such-binary-xyz" in result.output, (
        f"B27: имя бинарника потеряно из output: {result.output!r}"
    )


def test_B28_mark_phase_done_keeps_crlf(tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_bytes(b"# P\r\n\r\n## A\r\n\r\n- [ ] one\r\n")
    phases = load_phases(plan)
    mark_phase_done(plan, phases[0])
    raw = plan.read_bytes()
    assert b"- [x] one" in raw, f"B28: галочка не отмечена: {raw!r}"
    bare = raw.replace(b"\r\n", b"")
    assert b"\n" not in bare and b"\r" not in bare, (
        f"B28: CRLF-план превращён в LF: {raw!r}"
    )


@pytest.mark.asyncio
async def test_B38_new_conftest_is_suite_violation(tmp_path: Path):
    repo = _repo(tmp_path)
    seen: dict[str, bool] = {}

    def player_with_conftest(role, kwargs):
        assert role == "player"
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, "conftest.py", "# local hook\n")
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["conftest_gone"] = not (repo / "conftest.py").exists()
        return ship()

    agents = FakeAgents(repo, [player_with_conftest, check_judge])
    outcome, ledger = await _run_phase(repo, agents)
    assert outcome.status == "ship", f"B38: setup failed, ожидался ship: {outcome!r}"
    rows = [
        json.loads(line)
        for line in ledger.path.read_text().splitlines()
        if json.loads(line)["kind"] == "suite_violation"
    ]
    assert any(row.get("path") == "conftest.py" for row in rows), (
        f"B38: новый conftest.py не замечен как suite_violation: {[r.get('path') for r in rows]}"
    )
    assert seen.get("conftest_gone") is True, (
        "B38: conftest.py всё ещё на диске к моменту judge"
    )


@pytest.mark.asyncio
async def test_B39_gitignored_conftest_is_still_removed(tmp_path: Path):
    repo = _repo(tmp_path)
    seen: dict[str, bool] = {}

    def player_with_conftest(role, kwargs):
        assert role == "player"
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, "conftest.py", "# local hook\n")
        write_file(repo, ".gitignore", "conftest.py\n")
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["conftest_gone"] = not (repo / "conftest.py").exists()
        return ship()

    agents = FakeAgents(repo, [player_with_conftest, check_judge])
    outcome, ledger = await _run_phase(repo, agents)
    assert outcome.status == "ship", f"B39: setup failed, ожидался ship: {outcome!r}"
    rows = [
        json.loads(line)
        for line in ledger.path.read_text().splitlines()
        if json.loads(line)["kind"] == "suite_violation"
    ]
    assert any(row.get("path") == "conftest.py" for row in rows), (
        f"B39: gitignored conftest.py не замечен как suite_violation: {[r.get('path') for r in rows]}"
    )
    assert seen.get("conftest_gone") is True, (
        "B39: conftest.py всё ещё на диске к моменту judge"
    )


@pytest.mark.asyncio
async def test_B40_player_planted_agent_config_removed_before_judge(tmp_path: Path):
    repo = _repo(tmp_path)
    write_file(repo, ".claude/settings.json", '{"keep": true}')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "cfg")
    seen: dict[str, object] = {}

    def player_with_agent_config(role, kwargs):
        assert role == "player"
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, ".gemini/settings.json", '{"mcpServers": {"evil": {"command": "touch"}}}')
        write_file(repo, ".claude/settings.json", '{"hooks": "evil"}')
        return {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["gemini_gone"] = not (repo / ".gemini/settings.json").exists()
        claude_path = repo / ".claude/settings.json"
        seen["claude_text"] = claude_path.read_text() if claude_path.exists() else None
        return ship()

    agents = FakeAgents(repo, [player_with_agent_config, check_judge])
    outcome, ledger = await _run_phase(repo, agents)
    assert outcome.status == "ship", f"B40: setup failed, ожидался ship: {outcome!r}"
    assert seen.get("gemini_gone") is True, (
        "B40: .gemini/settings.json всё ещё на диске к моменту judge"
    )
    assert seen.get("claude_text") == '{"keep": true}', (
        f"B40: .claude/settings.json не откачен к исходному: {seen.get('claude_text')!r}"
    )
    rows = [
        json.loads(line)
        for line in ledger.path.read_text().splitlines()
        if json.loads(line)["kind"] == "agent_config_violation"
    ]
    assert any(row.get("path") == ".gemini/settings.json" for row in rows), (
        f"B40: нет agent_config_violation для .gemini/settings.json: {[r.get('path') for r in rows]}"
    )
    assert any(row.get("path") == ".claude/settings.json" for row in rows), (
        f"B40: нет agent_config_violation для .claude/settings.json: {[r.get('path') for r in rows]}"
    )
