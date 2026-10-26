"""Stop helpers shared by _PhaseRunner: flag check + stale lock drop + halt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.loop.watchdog import stop_requested


def drop_stale_index_lock(repo: str | Path, run_dir: str | Path) -> None:
    """Remove a stale .git/index.lock under the owner lock. Never raises."""
    try:
        from src.loop.state import owner_lock

        lock_path = Path(repo) / ".git" / "index.lock"
        with owner_lock(Path(run_dir) / "state.json"):
            try:
                if lock_path.is_file() and not lock_path.is_symlink():
                    lock_path.unlink()
            except OSError:
                pass
    except Exception:
        pass


def check_stop(run_dir: Any) -> bool:
    """True when a stop was requested. Never raises (flag handles errors)."""
    return bool(stop_requested(run_dir))


def _live_stop(run_dir: Any) -> bool:
    """Stop for a still-running run, ignoring done/stopped owners."""
    try:
        from src.loop import stop_flag as _flag
        return bool(_flag.live(run_dir))
    except Exception:
        return check_stop(run_dir)


def halt(runner: Any, reason: str) -> Any:
    """Halt as user_stop when flagged (dropping a stale lock first)."""
    if _live_stop(runner.run_dir):
        drop_stale_index_lock(runner.repo, runner.run_dir)
        reason = "user_stop"
    return runner._decider().halt(reason)


def stop_hit(runner: Any) -> Any | None:
    """When flagged, halt as user_stop; else None."""
    if not _live_stop(runner.run_dir):
        return None
    drop_stale_index_lock(runner.repo, runner.run_dir)
    return halt(runner, "user_stop")


def hit_before(runner: Any) -> Any:
    """Stop before a step: a present flag must not launch more work."""
    return stop_hit(runner)


def hit_after_player(runner: Any, player_ok: Any) -> Any:
    """Stop after the player: a killed step stops at once."""
    if player_ok is True:
        return stop_hit(runner)
    return None if not _live_stop(runner.run_dir) else stop_hit(runner)


def hit_gate_red(runner: Any, gate: Any) -> Any:
    """Stop after a red gate: wins over more player calls."""
    if gate is None or bool(getattr(gate, "ok", False)):
        return None
    return stop_hit(runner)


def hit_after_verdict(runner: Any) -> Any:
    """Stop after the judge: a verdict must not become a commit."""
    return stop_hit(runner)


def hit_before_ship(runner: Any) -> Any:
    """Stop right before the commit: SHIP must not land after a stop."""
    return stop_hit(runner)


def checks(runner: Any) -> Any:
    """Bound stop checks for _PhaseRunner (phase.py stays under ceiling)."""
    pre = lambda: hit_before(runner)  # noqa: E731
    aft = lambda ok: hit_after_player(runner, ok)  # noqa: E731
    red = lambda gate: hit_gate_red(runner, gate)  # noqa: E731
    aft_judge = lambda: hit_after_verdict(runner)  # noqa: E731
    pre_ship = lambda: hit_before_ship(runner)  # noqa: E731
    return (pre, aft, red, pre, aft_judge, pre_ship)
