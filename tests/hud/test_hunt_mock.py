"""Hunt 2026-11-13: мок релея должен вести себя как воркер на /jobs/*."""
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "relay" / "dev"))
import mock  # noqa: E402


@pytest.fixture()
def base():
    server = mock.serve(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mock.Handler.jobs_cards = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    mock.Handler.jobs_cards = []


def _req(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read())


def test_B11_post_jobs_cards_is_stored_like_the_worker(base):
    # Мок хранит карточки в памяти и отдаёт их на GET /jobs/cards, значит моделирует
    # /jobs/*; POST /jobs/cards воркера (запись карточек ноутбуком) не должен быть фейковым ok.
    card = {"id": "job:a", "kind": "job", "version": "v1", "hot": False, "rank": 1, "title": "a"}
    _req(f"{base}/jobs/cards", "POST", {"cards": [card], "drop": []})
    got = _req(f"{base}/jobs/cards")
    assert [c["id"] for c in got["cards"]] == ["job:a"]
