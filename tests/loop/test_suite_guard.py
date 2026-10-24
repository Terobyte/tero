"""suite_guard.py: защита набора тестов вне phase.py, без цикла импорта."""

from __future__ import annotations

import ast
import os
from pathlib import Path

from src.config import Config
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.plan import load_phases, load_preamble
from tests.loop.fakes import FakeAgents, git, init_repo, write_file

ROOT = Path(__file__).resolve().parent.parent.parent

PLAN = """# Demo

## Only

- [ ] one
"""


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_suite_guard_imports_no_phase():
    names = _imports(ROOT / "src/loop/suite_guard.py")
    assert not {n for n in names if n.startswith(("src.loop.phase", "src.loop.run"))}


def test_is_test_path_same_object():
    import src.loop.phase as phase
    import src.loop.suite_guard as suite_guard

    assert phase.is_test_path is suite_guard.is_test_path


def test_snapshot_guarded_direct(tmp_path: Path):
    from src.loop import suite_guard
    from src.loop.phase import _PhaseRunner

    repo = init_repo(tmp_path, PLAN)
    write_file(repo, "conftest.py", "")
    write_file(repo, "AGENTS.md", "be nice\n")
    write_file(repo, "shared/notes.md", "x\n")
    os.symlink("shared", repo / ".claude")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "guarded")

    suite_hashes = suite_guard.file_hashes(repo, suite_guard._suite_paths(repo))
    direct, targets = suite_guard._snapshot_guarded(repo, suite_hashes)

    runner = _PhaseRunner(
        phase=load_phases(repo / "plan.md")[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=Config(plan_file="plan.md"),
        agents=FakeAgents(repo, []),
        test_command=detect(repo),
        ledger=Ledger(new_run_dir(repo, "demo")),
    )
    assert direct == runner.guarded
    assert targets == runner.link_targets
    assert direct["AGENTS.md"] == ("file", b"be nice\n")
    assert direct[".claude"] == ("link", "shared")
    assert direct["shared/notes.md"] == ("file", b"x\n")
    assert "conftest.py" not in direct  # tracked suite file: guarded by suite_hashes
