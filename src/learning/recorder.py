"""Record run history for coach-player sessions."""

import fcntl
import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional


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
    feedback: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RunRecorder:
    """Manages run history storage."""

    def __init__(self, knowledge_dir: str = ".g3/knowledge"):
        self.knowledge_dir = Path(knowledge_dir)
        self.runs_file = self.knowledge_dir / "runs.jsonl"

    def _ensure_dir(self):
        """Ensure knowledge directory exists."""
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_turn_details(turn_details: Any) -> list[TurnDetail]:
        """Coerce stored turn details into TurnDetail dataclasses."""
        if not isinstance(turn_details, list):
            return []

        normalized: list[TurnDetail] = []
        for item in turn_details:
            if isinstance(item, TurnDetail):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(TurnDetail(**item))
        return normalized

    @staticmethod
    def _build_record_from_kwargs(kwargs: dict[str, Any]) -> RunRecord:
        """Support legacy orchestrator calls that pass keyword fields instead of RunRecord."""
        config = kwargs.get("config")
        if not isinstance(config, dict):
            config = {}

        decision = kwargs.get("decision")
        decision_action = getattr(decision, "action", "")
        status = kwargs.get("status")
        if not isinstance(status, str) or not status:
            if decision_action in ("winner_a", "winner_b"):
                status = "approved"
            else:
                status = "completed"

        metadata = dict(kwargs)
        feedback = metadata.pop("feedback", None)

        return RunRecord(
            run_id=metadata.get("run_id") or generate_run_id(),
            timestamp=metadata.get("timestamp") or time.strftime("%Y-%m-%d %H:%M:%S"),
            requirements_file=metadata.get("requirements_file")
            or metadata.get("task_file")
            or "",
            turns_used=int(metadata.get("turns_used") or metadata.get("rounds_used") or 0),
            max_turns=int(
                metadata.get("max_turns")
                or config.get("max_turns")
                or config.get("max_rounds")
                or 0
            ),
            status=status,
            total_duration_s=float(metadata.get("total_duration_s") or 0.0),
            turn_details=RunRecorder._normalize_turn_details(
                metadata.get("turn_details", [])
            ),
            feedback=feedback if isinstance(feedback, str) else None,
            metadata=metadata,
        )

    def _read_all_records(self) -> list[RunRecord]:
        """Read all stored records from disk in file order."""
        if not self.runs_file.exists():
            return []

        records = []
        with open(self.runs_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                data["turn_details"] = self._normalize_turn_details(
                    data.get("turn_details") or []
                )
                if not isinstance(data.get("metadata"), dict):
                    data["metadata"] = {}
                records.append(RunRecord(**data))
        return records

    def _write_all_records(self, records: list[RunRecord]) -> None:
        """Rewrite the JSONL file with the provided records."""
        self._ensure_dir()
        with open(self.runs_file, "w") as f:
            for record in records:
                f.write(json.dumps(asdict(record)) + "\n")

    def record(self, record: RunRecord | None = None, **kwargs) -> str:
        """Append a run record to history and return its run ID."""
        if record is not None and kwargs:
            raise TypeError("record() accepts either a RunRecord or keyword fields, not both")
        if record is None:
            record = self._build_record_from_kwargs(kwargs)
        elif not isinstance(record, RunRecord):
            raise TypeError("record() expects a RunRecord instance")

        self._ensure_dir()
        with open(self.runs_file, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(asdict(record)) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return record.run_id

    def history(self, limit: int | None = 10) -> list[RunRecord]:
        """Retrieve recent run records."""
        records = self._read_all_records()

        # Return most recent first, limited
        if not records:
            return []
        if limit is None:
            return list(reversed(records))
        return list(reversed(records[-limit:]))

    def load_all(self) -> list[RunRecord]:
        """Return all stored records, newest first."""
        return self.history(limit=None)

    def update_feedback(self, run_id: str, rating: str) -> bool:
        """Persist user feedback for an existing run. Returns True if updated."""
        records = self._read_all_records()
        updated = False

        for index, record in enumerate(records):
            if record.run_id != run_id:
                continue
            records[index] = RunRecord(
                run_id=record.run_id,
                timestamp=record.timestamp,
                requirements_file=record.requirements_file,
                turns_used=record.turns_used,
                max_turns=record.max_turns,
                status=record.status,
                total_duration_s=record.total_duration_s,
                turn_details=record.turn_details,
                feedback=rating,
                metadata=record.metadata,
            )
            updated = True
            break

        if updated:
            self._write_all_records(records)
        return updated


def generate_run_id() -> str:
    """Generate a unique run ID based on timestamp and random suffix."""
    import random
    return f"run-{int(time.time() * 1000)}-{random.randint(0, 9999):04d}"
