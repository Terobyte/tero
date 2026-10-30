"""Старт ledger прогона (вынесено из run.py, план 09: место под поток опроса).

Повторный экспорт: `run` берёт `_start_ledger` отсюда.
"""

from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.loop.ledger import Ledger


def _start_ledger(run_dir: Path, cfg: Config, slug: str) -> Ledger:
    ledger = Ledger(run_dir)
    ledger.event("run_start", plan=cfg.plan_file, slug=slug)
    ledger.event(
        "compact_config",
        player_at_tokens=cfg.compact_player_at_tokens,
        judge_at_tokens=cfg.compact_judge_at_tokens,
    )
    return ledger
