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


def _key(create: bool = False) -> bytes | None:
    path = key_path()
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
        return path.read_bytes().strip() or None
    except OSError:
        if not create:
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(value)
    return value


def _mac(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def _answer_message(payload: Mapping[str, Any]) -> str:
    parts = ("uuid", "question_id", "action", "text")
    return "\n".join(str(payload.get(k) or "") for k in parts)


def sign_answer(payload: dict[str, Any]) -> dict[str, Any]:
    """Add `sig` to a human answer (creates the key on first use)."""
    key = _key(create=True)
    payload["sig"] = _mac(key, _answer_message(payload)) if key else ""
    return payload


def who_of(payload: Mapping[str, Any]) -> str:
    """Declared `who`, except `human` without a valid signature becomes `agent`.

    Only `human` carries rights (allow, accept/reject, no budget), so it is
    the only claim that needs proof. Never raises.
    """
    try:
        if payload.get("who") != "human":
            return str(payload.get("who") or "agent")
        key = _key()
        sig = str(payload.get("sig") or "")
        if key and sig and hmac.compare_digest(sig, _mac(key, _answer_message(payload))):
            return "human"
    except Exception:
        pass
    return "agent"


def _list_message(items: list[str]) -> str:
    return json.dumps(sorted(items), ensure_ascii=False)


def sign_list(items: list[str]) -> str:
    key = _key(create=True)
    return _mac(key, _list_message(items)) if key else ""


def trusted_list(items: Any, sig: Any) -> list[str]:
    """`items` when `sig` matches, else []. Never raises."""
    try:
        values = [str(x) for x in (items or [])]
        key = _key()
        if key and sig and hmac.compare_digest(str(sig), _mac(key, _list_message(values))):
            return values
    except Exception:
        pass
    return []
