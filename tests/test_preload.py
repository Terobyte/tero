"""A live run must survive edits to its own checkout (2026-10-28 crash).

`tero` runs from the repo it is installed from and imports lazily. Moving
src/loop/opus_decision.py while a run was going crashed it at the next lazy
import: ImportError: cannot import name 'opus_decision' from 'src.loop'.
"""

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LAZY_AFTER_MOVE = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, sys.argv[1])
    import src.cli_entry
    if sys.argv[2] == "preload":
        from src.preload import preload_all
        preload_all()
    # the refactor lands while the run is going
    loop = os.path.join(sys.argv[1], "src", "loop", "decision")
    os.rename(os.path.join(loop, "opus.py"), os.path.join(loop, "opus_moved.py"))
    from src.loop.decision import opus  # a lazy import deep inside a round
    print("ok", opus.DECISION_TIMEOUT_S)
""")


def _copy_src(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "src", tmp_path / "src",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def _run(tmp_path: Path, mode: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", LAZY_AFTER_MOVE, str(_copy_src(tmp_path)), mode],
                          capture_output=True, text=True, timeout=60)


def test_without_preload_a_moved_module_crashes_the_run(tmp_path):
    # pins the failure mode the preload exists for
    proc = _run(tmp_path, "lazy")
    assert proc.returncode != 0 and "cannot import name 'opus'" in proc.stderr


def test_preload_keeps_lazy_imports_working_after_a_move(tmp_path):
    proc = _run(tmp_path, "preload")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("ok")


def test_preload_imports_every_src_module():
    import pkgutil

    import src
    from src.preload import preload_all

    preload_all()
    names = {m.name for m in pkgutil.walk_packages(src.__path__, "src.")}
    assert names and names <= set(sys.modules)


@pytest.mark.parametrize("command", ["go", "resume", "wait", "hud"])
def test_long_running_commands_preload(command):
    from src.cli_entry import PRELOAD_COMMANDS

    assert command in PRELOAD_COMMANDS
