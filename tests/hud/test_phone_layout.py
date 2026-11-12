"""Вёрстка и кнопки телефонного пульта в настоящем браузере (Chrome через playwright).

Страница — relay/public (то, что реально отдаёт релей), данные — мок relay/dev/mock.py.
Ловит то, чего не видят node-тесты: точки фаз за краем экрана, план не открывается,
плашка «нет связи», отсчёт ответа с «отменить». Без playwright или Chrome — skip.
"""
from __future__ import annotations

import sys
import threading
import urllib.request
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


@pytest.fixture()
def page(browser, base_url):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    ctx.add_init_script("localStorage.setItem('tero_token', 'mock')")
    pg = ctx.new_page()
    pg.goto(base_url + "/")
    pg.wait_for_selector("#question button[data-action]", timeout=10000)
    yield pg
    ctx.close()


def test_header_fits_and_dots_open_the_plan(page):
    over = page.evaluate("""() => [...document.querySelectorAll('#top *')]
        .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().right > innerWidth + 1)
        .map(e => e.id || e.className)""")
    assert over == [], f"за правым краем экрана: {over}"
    outside = page.evaluate("""() => { const box = document.getElementById('phases').getBoundingClientRect();
        return [...document.querySelectorAll('#phases .dot')].filter(d => { const r = d.getBoundingClientRect();
          return r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1; }).length; }""")
    assert outside == 0, "точки фаз вылезают из своего блока"
    assert page.locator(".phase-count").inner_text() == "7/23"
    page.locator("#phases .dot").nth(3).click()
    assert "open" in page.locator("#plan").get_attribute("class")
    page.locator("#card").click(position={"x": 5, "y": 300})
    assert "open" not in (page.locator("#plan").get_attribute("class") or "")


def test_answer_countdown_cancel_sends_nothing(page):
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    page.locator('#question button[data-action="stop"]').click()
    page.wait_for_selector("#pending")
    assert page.locator('#question button[data-action="more"]').is_disabled()
    page.locator("#pending-cancel").click()
    page.wait_for_timeout(6500)
    assert posts == [], "отменённый ответ ушёл на релей"
    assert not page.locator('#question button[data-action="more"]').is_disabled()
    assert "отменено" in page.locator("#answer-msg").inner_text()


def test_answer_after_countdown_is_sent_once(page):
    posts = []
    page.on("request", lambda r: posts.append(r.post_data) if r.method == "POST" else None)
    page.locator('#question button[data-action="more"]').click()
    page.locator('#question button[data-action="more"]').click(force=True)
    page.wait_for_function("document.getElementById('answer-msg').textContent.includes('отправлено')", timeout=10000)
    page.wait_for_timeout(500)
    assert len([p for p in posts if '"more"' in (p or "")]) == 1


def test_offline_banner_after_two_failures_and_back(page, base_url):
    assert page.locator("#offline").is_hidden()
    urllib.request.urlopen(base_url + "/__mock/offline?on=1").read()
    try:
        page.wait_for_selector("#offline", state="visible", timeout=20000)
        assert "нет связи" in page.locator("#offline").inner_text()
        assert page.locator('#question button[data-action="more"]').is_disabled()
    finally:
        urllib.request.urlopen(base_url + "/__mock/offline?on=0").read()
    page.wait_for_selector("#offline", state="hidden", timeout=20000)
    assert not page.locator('#question button[data-action="more"]').is_disabled()


def test_answer_refusal_survives_polling(page):
    # мок отвечает 422 на skip; отказ не должен стереться следующим опросом (4 с)
    page.locator('#question button[data-action="skip"]').click()
    page.wait_for_function("document.getElementById('answer-msg').textContent.includes('закрыт')", timeout=10000)
    page.wait_for_timeout(9000)
    assert "закрыт" in page.locator("#answer-msg").inner_text()
