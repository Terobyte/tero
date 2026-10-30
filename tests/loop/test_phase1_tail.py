"""Хвост фазы 1 плана 09: гайд усыпляет с --messages; подпись без гонки."""

from __future__ import annotations

import threading


def test_guide_sleeps_with_messages():
    from src.commands.guide_cmds import HOSTS_GUIDE

    assert "--messages" in HOSTS_GUIDE


def test_concurrent_sign_message_no_race(tmp_path, monkeypatch):
    """Поток опроса и владелец подписывают одновременно: FileExistsError нет.

    F15: детерминировано — первый signer приостановлен после os.open (пустой
    ключ уже создан, но не записан), второй signer запускается в этом окне;
    релиз только после того, как второй signer гарантированно вошёл в ожидание
    содержимого (_read_key_bytes), а не по сну. F18: удержание 0.7 с дольше
    старого бюджета ожидания (0.5 с) — обе подписи всё равно проходят
    message_ok, пустой подписи нет.
    """
    import json
    import os as _os
    import threading
    import time

    import src.loop.human_key as hk
    from src.loop.human_key import message_ok, sign_message

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    real_open = _os.open
    entered = threading.Event()
    release = threading.Event()
    in_wait = threading.Event()
    claimed = {"n": 0}
    errors: list[BaseException] = []
    payloads: list[dict] = []
    real_read = hk._read_key_bytes

    def watched_read(path):
        in_wait.set()
        return real_read(path)

    monkeypatch.setattr(hk, "_read_key_bytes", watched_read)

    def patched_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if str(path).endswith("human.key") and claimed["n"] == 0:
            claimed["n"] += 1
            entered.set()
            assert release.wait(timeout=5), "второй signer не дождался окна"
        return fd

    monkeypatch.setattr(_os, "open", patched_open)

    def go(i: int) -> None:
        try:
            payloads.append(sign_message("run1", {"uuid": f"u{i}", "kind": "message",
                                                  "text": "t", "ts": 1.0}))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=go, args=(0,))
    t1.start()
    assert entered.wait(timeout=5), "первый signer не создал ключ"
    t2 = threading.Thread(target=go, args=(1,))
    t2.start()
    assert in_wait.wait(timeout=5), "второй signer не вошёл в ожидание ключа"
    time.sleep(1.7)  # как в репродукции F18: дольше суммарного старого бюджета
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not errors
    assert len(payloads) == 2
    assert all(message_ok("run1", json.loads(json.dumps(p))) for p in payloads)
