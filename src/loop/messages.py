"""Сообщения хозяину: `inbox/msg-<uuid>.json` (подписаны) → `msg-<uuid>.read.json`.

Верим только подписи `human_key`; чужой файл игнорируется со строкой в `hud.log`.
Прочитанным сообщение становится атомарным rename: печатает тот, кто его переименовал.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from src.loop.human_key import message_ok

_LOGGED: set[str] = set()


def _log_unsigned(run_dir: Path, name: str) -> None:
    if f"{run_dir}/{name}" in _LOGGED:
        return
    _LOGGED.add(f"{run_dir}/{name}")
    try:
        from src.fsio import append_text

        append_text(run_dir / "hud.log",
                    f"{time.strftime('%Y-%m-%dT%H:%M:%S')} inbox сообщение: нет верной подписи, игнор\n")
    except OSError:
        pass


def _files(run_dir: Path, read: bool) -> list[Path]:
    from src.loop.inbox import inbox_dir

    inbox = inbox_dir(run_dir)
    if inbox is None:
        return []
    try:
        found = [p for p in inbox.glob("msg-*.json")
                 if p.is_file() and not p.is_symlink() and p.name.endswith(".read.json") == read]
    except OSError:
        return []
    return sorted(found)


def _load(run_dir: Path, path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _log_unsigned(run_dir, path.name)
        return None
    if not isinstance(data, dict) or not message_ok(run_dir.name, data):
        _log_unsigned(run_dir, path.name)
        return None
    return data


def count_unread(run_dir: str | Path) -> int:
    run = Path(run_dir)
    return sum(1 for p in _files(run, False) if _load(run, p) is not None)


def take_unread(run_dir: str | Path) -> list[str]:
    """Тексты непрочитанных подписанных сообщений; помечены переименованием."""
    run = Path(run_dir)
    out: list[str] = []
    for path in _files(run, False):
        data = _load(run, path)
        if data is None:
            continue
        try:
            os.rename(path, path.with_name(path.name[:-len(".json")] + ".read.json"))
        except OSError:
            continue  # его уже забрал другой wait
        out.append(str(data.get("text") or ""))
    return out


def _ts_of(data: dict[str, Any]) -> float:
    try:
        return float(data.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0


def history(run_dir: str | Path) -> list[str]:
    """Все подписанные сообщения (прочитанные и нет) без пометки."""
    run = Path(run_dir)
    rows = [d for p in _files(run, True) + _files(run, False) if (d := _load(run, p)) is not None]
    rows.sort(key=_ts_of)
    return [str(d.get("text") or "") for d in rows]
