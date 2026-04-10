# src/utils/cost_tracker.py
"""
AI cost tracking module.

Tracks API costs across different AI providers (Gemini, Claude, OpenAI).
Provides budget enforcement and cost reporting.
"""
from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class Provider(Enum):
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


@dataclass
class CostEntry:
    """Single cost tracking entry."""
    timestamp: datetime
    provider: Provider
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    operation: str = "unknown"
    metadata: dict = field(default_factory=dict)


class CostTracker:
    """
    Tracks AI API costs with budget enforcement.

    Features:
    - Per-provider cost tracking
    - Budget limits with enforcement
    - Cost reporting and export
    """

    # Pricing per 1M tokens (as of 2024)
    PRICING = {
        Provider.GEMINI: {
            "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
            "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
            "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
        },
        Provider.ANTHROPIC: {
            "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
            "claude-3-opus": {"input": 15.00, "output": 75.00},
            "claude-3-haiku": {"input": 0.25, "output": 1.25},
        },
        Provider.OPENAI: {
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4-turbo": {"input": 10.00, "output": 30.00},
        },
    }

    def __init__(
        self,
        daily_budget: float = 10.0,
        monthly_budget: float = 200.0,
        storage_path: Optional[Path] = None,
    ):
        self.daily_budget = daily_budget
        self.monthly_budget = monthly_budget
        self.storage_path = storage_path or Path("data/costs/history.jsonl")
        self._entries: list[CostEntry] = []
        self._load_history()

    def _load_history(self) -> None:
        """Load cost history from storage."""
        if not self.storage_path.exists():
            return
        with open(self.storage_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    ts = self._normalize_datetime(
                        datetime.fromisoformat(str(data["timestamp"]))
                    )
                    self._entries.append(CostEntry(
                        timestamp=ts,
                        provider=Provider(data["provider"]),
                        model=str(data["model"]),
                        input_tokens=int(data["input_tokens"]),
                        output_tokens=int(data["output_tokens"]),
                        cost_usd=float(data["cost_usd"]),
                        operation=str(data.get("operation", "unknown")),
                        metadata=data.get("metadata", {}),
                    ))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    warnings.warn(
                        f"Skipping corrupted history line: {line[:80]!r} ({exc})"
                    )

    def _save_entry(self, entry: CostEntry) -> None:
        """Save entry to storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "a") as f:
            f.write(json.dumps({
                "timestamp": entry.timestamp.isoformat(),
                "provider": entry.provider.value,
                "model": entry.model,
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "cost_usd": entry.cost_usd,
                "operation": entry.operation,
                "metadata": entry.metadata,
            }) + "\n")

    def calculate_cost(
        self,
        provider: Provider,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate cost for a given operation."""
        pricing = self.PRICING.get(provider, {}).get(model, {"input": 0, "output": 0})
        input_cost = (input_tokens / 1_000_000) * pricing.get("input", 0)
        output_cost = (output_tokens / 1_000_000) * pricing.get("output", 0)
        return input_cost + output_cost

    def record(
        self,
        provider: Provider,
        model: str,
        input_tokens: int,
        output_tokens: int,
        operation: str = "unknown",
        metadata: Optional[dict] = None,
    ) -> CostEntry:
        """Record a cost entry."""
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts cannot be negative")

        cost = self.calculate_cost(provider, model, input_tokens, output_tokens)

        entry = CostEntry(
            timestamp=datetime.now(timezone.utc),
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            operation=operation,
            metadata=metadata or {},
        )

        self._entries.append(entry)
        self._save_entry(entry)
        return entry

    def get_daily_total(self, date: Optional[datetime] = None) -> float:
        """Get total cost for a specific day."""
        date = self._normalize_datetime(date or datetime.now(timezone.utc))
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)

        return sum(
            e.cost_usd for e in self._entries
            if day_start <= e.timestamp <= day_end
        )

    def get_monthly_total(self, date: Optional[datetime] = None) -> float:
        """Get total cost for a specific month."""
        date = self._normalize_datetime(date or datetime.now(timezone.utc))
        month_start = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_start.month == 12:
            month_end = month_start.replace(
                year=month_start.year + 1, month=1, day=1
            )
        else:
            month_end = month_start.replace(month=month_start.month + 1, day=1)

        return sum(
            e.cost_usd for e in self._entries
            if month_start <= e.timestamp < month_end
        )

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        """Treat naive datetimes as UTC so persisted history remains comparable."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def can_spend(self, estimated_cost: float) -> bool:
        """Check if we're within budget for a proposed cost."""
        daily_used = self.get_daily_total()
        monthly_used = self.get_monthly_total()

        return (
            daily_used + estimated_cost <= self.daily_budget and
            monthly_used + estimated_cost <= self.monthly_budget
        )

    def get_report(self) -> dict:
        """Get a cost summary report."""
        by_provider = {}
        for entry in self._entries:
            key = entry.provider.value
            if key not in by_provider:
                by_provider[key] = {"count": 0, "total": 0.0}
            by_provider[key]["count"] += 1
            by_provider[key]["total"] += entry.cost_usd

        return {
            "daily_total": self.get_daily_total(),
            "monthly_total": self.get_monthly_total(),
            "daily_budget": self.daily_budget,
            "monthly_budget": self.monthly_budget,
            "by_provider": by_provider,
            "total_entries": len(self._entries),
        }
