"""Протокол ретранслятора и заглушки для фазы без настоящего клиента."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Answer:
    question_id: str = ""
    action: str = ""
    text: str = ""
    who: str = ""
    why: str = ""
    uuid: str = ""
    woken: bool = False
    agent_pid: int | None = None
    agent_pid_started: float | None = None
    host: str = ""
    # "relay" — ответ пришёл с телефона; локальный inbox оставляет "".
    via: str = ""
    # answer | message | wake — вид записи на ретрансляторе (план 09)
    kind: str = "answer"


class RelayClient(Protocol):
    def push_state(self, state: dict) -> bool: ...
    def fetch_answers(self, question_id: str) -> list[Answer]: ...
    def fetch_all(self) -> list[Answer]: ...
    def ack(self, uuid: str, reason: str = "") -> bool: ...
    def notify(self, text: str) -> bool: ...


_RELAYS: dict[str, Any] = {}


def make_relay(cfg: Any = None, fetch: Any = None, run_id: str = "",
               machine_id: str = "", run_dir: Any = None) -> RelayClient:
    """Точка подмены: реальный клиент, без настройки — NoopRelay.

    Один клиент на прогон: повторный вызов с тем же run_id отдаёт тот же объект.
    """
    key = str(run_id or "")
    if key and key in _RELAYS:
        got = _RELAYS[key]
        try:
            if run_dir is not None:
                got.run_dir = run_dir
        except Exception:
            pass
        return got
    try:
        from src.relay.client import RelayClient as _Real

        real = _Real(cfg, fetch=fetch, run_id=run_id, machine_id=machine_id,
                     run_dir=run_dir)
        if getattr(real, "_base", "") or fetch is not None:
            if key:
                _RELAYS[key] = real
            return real  # type: ignore[return-value]
    except Exception:
        pass
    return NoopRelay()


class NoopRelay:
    """Нет ретранслятора: ничего не шлёт, ответов нет."""

    def push_state(self, state: dict) -> bool:
        return False

    def fetch_answers(self, question_id: str) -> list[Answer]:
        return []

    def fetch_all(self) -> list[Answer]:
        return []

    def ack(self, uuid: str, reason: str = "") -> bool:
        return False

    def notify(self, text: str) -> bool:
        return False


class FakeRelay:
    """Списки в памяти для тестов."""

    def __init__(self, answers: list[Answer] | None = None) -> None:
        self.answers: list[Answer] = list(answers or [])
        self.pushed: list = []
        self.notified: list[str] = []
        self.acked: list[str] = []
        self.reasons: dict[str, str] = {}
        self.gets = 0

    def push_state(self, state: dict) -> bool:
        self.pushed.append(state)
        return True

    def fetch_answers(self, question_id: str) -> list[Answer]:
        return [a for a in self.answers if a.question_id == question_id]

    def fetch_all(self) -> list[Answer]:
        self.gets += 1
        return list(self.answers)

    def ack(self, uuid: str, reason: str = "") -> bool:
        self.acked.append(uuid)
        self.reasons[uuid] = reason
        self.answers = [a for a in self.answers if a.uuid != uuid]
        return True

    def notify(self, text: str) -> bool:
        self.notified.append(text)
        return True
