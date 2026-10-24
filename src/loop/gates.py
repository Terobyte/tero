"""Detect and run the repo's test command."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


_PYTEST_FAILED = re.compile(
    r"^(?:FAILED\s+(\S+)|(\S+::\S+)\s+FAILED)\b",
    re.MULTILINE,
)


@dataclass(frozen=True)
class TestCommand:
    __test__ = False
    name: str
    run_all: list[str]
    run_files: list[str] | None


@dataclass(frozen=True)
class GateResult:
    ok: bool
    exit_code: int
    output: str
    command_name: str
    timed_out: bool = False


def detect(
    repo: str | Path,
    test_command: str = "",
    test_command_scoped: str = "",
) -> TestCommand | None:
    if test_command.strip():
        scoped = (
            shlex.split(test_command_scoped) if test_command_scoped.strip() else None
        )
        return TestCommand(
            name="override",
            run_all=shlex.split(test_command),
            run_files=scoped,
        )

    root = Path(repo)
    if _has_pytest(root):
        return TestCommand(
            name="pytest",
            run_all=["python", "-m", "pytest", "-q"],
            run_files=["python", "-m", "pytest", "-q", "{files}"],
        )

    package = root / "package.json"
    if package.is_file() and _npm_has_test(package):
        return TestCommand(
            name="npm",
            run_all=["npm", "test", "--silent"],
            run_files=["npm", "test", "--silent", "--", "{files}"],
        )

    if (root / "Cargo.toml").is_file():
        return TestCommand(name="cargo", run_all=["cargo", "test"], run_files=None)

    if (root / "go.mod").is_file():
        return TestCommand(name="go", run_all=["go", "test", "./..."], run_files=None)

    return None


def run_all(
    command: TestCommand,
    repo: str | Path,
    timeout_s: int = 900,
) -> GateResult:
    return _run(command.run_all, command.name, Path(repo), timeout_s)


def run_files(
    command: TestCommand,
    paths: list[str],
    repo: str | Path,
    timeout_s: int = 900,
) -> GateResult:
    if not command.run_files:
        raise ValueError("this test command has no scoped runner")
    argv = _expand_files(command.run_files, paths)
    return _run(argv, command.name, Path(repo), timeout_s)


def fingerprint(result: GateResult) -> frozenset[str] | None:
    if result.command_name != "pytest":
        return None
    return frozenset(m.group(1) or m.group(2) for m in _PYTEST_FAILED.finditer(result.output)) or None


def print_red_tail(output: str) -> None:
    for line in output.splitlines()[-15:]:
        print(f"    {line}", flush=True)


def _has_pytest(root: Path) -> bool:
    if (root / "pyproject.toml").is_file():
        return True
    if (root / "pytest.ini").is_file():
        return True
    if (root / "conftest.py").is_file():
        return True
    tests = root / "tests"
    return tests.is_dir() and any(tests.glob("*.py"))


def _npm_has_test(package: Path) -> bool:
    try:
        data = json.loads(package.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    scripts = data.get("scripts")
    return isinstance(scripts, dict) and "test" in scripts


def _expand_files(argv: list[str], paths: list[str]) -> list[str]:
    out: list[str] = []
    for token in argv:
        if token == "{files}":
            out.extend(paths)
        else:
            out.append(token)
    return out


def _run(argv: list[str], name: str, repo: Path, timeout_s: int) -> GateResult:
    resolved = [_resolve_python(part) for part in argv]
    try:
        proc = subprocess.Popen(
            resolved,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Test suites love non-UTF-8 output; it must not crash the gate.
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        return GateResult(
            ok=False,
            exit_code=127,
            output=f"{resolved[0]}: {exc}",
            command_name=name,
        )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # Own process group: killpg takes out grandchildren (dev servers,
        # daemons) the suite spawned, not just the direct child.
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return GateResult(
            ok=False,
            exit_code=124,
            output=(stdout or "") + (stderr or ""),
            command_name=name,
            timed_out=True,
        )
    output = (stdout or "") + (stderr or "")
    return GateResult(
        ok=proc.returncode == 0,
        exit_code=proc.returncode,
        output=output,
        command_name=name,
    )


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the subprocess and everything left in its process group."""
    if hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def _resolve_python(token: str) -> str:
    if token == "python":
        return sys.executable
    return token
