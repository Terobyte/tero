"""`tero watch`: the per-machine watcher and its service install."""

from __future__ import annotations

import time

from src.watch import install as svc


def _poster():
    from src.relay.client import RelayClient
    from src.relay.machine import machine_id

    client = RelayClient(machine_id=machine_id())

    def _clean(value):
        from src.errors import scrub_secrets
        from src.relay.redact import redact

        try:
            from src.loop.git import secret_paths as _sp
            paths = list(_sp(".") or [])
        except Exception:
            paths = []
        if isinstance(value, str):
            # same cleaning as _redacted_state: env keys/tokens too, not only the relay key
            cleaned = scrub_secrets(redact(value, paths))
            try:
                return client._mask_key(cleaned)
            except Exception:
                return cleaned
        if isinstance(value, list):
            return [_clean(v) for v in value]
        if isinstance(value, dict):
            return {k: _clean(v) for k, v in value.items()}
        return value

    def post(path: str, body: dict):
        return client._call("POST", path, _clean(body) if isinstance(body, dict) else body)

    return post, client.machine_id


def run_watch(args) -> int:
    if getattr(args, "install", False):
        try:
            plat = svc.install()
        except RuntimeError as exc:
            print(f"сторож не установлен: {exc}")
            return 1
        print(f"сторож установлен ({plat}): {svc.status(plat)['path']}")
        for line in svc.hints(plat):
            print(line)
        for line in svc.repo_access_lines():
            print(line)
        return 0
    if getattr(args, "uninstall", False):
        svc.uninstall()
        print("сторож снят")
        return 0
    if getattr(args, "status", False):
        info = svc.status()
        state = "установлен" if info["installed"] else "не установлен"
        extra = ""
        if info["installed"] and info.get("running") is False:
            extra = ", не запущен"
        elif info["installed"] and info.get("running"):
            extra = ", запущен"
        print(f"сторож: {state}{extra} ({info['path']})")
        return 0
    from src.watch.service import POLL_S, Watcher, acquire_watch_lock

    lock = acquire_watch_lock()
    if lock is None:
        print("watch уже запущен на этой машине")
        return 0
    post, mid = _poster()
    watcher = Watcher(post, machine_id=mid)
    try:
        while True:
            watcher.cycle()
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        return 130
