"""CLI command handlers and the run dir they act on."""

from __future__ import annotations

from pathlib import Path


def _latest_run_dir(repo: Path) -> Path | None:
    runs = repo / ".tero" / "runs"
    if not runs.is_dir():
        return None
    try:
        entries = sorted(
            (p for p in runs.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )
    except OSError:
        return None
    return entries[-1] if entries else None


def resolve_run_dir(args) -> Path | None:
    """`--run` or the latest run in .tero/runs; None (after «Прогонов нет.») otherwise."""
    repo = Path(args.working_dir).expanduser().resolve()
    run_dir = Path(args.run).expanduser() if getattr(args, "run", None) else None
    if run_dir is None:
        run_dir = _latest_run_dir(repo)
    if run_dir is None or not (run_dir / "state.json").is_file():
        print("Прогонов нет.")
        return None
    return run_dir
