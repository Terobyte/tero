"""Record run history for coach-player sessions."""

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class TurnDetail:
    """Per-turn details."""
    role: str  # "player" or "coach"
    duration_s: float
    tools_used: int


@dataclass
class RunRecord:
    """A complete coach-player session record."""
    run_id: str
    timestamp: str
    requirements_file: str
    turns_used: int
    max_turns: int
    status: str  # "approved", "max_turns_reached", "failed", "interrupted"
    total_duration_s: float
    turn_details: list[TurnDetail]


class RunRecorder:
    """Manages run history storage."""

    def __init__(self, knowledge_dir: str = ".g3/knowledge"):
        self.knowledge_dir = Path(knowledge_dir)
        self.runs_file = self.knowledge_dir / "runs.jsonl"

    def _ensure_dir(self):
        """Ensure knowledge directory exists."""
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

    def record(self, record: RunRecord) -> None:
        """Append a run record to history."""
        self._ensure_dir()

        # Convert to dict, handling nested dataclasses
        data = asdict(record)

        with open(self.runs_file, "a") as f:
            f.write(json.dumps(data) + "\n")

    def history(self, limit: int = 10) -> list[RunRecord]:
        """Retrieve recent run records."""
        if not self.runs_file.exists():
            return []

        records = []
        with open(self.runs_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                # Reconstruct TurnDetail objects
                data["turn_details"] = [
                    TurnDetail(**td) for td in data.get("turn_details", [])
                ]
                records.append(RunRecord(**data))

        # Return most recent first, limited
        return list(reversed(records[-limit:])) if records else []


def generate_run_id() -> str:
    """Generate a unique run ID based on timestamp and random suffix."""
    import random
    return f"run-{int(time.time() * 1000)}-{random.randint(0, 9999):04d}"
