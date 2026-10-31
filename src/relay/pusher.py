"""Отправка state.json на ретранслятор: один демон на прогон (план 08-6).

Демон всегда шлёт свежий state.json с диска, а не копию из памяти: так
уходит уже scrubbed состояние, стартовый running и смена фазы/раунда без
отдельного вызова в каждом месте записи. Очередь из одного — флаг «есть
новое»; пульс раз в 5 мин. Сеть не держит owner_lock: демон только читает
state.json, а свой итог пишет в отдельный relay.json. На выходе процесса
atexit досылает последнее состояние (done/stopped) не дольше одного таймаута.
"""

from __future__ import annotations

import atexit
import threading
import time
from pathlib import Path
from typing import Any

from src.fsio import atomic_write_json, read_json_dict

PULSE_EVERY_S = 300.0
LOG_EVERY_S = 15.0  # stream.jsonl alone (the log) is pushed at most this often
POLL_S = 1.0
RELAY_FILE = "relay.json"
NO_LINK = "нет связи с ретранслятором"
PUSH_BROKEN = "пуш-канал сломан — перепривяжи"

_GUARD = threading.Lock()
_PUSHERS: dict[str, "Pusher"] = {}


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class Pusher:
    def __init__(self, run_dir: Path, relay: Any) -> None:
        self.run_dir = Path(run_dir)
        self.relay = relay
        self._cond = threading.Condition()
        self._dirty = True  # стартовое состояние уходит сразу
        self._busy = False
        self._sent: tuple[int, int] | None = None
        self._sent_log: tuple[int, int] | None = None
        self._sent_host: tuple[int, int] | None = None
        self._last = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="tero-relay-push")
        self._thread.start()

    @property
    def state_path(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def log_path(self) -> Path:
        return self.run_dir / "stream.jsonl"

    def poke(self) -> None:
        with self._cond:
            self._dirty = True
            self._cond.notify_all()

    def _due(self) -> bool:
        if self._dirty:
            return True
        sig = _signature(self.state_path)
        if sig is not None and sig != self._sent:
            return True
        since = time.monotonic() - self._last
        log_sig = _signature(self.log_path)
        if log_sig is not None and log_sig != self._sent_log and since >= LOG_EVERY_S:
            return True
        host_sig = _signature(self.run_dir / "host.jsonl")  # the host's lines: like the log
        if host_sig is not None and host_sig != self._sent_host and since >= LOG_EVERY_S:
            return True
        return since >= PULSE_EVERY_S

    def _loop(self) -> None:
        while True:
            with self._cond:
                while self._busy or not self._due():
                    self._cond.wait(timeout=POLL_S)
                self._dirty = False
                self._busy = True
            try:
                self.send_once()
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()

    def send_once(self) -> bool:
        sig = _signature(self.state_path)
        self._sent_log = _signature(self.log_path)
        self._sent_host = _signature(self.run_dir / "host.jsonl")
        state = read_json_dict(self.state_path)
        if state is None:
            self._last = time.monotonic()
            return False
        try:
            relay = self.relay
            try:
                relay.run_id = self.run_dir.name
            except Exception:
                pass
            try:
                relay.run_dir = self.run_dir
            except Exception:
                pass
            ok = bool(relay.push_state(state))
        except Exception:  # сеть не роняет прогон
            ok = False
        try:
            push_broken = not bool(self.relay.push_ok)
        except Exception:
            push_broken = False
        self._sent = sig
        self._last = time.monotonic()
        try:
            atomic_write_json(self.run_dir / RELAY_FILE,
                              {"ok": ok, "push_ok": not push_broken,
                               "error": "" if ok else NO_LINK, "at": time.time()},
                              ensure_ascii=False, mode=0o600)
        except Exception:
            pass
        return ok

    def flush(self, wait_s: float) -> None:
        """Финал: дождаться идущей отправки (не дольше wait_s), затем отправить
        последний state.json. Зависшая отправка не отменяет финал: старый
        запрос сам кончится по таймауту клиента, финал уходит следом."""
        deadline = time.monotonic() + wait_s
        with self._cond:
            while self._busy and time.monotonic() < deadline:
                self._cond.wait(timeout=0.05)
            if _signature(self.state_path) == self._sent and not self._dirty:
                return
            self._busy = True
            self._dirty = False
        try:
            self.send_once()
        finally:
            with self._cond:
                self._busy = False
                self._cond.notify_all()


def ensure_pusher(run_dir: str | Path, relay: Any) -> Pusher | None:
    """Демон прогона: создаётся один раз; NoopRelay — без демона."""
    from src.relay.iface import NoopRelay

    if relay is None or isinstance(relay, NoopRelay):
        return None
    key = str(Path(run_dir).absolute())
    with _GUARD:
        got = _PUSHERS.get(key)
        if got is None:
            got = _PUSHERS[key] = Pusher(Path(run_dir), relay)
        return got


def poke(run_dir: str | Path) -> None:
    with _GUARD:
        got = _PUSHERS.get(str(Path(run_dir).absolute()))
    if got is not None:
        got.poke()


def push_final(run_dir: str | Path) -> None:
    """done/stopped: демон прогона, даже если его ещё не было (resume)."""
    try:
        from src.relay.iface import make_relay

        path = Path(run_dir)
        state = read_json_dict(path / "state.json") or {}
        relay = make_relay(state.get("config") or None, run_id=path.name,
                           run_dir=path)
        got = ensure_pusher(path, relay)
        if got is not None:
            got.poke()
    except Exception:
        pass


def relay_status(run_dir: str | Path) -> str:
    """Для `tero status`: подсказка, если последняя отправка не удалась."""
    data = read_json_dict(Path(run_dir) / RELAY_FILE) or {}
    if data.get("ok") is False:
        return NO_LINK
    if data.get("push_ok") is False:
        return PUSH_BROKEN
    return ""


def _flush_all() -> None:
    from src.relay.client import TIMEOUT_S

    # Один таймаут — дождаться идущей отправки, второй — на сам финал
    # (done/stopped tero отправляет всегда; клиент сам режет запрос по TIMEOUT_S).
    with _GUARD:
        pushers = list(_PUSHERS.values())
    for got in pushers:
        try:
            got.flush(TIMEOUT_S)
        except Exception:
            pass


atexit.register(_flush_all)
