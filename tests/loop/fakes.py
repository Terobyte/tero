"""Scripted agents that edit a real temp repo."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

Step = Callable[[str, dict[str, Any]], dict[str, Any]]


PLAYER_DONE = {
    "status": "done",
    "changes": [{"file": "src/greet.py", "what": "greet", "why": "contract"}],
    "start_here": ["src/greet.py:1"],
    "blocked_reason": "",
}


def ship(**overrides: Any) -> dict[str, Any]:
    data = {
        "verdict": "SHIP",
        "previous": [],
        "findings": [],
        "user_question": "",
    }
    data.update(overrides)
    return data


def no_ship(findings: list[dict[str, Any]], previous: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "verdict": "NO_SHIP",
        "previous": previous or [],
        "findings": findings,
        "user_question": "",
    }


def finding(
    *,
    severity: str = "blocking",
    route: str = "player",
    file: str = "src/greet.py",
    line: int | None = 1,
    problem: str = "missing greet",
    expected: str = "returns hello",
    id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "severity": severity,
        "route": route,
        "file": file,
        "line": line,
        "problem": problem,
        "expected": expected,
    }


class FakeAgents:
    def __init__(self, repo: Path, steps: list[Step]) -> None:
        self.repo = repo
        self.steps = list(steps)
        self.calls: list[str] = []
        self.models: list[str] = []

    async def run_role(self, role: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(role)
        self.models.append(kwargs.get("model") or "")
        if not self.steps:
            raise AssertionError(f"unexpected {role} call, script exhausted")
        return self.steps.pop(0)(role, kwargs)


def write_file(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def init_repo(tmp_path: Path, plan: str) -> Path:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "tero@test")
    git(tmp_path, "config", "user.name", "tero")
    write_file(tmp_path, "plan.md", plan)
    write_file(tmp_path, "src/__init__.py", "")
    write_file(tmp_path, "tests/test_smoke.py", "def test_smoke():\n    assert True\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "init")
    return tmp_path


def player_writes(repo: Path, rel: str, content: str, report: dict[str, Any] | None = None) -> Step:
    def step(role: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        assert role == "player"
        write_file(repo, rel, content)
        return report or PLAYER_DONE

    return step


def judge_returns(payload: dict[str, Any]) -> Step:
    def step(role: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        assert role == "judge"
        return payload

    return step


def write_tester_file(repo: Path, rel: str, content: str, finding_id: str, name: str) -> Step:
    def step(role: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        assert role == "tester"
        write_file(repo, rel, content)
        return {
            "tests": [{"finding_id": finding_id, "path": rel, "name": name}],
            "skipped": [],
        }

    return step
