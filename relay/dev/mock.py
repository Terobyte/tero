"""Мок релея для отладки страницы пульта без Cloudflare и без живого tero.

    python3 relay/dev/mock.py [--port 8799]

Раздаёт relay/public (no-store) и отвечает на /runs, /state, POST-ответы.
Состояния синтетические: живой прогон с вопросом (23 фазы), сданный с долгом, остановленный.
GET /__mock/offline?on=1 — все API отвечают 503 (проверка плашки «нет связи»), on=0 — назад.
POST с "skip" в теле → 422 no open question (проверка показа ошибки ответа).
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PUBLIC = Path(__file__).resolve().parent.parent / "public"
MACHINE = "mac:mock"
TITLES = [f"Ф{i}. шаг {i}: что-то про движок" for i in range(1, 24)]


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))


def _sectors(current: int, debt: set[int], shipped_all: bool = False) -> list[dict]:
    out = []
    for i, title in enumerate(TITLES):
        color = "green" if shipped_all or i < current else "yellow" if i == current else "grey"
        if i + 1 in debt:
            color = "orange"
        out.append({"title": title, "color": color})
    return out


def _log(now: float) -> list[dict]:
    rows = []
    for k in range(12):
        rows.append({"ts": _iso(now - 600 + k * 40), "role": "player", "type": "tool",
                     "text": f"Bash npx vitest run test/unit/very/long/path/to/some.test.ts --reporter dot {k}"})
        rows.append({"ts": _iso(now - 590 + k * 40), "role": "player", "type": "text", "text": f"шаг {k}: тесты зелёные"})
    rows.append({"ts": _iso(now - 30), "role": "judge", "type": "verdict",
                 "text": json.dumps({"verdict": "NO_SHIP", "blocking": ["F1"], "findings": ["F1: беда → проверка"]})})
    return rows


def _view(now: float, *, light: str, index: int, sectors: list[dict], stop_text: str = "", done_text: str = "") -> dict:
    return {"light": light, "light_text": "", "sectors": sectors, "phase_title": TITLES[index],
            "phase_index": index, "total_phases": len(TITLES), "round": 4, "phase_started_at": now - 1800,
            "role": "player", "role_line": "player · codex", "doing": "гоняет тесты", "gate_started_at": None,
            "last_event_at": now - 20, "verdict": {}, "stop_text": stop_text, "done_text": done_text,
            "debt_line": "", "agent": {}, "messages_waiting": 0, "resume": None, "log": _log(now),
            "built_at": now, "quiet_after_s": 600}


def states(now: float) -> dict[str, dict]:
    debts = [{"phase": 5, "reason": "fuse", "findings": ["F3: гонка в кэше → прогнать дважды"]}]
    base = {"machine_id": MACHINE, "model": "codex", "models": ["codex", "player sonnet"], "role": "player",
            "round": 4, "total_phases": len(TITLES), "phone_text_answers": True, "decisions": [],
            "limits": {"codex": "токены 174663", "claude": "токены 389104"}, "agent": {}}
    live = {**base, "run_id": "20261112-101500000000-mock-live", "status": "waiting_human",
            "phase": TITLES[6], "phase_index": 6, "updated_at": _iso(now), "debts": debts,
            "question": {"id": "7-1", "text": "Фаза 7 застряла: 6 раундов NO_SHIP. Что делать?",
                         "actions": ["more", "skip", "model", "stop"]},
            "view": _view(now, light="yellow", index=6, sectors=_sectors(6, {5}))}
    done = {**base, "run_id": "20261111-090000000000-mock-done", "status": "done", "phase": TITLES[-1],
            "phase_index": 22, "updated_at": _iso(now - 86400), "debts": debts, "question": None,
            "view": _view(now - 86400, light="ready", index=22, sectors=_sectors(23, {5}, True),
                          done_text="⚠ сдано с долгом 1")}
    stopped = {**base, "run_id": "20261110-090000000000-mock-stopped", "status": "stopped", "phase": TITLES[18],
               "phase_index": 18, "updated_at": _iso(now - 2 * 86400), "debts": [], "question": None,
               "view": _view(now - 2 * 86400, light="grey", index=18, sectors=_sectors(18, set()),
                             stop_text="user_stop")}
    return {s["run_id"]: s for s in (live, done, stopped)}


class Handler(SimpleHTTPRequestHandler):
    offline = False
    jobs_cards: list[dict] = []
    jobs_decisions: list[dict] = []
    jobs_decision_counter = 0
    jobs_settings = {"mode": "push", "push_cap": 4, "laptop_seen": 0.0}
    jobs_laptop_quiet = False

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(PUBLIC), **k)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/__mock/offline":
            Handler.offline = q.get("on", ["1"])[0] == "1"
            return self._json({"offline": Handler.offline})
        if u.path == "/__mock/jobs":
            Handler.jobs_laptop_quiet = q.get("laptop_quiet", ["0"])[0] == "1"
            return self._json({"ok": True})
        if u.path.endswith((".html", ".js")) or u.path == "/":
            return super().do_GET()
        if Handler.offline:
            return self._json({"error": "mock offline"}, 503)
        if u.path == "/jobs/cards":
            laptop_seen = 0.0 if Handler.jobs_laptop_quiet else time.time() * 1000
            return self._json({"cards": Handler.jobs_cards, "mode": Handler.jobs_settings["mode"],
                                "push_cap": Handler.jobs_settings["push_cap"], "laptop_seen": laptop_seen,
                                "now": time.time() * 1000})
        if u.path == "/jobs/decisions":
            return self._json({"decisions": Handler.jobs_decisions})
        st = states(time.time())
        if u.path == "/runs":
            return self._json({"runs": [{"run_id": k, "machine_id": MACHINE, "status": v["status"],
                                         "updated_at": v["updated_at"]} for k, v in st.items()]})
        if u.path == "/state":
            return self._json({"state": st.get(q.get("run_id", [""])[0]), "marks": {}})
        if u.path == "/manifest.webmanifest":
            return self._json({"name": "теро-пульт", "short_name": "теро", "display": "standalone", "start_url": "/"})
        if u.path in ("/answers", "/commands/last", "/push/vapid"):
            return self._json({})
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n).decode() if n else ""
        if Handler.offline:
            return self._json({"error": "mock offline"}, 503)
        u = urlparse(self.path)
        if u.path == "/jobs/decide":
            payload = json.loads(raw) if raw else {}
            card_id = payload.get("card_id")
            version = payload.get("version")
            action = payload.get("action")
            card = next((c for c in Handler.jobs_cards if c["id"] == card_id), None)
            if card is None:
                return self._json({"error": "unknown card"}, 404)
            if card.get("version") != version:
                return self._json({"error": "stale"}, 409)
            is_engine = card.get("kind") == "job" and action == "engine"
            prior = next((d for d in Handler.jobs_decisions if d["card_id"] == card_id
                          and d["version"] == version
                          and not (card.get("kind") == "job" and d["action"] == "engine")), None)
            if prior is not None:
                if prior["action"] == action:
                    return self._json({"ok": True})
                return self._json({"error": "decided", "action": prior["action"]}, 409)
            if is_engine:
                Handler.jobs_decisions[:] = [d for d in Handler.jobs_decisions
                                             if d["card_id"] != card_id or d["action"] != "engine"]
            Handler.jobs_decision_counter += 1
            Handler.jobs_decisions.append({"id": str(Handler.jobs_decision_counter), "card_id": card_id,
                                            "version": version, "action": action, "data": payload.get("data") or {}})
            return self._json({"ok": True})
        if u.path == "/jobs/cards":   # как воркер: упсёрт по id, решения старой версии гибнут, drop снимает
            payload = json.loads(raw) if raw else {}
            for card in payload.get("cards") or []:
                Handler.jobs_decisions[:] = [d for d in Handler.jobs_decisions
                                             if d["card_id"] != card["id"] or d["version"] == card["version"]]
                Handler.jobs_cards[:] = [c for c in Handler.jobs_cards if c["id"] != card["id"]] + [card]
            drop = set(payload.get("drop") or [])
            Handler.jobs_cards[:] = [c for c in Handler.jobs_cards if c["id"] not in drop]
            return self._json({"ok": True})
        if u.path == "/jobs/decisions/ack":
            ids = set((json.loads(raw) if raw else {}).get("ids") or [])
            Handler.jobs_decisions[:] = [d for d in Handler.jobs_decisions if d["id"] not in ids]
            return self._json({"ok": True})
        if u.path == "/jobs/settings":
            payload = json.loads(raw) if raw else {}
            if "mode" in payload:
                Handler.jobs_settings["mode"] = payload["mode"]
            if "push_cap" in payload:
                Handler.jobs_settings["push_cap"] = payload["push_cap"]
            return self._json({"ok": True})
        if '"skip"' in raw:
            return self._json({"error": "no open question"}, 422)
        return self._json({"ok": True})

    def end_headers(self):
        self.send_header("cache-control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    port = ap.parse_args().port
    print(f"мок релея: http://127.0.0.1:{port}/  (в консоли: localStorage.tero_token='mock'; телефонный вид — сузь окно до 390 px)")
    serve(port).serve_forever()
