"""Start-gate refusal: `stopped` with a counted `resume` (plan 09, phase 3).

Extract of `run._fail_start`: the same write, now with the model `resume`
so the phone sees `refused` with a reason instead of an empty field.
"""

from __future__ import annotations

from src.loop.session import RunOutcome, RunSession
from src.loop.state import RunState
from src.loop.transition import start_fail_transition


def fail_start(
    state: RunState,
    run_dir: Path,
    session: RunSession,
    reason: str,
    doing: str = "",
    status: str = "error",
) -> RunOutcome:
    """Refuse the start: run to `stopped`, release the session, report."""
    start_fail_transition(state, run_dir, reason=reason, doing=doing)
    session.release()
    return RunOutcome(status=status, exit_code=1, reason=reason)
