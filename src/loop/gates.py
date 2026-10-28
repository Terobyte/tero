"""Detect and run the repo's test command."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


GATE_TEST_HANG_S = 120

KILL_GROUP_GRACE_S = 1.0

_STACK_LINE_RE = re.compile(r'File "([^"]+)", line \d+ in (test_\w+)')

_COMPLETED_RE = re.compile(r"\b(PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)\b")


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


def tail_lines(output: str, limit: int = 40) -> str:
    """Last ~limit lines of gate output for stream/state (never streamed live)."""
    return "\n".join(output.splitlines()[-limit:])


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
    from src.loop import watchdog as _wd

    from src.process_guard import ProcessGuard
    from src import proc_tag

    resolved = pytest_argv([_resolve_python(part) for part in argv])
    before = ProcessGuard().snapshot_pids()
    _wd.note_step_before(before)
    env, tag = proc_tag.tagged_env()
    started = proc_tag.spawn_time()
    try:
        return _run_tagged(resolved, name, repo, timeout_s, env, before)
    finally:
        # B21: workers that left the group (own session, parent gone) die with the gate
        proc_tag.sweep(tag, since=started)


def _run_tagged(resolved: list[str], name: str, repo: Path, timeout_s: int,
                env: dict[str, str], before: set[int]) -> GateResult:
    from src.loop import watchdog as _wd

    try:
        proc = subprocess.Popen(
            resolved,
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # test suites print non-UTF-8 bytes; that must not crash the gate (v4 BUG-002)
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        _wd.note_step_before(None)
        return GateResult(
            ok=False,
            exit_code=127,
            output=f"{resolved[0]}: {exc}",
            command_name=name,
        )
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except KeyboardInterrupt:
        _kill_group(proc.pid)
        _wd._kill_new_processes(before)
        raise
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        try:
            out, _ = proc.communicate(timeout=10)
        except Exception:
            try:
                _kill_group(proc.pid, kill=True)
            except Exception:
                pass
            out = ""
            proc.wait()
        output = f"{out or ''}\n{hung_test_report(out or '', on_timeout=True)}".rstrip() if out else ""
        _wd._kill_new_processes(before)
        _wd.note_step_before(None)
        return GateResult(
            ok=False,
            exit_code=124,
            output=output,
            command_name=name,
            timed_out=True,
        )
    finally:
        _wd.note_step_before(None)
    output = (out or "") + hung_suffix(out or "")
    return GateResult(
        ok=proc.returncode == 0,
        exit_code=proc.returncode,
        output=output,
        command_name=name,
        timed_out=False,
    )


def _is_pytest_argv(argv: list[str]) -> bool:
    for token in argv:
        prog = Path(token).name
        if prog in ("pytest", "pytest.exe") or (
            prog.endswith(".py") and Path(token).stem == "pytest"
        ):
            return True
    if "-m" in argv:
        try:
            nxt = argv[argv.index("-m") + 1]
        except IndexError:
            return False
        return nxt == "pytest" or Path(nxt).name in ("pytest", "pytest.exe")
    return False


def _has_own_faulthandler_timeout(argv: list[str]) -> bool:
    for i, token in enumerate(argv):
        if token == "faulthandler_timeout" or token.startswith("faulthandler_timeout="):
            return True
        if token == "-o" and i + 1 < len(argv):
            if argv[i + 1].split("=", 1)[0] == "faulthandler_timeout":
                return True
        elif token == "-o=" and i + 1 < len(argv):
            if argv[i + 1].split("=", 1)[0] == "faulthandler_timeout":
                return True
        elif token.startswith("-o") and len(token) > 2:
            rest = token[2:].lstrip("=")
            if rest.split("=", 1)[0] == "faulthandler_timeout":
                return True
        if token == "--override-ini" and i + 1 < len(argv):
            if argv[i + 1].split("=", 1)[0] == "faulthandler_timeout":
                return True
        if token.startswith("--override-ini="):
            rest = token[len("--override-ini="):]
            if rest.split("=", 1)[0] == "faulthandler_timeout":
                return True
    return False


def pytest_argv(argv: list[str]) -> list[str]:
    """Add `-o faulthandler_timeout=` to a pytest argv (never overrides existing)."""
    if not _is_pytest_argv(argv):
        return list(argv)
    if _has_own_faulthandler_timeout(argv):
        return list(argv)
    return [*argv, "-o", f"faulthandler_timeout={GATE_TEST_HANG_S}"]


def _hung_name_for_frame(path: str, fn: str) -> str:
    norm = path.replace("\\", "/")
    idx = norm.find("tests/")
    if idx >= 0:
        return f"{norm[idx:]}::{fn}"
    return f"tests/...::{fn}"


def _hung_test_names(lines: list[str]) -> list[str]:
    names: list[str] = []
    node_re = re.compile(r"(test_[\w/.-]*\.py::test_\w+|tests/\S+?::test_\w+)")
    for line in lines:
        m = _STACK_LINE_RE.search(line)
        if m:
            fn = m.group(2)
            name = _hung_name_for_frame(m.group(1), fn)
            if names and names[-1] == name:
                continue
            names.append(name)
        m2 = node_re.search(line)
        # Timeout without a faulthandler dump: a parsed node id is only a
        # currently-running test when the line carries no completion verdict.
        if (
            m2
            and not _COMPLETED_RE.search(line)
            and (not names or names[-1] != m2.group(1))
        ):
            names.append(m2.group(1))
    return names


def hung_test_report(output: str, on_timeout: bool = False) -> str:
    """`tests/...::test_x` + last ≤40 stack lines, or '' when no faulthandler dump."""
    has_dump = "Timeout (0:" in output or "Thread 0x" in output
    if not has_dump and not on_timeout:
        return ""
    lines = output.splitlines()
    if has_dump:
        dump_idx = next(
            (i for i, line in reversed(list(enumerate(lines))) if "Timeout (0:" in line or "Thread 0x" in line),
            None,
        )
        tail = lines[dump_idx:] if dump_idx is not None else lines[-40:]
        names = _hung_test_names(tail)
        head = names[-1] if names else "тест неизвестен"
        return "\n".join([head, *tail[-40:]])
    names = _hung_test_names(lines)
    return names[-1] if names else ""


def hung_suffix(output: str) -> str:
    report = hung_test_report(output)
    return f"\n{report}" if report else ""


def _kill_group(pid: int, kill: bool = False) -> None:
    sig = signal.SIGKILL if kill else signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if kill:
        return
    deadline = time.time() + KILL_GROUP_GRACE_S
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except (PermissionError, OSError):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _resolve_python(token: str) -> str:
    if token == "python":
        return sys.executable
    return token
