"""Test command detection, scoped runs, and pytest fingerprints."""

from pathlib import Path

from src.loop.gates import detect, fingerprint, run_all, run_files


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_detect_pytest_from_tests_dir(tmp_path: Path):
    _write(tmp_path / "tests" / "test_a.py", "def test_ok():\n    assert True\n")
    cmd = detect(tmp_path)
    assert cmd is not None
    assert cmd.name == "pytest"
    assert cmd.run_all == ["python", "-m", "pytest", "-q"]
    assert cmd.run_files == ["python", "-m", "pytest", "-q", "{files}"]


def test_detect_pytest_from_pytest_ini(tmp_path: Path):
    _write(tmp_path / "pytest.ini", "[pytest]\n")
    cmd = detect(tmp_path)
    assert cmd is not None
    assert cmd.name == "pytest"


def test_detect_npm_when_package_has_test_script(tmp_path: Path):
    _write(tmp_path / "package.json", '{"scripts": {"test": "vitest"}}\n')
    cmd = detect(tmp_path)
    assert cmd is not None
    assert cmd.name == "npm"
    assert cmd.run_all == ["npm", "test", "--silent"]
    assert cmd.run_files == ["npm", "test", "--silent", "--", "{files}"]


def test_detect_skips_package_without_test_script(tmp_path: Path):
    _write(tmp_path / "package.json", '{"scripts": {"build": "tsc"}}\n')
    assert detect(tmp_path) is None


def test_detect_cargo(tmp_path: Path):
    _write(tmp_path / "Cargo.toml", "[package]\nname='x'\n")
    cmd = detect(tmp_path)
    assert cmd is not None
    assert cmd.name == "cargo"
    assert cmd.run_all == ["cargo", "test"]
    assert cmd.run_files is None


def test_detect_go(tmp_path: Path):
    _write(tmp_path / "go.mod", "module example.com/x\n")
    cmd = detect(tmp_path)
    assert cmd is not None
    assert cmd.name == "go"
    assert cmd.run_all == ["go", "test", "./..."]
    assert cmd.run_files is None


