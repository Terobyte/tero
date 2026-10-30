"""Human answers are signed, not self-declared (B7/B10).

`who: "human"` in an inbox file used to be trusted as written, and the inbox
lives inside the worktree the player writes to. Now `tero answer --human` on a
TTY signs the answer with `~/.tero/human.key` (outside the worktree, 0600) and
the owner trusts `human` only with a valid signature. The same key signs the
`allowed_secrets` list in `state.json`, so an edited list is ignored.

An agent under the same uid with a shell can still read the key: this guards
against a sandboxed or confused agent, not a deliberate attacker.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any, Mapping


def key_path() -> Path:
    return Path.home() / ".tero" / "human.key"


def _read_key_bytes(path: Path, timeout_s: float = 6.0) -> bytes | None:
    """Содержимое ключа, ожидая запись: соседний поток мог создать пустой файл.

    Гонка F15/F18: читаем до `timeout_s`, а не 0.5 с — иначе второй signer
    при долгой записи первого подпишет пустой подписью и сообщение потеряется.
    """
    import time as _time

    start = _time.monotonic()
    while True:
        try:
            data = path.read_bytes().strip()
        except OSError:
            return None
        if data:
            return data
        if _time.monotonic() - start >= timeout_s:
            return None
        _time.sleep(0.005)


def _key(create: bool = False) -> bytes | None:
    path = key_path()
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
        got = _read_key_bytes(path)
        if got is not None:
            return got
        if not create:
            return None
    except OSError:
        if not create:
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32).encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Гонка: ключ уже создал соседний поток — читаем его, а не падаем.
        # Файл может быть ещё пустым (создан, но не записан): ждём содержимое.
        return _read_key_bytes(path)
    with os.fdopen(fd, "wb") as fh:
        fh.write(value)
    return value


def _mac(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def _answer_message(run_id: str, payload: Mapping[str, Any]) -> str:
    # run id in the message: an old signed answer copied from another run's inbox
    # (question ids like "1-1" repeat across runs) does not verify here
    parts = ("uuid", "question_id", "action", "text")
    return json.dumps(["answer", run_id, *(str(payload.get(k) or "") for k in parts)],
                      ensure_ascii=False)


def sign_answer(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Add `sig` to a human answer for run `run_id` (creates the key on first use)."""
    key = _key(create=True)
    payload["sig"] = _mac(key, _answer_message(run_id, payload)) if key else ""
    return payload


def who_of(run_id: str, payload: Mapping[str, Any]) -> str:
    """Declared `who`, except `human` without a valid signature becomes `agent`.

    Only `human` carries rights (allow, accept/reject, no budget), so it is
    the only claim that needs proof. Never raises.
    """
    try:
        if payload.get("who") != "human":
            return str(payload.get("who") or "agent")
        key = _key()
        sig = str(payload.get("sig") or "")
        if key and sig and hmac.compare_digest(sig, _mac(key, _answer_message(run_id, payload))):
            return "human"
    except Exception:
        pass
    return "agent"


def _list_message(run_id: str, items: list[str]) -> str:
    return json.dumps(["allowed_secrets", run_id, sorted(items)], ensure_ascii=False)


def sign_list(run_id: str, items: list[str]) -> str:
    key = _key(create=True)
    return _mac(key, _list_message(run_id, items)) if key else ""


def trusted_list(run_id: str, items: Any, sig: Any) -> list[str]:
    """`items` when `sig` matches for this run, else []. Never raises."""
    try:
        values = [str(x) for x in (items or [])]
        key = _key()
        if key and sig and hmac.compare_digest(str(sig), _mac(key, _list_message(run_id, values))):
            return values
    except Exception:
        pass
    return []


def _file_message(kind: str, run_id: str, payload: Mapping[str, Any]) -> str:
    # one message shape for every signed run file (lease, msg-*, wake.json,
    # resume-answer.json): every key but `sig`, sorted, run id inside
    body = {str(k): payload[k] for k in payload if k != "sig"}
    return json.dumps([str(kind), str(run_id), body], ensure_ascii=False, sort_keys=True,
                      default=str)


def sign_file(kind: str, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Add `sig` to a run file the owner or host must trust (creates the key)."""
    key = _key(create=True)
    payload["sig"] = _mac(key, _file_message(kind, run_id, payload)) if key else ""
    return payload


def file_signed(kind: str, run_id: str, payload: Mapping[str, Any]) -> bool:
    """True when `payload.sig` matches this run and kind. Never raises."""
    try:
        key = _key()
        sig = str(payload.get("sig") or "")
        return bool(key and sig and hmac.compare_digest(sig, _mac(key, _file_message(kind, run_id, payload))))
    except Exception:
        return False



def _message_text(run_id: str, payload: Mapping[str, Any]) -> str:
    return json.dumps(["msg", run_id, str(payload.get("uuid") or ""), str(payload.get("text") or "")],
                      ensure_ascii=False)


def sign_message(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Add `sig` to a message to the host (`inbox/msg-<uuid>.json`)."""
    key = _key(create=True)
    payload["sig"] = _mac(key, _message_text(run_id, payload)) if key else ""
    return payload


def message_ok(run_id: str, payload: Mapping[str, Any]) -> bool:
    """True only for a message signed by this machine's key. Never raises."""
    try:
        key = _key()
        sig = str(payload.get("sig") or "")
        return bool(key and sig and hmac.compare_digest(sig, _mac(key, _message_text(run_id, payload))))
    except Exception:
        return False
