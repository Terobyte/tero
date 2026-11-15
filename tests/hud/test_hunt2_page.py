"""Hunt 2 (2026-11-13): B32, B21 (страница), B35 (мок)."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "relay" / "dev"))

from test_jobs_page import base_url, browser, page, _reset_jobs_state  # noqa: F401,E402
import mock  # noqa: E402


def test_b32_cleared_draft_must_not_come_back_after_poll(page, base_url):
    mock.Handler.jobs_cards = [{
        "id": "ask:d", "kind": "ask", "version": "v1", "rank": 1,
        "payload": {"title": "QA", "company": "Acme", "items": [
            {"label": "Why", "kind": "text", "required": False, "draft": "old draft"}]},
    }]
    page.goto(base_url + "/jobs.html")
    ta = page.locator('[data-card-id="ask:d"] textarea[name="Why"]')
    ta.wait_for()
    assert ta.input_value() == "old draft"
    ta.fill("")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(600)
    val = page.locator('[data-card-id="ask:d"] textarea[name="Why"]').input_value()
    assert val == "", f"expected '' got {val!r}"


def test_b21_page_keys_decision_data_by_item_key(page, base_url):
    mock.Handler.jobs_cards = [{
        "id": "ask:k", "kind": "ask", "version": "v1", "rank": 1,
        "payload": {"title": "QA", "company": "Acme", "items": [
            {"label": "Q one", "key": "q0", "kind": "text", "required": True, "draft": ""}]},
    }]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:k"]')
    page.fill('[data-card-id="ask:k"] textarea', "hello")
    posts = []
    page.on("request", lambda r: posts.append(r.post_data) if r.method == "POST" else None)
    page.click('[data-card-id="ask:k"] [data-action="answers"]')
    page.wait_for_timeout(400)
    sent = [json.loads(p) for p in posts if p and '"action":"answers"' in p]
    assert len(sent) == 1, posts
    assert sent[0]["data"] == {"q0": "hello"}, f"got {sent[0]['data']!r}"


# ---- мок ----
@pytest.fixture()
def mbase():
    server = mock.serve(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mock.Handler.jobs_cards = []
    mock.Handler.jobs_decisions = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    mock.Handler.jobs_cards = []
    mock.Handler.jobs_decisions = []


def _post(url, body):
    r = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                               headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url):
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def test_b35a_mock_engine_then_yes_is_allowed(mbase):
    mock.Handler.jobs_cards = [{"id": "job:a", "kind": "job", "version": "v1", "rank": 1}]
    assert _post(f"{mbase}/jobs/decide", {"card_id": "job:a", "version": "v1", "action": "engine",
                                          "data": {"engine": "chrome"}})[0] == 200
    status, body = _post(f"{mbase}/jobs/decide", {"card_id": "job:a", "version": "v1", "action": "yes"})
    assert status == 200, f"expected 200 like the worker, got {status} {body}"


def test_b35b_mock_decision_ids_stay_unique_after_ack(mbase):
    mock.Handler.jobs_cards = [{"id": f"job:{i}", "kind": "job", "version": "v1", "rank": 1} for i in "abc"]
    for i in "ab":
        assert _post(f"{mbase}/jobs/decide", {"card_id": f"job:{i}", "version": "v1", "action": "yes"})[0] == 200
    ids = [d["id"] for d in _get(f"{mbase}/jobs/decisions")["decisions"]]
    _post(f"{mbase}/jobs/decisions/ack", {"ids": [ids[0]]})
    assert _post(f"{mbase}/jobs/decide", {"card_id": "job:c", "version": "v1", "action": "yes"})[0] == 200
    got = [d["id"] for d in _get(f"{mbase}/jobs/decisions")["decisions"]]
    assert len(got) == len(set(got)) == 2, f"ids not unique: {got}"
