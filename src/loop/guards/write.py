"""Read-only петля игрока: контроль по ходу вызова, а не только на отчёте.

Прогоны 223814/231742: 52 read, 9 grep, 0 write за 25 минут во всех сессиях,
ход кончился пустым stop. Счётчик идёт по tool-событиям, которые и так уходят
в stream: после READ_ONLY_LIMIT вызовов подряд без записи вызов обрывается
(`opencode run` не принимает сообщений на ходу) и продолжается в той же сессии
с пинком; после пинка на первую запись KICK_LIMIT вызовов, иначе blocked
read_only_loop. Запись возвращает обычный порог: 06-pult-decisions фаза 2 —
поводок в 3 вызова держался до конца хода и обрывал игрока на прогоне тестов.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Awaitable, Callable

READ_ONLY_LIMIT = 30
KICK_LIMIT = 3
READ_ONLY_REASON = "read_only_loop"
WRITE_TOOLS = frozenset(
    {"write", "edit", "multiedit", "patch", "apply_patch", "file_change", "notebookedit"}
)


class ReadOnlyLoop(Exception):
    """Порог чтений без записи: текущий вызов игрока обрывается."""

    def __init__(self, calls: int) -> None:
        super().__init__(f"{calls} вызовов без записи")
        self.calls = calls


def tree_mark(working_dir: str) -> str:
    """Отпечаток грязных файлов дерева: ловит запись через bash, не только write/edit."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "-uall", "-z"],
            cwd=working_dir,
            capture_output=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    marks = []
    for rel in sorted(p[3:] for p in out.decode(errors="replace").split("\0") if len(p) > 3):
        try:
            st = os.stat(os.path.join(working_dir, rel))
            marks.append(f"{rel}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            marks.append(f"{rel}:-")
    return "\n".join(marks)


class WriteGuard:
    """Обёртка emit игрока: считает tool-вызовы подряд без записи в дерево.

    `session` уходит провайдеру: opencode кладёт туда id своей сессии, а
    при resume продолжает её — пинок и repair не перечитывают дерево заново.
    """

    def __init__(self, working_dir: str, emit: Callable[..., None] | None) -> None:
        self.working_dir = working_dir
        self._emit = emit
        self.limit = READ_ONLY_LIMIT
        self.streak = 0
        self.total = 0
        self.wrote = False
        self.session: dict[str, Any] = {"id": "", "resume": False}
        self._start = self._mark = tree_mark(working_dir)

    def emit(self, kind: str, **fields: Any) -> None:
        if self._emit is not None:
            self._emit(kind, **fields)
        if kind == "tool":
            self._tool(str(fields.get("text") or ""))

    def _tool(self, line: str) -> None:
        self.total += 1
        if line.split(" ", 1)[0].lower() in WRITE_TOOLS:
            self._written()
            return
        self.streak += 1
        if self.streak < self.limit:
            return
        if tree_mark(self.working_dir) != self._mark:
            self._written()
            return
        raise ReadOnlyLoop(self.streak)

    def _written(self) -> None:
        self.wrote = True
        self.streak = 0
        self.limit = READ_ONLY_LIMIT
        self._mark = tree_mark(self.working_dir)

    def never_wrote(self) -> bool:
        return not self.wrote and tree_mark(self.working_dir) == self._start

    def kick(self, calls: int) -> None:
        self.limit = KICK_LIMIT
        self.streak = 0
        self.emit(
            "kick",
            role="player",
            calls=calls,
            text=f"пинок: {calls} вызовов без записи — следующие {KICK_LIMIT} должны писать в дерево",
        )

    def report(self) -> dict[str, Any]:
        tail = "в дерево ничего не записано" if self.never_wrote() else f"последние {self.streak} без записи"
        reason = f"{READ_ONLY_REASON}: {self.total} вызовов инструментов, {tail}"
        self.emit("read_only_loop", role="player", calls=self.total, text=reason)
        return {"status": "blocked", "changes": [], "start_here": [], "blocked_reason": reason}


def player_guard(role: str, working_dir: str, emit: Callable[..., None] | None) -> WriteGuard | None:
    return WriteGuard(working_dir, emit) if role == "player" else None


async def watched(
    guard: WriteGuard | None,
    prompt: str,
    call: Callable[[str], Awaitable[str | None]],
) -> str | dict[str, Any] | None:
    """Вызов роли под контролем: петля → пинок; петля после пинка → blocked-отчёт.

    Каждый следующий вызов (пинок, repair) продолжает сессию первого.
    """
    if guard is None:
        return await call(prompt)
    from src.loop.prompts import kick_prompt

    try:
        return await call(prompt)
    except ReadOnlyLoop as exc:
        if guard.limit == KICK_LIMIT:
            return guard.report()
        calls = exc.calls
    finally:
        guard.session["resume"] = bool(guard.session["id"])
    guard.kick(calls)
    try:
        return await call(kick_prompt(prompt, calls, KICK_LIMIT))
    except ReadOnlyLoop:
        return guard.report()
