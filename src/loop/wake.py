"""Пробуждение агента-хозяина: одна функция под замком, трамплин, лимиты (план 09, фаза 2).

Вынесено из autopilot.py; `wake_command` и `host` — только из ~/.tero/config.yaml."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from src.config_env import global_defaults
from src.hud.registry import current_pid_started, pid_is_alive
from src.loop import lease as lease_mod
from src.loop.autopilot_wake import wake_env
from src.loop.human_key import file_signed, sign_file
from src.loop.state import RunState, owner_lock
from src.loop.wake_store import (
    AUTO_DAILY,
    DAY_S,  # noqa: F401 — реэкспорт машинного хранилища
    MANUAL_DAILY,
    MIN_GAP_S,
    _events,
    _int,
    _lock_of,
    _note,
    global_woken_pids,  # noqa: F401
    log_path,  # noqa: F401
    remember_global,  # noqa: F401
)

__all__ = ["wake", "request_wake", "restricted_refusal", "woken_pid",
           "remember_global", "global_woken_pids", "log_path"]
TRAMPOLINE = 'exec "$@" </dev/null >>"$0" 2>&1 & echo $!'
RESTRICTED = ("resume", "go", "relay", "pair", "watch", "wake")
ALLOWED_TOOLS = ("Bash(tero status:*),Bash(tero guide:*),Bash(tero inbox:*),"
                 "Bash(tero answer:*),Bash(tero notify:*)")
DISALLOWED_TOOLS = "Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task,Agent"
_TASKS = {
    "question": "ответь на вопрос {question_id}",
    "message": ("прочитай `tero inbox -w {repo} --run {run_dir}` и сделай, что просят; "
                "человеку пиши через `tero notify`"),
    "manual": "посмотри `tero status` и реши",
}
_KEY_FIELD = {"question": "question_id", "message": "msg_uuid"}
_PLACEHOLDER = re.compile(r"\{(repo|run_dir|question_id|task|reason)\}")

popen_factory: Callable[..., Any] | None = None  # точка подмены в тестах
default_alive: Callable[[Any, Any], bool] = pid_is_alive
_LOCK = threading.RLock()


# --- woken: метки пробуждений в state.json и общий список машины -----------------------------

def woken_list(run_dir: str | Path) -> list[dict]:
    """`state.woken` списком; старый словарь по `question_id` читается."""
    try:
        raw = (RunState.read(Path(run_dir) / "state.json") or {}).get("woken")
    except (AttributeError, OSError, TypeError, ValueError):
        return []
    if isinstance(raw, dict):
        return [{"reason": "question", "question_id": k, "pid": _int(v), "at": 0.0}
                for k, v in raw.items()]
    return [dict(e) for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def woken_pid(run_dir: str | Path, question_id: str) -> int | None:
    for e in woken_list(run_dir):
        if e.get("question_id") == question_id and _int(e.get("pid")) > 0:
            return _int(e.get("pid"))
    return None


def run_woken_pids(run_dir: str | Path) -> set[int]:
    return {_int(e.get("pid")) for e in woken_list(run_dir) if _int(e.get("pid")) > 0}


def is_woken() -> bool:
    """Мы — разбуженный: `TERO_WOKEN=1` или предок записан в общем списке машины."""
    if os.environ.get("TERO_WOKEN") == "1":
        return True
    pids = global_woken_pids()
    if not pids:
        return False
    if os.getppid() in pids:
        return True
    try:
        from src.loop.host_id import ancestors

        return any(p.pid in pids for p in ancestors())
    except Exception:
        return False


def restricted_refusal(command: str) -> str:
    """Текст отказа разбуженному для `command`; пустая строка, если можно."""
    if command in RESTRICTED and is_woken():
        return f"`tero {command}` разбуженному агенту запрещено; отвечай только `tero answer` retry/skip/stop"
    return ""


# --- команда пробуждения ---------------------------------------------------------------------

def _repo_of(run_dir: Path) -> Path:
    return run_dir.parent.parent.parent


def _command(reason: str, key: str, run_dir: Path) -> list[str] | None:
    g = global_defaults()
    tpl, host = str(g.get("wake_command") or "").strip(), str(g.get("host") or "").strip().lower()
    repo = _repo_of(run_dir)
    qid = key if reason == "question" else ""
    subs = {"repo": str(repo), "run_dir": str(run_dir), "question_id": qid, "reason": reason}

    def fill(text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: subs.get(m.group(1), ""), text)

    subs["task"] = fill(_TASKS.get(reason, _TASKS["manual"]))
    if tpl:
        try:
            return [fill(tok) for tok in shlex.split(tpl)]
        except ValueError:
            raise ValueError("плохая команда пробуждения: не могу разобрать wake_command")
    if host != "claude":
        return None
    prompt = fill("Ты мозг tero. Выполни: tero guide --woken; "
                  "tero status --json -w {repo} --run {run_dir}; затем {task}")
    return ["claude", "-p", prompt, "--tools", "Bash", "--setting-sources", "",
            "--strict-mcp-config", "--permission-mode", "dontAsk", "--max-turns", "30",
            "--allowedTools", ALLOWED_TOOLS, "--disallowedTools", DISALLOWED_TOOLS]


def _read_pid(proc: Any) -> int:
    out: list[Any] = []

    def reader() -> None:
        try:
            out.append(proc.stdout.readline())
        except Exception:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(5)
    line = out[0] if out else b""
    text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
    return _int(text.strip())


def _launch(cmd: list[str], run_dir: Path, host: str, popen: Any) -> int:
    """Трамплин: внук вне группы владельца, `sh` дожидаемся сразу, вывод внука в wake.log."""
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    env = wake_env(host)
    env["TERO_WOKEN"] = "1"
    repo = _repo_of(run_dir)
    proc = (popen or popen_factory or subprocess.Popen)(
        ["sh", "-c", TRAMPOLINE, str(log), *cmd], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=str(repo) if repo.is_dir() else None,
        start_new_session=True, env=env)
    try:
        return _read_pid(proc)
    finally:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


# --- счётчики и журнал на машине: `_events`, `_note` из `wake_store` ---------------------------
# Весь общий `wake.log` пишется под `flock` (и события, и отказы запуска/аренды/метки).


def _note_locked(line: dict) -> None:
    try:
        with _lock_of(log_path()):
            _note(line)
    except Exception:
        pass


# --- запись в state.json ---------------------------------------------------------------------

def _stamp(run_dir: Path, entry: dict) -> bool:
    fp = run_dir / "state.json"
    try:
        with owner_lock(fp):
            RunState(fp).update(woken=[*woken_list(run_dir), entry])
            return entry in woken_list(run_dir)
    except Exception:
        return False


def _patch(run_dir: Path, entry: dict, field: str, **fields: Any) -> bool:
    """Дописать pid/pid_started в метку; False, если запись не сохранилась."""
    fp = run_dir / "state.json"
    try:
        with owner_lock(fp):
            items = woken_list(run_dir)
            for e in items:
                if e.get("at") == entry["at"] and e.get(field) == entry.get(field) and not _int(e.get("pid")):
                    e.update(fields)
                    break
            RunState(fp).update(woken=items)
            got = [e for e in woken_list(run_dir)
                   if e.get("at") == entry["at"] and e.get(field) == entry.get(field)]
            return bool(got) and all(
                _int(e.get("pid")) == _int(fields.get("pid"))
                and e.get("pid_started") == fields.get("pid_started")
                and _int(e.get("pid")) > 0 for e in got)
    except Exception:
        return False
    return False


def _kill(pid: int) -> None:
    """Остановить запущенного без аренды/метки: не оставлять сироту (F8)."""
    try:
        if int(pid) > 0:
            os.kill(int(pid), signal.SIGTERM)
    except Exception:
        pass


def _decide(run_dir: Path, reason: str, result: str, ts: float, key: str) -> None:
    fp = run_dir / "state.json"
    entry: dict[str, Any] = {"kind": "wake", "reason": reason, "result": result, "at": ts}
    field = _KEY_FIELD.get(reason, "uuid")
    if key and reason in (*_KEY_FIELD, "manual"):
        entry[field] = key
    try:
        with owner_lock(fp):
            RunState(fp).update(decisions=[*((RunState.read(fp) or {}).get("decisions") or []), entry])
    except Exception:
        pass


def _has_decision(run_dir: Path, reason: str, key: str) -> bool:
    """Один вопрос/uuid — один отказ: повторные циклы дубликаты не пишут (F19)."""
    if not key or reason not in (*_KEY_FIELD, "manual"):
        return False
    field = _KEY_FIELD.get(reason, "uuid")
    try:
        decisions = (RunState.read(run_dir / "state.json") or {}).get("decisions") or []
    except Exception:
        return False
    return any(isinstance(d, dict) and d.get("kind") == "wake"
               and d.get("reason") == reason and d.get(field) == key for d in decisions)


# --- главная функция -------------------------------------------------------------------------

def on_duty(run_dir: str | Path, now: float | None = None) -> bool:
    return lease_mod.on_duty(run_dir, now, default_alive)


def wake(run_dir: str | Path, reason: str, key: str = "", *, now: float | None = None,
         alive: Callable[[Any, Any], bool] | None = None, agent_timeout_s: float | None = None,
         popen: Any = None) -> dict[str, Any]:
    """Разбудить агента: `{ok, result, pid}`. Все проверки, запуск и аренда — под одним замком."""
    run_dir, key = Path(run_dir), str(key or "")
    ts = time.time() if now is None else float(now)
    with _LOCK:
        try:
            res = _wake_locked(run_dir, reason, key, ts, alive or default_alive, agent_timeout_s, popen)
        except Exception:
            res = _no("пробуждение не удалось")
    if not res.get("ok") and _has_decision(run_dir, reason, key):
        return res
    _decide(run_dir, reason, res["result"], ts, key)
    return res


def _no(result: str) -> dict[str, Any]:
    return {"ok": False, "result": result, "pid": 0}


def _wake_locked(run_dir: Path, reason: str, key: str, ts: float, alive: Callable[[Any, Any], bool],
                 timeout: float | None, popen: Any) -> dict[str, Any]:
    field = _KEY_FIELD.get(reason, "uuid")
    if key and reason in _KEY_FIELD and any(e.get(field) == key for e in woken_list(run_dir)):
        return _no("на этот вопрос или сообщение уже будили")
    if lease_mod.on_duty(run_dir, ts, alive):
        until = float((lease_mod.read(run_dir) or {}).get("until") or 0)
        return _no(f"агент дежурит, аренда до {time.strftime('%H:%M', time.localtime(until))}")
    try:
        cmd = _command(reason, key, run_dir)
    except ValueError as exc:
        return _no(str(exc) or "плохая команда пробуждения")
    if cmd is None:
        return _no("команды пробуждения нет: задай `host: claude` или `wake_command` в ~/.tero/config.yaml")
    kind = "manual" if reason == "manual" else "auto"
    with _lock_of(log_path()):
        why = _limits(run_dir.name, kind, ts)
        if not why:
            _note({"wake_event": 1, "at": ts, "kind": kind, "run": run_dir.name, "reason": reason})
    if why:
        return _no(why)
    entry = {"reason": reason, field: key, "at": ts, "pid": 0}
    if not _stamp(run_dir, entry):
        return _no("не могу записать метку пробуждения")
    host = str(global_defaults().get("host") or "").strip().lower()
    try:
        pid = _launch(cmd, run_dir, host, popen)
    except Exception as exc:
        _note_locked({"wake_failed": type(exc).__name__, "at": ts, "run": run_dir.name})
        return _no("запуск не удался")
    started = current_pid_started(pid) if pid > 0 else None
    if pid <= 0 or started is None:
        _note_locked({"wake_failed": "pid", "at": ts, "run": run_dir.name, "pid": pid})
        return _no("запуск не удался: процесс не найден")
    if timeout is None:
        from src.loop.autopilot import agent_timeout_of

        timeout = agent_timeout_of(run_dir)
    if not lease_mod.write(run_dir, until=ts + float(timeout), pid=pid, pid_started=started,
                           host=host, session="", now=ts, if_may_take=True, is_alive=alive):
        _note_locked({"wake_failed": "lease", "at": ts, "run": run_dir.name, "pid": pid})
        _kill(pid)  # F28: аренду занял другой писатель после проверки — её не трогаем
        if lease_mod.on_duty(run_dir, ts, alive):
            until = float((lease_mod.read(run_dir) or {}).get("until") or 0)
            return _no(f"агент дежурит, аренда до {time.strftime('%H:%M', time.localtime(until))}")
        return _no("не могу записать аренду")
    if not _patch(run_dir, entry, field, pid=pid, pid_started=started):
        _note_locked({"wake_failed": "woken-patch", "at": ts, "run": run_dir.name, "pid": pid})
        _kill(pid)
        return _no("не могу записать pid пробуждения")
    remember_global(pid, started, ts)
    return {"ok": True, "result": f"агент запущен (pid {pid})", "pid": pid}


def _limits(run: str, kind: str, now: float) -> str:
    ev = _events(now)
    if any(e.get("run") == run and now - float(e.get("at") or 0) < MIN_GAP_S for e in ev):
        return "пробуждение чаще раза в 5 мин на прогон"
    n = sum(1 for e in ev if e.get("kind") == kind)
    if kind == "manual" and n >= MANUAL_DAILY:
        return f"ручных пробуждений больше {MANUAL_DAILY} в сутки на машину"
    if kind == "auto" and n >= AUTO_DAILY:
        return f"автоматических пробуждений больше {AUTO_DAILY} в сутки на машину"
    return ""


# --- ручной запрос из терминала ---------------------------------------------------------------

def request_wake(run_dir: str | Path) -> bool:
    """`tero wake`: подписанный inbox/wake.json, его подберёт поток владельца."""
    from src.loop.inbox import write_inbox_file

    payload = sign_file("wake", Path(run_dir).name, {"ts": time.time()})
    return write_inbox_file(run_dir, "wake.json", json.dumps(payload))


def take_wake_file(run_dir: str | Path) -> str:
    """'' — файла нет; 'ok' — подписан; 'bad' — без верной подписи (файл убран)."""
    from src.loop.inbox import inbox_dir

    box = inbox_dir(run_dir)
    path = box / "wake.json" if box is not None else None
    if path is None:
        return ""
    if path.is_symlink():
        path.unlink(missing_ok=True)  # ссылку убрать, за целью не ходить
        lease_mod._log(run_dir, "wake.json без верной подписи, игнорирую")
        return "bad"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict) and file_signed("wake", Path(run_dir).name, data):
        return "ok"
    lease_mod._log(run_dir, "wake.json без верной подписи, игнорирую")
    return "bad"
