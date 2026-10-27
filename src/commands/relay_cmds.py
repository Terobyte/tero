"""`tero relay setup` и `tero pair`: привязка телефона (план 08-5)."""

from __future__ import annotations

import json
import secrets as _secrets
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


def _home() -> Path:
    from pathlib import Path as _P

    import os as _os

    return _P(_os.path.expanduser("~"))


def _config_path() -> Path:
    return _home() / ".tero" / "config.yaml"


def _secrets_path() -> Path:
    return _home() / ".tero" / "secrets.env"


def _read_relay_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    import yaml

    try:
        text = _config_path().read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        data = yaml.safe_load(text) or {}
    except Exception:
        data = {}
    section = data.get("relay") if isinstance(data, dict) else None
    url = section.get("url", "") if isinstance(section, dict) else ""
    return str(url).rstrip("/") if url else ""


def _read_write_key() -> str:
    try:
        lines = _secrets_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line.startswith("RELAY_WRITE_KEY="):
            value = line[len("RELAY_WRITE_KEY="):].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
                value = value[1:-1]
            return value
    return ""


def DEFAULT_FETCH(  # noqa: N802 — имя точки подмены, как make_relay
    method: str, path: str, body: dict | None = None, token: str = ""
) -> Any:
    """Заглушка: настоящий HTTP собирает _http_fetch; подменяется фейком в тестах."""
    raise NotImplementedError


def _http_fetch(base_url: str) -> Callable:
    def _fetch(method: str, path: str, body: dict | None = None, token: str = "") -> Any:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            base_url + path, data=data if method != "GET" else None, method=method
        )
        req.add_header("content-type", "application/json")
        if token:
            req.add_header("authorization", f"Bearer {token}")

        class _Resp:
            def __init__(self, status: int, payload: dict) -> None:
                self.status = status
                self._payload = payload

            def json(self) -> dict:
                return self._payload

        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read().decode("utf-8")
                return _Resp(res.status, json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:  # 4xx/5xx — ответ со статусом, решает вызывающий
            try:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
            except ValueError:
                payload = {}
            return _Resp(exc.code, payload if isinstance(payload, dict) else {})
        except Exception as exc:  # сеть не роняет команду: честная ошибка
            raise RuntimeError(f"ретранслятор недоступен: {exc}") from exc

    return _fetch


def _ok_json(res: Any) -> dict | None:
    """Тело ответа 200 как dict, иначе None (401/5xx не принимаются за успех)."""
    if getattr(res, "status", 0) != 200:
        return None
    try:
        payload = res.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def run_pair(args: Any, fetch: Callable | None = None) -> int:
    """Одноразовый код: ссылка хозяину; --revoke/--reset отзывают токены."""
    base_url = _read_relay_url(getattr(args, "relay_url", None))
    if not base_url:
        print("нет адреса ретранслятора: сначала `tero relay setup`")
        return 1
    token = _read_write_key()
    do_fetch = fetch
    if do_fetch is None:
        do_fetch = _http_fetch(base_url)
    if getattr(args, "reset", False):
        res = do_fetch("POST", "/pair/reset", {}, token)
        payload = _ok_json(res)
        if payload is None:
            print(f"сброс не выполнен (HTTP {res.status})")
            return 1
        print(f"отозвано токенов: {payload.get('revoked', 0)}")
        return 0
    label = getattr(args, "revoke", None)
    if label:
        res = do_fetch("POST", "/pair/revoke", {"device_id": label}, token)
        payload = _ok_json(res)
        if payload is None:
            print(f"отзыв не выполнен (HTTP {res.status})")
            return 1
        print(f"устройство отозвано: {label} ({payload.get('revoked', 0)} токенов)")
        return 0
    res = do_fetch("POST", "/pair/code", {}, token)
    payload = _ok_json(res)
    code = str(payload.get("code") or "") if payload is not None else ""
    if not code:
        print(f"ретранслятор не выдал код (HTTP {res.status})")
        return 1
    host = base_url.split("://", 1)[-1]
    print(f"открой ссылку на телефоне: https://{host}/pair/{code}")
    return 0


def generate_vapid_keys(node_bin: str | None = None) -> dict:
    """VAPID P-256 через node (в stdlib Python P-256 нет)."""
    node = node_bin or shutil.which("node")
    if not node:
        raise RuntimeError("node не найден: установи Node ≥ 22.5 для генерации VAPID")
    root = Path(__file__).resolve().parent.parent.parent
    script = root / "relay" / "src" / "vapid_keygen.js"
    try:
        proc = subprocess.run(
            [node, str(script)], capture_output=True, text=True, timeout=60
        )
    except OSError as exc:
        raise RuntimeError(f"node не запустился: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"vapid_keygen упал: {proc.stderr.strip()[:200]}")
    try:
        keys = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError("vapid_keygen отдал не JSON") from exc
    return {"public": keys["public"], "private": keys["private"]}


def _write_relay_url(base_url: str) -> None:
    """Адрес ретранслятора в ~/.tero/config.yaml (секция relay)."""
    import yaml

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    section = data.get("relay")
    if not isinstance(section, dict):
        section = {}
    section["url"] = base_url
    data["relay"] = section
    from src.fsio import atomic_write_text

    atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True), mode=0o600)


def run_relay_setup(args: Any) -> int:
    """Ключ записи + VAPID в ~/.tero/secrets.env (600); деплой — командами человеку."""
    from src.fsio import atomic_write_text

    base_url = getattr(args, "relay_url", None) or _read_relay_url(None)
    if not base_url:
        print("укажи адрес: `tero relay setup --relay-url https://tero-relay.<твой>.workers.dev`")
        return 1
    write_key = _secrets.token_urlsafe(32)
    vapid = generate_vapid_keys(getattr(args, "node", None))
    secrets_path = _secrets_path()
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        old = secrets_path.read_text(encoding="utf-8")
    except OSError:
        old = ""
    kept = [
        line
        for line in old.splitlines()
        if not line.strip().lstrip("export ").startswith(
            ("RELAY_WRITE_KEY=", "VAPID_PRIVATE_KEY=", "VAPID_PUBLIC_KEY=")
        )
    ]
    kept.append(f"RELAY_WRITE_KEY={write_key}")
    kept.append(f"VAPID_PRIVATE_KEY={json.dumps(vapid['private'])}")
    kept.append(f"VAPID_PUBLIC_KEY={json.dumps(vapid['public'])}")
    atomic_write_text(secrets_path, "\n".join(kept) + "\n", mode=0o600)
    _write_relay_url(base_url)
    host = base_url.split("://", 1)[-1]
    print(f"ключи записаны в {secrets_path} (права 600)")
    print("дальше вручную:")
    print("  wrangler login")
    print(f"  wrangler d1 create tero-relay  # database_id в relay/wrangler.toml")
    print("  # на базе от фазы 08-4 сначала миграция, иначе schema.sql упадёт:")
    print("  wrangler d1 execute tero-relay --remote --file=relay/migrate-04-05.sql")
    print("  wrangler d1 execute tero-relay --remote --file=relay/schema.sql")
    print("  cat ~/.tero/secrets.env  # RELAY_WRITE_KEY — значение для секрета ниже")
    print("  wrangler secret put RELAY_WRITE_KEY")
    print("  wrangler secret put VAPID_PRIVATE_KEY  # JSON из secrets.env")
    print(f"  wrangler deploy  # адрес: {host}")
    return 0
