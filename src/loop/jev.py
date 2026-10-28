"""Тяжесть находок от jev (план 08-2): запрос как body() в бенче, ответ, событие, сбой без стопа."""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from typing import Any, Callable

from src.relay.redact import redact

URL = "https://api.typesafe.ai/v1/systemone"
KEY_ENV = "TYPESAFE_API_KEY"
SECRETS_FILE = "~/.tero/secrets.env"
MODEL = "jev-latest"
BATCH = 8
LEVELS = ("blocker", "major", "minor", "nit")


def _criteria() -> dict[str, str]:
    # Точные строки SEV из jev-bench/v2_draft.py.
    return {
        "blocker": "Breaks the phase contract or the main feature, loses data, hangs, or leaks secrets",
        "major": "Real bug, but only on a rare or error path",
        "minor": "Edge case, weak or missing assertion in a test, small inconsistency",
        "nit": "Wording, formatting, help text, cosmetic or test-style preference",
    }


def load_api_key(secrets_file: str = SECRETS_FILE) -> str:
    """TYPESAFE_API_KEY из окружения, иначе строкой из secrets.env; нет — пусто."""
    import os
    from pathlib import Path

    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    try:
        lines = Path(secrets_file).expanduser().read_text().splitlines()
    except OSError:
        return ""
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line.startswith(KEY_ENV + "="):
            value = line[len(KEY_ENV) + 1:].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""


def _eligible(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in findings if f.get("route") != "discard_test"]


def build_body(
    findings: list[dict[str, Any]],
    *,
    phase: str,
    round_no: int,
    rounds_spent: int,
    n_closed: int,
    secret_paths: list[str] | tuple[str, ...],
    offset: int = 0,
) -> dict[str, Any]:
    """Тело запроса как body() в бенче: state + локальные вопросы s0..sN типа choice.

    Одна пачка — один вызов: ключи вопросов глобальные (s{offset}.. — исходный
    номер находки, ответ второй пачки s8..s10 ложится на позиции 8..10),
    open_findings — только элементы этой пачки, вопрос polishing не задаётся.
    """
    criteria = _criteria()
    open_findings = [
        redact(str(f.get("problem") or ""), secret_paths)[:600] for f in findings
    ]
    questions = {
        f"s{offset + i}": {
            "type": "choice",
            "instructions": f"How severe is open_findings[{i}]?",
            "criteria": criteria,
        }
        for i in range(len(findings))
    }
    return {
        "model": MODEL,
        "state": {
            "phase": redact(phase, secret_paths),
            "round": round_no,
            "rounds_already_spent": rounds_spent,
            "findings_fixed_in_earlier_rounds": n_closed,
            "open_findings": open_findings,
        },
        "questions": questions,
    }


def _default_post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except TimeoutError as exc:
        raise TimeoutError(str(exc) or "timeout") from exc


def _post_once(
    post: Callable[..., tuple[int, dict]],
    url: str,
    headers: dict,
    body: dict,
    timeout_s: float,
) -> tuple[int, dict] | Exception:
    holder: list = []
    done = threading.Event()

    def target() -> None:
        try:
            holder.append(post(url, headers, body, timeout_s))
        except Exception as exc:  # noqa: BLE001
            holder.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    if not done.wait(timeout_s):
        return TimeoutError(f"jev timeout {timeout_s}s")
    out = holder[0] if holder else TimeoutError("jev no result")
    thread.join(timeout=1)
    return out


def _sort_answers(answers: Any, n: int, offset: int = 0) -> list[list] | None:
    """answers[sN] -> [[choice, confidence], ...] в числовом порядке s{offset}...

    Формат ответа — как читает бенч: choice + confidence; confidence обязан быть
    конечным числом в [0, 1], иначе весь ответ — мусор (bad payload).
    """
    if not isinstance(answers, dict):
        return None
    sev: list[list] = []
    for i in range(offset, offset + n):
        row = answers.get(f"s{i}")
        if not isinstance(row, dict):
            return None
        if row.get("choice") not in LEVELS:
            return None
        conf = row.get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            return None
        # Range first: int/float vs bound compares fine at any magnitude without
        # overflow, and already excludes NaN/inf. math.isfinite() on a huge int
        # (e.g. 10**1000) raises OverflowError, so it must only run after conf
        # is known to be within [0, 1].
        if not (0.0 <= conf <= 1.0):
            return None
        if not math.isfinite(conf):
            return None
        sev.append([row["choice"], round(float(conf), 2)])
    return sev


def _read_tokens(payload: dict) -> int | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    if "input_tokens" not in usage:
        return None
    raw = usage["input_tokens"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    if raw < 0:
        return None
    return raw


def request_sev(
    findings: list[dict[str, Any]],
    *,
    phase: str = "",
    round_no: int = 0,
    rounds_spent: int = 0,
    n_closed: int = 0,
    secret_paths: list[str] | tuple[str, ...] = (),
    api_key: str = "",
    post: Callable[..., tuple[int, dict]] | None = None,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    """Тяжесть раунда; сбой → {"error": ...}, ключа и тела ответа в ошибке нет."""
    wanted = _eligible(findings)
    if not api_key:
        return {"error": "no api key"}
    send = post or _default_post
    wire = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    sev_all: list[list] = []
    tok = 0
    deadline = time.monotonic() + timeout_s
    for start in range(0, len(wanted), BATCH):
        chunk = wanted[start : start + BATCH]
        body = build_body(
            chunk,
            phase=phase,
            round_no=round_no,
            rounds_spent=rounds_spent,
            n_closed=n_closed,
            secret_paths=secret_paths,
            offset=start,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"error": "timeout"}
        out = _post_once(send, URL, wire, body, remaining)
        if isinstance(out, Exception):
            if isinstance(out, TimeoutError):
                return {"error": "timeout"}
            return {"error": "network"}
        status, payload = out
        if status != 200 or not isinstance(payload, dict):
            return {"error": f"http {status}"}
        batch = _sort_answers(payload.get("answers"), len(chunk), offset=start)
        batch_tok = _read_tokens(payload)
        if batch is None or batch_tok is None:
            return {"error": "bad payload"}
        tok += batch_tok
        sev_all.extend(batch)
    return {"sev": sev_all, "tok": tok}
