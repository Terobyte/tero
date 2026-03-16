# src/utils/structured_logger.py
"""
Structured JSON logging for CareerBot.

Enables:
- JSON logs for machine parsing
- Easy integration with logging aggregators (Grafana Loki, ELK)
- Human-readable console output in dev mode

Usage:
    from src.utils.structured_logger import get_logger
    logger = get_logger(__name__)

    logger.info("job_applied", job_id="abc123", company="Stripe", ats="greenhouse")
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


def json_sink(message: dict) -> None:
    """Custom sink that outputs JSON lines."""
    record = message["record"]
    output = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
    }
    # Add extra fields
    if record["extra"]:
        output["extra"] = record["extra"]
    print(json.dumps(output), file=sys.stderr)


def setup_logging(
    json_output: bool = False,
    log_file: str | None = None,
    level: str = "INFO",
) -> None:
    """
    Configure loguru for structured logging.

    Args:
        json_output: If True, output JSON lines to stderr
        log_file: Optional file path for logs
        level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    # Remove default handler
    logger.remove()

    if json_output:
        # JSON to stderr
        logger.add(
            json_sink,
            level=level,
            format="{message}",
        )
    else:
        # Human-readable to stderr
        logger.add(
            sys.stderr,
            level=level,
            format="<green>{time:HH:mm:ss}</green> | <level>{level:8}</level> | <cyan>{module:20}</cyan> - {message}",
            colorize=True,
        )

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_path),
            level=level,
            rotation="10 MB",
            retention="7 days",
            serialize=True,  # JSON format in file
        )

    logger.info("Logging configured", json_output=json_output, level=level)


def get_logger(name: str = __name__):
    """Get a logger instance with module name bound."""
    return logger.bind(module=name)
