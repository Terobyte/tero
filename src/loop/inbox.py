"""Входящие ответы на точку решения: файлы `inbox/` + ретранслятор.

Только владелец вызывает `take`: он применяет ответ одной записью
`state.json` (эффект + `applied_uuids` + журнал `decisions`), затем пишет
`inbox/<uuid>.result`. Остальные команды (`answer`) только кладут файлы.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from src.fsio import atomic_write_text
from src.loop.decision_actions import refuse_budget, refuse_model, spend_budget
from src.loop.human_key import who_of
from src.loop.state import RunState
from src.relay.iface import Answer, RelayClient

TEXT_CAP = 2000

_SAFE_UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _is_safe_uuid(uid: Any) -> bool:
    return isinstance(uid, str) and bool(_SAFE_UUID_RE.match(uid))


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
    if action == "answer" and len(text) > TEXT_CAP:
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
                if p.is_file() and not p.is_symlink()
                and p.name not in ("compact.json", "stop.json")
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
        # O_NOFOLLOW: a symlink planted at <uid>.result is never written through
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        flags |= os.O_EXCL
        try:
            fd = os.open(inbox / f"{uid}.result", flags, 0o644)
        except OSError:
            return
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
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

    def _apply(self, answer: Answer, actions: list) -> bool:
        """Одна атомарная запись state.json, затем `.result` «принято»."""
        if not _is_safe_uuid(answer.uuid):
            return False
        state = RunState.read(self.run_dir / "state.json") or {}
        applied = list(state.get("applied_uuids") or [])
        if answer.uuid in applied:
            try:
                self._write_result(answer.uuid, "принято", overwrite=True)
            except Exception:
                pass
            return True
        open_q = state.get("question")
        open_id = open_q.get("id") if isinstance(open_q, dict) else None
        if open_id != answer.question_id:
            return False
        refusal = self._refuse(
            {"action": answer.action, "text": answer.text, "who": answer.who,
             "question_id": answer.question_id,
             "woken": getattr(answer, "woken", False) if hasattr(answer, "woken") else False},
            actions, state,
        )
        if refusal is not None:
            self._write_result(answer.uuid, refusal, overwrite=True)
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
        self._write_result(answer.uuid, "принято", overwrite=True)
        answer.text = text
        return True

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
                text=human.text,
                who=human.who,
                why=human.why,
                uuid=human.uuid,
            )
            if self._apply(candidate, actions):
                self._reject_rest(question_id, candidate)
                return candidate
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
            payload = {**payload, "who": who_of(payload)}
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
            )
            if self._apply(candidate, actions):
                self._reject_rest(question_id, candidate)
                return candidate
            return None
        return None
