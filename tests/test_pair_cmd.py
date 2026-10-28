"""Фаза 08-5: `tero pair` и `tero relay setup` (красный: команд пока нет)."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import cli_entry


def _home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _pair_env(tmp_path, write_key="wk"):
    store = {}

    def fetch(method, path, body=None, token=""):
        if path == "/pair/code" and method == "POST":
            assert token == write_key
            return _Resp(200, {"code": "ABCDEFGH", "expires_at": 123})
        if path == "/pair/revoke":
            store["revoked"] = (body or {}).get("device_id")
            return _Resp(200, {"ok": True, "revoked": 2})
        if path == "/pair/reset":
            store["reset"] = True
            return _Resp(200, {"ok": True, "revoked": 4})
        raise AssertionError(f"unexpected {method} {path}")

    relay_url = "https://relay.example"
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        f"relay:\n  url: {relay_url}\n", encoding="utf-8"
    )
    secrets = tmp_path / "secrets.env"
    secrets.write_text(f"RELAY_WRITE_KEY={write_key}\n", encoding="utf-8")
    return fetch, relay_url, store


def test_relay_parsers_registered():
    parser = cli_entry.build_parser()
    relay = parser.parse_args(["relay", "setup"])
    assert relay.command == "relay" and relay.relay_command == "setup"
    pair = parser.parse_args(["pair"])
    assert pair.command == "pair"


def test_pair_prints_link_without_write_key(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    from src.commands import relay_cmds

    fetch, relay_url, _ = _pair_env(tmp_path)
    monkeypatch.setattr(relay_cmds, "DEFAULT_FETCH", fetch)
    monkeypatch.setattr(relay_cmds, "_config_path", lambda: tmp_path / "cfg" / "config.yaml")
    monkeypatch.setattr(relay_cmds, "_secrets_path", lambda: tmp_path / "secrets.env")

    args = SimpleNamespace(relay_url=None, revoke=None, reset=False)
    code = relay_cmds.run_pair(args, fetch=fetch)
    out = capsys.readouterr().out
    assert code == 0
    assert "ABCDEFGH" in out
    assert relay_url.split("://")[1] in out
    assert "wk" not in out


def test_relay_setup_writes_secrets_600_without_echo(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    from src.commands import relay_cmds

    node = tmp_path / "node"
    node.write_text(
        "#!/bin/sh\n"
        'printf \'{"public":{"kty":"EC"},"private":{"kty":"EC"}}\'',
        encoding="utf-8",
    )
    node.chmod(0o755)
    args = SimpleNamespace(relay_url="https://relay.example", node=str(node))
    code = relay_cmds.run_relay_setup(args)
    out = capsys.readouterr().out
    assert code == 0
    secrets = home / ".tero" / "secrets.env"
    assert secrets.is_file()
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    text = secrets.read_text(encoding="utf-8")
    assert "RELAY_WRITE_KEY=" in text and "VAPID_PRIVATE_KEY=" in text
    parsed = json.loads(text.split("VAPID_PUBLIC_KEY=", 1)[1])
    assert parsed == {"kty": "EC"}
    cfg = (home / ".tero" / "config.yaml").read_text(encoding="utf-8")
    assert "relay:" in cfg and "https://relay.example" in cfg
    assert "wrangler" in out
    assert "relay/schema.sql" in out
    assert "PRAGMA table_info(push_subs)" in out
    assert "relay/migrate-push-device.sql" in out
    assert "ОДИН раз" in out
    assert out.index("relay/migrate-push-device.sql") < out.index("--file=relay/schema.sql")
    for line in out.splitlines():
        assert '{"kty"' not in line
        assert "RELAY_WRITE_KEY=" not in line or "secret" in line.lower()


def test_pair_relay_setup_require_node_for_vapid(tmp_path, monkeypatch):
    from src.commands import relay_cmds

    with pytest.raises(RuntimeError):
        relay_cmds.generate_vapid_keys(node_bin="/nonexistent-node-xyz")


def test_pair_error_status_is_not_success(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    from src.commands import relay_cmds

    _pair_env(tmp_path)
    monkeypatch.setattr(relay_cmds, "_config_path", lambda: tmp_path / "cfg" / "config.yaml")
    monkeypatch.setattr(relay_cmds, "_secrets_path", lambda: tmp_path / "secrets.env")

    def fetch(method, path, body=None, token=""):
        return _Resp(401, {"error": "unauthorized"})

    for extra in ({}, {"revoke": "dev1"}, {"reset": True}):
        args = SimpleNamespace(**{"relay_url": None, "revoke": None, "reset": False, **extra})
        assert relay_cmds.run_pair(args, fetch=fetch) == 1
    out = capsys.readouterr().out
    assert "/pair/" not in out and "HTTP 401" in out


def test_http_fetch_sends_own_user_agent(monkeypatch):
    # Cloudflare отвечает 1010 на UA Python-urllib: клиент обязан представиться сам.
    import urllib.request

    from src.commands import relay_cmds

    seen = {}

    class _Res:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=0):
        seen["ua"] = req.get_header("User-agent")
        return _Res()

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    relay_cmds._http_fetch("https://relay.example")("POST", "/pair/code", {}, "k")
    assert seen["ua"] and "urllib" not in seen["ua"].lower()


def test_relay_upgrade_paths_support_legacy_and_fresh_schemas():
    import sqlite3

    relay = Path(__file__).resolve().parents[1] / "relay"
    for legacy in (False, True):
        db = sqlite3.connect(":memory:")
        if legacy:
            db.executescript("""
                CREATE TABLE devices (token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    label TEXT NOT NULL, revoked INTEGER DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE push_subs (endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL, dead INTEGER DEFAULT 0);
            """)
            db.executescript((relay / "migrate-04-05.sql").read_text())
            db.executescript((relay / "migrate-push-device.sql").read_text())
        db.executescript((relay / "schema.sql").read_text())
        db.executescript((relay / "schema.sql").read_text())
        for table in ("devices", "push_subs"):
            assert "device_id" in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        db.close()
