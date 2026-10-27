"""Реальный RelayClient: push/fetch/ack/notify через подменяемый fetch (план 08-6)."""

from __future__ import annotations

from typing import Any, Callable

from src.errors import scrub_secrets
from src.loop.git import secret_paths as _secret_paths
from src.relay.iface import Answer, RelayClient as _Proto  # noqa: F401 — реэкспорт имени
from src.relay.redact import redact

TIMEOUT_S = 5.0


def _cfg_get(cfg: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(cfg, name, default)
    except Exception:
        return default


def _relay_cfg() -> tuple[str, str]:
    """Адрес и ключ записи: только окружение и ~/.tero (не репо)."""
    import os
    from pathlib import Path

    url = os.environ.get("TERO_RELAY_URL", "")
    key = os.environ.get("TERO_RELAY_WRITE_KEY", "")
    if not url or not key:
        try:
            import yaml

            data = yaml.safe_load(
                (Path.home() / ".tero" / "config.yaml").read_text(encoding="utf-8")
            ) or {}
            sec = data.get("relay") if isinstance(data, dict) else {}
            url = url or (sec.get("url", "") if isinstance(sec, dict) else "")
        except Exception:
            pass
    if not key:
        try:
            for raw in (Path.home() / ".tero" / "secrets.env").read_text(
                encoding="utf-8"
            ).splitlines():
                line = raw.strip()
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if line.startswith("RELAY_WRITE_KEY="):
                    value = line[len("RELAY_WRITE_KEY="):].strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
                        value = value[1:-1]
                    key = value
                    break
        except Exception:
            pass
    return url.rstrip("/"), key


def _secret_list() -> list[str]:
    try:
        return list(_secret_paths(".") or [])
    except Exception:
        return []


class RelayClient:
    """HTTP-клиент ретранслятора; сеть не роняет прогон."""

    def __init__(self, cfg: Any = None, fetch: Callable | None = None,
                 run_id: str = "", machine_id: str = "") -> None:
        self._cfg = cfg
        base, key = _relay_cfg()
        if cfg is not None:
            base = str(_cfg_get(cfg, "relay_url", "") or base or "").rstrip("/")
            key = str(_cfg_get(cfg, "relay_write_key", "") or key or "")
        self._base = base
        self._key = key
        self._fetch = fetch
        self.run_id = run_id
        self.machine_id = machine_id
        self.last_error = ""
        self.last_ok = True

    def _call(self, method: str, path: str, body: dict | None = None,
              token: str = "") -> Any | None:
        do_fetch = self._fetch
        if do_fetch is None:
            try:
                from src.commands.relay_cmds import _http_fetch as _mk

                do_fetch = _mk(self._base) if self._base else None
            except Exception:
                do_fetch = None
        if do_fetch is None:
            return None
        import threading as _t

        box: dict[str, Any] = {}

        def _run() -> None:
            try:
                box["res"] = do_fetch(method, path, body, token or self._key)
            except Exception as exc:  # сеть не роняет прогон
                box["exc"] = exc

        worker = _t.Thread(target=_run, daemon=True, name="tero-relay-fetch")
        worker.start()
        worker.join(timeout=max(0.1, TIMEOUT_S - 0.2))
        if worker.is_alive():
            self.last_error = "timeout"
            self.last_ok = False
            return None
        if "exc" in box:
            self.last_error = str(box["exc"])[:200]
            self.last_ok = False
            return None
        res = box.get("res")
        if getattr(res, "status", 0) != 200:
            try:
                self.last_error = f"HTTP {res.status}"
            except Exception:
                self.last_error = "bad status"
            self.last_ok = False
            return None
        self.last_ok = True
        self.last_error = ""
        try:
            return res.json()
        except Exception:
            return {}

    def _mask_key(self, text: str) -> str:
        try:
            key = self._key or ""
            if key and len(key) >= 8 and isinstance(text, str):
                return text.replace(key, "[redacted]")
        except Exception:
            pass
        return text

    def _redacted_state(self, state: dict) -> dict:
        paths = _secret_list()
        out: dict[str, Any] = {}
        for key in ("run_id", "machine_id", "status", "phase", "phase_index",
                    "round", "role", "model", "question", "actions", "models",
                    "decisions", "debts", "limits", "findings",
                    "phone_text_answers", "updated_at"):
            if key in state:
                out[key] = state[key]
        if "phone_text_answers" not in out:
            try:
                cfg_block = state.get("config")
                phone = cfg_block.get("phone_text_answers") if isinstance(cfg_block, dict) else None
            except Exception:
                phone = None
            if phone is not None:
                out["phone_text_answers"] = phone
        if isinstance(state.get("updated_at"), str):
            out["state_at"] = state["updated_at"]  # порядок: старый push не затрёт новый
        if not out.get("run_id"):
            out["run_id"] = self.run_id
        if not out.get("machine_id"):
            out["machine_id"] = self.machine_id or _machine_id()
        try:

            def _clean(value: Any) -> Any:
                if isinstance(value, str):  # шаблоны секретов + значения из secrets.env
                    return scrub_secrets(redact(value, paths))
                if isinstance(value, list):
                    return [_clean(v) for v in value]
                if isinstance(value, dict):
                    return {k: _clean(v) for k, v in value.items()}
                return value

            out = {k: _clean(v) for k, v in out.items()}
            try:
                key = self._key or ""
                if key and len(key) >= 8:
                    def _mk(v: Any) -> Any:
                        if isinstance(v, str):
                            return v.replace(key, "[redacted]")
                        if isinstance(v, list):
                            return [_mk(x) for x in v]
                        if isinstance(v, dict):
                            return {kk: _mk(vv) for kk, vv in v.items()}
                        return v
                    out = _mk(out)
            except Exception:
                pass
        except Exception:
            pass
        return out

    def push_state(self, state: dict) -> bool:
        if not self._base or not (self._key or self._fetch):
            self.last_ok = False
            self.last_error = "ретранслятор не настроен"
            return False
        payload = self._redacted_state(dict(state or {}))
        got = self._call("POST", "/state", payload)
        if got is None:
            return False
        try:
            if isinstance(got, dict) and "push_ok" in got:
                self._push_ok = bool(got["push_ok"])
        except Exception:
            pass
        return True

    def fetch_answers(self, question_id: str) -> list[Answer]:
        if not self._base or not (self._key or self._fetch):
            return []
        try:
            from urllib.parse import quote as _q

            qid = _q(str(question_id), safe="")
            rid = _q(str(self.run_id or ""), safe="")
            mid = _q(str(self.machine_id or _machine_id() or ""), safe="")
            params = f"?question_id={qid}&run_id={rid}&machine_id={mid}"
        except Exception:
            params = f"?question_id={question_id}&run_id={self.run_id}&machine_id={self.machine_id}"
        got = self._call("GET", f"/answers{params}")
        if not isinstance(got, dict):
            return []
        rows = got.get("answers") or []
        out: list[Answer] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            out.append(Answer(
                question_id=str(row.get("question_id") or question_id),
                action=str(row.get("action") or ""),
                text=str(row.get("text") or ""),
                who=str(row.get("who") or "human"),
                why=str(row.get("why") or ""),
                uuid=str(row.get("uuid") or ""),
                via="relay",
            ))
        return out

    def ack(self, uuid: str, reason: str = "") -> bool:
        if not self._base or not (self._key or self._fetch):
            return False
        body: dict[str, Any] = {"uuid": uuid}
        if reason:
            try:
                reason = self._mask_key(redact(reason, _secret_list()))
            except Exception:
                pass
            body["reason"] = reason[:200]
        return self._call("POST", "/answers/ack", body) is not None

    def notify(self, text: str) -> bool:
        try:
            text = self._mask_key(redact(text or "", _secret_list()))
        except Exception:
            pass
        if not self._base or not (self._key or self._fetch):
            return False
        return self._call("POST", "/notify", {"text": text[:500]}) is not None

    @property
    def push_ok(self) -> bool:
        return bool(getattr(self, "_push_ok", True))


def _machine_id() -> str:
    try:
        import socket

        return socket.gethostname()[:64]
    except Exception:
        return ""