def test_detect_prefers_pytest_over_later_markers(tmp_path: Path):
    _write(tmp_path / "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path / "package.json", '{"scripts": {"test": "jest"}}\n')
    assert detect(tmp_path).name == "pytest"


def test_detect_override_command(tmp_path: Path):
    cmd = detect(tmp_path, test_command="make test", test_command_scoped="make test {files}")
    assert cmd is not None
    assert cmd.name == "override"
    assert cmd.run_all == ["make", "test"]
    assert cmd.run_files == ["make", "test", "{files}"]


def test_fingerprint_collects_failed_pytest_ids():
    output = """
tests/test_a.py::test_one FAILED
tests/test_a.py::test_two PASSED
tests/test_b.py::test_three FAILED
"""
    ids = fingerprint(type("R", (), {"output": output, "command_name": "pytest"})())
    assert ids == frozenset(
        {"tests/test_a.py::test_one", "tests/test_b.py::test_three"}
    )


def test_fingerprint_none_outside_pytest():
    result = type("R", (), {"output": "FAILED foo", "command_name": "cargo"})()
    assert fingerprint(result) is None


def test_run_all_green_pytest(tmp_path: Path):
    _write(tmp_path / "tests" / "test_ok.py", "def test_ok():\n    assert 1 == 1\n")
    result = run_all(detect(tmp_path), repo=tmp_path, timeout_s=30)
    assert result.ok is True
    assert result.exit_code == 0


def test_run_all_red_pytest_has_fingerprint(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_bad.py",
        "def test_bad():\n    assert False\n",
    )
    result = run_all(detect(tmp_path), repo=tmp_path, timeout_s=30)
    assert result.ok is False
    assert fingerprint(result) == frozenset({"tests/test_bad.py::test_bad"})


def test_run_files_scopes_pytest(tmp_path: Path):
    _write(tmp_path / "tests" / "test_ok.py", "def test_ok():\n    assert True\n")
    _write(tmp_path / "tests" / "test_bad.py", "def test_bad():\n    assert False\n")
    cmd = detect(tmp_path)
    good = run_files(cmd, ["tests/test_ok.py"], repo=tmp_path, timeout_s=30)
    bad = run_files(cmd, ["tests/test_bad.py"], repo=tmp_path, timeout_s=30)
    assert good.ok is True
    assert bad.ok is False


def test_gate_adds_faulthandler_for_pytest(tmp_path: Path, monkeypatch):
    import subprocess

    from src.loop import gates

    argv = ["python", "-m", "pytest", "-q"]
    out = gates.pytest_argv(argv)
    assert any(a.startswith("faulthandler_timeout=") for a in out)
    assert out != argv

    make_argv = ["make", "test"]
    assert gates.pytest_argv(make_argv) == make_argv

    own = ["python", "-m", "pytest", "-q", "-o", "faulthandler_timeout=10"]
    out_own = gates.pytest_argv(own)
    assert out_own == own

    _write(tmp_path / "tests" / "test_ok.py", "def test_ok():\n    assert 1 == 1\n")
    captured: dict = {}

    class _FakeProc:
        def __init__(self, *args, **kwargs):
            captured["argv"] = list(args[0])

        @property
        def pid(self):
            return 123456789

        def communicate(self, timeout=None):
            return ("", "")

        @property
        def returncode(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    gates.run_all(detect(tmp_path), repo=tmp_path, timeout_s=30)
    assert any("faulthandler_timeout=" in a for a in captured["argv"])


def test_gate_reports_hung_test_name(tmp_path: Path, monkeypatch):
    from src.loop import gates

    monkeypatch.setattr(gates, "GATE_TEST_HANG_S", 1)
    _write(tmp_path / "tests" / "test_hang.py", "import time\ndef test_hang():\n    time.sleep(4)\n")
    result = run_all(detect(tmp_path), repo=tmp_path, timeout_s=2)
    assert result.ok is False
    assert result.timed_out is True
    tail = "\n".join(result.output.splitlines()[-41:])
    assert "test_hang" in tail
    assert " in test_hang" in result.output


def test_gate_timeout_kills_process_group(tmp_path: Path):
    import os
    import subprocess

    from src.loop import gates

    script = tmp_path / "spawn_sleep.py"
    script.write_text(
        "import subprocess, time\n"
        "p = subprocess.Popen(['sleep', '30'])\n"
        "print(f'CHILD {p.pid}', flush=True)\n"
        "time.sleep(30)\n"
    )
    cmd = type("C", (), {"run_all": ["python", str(script)], "name": "probe"})()
    result = gates._run(cmd.run_all, cmd.name, tmp_path, 2)
    assert result.ok is False
    assert result.timed_out is True
    child_pid = None
    for line in result.output.splitlines():
        if "CHILD" in line:
            child_pid = int(line.split()[-1])
    assert child_pid is not None
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        # cleanup before failing
        try:
            os.kill(child_pid, 9)
        except OSError:
            pass
        raise AssertionError("child sleep still alive after gate timeout")
    assert subprocess.run(["true"]).returncode == 0


def test_pytest_argv_detects_pytest_binary_path():
    from src.loop import gates

    out = gates.pytest_argv(["./.venv/bin/pytest", "-q"])
    assert any(a.startswith("faulthandler_timeout=") for a in out)


def test_pytest_argv_keeps_override_ini():
    from src.loop import gates

    own_eq = ["python", "-m", "pytest", "-q", "--override-ini=faulthandler_timeout=10"]
    assert gates.pytest_argv(own_eq) == own_eq
    own_sp = ["python", "-m", "pytest", "-q", "--override-ini", "faulthandler_timeout=10"]
    assert gates.pytest_argv(own_sp) == own_sp


def test_pytest_argv_keeps_o_equals_form():
    from src.loop import gates

    own = ["python", "-m", "pytest", "-q", "-o=faulthandler_timeout=10"]
    assert gates.pytest_argv(own) == own


def test_pytest_argv_keeps_glued_o_form():
    from src.loop import gates

    own = ["python", "-m", "pytest", "-q", "-ofaulthandler_timeout=10"]
    assert gates.pytest_argv(own) == own


def test_gate_timeout_without_dump_still_names_test(tmp_path):
    from src.loop import gates

    script = tmp_path / "slow_no_dump.py"
    script.write_text(
        "import time\n"
        "print('tests/test_slow.py::test_slow ...', flush=True)\n"
        "time.sleep(30)\n"
    )
    result = gates._run(["python", str(script)], "probe", tmp_path, 2)
    assert result.ok is False
    assert result.timed_out is True
    tail = "\n".join(result.output.splitlines()[-41:])
    assert "tests/test_slow.py::test_slow" in tail


def test_run_resets_watchdog_on_start_error(tmp_path, monkeypatch):
    import subprocess

    from src.loop import gates
    from src.loop import watchdog as _wd

    def _boom(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    _wd.note_step_before({123456})
    try:
        gates._run(["python", "-m", "pytest", "-q"], "pytest", tmp_path, 30)
    finally:
        assert _wd.step_before() is None


def test_sigint_kills_group_and_reraises(tmp_path, monkeypatch):
    import subprocess

    import pytest

    from src.loop import gates

    killed = {}

    class _FakeProc:
        pid = 424242

        def communicate(self, timeout=None):
            raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(gates, "_kill_group", lambda pid, kill=False: killed.setdefault("pid", pid))
    with pytest.raises(KeyboardInterrupt):
        gates._run(["sleep", "30"], "probe", tmp_path, 30)
    assert killed.get("pid") == 424242


def test_pytest_argv_ignores_pytest_prefixed_script():
    from src.loop import gates

    argv = ["python", "pytest_helper.py", "-q"]
    assert gates.pytest_argv(argv) == argv
    argv_m = ["python", "-m", "pytest_helper"]
    assert gates.pytest_argv(argv_m) == argv_m


def test_hung_report_picks_last_test():
    from src.loop import gates

    output = "\n".join(
        [
            "tests/test_a.py::test_a PASSED",
            "Timeout (0:00:05)!",
            '  File "/w/tests/test_a.py", line 10 in test_a',
            "    time.sleep(60)",
            "Timeout (0:00:05)!",
            '  File "/w/tests/test_b.py", line 20 in test_b',
            "    time.sleep(60)",
        ]
    )
    head = gates.hung_test_report(output).splitlines()[0]
    assert "test_b" in head
    assert "test_a" not in head


def test_hung_report_normalizes_absolute_path():
    from src.loop import gates

    output = "\n".join(
        [
            "Timeout (0:00:05)!",
            '  File "/tmp/xyz/tests/test_hang.py", line 5 in test_hang',
            "    time.sleep(60)",
        ]
    )
    assert gates.hung_test_report(output).splitlines()[0] == "tests/test_hang.py::test_hang"


def test_gate_timeout_cleanup_stays_fast(tmp_path):
    import time

    from src.loop import gates

    start = time.monotonic()
    result = gates._run(["sleep", "30"], "probe", tmp_path, 1)
    elapsed = time.monotonic() - start
    assert result.ok is False
    assert result.timed_out is True
    assert elapsed < 5


def test_timeout_with_only_passed_output_omits_name():
    from src.loop import gates

    output = "tests/test_done.py::test_done PASSED\n"
    assert gates.hung_test_report(output, on_timeout=True) == ""
