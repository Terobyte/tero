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
