"""Входящие ответы на точку решения: файлы `inbox/` + ретранслятор.

Только владелец вызывает `take`: он применяет ответ одной записью
`state.json` (эффект + `applied_uuids` + журнал `decisions`), затем пишет
`inbox/<uuid>.result`. Остальные команды (`answer`) только кладут файлы.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from src.fsio import atomic_write_text
from src.loop.decision.actions import refuse_budget, refuse_model, spend_budget
from src.loop.human_key import who_of
from src.loop.state import RunState
from src.relay.iface import Answer, RelayClient

TEXT_CAP = 2000

# Идентификаторы inbox — в inbox_ids: ими же пользуется поток опроса.
from src.loop.inbox_ids import _is_safe_uuid


def _read_payload(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("uuid") or not data.get("question_id") or not data.get("action"):
        return None
    if not _is_safe_uuid(data.get("uuid")):
        return None
    return data


def _pid_of(payload: dict[str, Any]) -> int | None:
    """pid автора ответа: рядом могут лежать и `agent_pid`, и `pid`."""
    for key in ("agent_pid", "pid"):
        try:
            num = int(payload.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if num > 0:
            return num
    return None


def _started_of(payload: dict[str, Any]) -> float | None:
    value = payload.get("agent_pid_started")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def inbox_dir(run_dir: str | Path, create: bool = False) -> Path | None:
    """`<run_dir>/inbox`, если это настоящая папка внутри прогона, иначе None.

    Симлинк вместо папки увёл бы записи `answer`/`compact`/`.result` за
    пределы `run_dir`, поэтому такая папка не принимается вовсе.
    """
    run = Path(run_dir)
    inbox = run / "inbox"
    try:
        if create and not inbox.is_symlink():
            inbox.mkdir(parents=True, exist_ok=True)
        if inbox.is_symlink() or not inbox.is_dir():
            return None
        if inbox.resolve().parent != run.resolve():
            return None
    except OSError:
        return None
    return inbox


def write_inbox_file(run_dir: str | Path, name: str, text: str) -> bool:
    """Атомарно положить `inbox/<name>` (временный файл + rename). False при отказе."""
    inbox = inbox_dir(run_dir, create=True)
    if inbox is None or "/" in name or name in ("", ".", ".."):
        return False
    try:
        atomic_write_text(inbox / name, text, mode=0o600)
    except OSError:
        return False
    return True


def _cap(action: str, text: Any) -> str:
    text = text if isinstance(text, str) else ""
    if len(text) > TEXT_CAP:
        return text[:TEXT_CAP]
    return text


class Inbox:
    """Приём ответов владельца: локальные файлы + человек из ретранслятора."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self._state = RunState(self.run_dir / "state.json")

    def _inbox_files(self) -> list[Path]:
        inbox = inbox_dir(self.run_dir)
        if inbox is None:
            return []
        try:
            return sorted(
                p for p in inbox.glob("*.json")
                if p.is_file() and not p.is_symlink() and _is_safe_uuid(p.stem)
                and not p.name.startswith("msg-")
                and p.name not in ("compact.json", "stop.json", "wake.json", "resume-answer.json")
            )
        except OSError:
            return []

    def _write_result(self, uid: str, text: str, overwrite: bool = False) -> None:
        inbox = inbox_dir(self.run_dir)
        if inbox is None or not _is_safe_uuid(uid):
            return
        # F16: overwrite — атомарно через fsio (temp+rename не следует по
        # симлинкам, меняет саму ссылку); отказ без overwrite — O_EXCL+O_NOFOLLOW.
        if overwrite:
            try:
                if (inbox / f"{uid}.result").is_symlink():
                    return
                atomic_write_text(inbox / f"{uid}.result", text,
                                  encoding="utf-8", mode=0o644)
            except (OSError, ValueError):
                pass
            return
        # Whole file or nothing: written to a temp name, then linked into place
        # (`link` fails when the target exists, like O_EXCL, and never follows a
        # symlink planted at <uid>.result). A reader never sees an empty file.
        tmp = inbox / f".{uid}.result.{os.getpid()}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.link(tmp, inbox / f"{uid}.result")
        except OSError:
            pass
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _reject(self, path: Path, payload: dict[str, Any], text: str) -> None:
        self._write_result(str(payload.get("uuid")), text)

    @staticmethod
    def _refuse(payload: dict[str, Any], actions: list, state: dict | None = None) -> str | None:
        state = state or {}
        action, who = payload.get("action"), payload.get("who") or "agent"
        open_q = state.get("question") if isinstance(state.get("question"), dict) else {}
        qid = str(payload.get("question_id") or open_q.get("id") or "")
        if action not in actions:
            # Бюджет сузил вопрос до skip|stop: у человека бюджета нет.
            if action not in (open_q.get("all_actions") or []):
                return "отклонено: действие недопустимо"
            if who != "human":
                return "отклонено: бюджет исчерпан"
        if action == "model":
            refusal = refuse_model(payload.get("text"))
            if refusal is not None:
                return refusal
        if action == "allow" and who != "human":
            return "отклонено: allow только человек"
        if action in ("accept", "reject") and who != "human":
            return "отклонено: accept/reject только человек"
        if action not in ("retry", "skip", "stop") and payload.get("woken"):
            return "отклонено: разбуженному агенту только retry|skip|stop"
        return refuse_budget(state, qid, action, who)

    def _apply(self, answer: Answer, actions: list,
                 relay: RelayClient | None = None) -> bool:
        """Одна атомарная запись state.json, затем `.result` «принято».

        Для ответа с телефона (relay задан) `.result` в inbox не пишется:
        контракт требует, чтобы каталог остался пустым; владелец видит ack.
        """
        if not _is_safe_uuid(answer.uuid):
            return False
        phone = relay is not None
        state = RunState.read(self.run_dir / "state.json") or {}
        applied = list(state.get("applied_uuids") or [])
        if answer.uuid in applied:
            if not phone:
                try:
                    self._write_result(answer.uuid, "принято", overwrite=True)
                except Exception:
                    pass
            self._ack(relay, answer.uuid, "")
            return True
        open_q = state.get("question")
        open_id = open_q.get("id") if isinstance(open_q, dict) else None
        if open_id != answer.question_id:
            self._ack(relay, answer.uuid, "не тот вопрос")
            return False
        if phone and (answer.action == "text" or bool(answer.text)) \
                and not self._phone_text(state):
            self._ack(relay, answer.uuid, "текст с телефона выключен")
            return False
        refusal = self._refuse(
            {"action": answer.action, "text": answer.text, "who": answer.who,
             "question_id": answer.question_id,
             "woken": getattr(answer, "woken", False) if hasattr(answer, "woken") else False},
            actions, state,
        )
        if refusal is not None:
            if not phone:
                self._write_result(answer.uuid, refusal, overwrite=True)
            self._ack(relay, answer.uuid, refusal)
            return False
        text = _cap(answer.action, answer.text)
        decisions = list(state.get("decisions") or [])
        decisions.append(
            {
                "who": answer.who,
                "action": answer.action,
                "text": text,
                "why": answer.why,
                "ts": time.time(),
                "question_id": answer.question_id,
            }
        )
        fields: dict[str, Any] = {"applied_uuids": applied + [answer.uuid], "decisions": decisions}
        budget = spend_budget(state, answer.question_id, answer.action, answer.who)
        if budget is not None:
            fields["budget"] = budget
        self._state.update(**fields)
        fresh = RunState.read(self.run_dir / "state.json")
        if fresh is None or answer.uuid not in (fresh.get("applied_uuids") or []):
            return False
        if not phone:
            self._write_result(answer.uuid, "принято", overwrite=True)
        answer.text = text
        self._ack(relay, answer.uuid, "")
        return True

    @staticmethod
    def _phone_text(state: dict) -> bool:
        """Only ~/.tero/config.yaml decides: the snapshot in state.json lies in the repo."""
        from src.config_env import phone_text_allowed

        _ = state
        try:
            return phone_text_allowed()
        except Exception:
            return True

    @staticmethod
    def _ack(relay: RelayClient | None, uuid: str, reason: str) -> None:
        """ack в фоне: сеть не задерживает stop сверх таймаута GET."""
        ack = getattr(relay, "ack", None) if relay is not None else None
        if not callable(ack) or not uuid:
            return

        def _send() -> None:
            try:
                ack(uuid, reason) if reason else ack(uuid)
            except Exception:
                pass

        threading.Thread(target=_send, daemon=True, name="tero-relay-ack").start()

    def _reject_rest(self, question_id: str, winner: Answer) -> None:
        seen = f"{winner.who}, {winner.action}"
        for path in self._inbox_files():
            payload = _read_payload(path)
            if payload is None:
                continue
            if payload.get("uuid") == winner.uuid:
                continue
            if payload.get("question_id") != question_id:
                continue
            self._reject(path, payload, f"отклонено: уже отвечено: {seen}")

    def take(self, question_id: str, relay: RelayClient) -> Answer | None:
        """Принять один ответ на открытый вопрос. Первый принятый выигрывает."""
        state = RunState.read(self.run_dir / "state.json") or {}
        open_q = state.get("question")
        open_id = open_q.get("id") if isinstance(open_q, dict) else None
        actions = list(open_q.get("actions") or []) if isinstance(open_q, dict) else []
        if open_id != question_id:
            for path in self._inbox_files():
                payload = _read_payload(path)
                if payload is not None and payload.get("question_id") == question_id:
                    self._reject(path, payload, f"отклонено: вопрос {question_id} не открыт")
            try:
                for ans in relay.fetch_answers(question_id) or []:
                    if getattr(ans, "uuid", ""):
                        self._ack(relay, ans.uuid, "не тот вопрос")
            except Exception:
                pass
            return None
        try:
            remote = relay.fetch_answers(question_id) or []
        except Exception:
            remote = []
        human = next((a for a in remote if a.who == "human"), None)
        if human is not None:
            candidate = Answer(
                question_id=human.question_id or question_id,
                action=human.action,
                text=_cap(human.action, human.text),
                who=human.who,
                why=human.why,
                uuid=human.uuid,
                via="relay",
            )
            if self._apply(candidate, actions, relay):
                self._reject_rest(question_id, candidate)
                seen = f"отклонено: уже отвечено: {candidate.who}, {candidate.action}"
                for other in remote:
                    if other.uuid and other.uuid != candidate.uuid:
                        self._ack(relay, other.uuid, seen)
                return candidate
            return None
        applied = set(state.get("applied_uuids") or [])
        for path in self._inbox_files():
            payload = _read_payload(path)
            if payload is None:
                continue
            if payload.get("uuid") in applied:
                continue
            if payload.get("question_id") != question_id:
                self._reject(
                    path, payload,
                    f"отклонено: вопрос {payload.get('question_id')} не открыт",
                )
                continue
            # F8/F20: владелец не дописывает woken и не верит заявленному
            # agent_pid. Маркер woken ставит только tero answer после проверки
            # идентичности сессии; здесь он только читается.
            # B7: human только с подписью ключа вне дерева, иначе agent.
            payload = {**payload, "who": who_of(self.run_dir.name, payload)}
            refusal = self._refuse(payload, actions, state)
            if refusal is not None:
                self._reject(path, payload, refusal)
                continue
            candidate = Answer(
                question_id=question_id,
                action=payload.get("action"),
                text=_cap(payload.get("action"), payload.get("text")),
                who=payload["who"],
                why=payload.get("why") or "",
                uuid=payload.get("uuid"),
                woken=bool(payload.get("woken")),
                agent_pid=_pid_of(payload),
                agent_pid_started=_started_of(payload),
                host=str(payload.get("host") or ""),
            )
            if self._apply(candidate, actions):
                self._reject_rest(question_id, candidate)
                return candidate
            return None
        return None
