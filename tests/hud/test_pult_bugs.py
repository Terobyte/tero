"""Багхант 2026-11-12 (bugs.md): поведение pult.js в настоящем Chrome. Красные, пока баги не починены.

Страница — relay/public, данные — relay/dev/mock.py; сбои и медленные ответы — page.route().
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "relay" / "dev"))

sync_api = pytest.importorskip("playwright.sync_api")
import mock  # noqa: E402


@pytest.fixture(scope="module")
def base_url():
    server = mock.serve(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    mock.Handler.offline = False


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome")
        except Exception as exc:  # нет установленного Chrome
            pytest.skip(f"chrome недоступен: {exc}")
        yield b
        b.close()


def _new_page(browser, *, token=True, permissions=None):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, permissions=permissions or [])
    if token:
        ctx.add_init_script("localStorage.setItem('tero_token', 'mock')")
    ctx.add_init_script(
        "window.__rej = []; addEventListener('unhandledrejection', e => window.__rej.push(String(e.reason)));")
    return ctx, ctx.new_page()


def _track_state(page):
    """Счётчик ответов /state: по нему видно, когда опрос только что перерисовал вопрос."""
    box = {"n": 0}
    page.on("response", lambda r: box.__setitem__("n", box["n"] + 1)
            if "/state" in r.url and r.request.method == "GET" else None)
    return box


def _wait_state(page, box, past, timeout=9.0):
    # события playwright (sync) доставляются только внутри вызовов page.*, не в time.sleep
    end = time.time() + timeout
    while box["n"] <= past and time.time() < end:
        page.wait_for_timeout(50)
    assert box["n"] > past, "опрос /state не пришёл"
    page.wait_for_timeout(200)  # дать странице дорисовать


def _patch_state(page, patch):
    def handler(route):
        resp = route.fetch()
        data = resp.json()
        patch(data)
        route.fulfill(response=resp, body=json.dumps(data), content_type="application/json")
    page.route("**/state?*", handler)


def test_B01_draft_does_not_move_to_other_run_with_same_question_id(browser, base_url):
    ctx, page = _new_page(browser)
    runs = {"order": ["A", "B"]}
    tmpl = mock.states(time.time())["20261112-101500000000-mock-live"]

    def runs_h(route):
        lst = [{"run_id": f"20261112-1015-run{k}", "machine_id": mock.MACHINE, "status": "waiting_human",
                "updated_at": tmpl["updated_at"]} for k in runs["order"]]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"runs": lst}))

    asked = []

    def state_h(route):
        rid = route.request.url.split("run_id=")[1].split("&")[0]
        asked.append(rid)
        st = {**tmpl, "run_id": rid}
        st["view"] = {**st["view"]}
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"state": st, "marks": {}}))

    page.route("**/runs?*", runs_h)
    page.route("**/state?*", state_h)
    page.goto(base_url + "/")
    page.wait_for_selector("#question textarea[name=text]", timeout=10000)
    page.fill("#question textarea[name=text]", "секретный черновик для прогона A")
    page.focus("#question textarea[name=text]")
    runs["order"] = ["B", "A"]  # теперь pickRun берёт B (тот же question.id 7-1)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")  # runsAt=0 + tick
    page.wait_for_timeout(1500)
    shown = page.locator("#question textarea[name=text]").input_value()
    ctx.close()
    assert asked[0].endswith("runA") and asked[-1].endswith("runB"), f"предпосылка: пульт переключился A→B, запросы: {asked}"
    assert shown == "", (f"черновик прогона A уехал в форму прогона B: ожидали пустое поле "
                         f"(черновик привязан к run_id+machine_id+qid), получили {shown!r}")


def test_B03_answer_failure_does_not_leave_new_question_buttons_disabled(browser, base_url):
    ctx, page = _new_page(browser)
    held = []
    page.route("**/answer", lambda r: held.append(r))
    box = _track_state(page)
    page.goto(base_url + "/")
    page.wait_for_selector("#question button[data-action]", timeout=10000)
    _wait_state(page, box, box["n"])  # ждём первый опрос, чтобы следующий был через ~4 с
    page.locator('#question button[data-action="more"]').click()
    end = time.time() + 8
    while not held and time.time() < end:
        page.wait_for_timeout(50)
    assert held, "POST /answer не ушёл через 5 с"
    _wait_state(page, box, box["n"])  # опрос перерисовал вопрос, пока POST висит
    assert page.locator('#question button[data-action="more"]').is_disabled(), "предпосылка: во время POST кнопки заняты"
    held[0].fulfill(status=500, content_type="application/json", body=json.dumps({"error": "boom"}))
    page.wait_for_timeout(300)
    disabled = page.locator('#question button[data-action="more"]').is_disabled()
    ctx.close()
    assert not disabled, "после отказа POST /answer кнопки нового вопроса ждут следующего опроса: ожидали сразу enabled (applyLocks по живому DOM), получили disabled"


def test_B04_double_tap_send_message_posts_once(browser, base_url):
    ctx, page = _new_page(browser)
    posts = []

    def h(route):
        if "message" in (route.request.post_data or ""):
            posts.append(route)
        else:
            route.continue_()

    page.route("**/answer", h)
    page.goto(base_url + "/")
    page.wait_for_selector("#msg-btn", timeout=10000)
    page.fill("#message input[name=message]", "привет хозяин")
    page.evaluate("() => { const b = document.getElementById('msg-btn'); b.click(); b.click(); }")
    page.wait_for_timeout(700)
    n = len(posts)
    for r in posts:
        r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
    ctx.close()
    assert n == 1, f"двойной тап «отправить»: ожидали 1 POST kind=message, получили {n}"


def test_B05_pending_countdown_ticks(browser, base_url):
    ctx, page = _new_page(browser)
    box = _track_state(page)
    page.goto(base_url + "/")
    page.wait_for_selector("#question button[data-action]", timeout=10000)
    _wait_state(page, box, box["n"])  # следующий опрос (перерисовка) — не раньше чем через ~4 с
    page.locator('#question button[data-action="stop"]').click()
    page.wait_for_selector("#pending")
    first = page.locator("#pending").inner_text()
    page.wait_for_timeout(2300)
    later = page.locator("#pending").inner_text()
    ctx.close()
    assert first != later, f"отсчёт застыл: ожидали 5…4…3, через 2.3 с текст всё тот же: {later!r}"


def test_B07_network_errors_in_handlers_are_reported_not_swallowed(browser, base_url):
    got = {}
    expected = {"pair": "ошибка видна", "resume": "ошибка видна", "wake": "ошибка видна", "message": "ошибка видна"}

    # привязка
    ctx, page = _new_page(browser, token=False)
    page.route("**/pair/exchange", lambda r: r.abort())
    page.goto(base_url + "/")
    page.wait_for_selector("#pair-btn", timeout=10000)
    page.fill("#pair-code", "ABCDEFGH")
    page.click("#pair-btn")
    page.wait_for_timeout(500)
    msg = page.locator("#pair-msg").inner_text()
    got["pair"] = "ошибка видна" if msg and msg != "проверяю код…" else f"висит {msg!r}"
    ctx.close()

    def live_state(data):
        now = time.time()
        data["state"]["view"]["agent"] = {"on_duty": False}
        data["state"]["view"]["resume"] = {"mode": "ok", "why": ""}
        data["marks"] = {"machine_last_seen": now}

    ctx, page = _new_page(browser)
    _patch_state(page, live_state)
    page.route("**/commands", lambda r: r.abort() if r.request.method == "POST" else r.continue_())
    page.route("**/answer", lambda r: r.abort())
    page.goto(base_url + "/")
    page.wait_for_selector("#msg-btn", timeout=10000)
    for key, btn, err in (("resume", "#resume-btn", "#resume-error"), ("wake", "#wake-btn", "#wake-error")):
        if page.locator(btn).count() == 0:
            got[key] = f"кнопки {btn} нет (предпосылка теста не создана)"
            continue
        page.click(btn)
        page.wait_for_timeout(400)
        t = page.locator(err).inner_text()
        got[key] = "ошибка видна" if t else "молчит"
    page.fill("#message input[name=message]", "привет")
    page.click("#msg-btn")
    page.wait_for_timeout(400)
    got["message"] = "ошибка видна" if page.locator("#msg-error").inner_text() else "молчит"
    rej = page.evaluate("window.__rej")
    ctx.close()
    assert got == expected, (f"при обрыве сети ожидали видимый текст ошибки у каждой кнопки, получили {got}; "
                             f"unhandled rejections: {rej}")


def test_B08_vapid_failure_is_not_reported_as_missing_key(browser, base_url):
    ctx, page = _new_page(browser, permissions=["notifications"])
    page.route("**/push/vapid", lambda r: r.fulfill(status=503, content_type="application/json",
                                                     body='{"error":"down"}'))
    page.goto(base_url + "/")
    page.wait_for_selector("#push-btn", timeout=10000)
    page.click("#push-btn")
    page.wait_for_function("() => { const t = document.getElementById('push-msg'); "
                           "return t && t.textContent && t.textContent !== 'подписываю…'; }", timeout=10000)
    msg = page.locator("#push-msg").inner_text()
    ctx.close()
    assert "503" in msg and "не задан ключ" not in msg, (
        f"/push/vapid ответил 503: ожидали «релей ответил 503», получили {msg!r}")
