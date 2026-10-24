"""Протокол ретранслятора и заглушки для фазы без настоящего клиента."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Answer:
    question_id: str = ""
    action: str = ""
    text: str = ""
    who: str = ""
    why: str = ""
    uuid: str = ""


class RelayClient(Protocol):
    def push_state(self, state: dict) -> bool: ...
    def fetch_answers(self, question_id: str) -> list[Answer]: ...
    def notify(self, text: str) -> bool: ...


class NoopRelay:
    """Нет ретранслятора: ничего не шлёт, ответов нет."""

    def push_state(self, state: dict) -> bool:
        return False

    def fetch_answers(self, question_id: str) -> list[Answer]:
        return []

    def notify(self, text: str) -> bool:
        return False


class FakeRelay:
    """Списки в памяти для тестов."""

    def __init__(self, answers: list[Answer] | None = None) -> None:
        self.answers: list[Answer] = list(answers or [])
        self.pushed: list = []
        self.notified: list[str] = []

    def push_state(self, state: dict) -> bool:
        self.pushed.append(state)
        return True

    def fetch_answers(self, question_id: str) -> list[Answer]:
        return [a for a in self.answers if a.question_id == question_id]

    def notify(self, text: str) -> bool:
        self.notified.append(text)
        return True
