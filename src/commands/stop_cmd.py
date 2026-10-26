"""`tero stop`: ask the run owner to stop via inbox/stop.json."""

from __future__ import annotations

import time

from src.commands import resolve_run_dir
from src.loop.inbox import write_inbox_file
from src.loop.state import RunState


def run_stop(args) -> int:
    """Write inbox/stop.json unless the run already ended. Code 1 when not running."""
    import json as _json

    run_dir = resolve_run_dir(args)
    if run_dir is None:
        return 1
    try:
        state = RunState.read(run_dir / "state.json") or {}
    except Exception:
        state = {}
    if (state.get("status") or "") in ("done", "stopped"):
        print("прогон не идёт")
        return 1
    payload = _json.dumps(
        {"who": "human", "ts": time.time()}, ensure_ascii=False
    )
    if not write_inbox_file(run_dir, "stop.json", payload):
        print("не могу записать stop.json")
        return 1
    print("стоп запрошен")
    return 0
