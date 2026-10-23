"""Tests for `tero review` (read-only snapshot inspector)."""

from types import SimpleNamespace

from src import cli_entry
from src.loop import snapshot as snapshot_module
from src.loop.state import RunState
from tests.loop import fakes


def _review_args(repo, run=None):
    return SimpleNamespace(working_dir=str(repo), run=run)


def test_review_prints_ref_and_diff(tmp_path, capsys):
    repo = fakes.init_repo(tmp_path, "# Demo\n\n## One\n\n- [ ] x\n")
    base = fakes.git(repo, "rev-parse", "HEAD").strip()
    fakes.write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
    ref = "refs/tero/testrun/phase-00-round01"
    snapshot_module.take_snapshot(repo, base, ref)

    run_dir = repo / ".tero" / "runs" / "20260101-000000-demo"
    run_dir.mkdir(parents=True)
    RunState(run_dir / "state.json").update(
        status="judge_pending",
        phase="One",
        round=1,
        snapshot_ref=ref,
        base=base,
    )

    code = cli_entry.run_review(_review_args(repo))

    out = capsys.readouterr().out
    assert code == 0
    assert ref in out
    assert base in out
    assert "greet" in out


def test_review_without_snapshot(tmp_path, capsys):
    run_dir = tmp_path / ".tero" / "runs" / "20260101-000000-demo"
    run_dir.mkdir(parents=True)
    RunState(run_dir / "state.json").update(status="judge_pending", round=1)

    code = cli_entry.run_review(_review_args(tmp_path))

    out = capsys.readouterr().out
    assert code == 1
    assert "снимка нет" in out
