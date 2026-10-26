"""Scoped gate: select_tests и полный прогон перед SHIP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import Config
from src.loop import gate_scope
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from tests.loop import fakes as scripted
from tests.loop.fakes import (
    FakeAgents, finding, judge_returns, no_ship, player_writes, ship, write_file,
)

PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _srepo(workdir: Path) -> tuple[Path, str]:
    """Мини-репо: checkpoint-коммит, дальше правки в рабочем дереве."""
    repo = workdir / "mini" if workdir.name != "mini" else workdir
    repo.mkdir(parents=True, exist_ok=True)
    scripted.git(repo, "init")
    write_file(repo, "src/__init__.py", "")
    write_file(repo, "src/gates.py", "VALUE = 1\n")
    write_file(
        repo,
        "tests/test_gates.py",
        "from src.gates import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
    )
    write_file(repo, "tests/test_other.py", "def test_other():\n    assert True\n")
    write_file(repo, "tests/test_structure.py", "def test_structure():\n    assert True\n")
    write_file(repo, "README.md", "hi\n")
    scripted.git(repo, "add", "-A")
    scripted.git(repo, "commit", "-m", "init")
    return repo, scripted.git(repo, "rev-parse", "HEAD").strip()


def test_changed_src_selects_referring_tests_plus_structure(tmp_path: Path):
    repo, checkpoint = _srepo(tmp_path)
    write_file(repo, "src/gates.py", "VALUE = 2\n")
    picked = gate_scope.select_tests(repo, checkpoint)
    assert picked is not None
    assert "tests/test_gates.py" in picked
    assert "tests/test_structure.py" in picked
    assert "tests/test_other.py" not in picked


def test_conftest_or_orphan_src_means_full_suite(tmp_path: Path):
    repo, checkpoint = _srepo(tmp_path)
    write_file(repo, "tests/conftest.py", "import pytest\n")
    assert gate_scope.select_tests(repo, checkpoint) is None

    repo2, checkpoint2 = _srepo(tmp_path / "s2")
    write_file(repo2, "src/nobody.py", "X = 1\n")
    scripted.git(repo2, "add", "-A")
    # src-модуль, на который не ссылается ни один тест, — полный набор.
    assert gate_scope.select_tests(repo2, checkpoint2) is None


def test_src_without_matches_alongside_changed_test_means_full_suite(tmp_path: Path):
    """Блокер: изменённый тест + src-модуль-сирота → None, а не частичный список."""
    repo, checkpoint = _srepo(tmp_path)
    write_file(
        repo,
        "tests/test_gates.py",
        "from src.gates import VALUE\n\ndef test_value():\n    assert VALUE == 2\n",
    )
    write_file(repo, "src/orphan.py", "X = 1\n")
    scripted.git(repo, "add", "-A")
    assert gate_scope.select_tests(repo, checkpoint) is None


def test_full_gate_write_tree_git_error_still_runs_all(tmp_path: Path, monkeypatch):
    """write_tree бросает GitError → run_all вызван, исключение не вылетает."""
    from src.loop.git import GitError

    def boom(_repo):
        raise GitError("boom")

    command, ok_result = _green_full()
    run_calls: list[list[str]] = []
    monkeypatch.setattr(
        gate_scope, "run_all",
        lambda command, repo, timeout_s=900: run_calls.append(list(command.run_all)) or ok_result,
    )
    monkeypatch.setattr("src.loop.git.write_tree", boom)
    ctx, ledger, st = _fakes(tmp_path)
    outcome = gate_scope.full_gate(
        command, tmp_path, ctx, ledger, st, timeout_s=30, enabled=True,
    )
    assert outcome == "green"
    assert len(run_calls) == 1
    assert st.last_green_full_tree is None


def test_only_md_change_runs_structure_only(tmp_path: Path):
    repo, checkpoint = _srepo(tmp_path)
    write_file(repo, "README.md", "hi v2\n")
    assert gate_scope.select_tests(repo, checkpoint) == ["tests/test_structure.py"]


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


async def _run(repo: Path, agents: FakeAgents, cfg: Config):
    from src.loop.plan import load_phases, load_preamble

    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    return await run_phase(
        phases[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=cfg,
        agents=agents,
        test_command=detect(repo),
        ledger=ledger,
    ), ledger


def _events(ledger: Ledger) -> list[dict]:
    return [json.loads(line) for line in ledger.path.read_text().splitlines()]


def _rev_count(repo: Path) -> int:
    return int(scripted.git(repo, "rev-list", "--count", "HEAD").strip())


def _counting(calls: list[str], real_run_all):
    def run(command, repo, timeout_s=900):
        calls.append("run_all")
        return real_run_all(command, repo=repo, timeout_s=timeout_s)
    return run


@pytest.mark.asyncio
async def test_scoped_green_ship_red_full_no_commit_next_player(tmp_path: Path):
    """Scoped зелёный, SHIP, полный красный: без коммита, дальше — игрок."""
    repo = _repo(tmp_path)
    write_file(
        repo,
        "tests/test_greet.py",
        "from src.greet import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
    )
    write_file(repo, "tests/test_extra.py", "def test_extra():\n    assert False\n")
    scripted.git(repo, "add", "-A")
    scripted.git(repo, "commit", "-m", "red extra")
    before = _rev_count(repo)

    def fix_extra(_role, _kw):
        write_file(repo, "tests/test_extra.py", "def test_extra():\n    assert True\n")
        return dict(scripted.PLAYER_DONE)

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
            fix_extra,
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents, _cfg(gate_scope="changed"))
    assert outcome.status == "ship"
    assert agents.calls == ["player", "judge", "player", "judge"]
    assert "full_gate_red" in [e["kind"] for e in _events(ledger)]
    assert _rev_count(repo) == before + 1


@pytest.mark.asyncio
async def test_full_fallback_green_ships_with_single_run_all(
    tmp_path: Path, monkeypatch
):
    """Гейт ушёл в полный (fallback) и зелёный: run_all ровно один за раунд."""
    import src.loop.phase as phase_module

    repo = _repo(tmp_path)
    gate_calls: list[str] = []
    full_calls: list[str] = []
    real_run_all = phase_module.run_all


    def fail_run_files(command, paths, repo, timeout_s=900):
        raise AssertionError("scoped не должен вызываться при fallback")

    monkeypatch.setattr(phase_module, "run_all", _counting(gate_calls, real_run_all))
    monkeypatch.setattr(phase_module, "run_files", fail_run_files)
    monkeypatch.setattr(gate_scope, "run_all", _counting(full_calls, real_run_all))
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents, _cfg(gate_scope="changed"))
    assert outcome.status == "ship"
    assert gate_calls == ["run_all"]
    assert full_calls == []


@pytest.mark.asyncio
async def test_empty_scope_runs_full_each_gate_never_scoped(
    tmp_path: Path, monkeypatch
):
    """gate_scope == '': run_all на каждом гейте, run_files не вызывается."""
    import src.loop.phase as phase_module

    repo = _repo(tmp_path)
    calls: list[str] = []
    real_run_all = phase_module.run_all

    def fail_run_files(command, paths, repo, timeout_s=900):
        raise AssertionError("run_files не должен вызываться")

    monkeypatch.setattr(phase_module, "run_all", _counting(calls, real_run_all))
    monkeypatch.setattr(phase_module, "run_files", fail_run_files)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(problem="returns nope")])),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents, _cfg())
    assert outcome.status == "ship"
    assert calls == ["run_all", "run_all"]


def _fakes(repo: Path):
    """Минимальные ctx/ledger/st для прямого вызова finish_gate/full_gate."""
    from types import SimpleNamespace

    class _Ctx:
        def __init__(self) -> None:
            self.emits: list[dict] = []
            self.states: list[dict] = []

        def round_file(self, name: str) -> Path:
            return repo / name

        def emit(self, kind: str, **kwargs) -> None:
            self.emits.append({"kind": kind, **kwargs})

        def update_state(self, **kwargs) -> None:
            self.states.append(kwargs)

    class _Ledger:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def event(self, kind: str, **kwargs) -> None:
            self.events.append({"kind": kind, **kwargs})

        def artifact(self, path: Path, content: str) -> None:
            pass

    st = SimpleNamespace(
        round=1, gate_scope="", gate_files=[], gate_command="",
        gate_exit_code=0, gate_output="", last_green_full_tree=None,
    )
    return _Ctx(), _Ledger(), st


def _git_repo(tmp_path: Path) -> Path:
    return _srepo(tmp_path)[0]


def _green_full():
    from src.loop.gates import GateResult, TestCommand

    command = TestCommand(
        name="pytest", run_all=["python", "-m", "pytest", "-q"], run_files=None
    )
    result = GateResult(ok=True, exit_code=0, output="1 passed", command_name="pytest")
    return command, result


def test_unmarked_green_gate_no_stage_no_scope(tmp_path: Path):
    """gate_scope == '': зелёный полный гейт не стейджит, scope нигде не пишет."""
    repo = _git_repo(tmp_path)
    write_file(repo, "README.md", "v2\n")
    write_file(repo, "new.txt", "new\n")

    ctx, ledger, st = _fakes(repo)
    command, result = _green_full()
    gate_scope.finish_gate(
        ctx, ledger, st, command=command, result=result, run=None,
        scope="полный", repo=repo, marked=False, timeout_s=30, duration=0.1,
    )
    assert scripted.git(repo, "diff", "--cached", "--name-only").strip() == ""
    gate_events = [e for e in ledger.events if e["kind"] == "gate"]
    assert len(gate_events) == 1 and "scope" not in gate_events[0]
    assert ctx.states and "scope" not in ctx.states[-1]["gate"]
    assert "scope" not in ctx.emits[-1]
    # Факт для промпта судьи при этом записан, дерево не запомнено.
    assert st.gate_command == "python -m pytest -q"
    assert st.gate_exit_code == 0
    assert st.last_green_full_tree is None


def test_marked_green_gate_records_tree_and_scope(tmp_path: Path):
    """marked=True (контроль): write_tree вызван, scope в событии/состоянии."""
    repo = _git_repo(tmp_path)
    write_file(repo, "README.md", "v2\n")

    ctx, ledger, st = _fakes(repo)
    command, result = _green_full()
    gate_scope.finish_gate(
        ctx, ledger, st, command=command, result=result, run=None,
        scope="полный", repo=repo, marked=True, timeout_s=30, duration=0.1,
    )
    assert st.last_green_full_tree
    gate_events = [e for e in ledger.events if e["kind"] == "gate"]
    assert gate_events[0]["scope"] == "полный"
    assert ctx.states[-1]["gate"]["scope"] == "полный"
    assert ctx.emits[-1]["scope"] == "полный"


def test_full_gate_skips_second_run_on_same_tree(tmp_path: Path, monkeypatch):
    """Зелёный полный гейт уже был на этом дереве — run_all не вызывается."""
    from src.loop.git import write_tree

    repo = _git_repo(tmp_path)
    _, _, st = _fakes(repo)
    st.last_green_full_tree = write_tree(repo)
    command, _ = _green_full()

    def fail_run_all(command, repo, timeout_s=900):
        raise AssertionError("run_all не должен вызываться повторно")

    monkeypatch.setattr(gate_scope, "run_all", fail_run_all)
    ctx, ledger, _ = _fakes(repo)
    outcome = gate_scope.full_gate(
        command, repo, ctx, ledger, st, timeout_s=30, enabled=True,
    )
    assert outcome is None


def test_judge_prompt_has_gate_fact_block():
    from src.loop.prompts import judge_prompt

    text = judge_prompt(
        preamble="P",
        contract="C",
        gate_fact=gate_scope.format_gate_fact(
            command="pytest -q", scope="scoped", files=["tests/test_a.py"],
            exit_code=0, tail="1 passed",
        ),
    )
    assert "## Тесты цикла" in text
    assert "не перезапускай" in text
    assert "tests/test_a.py" in text
