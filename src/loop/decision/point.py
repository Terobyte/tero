"""Точка решения: id, причина, вопрос и допустимые действия."""

from __future__ import annotations

from dataclasses import dataclass, field

ACTIONS_BY_REASON: dict[str, list[str]] = {
    "user": ["answer", "skip", "stop"],
    "stuck": ["more", "model", "skip", "stop"],
    "fuse": ["more", "model", "skip", "stop"],
    "provider_unavailable": ["retry", "wait", "model", "stop"],
    "provider_auth": ["retry", "wait", "model", "stop"],
    "provider_quota": ["retry", "wait", "model", "stop"],
    "secrets": ["allow", "retry", "skip", "stop"],
    "judge_pending": ["retry", "skip", "stop", "accept", "reject"],
}


def actions_for(reason: str) -> list[str]:
    """Допустимые действия по причине точки решения."""
    return list(ACTIONS_BY_REASON.get(reason, []))


@dataclass
class Decision:
    id: str
    reason: str
    question: str
    actions: list[str] = field(default_factory=list)
