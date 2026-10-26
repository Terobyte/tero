"""An unexpected exception inside a phase stops the run cleanly (v4 BUG-001).

Without this the exception left `tero go` with a traceback, no `run_end` and the
player's half-done work staged, so the next run refused a dirty tree. Now the
tree goes into a snapshot ref first (the work is not thrown away), then rolls
back to the head the phase started from, and the run stops with `crash`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.errors import scrub_secrets
from src.loop.phase_ctx import PhaseOutcome


def phase_base(repo: Any) -> str:
    """Head before the phase: the rollback target. "" when unknown. Never raises."""
    from src.loop.git import head

    try:
        return head(Path(repo)) if repo else ""
    except Exception:
        return ""


def crash_outcome(repo: Any, base: str, run_dir: Path, phase_index: int, exc: BaseException) -> PhaseOutcome:
    """Snapshot + rollback to `base`, then a stop outcome. Never raises."""
    from src.loop import snapshot as snap
    from src.loop.git import rollback

    ref = snap.ref_name(Path(run_dir).name, phase_index, 0).replace("-round00", "-crash")
    saved = ""
    if repo and base:
        try:
            snap.take_snapshot(Path(repo), base, ref)
            if snap.verify(Path(repo), ref):
                saved = ref
                rollback(Path(repo), base)
                # `tero resume` of a stopped run continues from snapshot_ref + base
                from src.loop.state import RunState

                RunState(Path(run_dir) / "state.json").update(snapshot_ref=ref, base=base)
        except Exception:
            saved = ""
    why = scrub_secrets(f"{type(exc).__name__}: {exc}")[:300]
    where = f"работа в `{saved}`, дерево откатено" if saved else "снимок не сделан, дерево не тронуто"
    return PhaseOutcome(status="stop", reason="crash", stop_text=f"сбой tero: {why}; {where}")
