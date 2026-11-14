"""B10: ответ более старого /jobs/cards, пришедший последним, затирает новый рендер."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_jobs_page import base_url, browser, page, _reset_jobs_state  # noqa: F401


def _card(version):
    return {"id": "status:b10", "kind": "status", "version": version, "rank": 1,
            "payload": {"title": "QA", "company": "Acme", "stage": "stage-" + version}}


def _body(version):
    return json.dumps({"cards": [_card(version)], "mode": "push", "push_cap": 4,
                       "laptop_seen": 0, "now": 1})


def test_B10_older_cards_response_resolving_last_must_not_overwrite_newer(page, base_url):
    held = []
    page.route("**/jobs/cards", lambda route: held.append(route))

    page.goto(base_url + "/jobs.html")
    for _ in range(50):
        if len(held) >= 1:
            break
        page.wait_for_timeout(50)
    assert len(held) == 1, "первый /jobs/cards не ушёл"

    # второй load стартует, пока первый висит
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    for _ in range(50):
        if len(held) >= 2:
            break
        page.wait_for_timeout(50)
    assert len(held) == 2, "второй /jobs/cards не ушёл"

    # новый запрос отвечает первым с НОВОЙ версией
    held[1].fulfill(status=200, content_type="application/json", body=_body("v2-new"))
    page.wait_for_selector('[data-card-id="status:b10"][data-version="v2-new"]')

    # старый запрос отвечает последним со СТАРОЙ версией
    held[0].fulfill(status=200, content_type="application/json", body=_body("v1-old"))
    page.wait_for_timeout(500)

    shown = page.locator('[data-card-id="status:b10"]').get_attribute("data-version")
    assert shown == "v2-new", f"страница показывает устаревшую версию {shown!r}"
