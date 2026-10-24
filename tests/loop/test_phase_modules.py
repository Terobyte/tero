"""Шаги фазы в своих модулях: без цикла импорта, те же объекты, те же поля событий."""

from __future__ import annotations

import ast
from pathlib import Path

from src.config import Config
from src.loop.plan import Phase

ROOT = Path(__file__).resolve().parent.parent.parent


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


def _assert_no_phase_imports(*rels: str) -> None:
    for rel in rels:
        names = _imports(ROOT / rel)
        bad = {n for n in names if n.startswith(("src.loop.phase.", "src.loop.run"))}
        bad |= {n for n in names if n == "src.loop.phase"}
        assert not bad, f"{rel} imports {bad}"


def test_step_modules_import_no_phase():
    _assert_no_phase_imports(
        "src/loop/phase_ctx.py", "src/loop/compact_step.py", "src/loop/tester_step.py"
    )


def test_reexports_same_objects():
    import src.loop.phase as phase
    import src.loop.phase_ctx as phase_ctx

    assert phase._State is phase_ctx.PhaseState
    assert phase.PhaseOutcome is phase_ctx.PhaseOutcome
    assert phase._Next is phase_ctx._Next
    assert phase.K_MAX is phase_ctx.K_MAX


class _FakeStream:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, type: str, **fields) -> None:
        self.events.append((type, fields))


def test_emit_fields_unchanged(tmp_path, monkeypatch):
    from src.loop.phase_ctx import PhaseContext, PhaseState

    secret = "sk-test-0123456789abcdef"
    monkeypatch.setenv("TERO_TEST_API_KEY", secret)
    stream = _FakeStream()
    st = PhaseState(round=3)
    ctx = PhaseContext(
        phase=Phase(index=0, title="Demo", body="", checkboxes=[], start_line=0, end_line=1),
        total_phases=1,
        repo=tmp_path,
        plan_path=tmp_path / "plan.md",
        preamble="",
        cfg=Config(),
        agents=None,
        test_command=None,
        tester_enabled=False,
        ledger=None,
        run_dir=tmp_path,
        state_io=None,
        stream=stream,
        pulse=None,
        checkpoint="",
        contract="",
        suite_hashes={},
        guarded={},
        link_targets=set(),
        st=st,
    )

    ctx.emit("gate", ok=False, exit_code=1, role="gate", tail=f"token {secret}")
    ctx.emit("verdict", verdict="SHIP")

    (kind, first), (_, second) = stream.events
    assert kind == "gate"
    assert first["phase"] == "Demo"
    assert first["round"] == 3
    assert first["role"] == "gate"
    assert first["text"].startswith("тесты: красные (код 1)")
    assert secret not in first["text"] and secret not in first["tail"]
    assert second["role"] == "gate"  # role sticks from the previous event
    assert second["text"] == "SHIP"
