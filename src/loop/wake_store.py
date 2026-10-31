"""Машинное хранилище пробуждений: `~/.tero/wake.log`, `woken.json` (план 09, фаза 2).

Вынесено из `wake.py` ради потолка файла. Только stdlib + `fsio`, без циклов."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from src.fsio import append_text, atomic_write_json, read_json_dict

MIN_GAP_S, MANUAL_DAILY, AUTO_DAILY, DAY_S = 300, 10, 20, 86400


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _tero_home() -> Path:
    return Path.home() / ".tero"


def log_path() -> Path:
    return _tero_home() / "wake.log"


class _Flock:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> "_Flock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: Any) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _lock_of(path: Path) -> _Flock:
    return _Flock(path.with_name(path.name + ".lock"))


def _woken_file() -> Path:
    return _tero_home() / "woken.json"


def _global_items(now: float) -> list[dict]:
    data = read_json_dict(_woken_file()) or {}
    items = data.get("items") if isinstance(data, dict) else None
    return [e for e in (items or []) if isinstance(e, dict) and now - float(e.get("at") or 0) < DAY_S]


def remember_global(pid: int, started: float | None, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    try:
        with _lock_of(_woken_file()):
            items = _global_items(ts) + [{"pid": int(pid), "started": started, "at": ts}]
            atomic_write_json(_woken_file(), {"items": items}, mode=0o600)
    except Exception:
        pass


def global_woken_pids(now: float | None = None) -> set[int]:
    ts = time.time() if now is None else now
    try:
        return {_int(e.get("pid")) for e in _global_items(ts)} - {0}
    except Exception:
        return set()


def _events(now: float) -> list[dict]:
    try:
        lines = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        if not line.startswith('{"wake_event"'):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and now - float(e.get("at") or 0) < DAY_S:
            out.append(e)
    return out


def _note(line: dict) -> None:
    try:
        append_text(log_path(), json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass
