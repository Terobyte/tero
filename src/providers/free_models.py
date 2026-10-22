"""Free opencode models: the free list rotates, so it is read from the CLI, not pinned."""

from __future__ import annotations

import subprocess

# model value meaning "any free model opencode lists right now"
FREE = "free"

_LIST_TIMEOUT_S = 30


def is_free(model: str) -> bool:
    """opencode zen marks free models ``-free``, openrouter ``:free``."""
    m = model.strip().lower()
    return m.endswith("-free") or m.endswith(":free")


def list_models(command: str = "opencode") -> list[str]:
    """Model ids from ``opencode models``; empty when the CLI is missing or fails."""
    try:
        out = subprocess.run(
            [command, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_LIST_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    models = []
    for line in out.stdout.splitlines():
        words = line.split()
        if words and "/" in words[0]:
            models.append(words[0])
    return models


def free_models(models: list[str]) -> list[str]:
    return [m for m in models if is_free(m)]
