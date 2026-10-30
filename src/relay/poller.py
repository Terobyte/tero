"""Поток опроса ретранслятора (план 09): один GET на весь прогон.

Поток владельца держит последний GET в памяти. `Inbox.take` получает адаптер
и больше сам в сеть не ходит. Сообщения поток кладёт подписанными в
`inbox/msg-<uuid>.json` и квитирует, ответы на закрытые вопросы квитирует сам.
Сеть не держит `owner_lock`: GET и ack идут вне замков.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from src.loop.state import RunState, owner_lock
from src.relay.iface import Answer, NoopRelay, make_relay

WAIT_EVERY_S = 10.0
STEP_EVERY_S = 30.0
TICK_S = 0.5
TEXT_CAP = 2000
_TERMINAL = ("done", "stopped")

_GUARD = threading.Lock()
_POLLERS: dict[str, "Poller"] = {}


class _Adapter:
    """Что видит `Inbox.take`: кеш последнего GET вместо сети."""

    def __init__(self, poller: "Poller") -> None:
        self._p = poller

    def fetch_answers(self, question_id: str) -> list[Answer]:
        return self._p.cached(question_id)

    def fetch_all(self) -> list[Answer]:
        return []

    def ack(self, uuid: str, reason: str = "") -> bool:
        return self._p.ack(uuid, reason)

    def push_state(self, state: dict) -> bool:
        return self._p.relay.push_state(state)

    def notify(self, text: str) -> bool:
        return self._p.relay.notify(text)


class Poller:
    def __init__(self, run_dir: str | Path, relay: Any,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.run_dir = Path(run_dir)
        self.relay = relay
        self.adapter = _Adapter(self)
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: list[Answer] = []
        self._acked: set[str] = set()
        self._last: float | None = None
        self._was_waiting = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def _state_path(self) -> Path:
        return self.run_dir / "state.json"

    def cached(self, question_id: str) -> list[Answer]:
        applied = set((RunState.read(self._state_path) or {}).get("applied_uuids") or [])
        with self._lock:
            return [a for a in self._cache
                    if a.kind == "answer" and a.question_id == question_id
                    and a.uuid not in applied and a.uuid not in self._acked]

    def ack(self, uuid: str, reason: str = "") -> bool:
        try:
            ok = bool(self.relay.ack(uuid, reason))
        except Exception:
            return False
        if ok:
            with self._lock:
                self._acked.add(uuid)
                self._cache = [a for a in self._cache if a.uuid != uuid]
        return ok

    def tick(self, now: float | None = None) -> bool:
        """GET, если пора: 10 с в waiting_*, 30 с во время шага, сразу при входе в ожидание."""
        now = self._clock() if now is None else now
        state = RunState.read(self._state_path) or {}
        waiting = str(state.get("status") or "").startswith("waiting_")
        entered = waiting and not self._was_waiting
        self._was_waiting = waiting
        every = WAIT_EVERY_S if waiting else STEP_EVERY_S
        if self._last is not None and not entered and now - self._last < every:
            return False
        self._last = now
        self._poll(state)
        return True

    def _poll(self, state: dict) -> None:
        try:
            rows = self.relay.fetch_all() or []
        except Exception:
            return
        question = state.get("question") if isinstance(state.get("question"), dict) else {}
        open_id = question.get("id")
        applied = set(state.get("applied_uuids") or [])
        keep: list[Answer] = []
        for row in rows:
            with self._lock:
                if row.uuid in self._acked:
                    continue
            if row.kind == "message":
                self._message(row)
            elif row.kind == "wake":
                self._wake(row)
            elif row.question_id != open_id:
                self.ack(row.uuid, "не тот вопрос")
            elif row.uuid in applied:
                self.ack(row.uuid, "")
            else:
                keep.append(row)
        with self._lock:
            self._cache = keep

    def _message(self, row: Answer) -> None:
        from src.config_env import phone_text_allowed
        from src.errors import scrub_secrets
        from src.loop.human_key import sign_message
        from src.loop.inbox import inbox_dir, write_inbox_file
        from src.loop.inbox_ids import _is_safe_uuid

        # F9: эффект идемпотентен — подписанный файл на диске (прочитанный
        # .read или нет) значит, что сообщение уже доставлено: только повтор ack.
        if self._message_done(row.uuid):
            self.ack(row.uuid, "")
            return
        try:
            allowed = phone_text_allowed()
        except Exception:
            allowed = True
        if not allowed:
            self.ack(row.uuid, "текст с телефона выключен")
            return
        if not _is_safe_uuid(row.uuid):
            self.ack(row.uuid, "плохой uuid")
            return
        # F8: текст с телефона — данные: секрет из secrets.env мажется до записи.
        # F20: лимит — после очистки: замена секрета на `[redacted]` не раздувает текст за 2000.
        text = scrub_secrets(row.text)[:TEXT_CAP]
        payload = sign_message(self.run_dir.name, {"uuid": row.uuid, "kind": "message",
                                                   "text": text, "ts": time.time()})
        if write_inbox_file(self.run_dir, f"msg-{row.uuid}.json",
                            json.dumps(payload, ensure_ascii=False)):
            self.ack(row.uuid, "")

    def _message_done(self, uuid: str) -> bool:
        """Подписанное сообщение уже лежит в inbox (прочитано или нет)."""
        from src.loop.human_key import message_ok
        from src.loop.inbox import inbox_dir

        box = inbox_dir(self.run_dir)
        if box is None or not uuid:
            return False
        for name in (f"msg-{uuid}.json", f"msg-{uuid}.read.json"):
            try:
                data = json.loads((box / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and message_ok(self.run_dir.name, data):
                return True
        return False

    def _wake(self, row: Answer) -> None:
        """Заглушка фазы 1: квитировать и записать в decisions; пробуждение — фаза 2."""
        path = self._state_path
        with owner_lock(path):
            state = RunState.read(path) or {}
            decisions = list(state.get("decisions") or [])
            # F9: повторный GET после упавшего ack не дублирует запись.
            if not any(isinstance(d, dict) and d.get("kind") == "wake"
                       and d.get("uuid") == row.uuid for d in decisions):
                decisions.append({"kind": "wake", "reason": "manual", "uuid": row.uuid,
                                  "result": "нет пробуждения (фаза 2)", "at": time.time()})
                RunState(path).update(decisions=decisions)
        self.ack(row.uuid, "")

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            self.tick()  # немедленный GET: владелец опрашивает уже во время setup
        except Exception:
            pass
        self._thread = threading.Thread(target=self._loop, daemon=True, name="tero-relay-poll")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self.tick()
                if (RunState.read(self._state_path) or {}).get("status") in _TERMINAL:
                    return
            except Exception:
                pass

    def stop(self) -> None:
        from src.relay.client import TIMEOUT_S

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=TIMEOUT_S)


def ensure_poller(run_dir: str | Path, relay: Any) -> Poller | None:
    """Один поток на прогон; NoopRelay — без потока."""
    if relay is None or isinstance(relay, NoopRelay):
        return None
    key = str(Path(run_dir).absolute())
    with _GUARD:
        got = _POLLERS.get(key)
        if got is not None and got._thread is not None and not got._thread.is_alive():
            # Поток умер (терминальный статус), а запись осталась: за живой не выдаём.
            _POLLERS.pop(key, None)
            got = None
        if got is None:
            got = _POLLERS[key] = Poller(Path(run_dir), relay)
            got.start()
        return got


def start_owner_poller(run_dir: str | Path, cfg: Any) -> Poller | None:
    """Поток опроса владельца до стартовых проверок (план 09, F19).

    `run()` зовёт после register, `resume()` — сразу после чтения cfg, до
    предпроверок и восстановления дерева. Один клиент на прогон гарантирует
    уже `make_relay` (кеш по run_id), повторный вызов дёшев. Стоп — через
    `RunSession._close`, ранние отказы `resume` останавливают явно.
    """
    try:
        relay = make_relay(cfg, run_id=Path(run_dir).name, run_dir=run_dir)
        return ensure_poller(run_dir, relay)
    except Exception:
        return None


def adapter_for(run_dir: str | Path, relay: Any) -> Any:
    """Адаптер потока для `Inbox.take`; без потока — сам клиент."""
    with _GUARD:
        got = _POLLERS.get(str(Path(run_dir).absolute()))
    return got.adapter if got is not None and got.relay is relay else relay


def stop_poller(run_dir: str | Path) -> None:
    """Остановить поток прогона: владелец завершился (включая judge_pending).

    Ожидание ограничено одним таймаутом внутри `Poller.stop`; потока нет — ничего.
    """
    key = str(Path(run_dir).absolute())
    with _GUARD:
        got = _POLLERS.pop(key, None)
    if got is not None:
        try:
            got.stop()
        except Exception:
            pass
