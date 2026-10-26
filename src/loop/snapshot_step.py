"""Round snapshots: take after the player, take+rollback on halt.

Extracted from phase.py (ceiling room for the stop flag);
_PhaseRunner keeps thin delegating methods with the same names.
"""

from __future__ import annotations


def take_after_player(runner: Any) -> str | None:
    """Snapshot the tree after the player call. None on failure."""
    from src.loop import snapshot as snapshot_mod

    run_id = runner.ledger.run_dir.name
    ref = snapshot_mod.ref_name(run_id, runner.phase.index, runner.state.round)
    try:
        snapshot_mod.take_snapshot(runner.repo, runner.checkpoint, ref)
        if not snapshot_mod.verify(runner.repo, ref):
            raise RuntimeError("snapshot verify failed")
    except Exception as exc:
        runner.ctx.update_state(snapshot_ref="")
        runner.ctx.emit(
            "snapshot_failed",
            ref=ref,
            base=runner.checkpoint,
            error=str(exc) or type(exc).__name__,
            text=f"снимок работы не сделан (`{ref}`)",
        )
        runner.ledger.event(
            "snapshot_failed",
            ref=ref,
            base=runner.checkpoint,
            error=str(exc) or type(exc).__name__,
            round=runner.state.round,
        )
        return None
    runner.ctx.update_state(snapshot_ref=ref, base=runner.checkpoint)
    return ref


def rollback_with_snapshot(runner: Any, reason: str) -> str | None:
    """Snapshot the tree, then roll back to the checkpoint. None on failure."""
    from src.loop import snapshot as snapshot_mod
    from src.loop.git import rollback

    run_id = runner.ledger.run_dir.name
    ref = snapshot_mod.ref_name(run_id, runner.phase.index, runner.state.round)
    try:
        snapshot_mod.take_snapshot(runner.repo, runner.checkpoint, ref)
        if not snapshot_mod.verify(runner.repo, ref):
            return None
    except Exception:
        return None
    runner.ctx.update_state(snapshot_ref=ref, base=runner.checkpoint)
    runner.ctx.emit(
        "snapshot",
        ref=ref,
        base=runner.checkpoint,
        reason=reason,
        text=f"снимок работы в `{ref}`",
    )
    runner.ledger.event(
        "snapshot", ref=ref, base=runner.checkpoint, reason=reason,
        round=runner.state.round,
    )
    rollback(runner.repo, runner.checkpoint)
    return ref
