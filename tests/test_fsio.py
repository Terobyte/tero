"""src/fsio.py: one atomic write and one tolerant JSON read for all of src/."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.fsio import atomic_write_text, read_json_dict

ROOT = Path(__file__).resolve().parent.parent


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def test_atomic_write_failure_keeps_old(tmp_path, monkeypatch):
    target = tmp_path / "f.txt"
    target.write_text("old", encoding="utf-8")

    def broken_os_replace(src, dst):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(os, "replace", broken_os_replace)
        with pytest.raises(OSError):
            atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]

    def broken_path_replace(self, dst):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(Path, "replace", broken_path_replace)
        with pytest.raises(OSError):
            atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_atomic_write_keeps_mode_and_crlf(tmp_path):
    target = tmp_path / "plan.md"
    target.write_bytes(b"a\r\nb\r\n")
    target.chmod(0o644)
    atomic_write_text(target, "x\r\ny\r\n", newline="")
    assert target.stat().st_mode & 0o777 == 0o644
    assert target.read_bytes() == b"x\r\ny\r\n"

    fresh = tmp_path / "new.md"
    atomic_write_text(fresh, "z\n")
    assert fresh.stat().st_mode & 0o777 == 0o666 & ~_umask()


def test_atomic_write_utf8_under_c_locale(tmp_path):
    target = tmp_path / "out.txt"
    text = ascii("кириллица")
    code = f"import sys; from src.fsio import atomic_write_text; atomic_write_text(sys.argv[1], {text})"
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    subprocess.run(
        [sys.executable, "-c", code, str(target)], cwd=ROOT, env=env, check=True, timeout=30
    )
    assert target.read_bytes() == "кириллица".encode("utf-8")


def test_read_json_dict_cases(tmp_path):
    missing = tmp_path / "missing.json"
    junk = tmp_path / "junk.json"
    junk.write_text("{not json", encoding="utf-8")
    listed = tmp_path / "list.json"
    listed.write_text("[1, 2]", encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    assert read_json_dict(missing) is None
    assert read_json_dict(junk) is None
    assert read_json_dict(listed) is None
    assert read_json_dict(good) == {"a": 1}


def test_mkstemp_only_in_fsio():
    allowed = {"src/fsio.py", "src/loop/snapshot.py", "src/providers/codex.py"}
    found = set()
    for path in sorted(ROOT.glob("src/**/*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.alias):
                name = node.name
            if name in ("mkstemp", "NamedTemporaryFile"):
                found.add(path.relative_to(ROOT).as_posix())
    assert found <= allowed, found - allowed
    assert "src/fsio.py" in found
