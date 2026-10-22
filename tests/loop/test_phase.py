"""Phase loop against a real git repo and real pytest, scripted agents."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import detect
from src.loop.git import head, list_files, status_short
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from tests.loop import fakes as scripted

FakeAgents = scripted.FakeAgents
finding = scripted.finding
_git = scripted.git
judge_returns = scripted.judge_returns
no_ship = scripted.no_ship
player_writes = scripted.player_writes
ship = scripted.ship
write_file = scripted.write_file
write_tester = scripted.write_tester_file


PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


async def _run(repo: Path, agents: FakeAgents, cfg: Config | None = None):
    from src.loop.plan import load_phases, load_preamble

    cfg = cfg or _cfg()
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


@pytest.mark.asyncio
async def test_ship_first_round_commits_and_checks_boxes(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert outcome.commit_sha
    assert "- [x] greet() returns hello" in (repo / "plan.md").read_text()
    assert "src/greet.py" in list_files(repo)
    assert status_short(repo) == ""


@pytest.mark.asyncio
async def test_phase_passes_role_effort(tmp_path: Path):
    repo = _repo(tmp_path)
    captured: list[tuple[str, str]] = []

    def capture_player(role, kwargs):
        captured.append((role, kwargs.get("effort") or ""))
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return scripted.PLAYER_DONE

    def capture_judge(role, kwargs):
        captured.append((role, kwargs.get("effort") or ""))
        return ship()

    agents = FakeAgents(repo, [capture_player, capture_judge])
    await _run(repo, agents, _cfg(player_effort="high", judge_effort="max"))
    assert captured == [("player", "high"), ("judge", "max")]


@pytest.mark.asyncio
async def test_no_ship_tester_red_then_player_ship(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester", problem="returns nope")])),
            write_tester(
                repo,
                "tests/test_greet.py",
                "from src.greet import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
                "F1",
                "test_greet",
            ),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(
                ship(previous=[{"id": "F1", "state": "resolved"}])
            ),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert "tests/test_greet.py" in list_files(repo)
    assert "tester" in agents.calls


@pytest.mark.asyncio
async def test_green_tester_file_is_deleted(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(no_ship([finding(route="tester")])),
            write_tester(
                repo,
                "tests/test_greet.py",
                "from src.greet import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
                "F1",
                "test_greet",
            ),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert not (repo / "tests/test_greet.py").exists()


@pytest.mark.asyncio
async def test_player_cannot_edit_protected_tester_file(tmp_path: Path, capsys):
    repo = _repo(tmp_path)

    def tamper(_role, _kw):
        (repo / "tests/test_greet.py").write_text("def test_greet():\n    assert True\n")
        return {
            "status": "done",
            "changes": [],
            "start_here": [],
            "blocked_reason": "",
        }

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester")])),
            write_tester(
                repo,
                "tests/test_greet.py",
                "from src.greet import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
                "F1",
                "test_greet",
            ),
            tamper,
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    kinds = [
        json_kind
        for json_kind in [
            __import__("json").loads(line)["kind"]
            for line in (ledger.path.read_text().splitlines())
        ]
    ]
    assert "protected_violation" in kinds
    assert "assert greet() == 'hello'" in (repo / "tests/test_greet.py").read_text()
    out = capsys.readouterr().out
    assert "tests/test_greet.py" in out
    assert "откат" in out


@pytest.mark.asyncio
async def test_player_cannot_delete_checkpoint_test(tmp_path: Path, capsys):
    repo = _repo(tmp_path)

    def delete_smoke(_role, _kw):
        (repo / "tests/test_smoke.py").unlink()
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return {
            "status": "done",
            "changes": [],
            "start_here": [],
            "blocked_reason": "",
        }

    agents = FakeAgents(
        repo,
        [
            delete_smoke,
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert (repo / "tests/test_smoke.py").exists()
    kinds = [
        __import__("json").loads(line)["kind"] for line in ledger.path.read_text().splitlines()
    ]
    assert "suite_violation" in kinds
    assert outcome.status == "ship"
    out = capsys.readouterr().out
    assert "tests/test_smoke.py" in out
    assert "восстановлен" in out


@pytest.mark.asyncio
async def test_judge_tamper_is_restored(tmp_path: Path, capsys):
    repo = _repo(tmp_path)

    def dirty_judge(_role, _kw):
        write_file(repo, "src/greet.py", "def greet():\n    return 'TAMPER'\n")
        return ship()

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            dirty_judge,
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    assert "return 'hello'" in (repo / "src/greet.py").read_text()
    kinds = [
        __import__("json").loads(line)["kind"] for line in ledger.path.read_text().splitlines()
    ]
    assert "judge_tamper" in kinds
    out = capsys.readouterr().out
    assert "judge изменил рабочее дерево" in out


@pytest.mark.asyncio
async def test_no_progress_escalates_then_stops(tmp_path: Path):
    repo = _repo(tmp_path)
    stuck = no_ship(
        [finding(problem="still broken")],
        previous=[{"id": "F1", "state": "still_open"}],
    )
    # first NO_SHIP creates F1 (previous empty). subsequent stall: nothing resolved.
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
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "stop"
    assert outcome.reason == "stuck"
    assert outcome.escalated is True
    assert "opus" in agents.models
    kinds = [
        __import__("json").loads(line)["kind"] for line in ledger.path.read_text().splitlines()
    ]
    assert "escalate" in kinds
    assert "rollback" in kinds
    assert (repo / "src/greet.py").exists() is False or head(repo)


@pytest.mark.asyncio
async def test_same_red_fingerprint_is_no_progress(tmp_path: Path):
    repo = _repo(tmp_path)
    write_file(
        repo,
        "tests/test_smoke.py",
        "from src.greet import greet\n\ndef test_smoke():\n    assert greet() == 'hello'\n",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "red ready")

    def broken(_role, _kw):
        write_file(repo, "src/greet.py", "def greet():\n    return 'nope'\n")
        return {
            "status": "done",
            "changes": [],
            "start_here": [],
            "blocked_reason": "",
        }

    agents = FakeAgents(
        repo,
        [broken, broken, broken],
    )
    outcome, ledger = await _run(repo, agents, _cfg(escalation_model="opus"))
    assert "judge" not in agents.calls
    kinds = [
        __import__("json").loads(line)["kind"] for line in ledger.path.read_text().splitlines()
    ]
    assert kinds.count("escalate") >= 1
    assert outcome.status == "stop"
    assert outcome.reason == "stuck"


@pytest.mark.asyncio
async def test_needs_user_rolls_back(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(
                {
                    "verdict": "NEEDS_USER",
                    "previous": [],
                    "findings": [],
                    "user_question": "which greeting?",
                }
            ),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "stop"
    assert outcome.reason == "user"
    assert outcome.user_question == "which greeting?"
    assert not (repo / "src/greet.py").exists()
    kinds = [
        __import__("json").loads(line)["kind"] for line in ledger.path.read_text().splitlines()
    ]
    assert "rollback" in kinds


@pytest.mark.asyncio
async def test_max_rounds_stops_with_fuse(tmp_path: Path):
    repo = _repo(tmp_path)
    steps = []
    for i in range(3):
        steps.append(player_writes(repo, "src/greet.py", f"def greet():\n    return '{i}'\n"))
        steps.append(
            judge_returns(
                no_ship(
                    [finding(problem=f"new {i}")],
                    previous=[{"id": f"F{i}", "state": "resolved"}] if i else [],
                )
            )
        )
    agents = FakeAgents(repo, steps)
    outcome, _ = await _run(repo, agents, _cfg(max_rounds=2))
    assert outcome.status == "stop"
    assert outcome.reason == "fuse"


@pytest.mark.asyncio
async def test_discard_test_removes_protected_file(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester")])),
            write_tester(
                repo,
                "tests/test_greet.py",
                "from src.greet import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
                "F1",
                "test_greet",
            ),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(
                no_ship(
                    [finding(route="discard_test", id="F1", problem="wrong test")],
                    previous=[{"id": "F1", "state": "still_open"}],
                )
            ),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert not (repo / "tests/test_greet.py").exists()


@pytest.mark.asyncio
async def test_ship_with_env_stops_secrets(tmp_path: Path):
    repo = _repo(tmp_path)

    def player_with_env(_role, _kw):
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, ".env", "SECRET=1\n")
        return {
            "status": "done",
            "changes": [{"file": "src/greet.py", "what": "greet", "why": "c"}],
            "start_here": [],
            "blocked_reason": "",
        }

    agents = FakeAgents(
        repo,
        [player_with_env, judge_returns(ship())],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "stop"
    assert outcome.reason == "secrets"
    assert ".env" not in list_files(repo)


def _events(ledger: Ledger) -> list[dict]:
    import json

    return [json.loads(line) for line in ledger.path.read_text().splitlines()]


@pytest.mark.asyncio
async def test_invalid_judge_is_logged_with_errors_and_raw(tmp_path: Path, capsys):
    repo = _repo(tmp_path)
    invalid = {
        "__invalid__": True,
        "errors": ["'verdict' is a required property"],
        "raw": "looks good to me",
    }
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(invalid),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(invalid),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.reason == "stuck"
    rows = [e for e in _events(ledger) if e["kind"] == "invalid"]
    assert rows[0]["role"] == "judge"
    assert rows[0]["errors"] == ["'verdict' is a required property"]
    raw = (ledger.run_dir / "phase-00/round-01/judge.invalid.txt").read_text()
    assert "looks good to me" in raw
    assert "'verdict' is a required property" in raw
    out = capsys.readouterr().out
    assert "'verdict' is a required property" in out
    assert "эскалация" in out


@pytest.mark.asyncio
async def test_artifacts_are_json_and_verdict_is_printed(tmp_path: Path, capsys):
    import json

    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'a'\n"),
            judge_returns(no_ship([finding(problem="returns a, not hello")])),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    player = json.loads((ledger.run_dir / "phase-00/round-01/player.json").read_text())
    judge = json.loads((ledger.run_dir / "phase-00/round-01/judge.json").read_text())
    assert player["status"] == "done"
    assert judge["verdict"] == "NO_SHIP"
    out = capsys.readouterr().out
    assert "NO_SHIP" in out
    assert "returns a, not hello" in out


@pytest.mark.asyncio
async def test_provider_error_is_logged(tmp_path: Path, capsys):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo, [lambda _r, _k: {"__unavailable__": True, "error": "quota exceeded"}]
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.reason == "provider_unavailable"
    rows = [e for e in _events(ledger) if e["kind"] == "provider_error"]
    assert rows == [
        {**rows[0], "role": "player", "error": "quota exceeded"}
    ]
    out = capsys.readouterr().out
    assert "player" in out
    assert "quota exceeded" in out


@pytest.mark.asyncio
async def test_gate_green_line_is_printed(tmp_path: Path, capsys):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    out = capsys.readouterr().out
    assert "тесты: зелёные (код 0," in out
    assert " с)" in out


@pytest.mark.asyncio
async def test_gate_red_line_and_tail_are_printed(tmp_path: Path):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(
                repo,
                "src/greet.py",
                "def greet():\n    raise RuntimeError('boom')\n",
            ),
        ],
    )
    write_file(
        repo,
        "tests/test_greet.py",
        "from src.greet import greet\n\ndef test_greet():\n    greet()\n",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add failing test")

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        outcome, _ = await _run(repo, agents, _cfg(max_rounds=1))
    out = buf.getvalue()
    assert "тесты: КРАСНЫЕ (код" in out
    assert "boom" in out


@pytest.mark.asyncio
async def test_round_lines_carry_phase_prefix(tmp_path: Path, capsys):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester", problem="returns nope")])),
            write_tester(
                repo,
                "tests/test_greet.py",
                "from src.greet import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
                "F1",
                "test_greet",
            ),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    out = capsys.readouterr().out
    assert "[фаза 1/1 · раунд 1] player" in out
    assert "[фаза 1/1 · раунд 1] judge" in out
    assert "[фаза 1/1 · раунд 1] tester" in out
    assert "[раунд " not in out


@pytest.mark.asyncio
async def test_gate_timeout_line_is_printed(tmp_path: Path, capsys):
    from src.loop.gates import GateResult
    from src.loop import phase as phase_module

    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship()),
        ],
    )

    def fake_run_all(command, repo, timeout_s=900):
        return GateResult(
            ok=False,
            exit_code=124,
            output="partial output",
            command_name="pytest",
            timed_out=True,
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(phase_module, "run_all", fake_run_all)
    try:
        outcome, _ = await _run(repo, agents, _cfg(max_rounds=1, test_timeout_s=7))
    finally:
        monkeypatch.undo()
    assert outcome.status == "stop"
    out = capsys.readouterr().out
    assert "тесты: таймаут (7 с)" in out
    assert "тесты: КРАСНЫЕ" not in out


@pytest.mark.asyncio
async def test_tester_start_and_result_lines_are_printed(tmp_path: Path, capsys):
    repo = _repo(tmp_path)
    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester", problem="returns nope")])),
            write_tester(
                repo,
                "tests/test_greet.py",
                "from src.greet import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
                "F1",
                "test_greet",
            ),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    out = capsys.readouterr().out
    assert "tester" in out
    assert "тестов написано 1, red-подтверждено 1" in out
