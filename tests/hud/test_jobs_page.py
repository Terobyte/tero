"""Вёрстка и кнопки /jobs.html в настоящем браузере (Chrome через playwright).

Данные — мок relay/dev/mock.py (/jobs/* в памяти). Контракт шага 4: пропуск
(skip) этих Playwright-тестов — провал, а не допустимый исход, так что
отсутствие playwright/Chrome здесь — ошибка теста, не skip.
"""
from __future__ import annotations

import sys
import threading
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "relay" / "dev"))

import playwright.sync_api as sync_api  # noqa: E402
import mock  # noqa: E402


@pytest.fixture(scope="module")
def base_url():
    server = mock.serve(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    mock.Handler.offline = False
    mock.Handler.jobs_cards = []
    mock.Handler.jobs_decisions = []
    mock.Handler.jobs_settings = {"mode": "push", "push_cap": 4, "laptop_seen": 0}
    mock.Handler.jobs_laptop_quiet = False


@pytest.fixture()
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome")
        except Exception as exc:  # нет установленного Chrome — контракт требует провал, не skip
            pytest.fail(f"chrome недоступен: {exc}")
        yield b
        b.close()


@pytest.fixture(autouse=True)
def _reset_jobs_state():
    mock.Handler.offline = False
    mock.Handler.jobs_cards = []
    mock.Handler.jobs_decisions = []
    mock.Handler.jobs_settings = {"mode": "push", "push_cap": 4, "laptop_seen": mock.time.time() * 1000}
    mock.Handler.jobs_laptop_quiet = False
    yield
    mock.Handler.offline = False
    mock.Handler.jobs_laptop_quiet = False


@pytest.fixture()
def page(browser, base_url):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    ctx.add_init_script("localStorage.setItem('tero_token', 'mock')")
    pg = ctx.new_page()
    yield pg
    ctx.close()


ASK_CARD = {
    "id": "ask:a", "kind": "ask", "version": "sha1", "rank": 1,
    "payload": {"title": "QA инженер", "company": "Acme", "items": [
        {"label": "Почему вы?", "kind": "text", "required": True, "draft": ""},
        {"label": "Готовы к офферу?", "kind": "yesno", "required": True},
        {"label": "Опыт", "kind": "choice", "options": ["1 год", "3 года"], "required": False},
    ]},
}


def test_ask_card_sends_all_answers_at_once(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    send = page.locator('[data-card-id="ask:a"] [data-action="answers"]')
    assert send.is_disabled()
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'потому что круто')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    assert not send.is_disabled()
    posts = []
    page.on("request", lambda r: posts.append(r.post_data) if r.method == "POST" else None)
    send.click()
    page.wait_for_timeout(300)
    decide_posts = [p for p in posts if p and '"action":"answers"' in p]
    assert len(decide_posts) == 1
    import json
    data = json.loads(decide_posts[0])["data"]
    assert len(data) == 3


def test_laptop_quiet_banner(page, base_url):
    urllib.request.urlopen(base_url + "/__mock/jobs?laptop_quiet=1").read()
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector("text=ноут молчит")


def test_cards_fit_phone_width(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    over = page.evaluate("""() => [...document.querySelectorAll('.card *')]
        .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().right > innerWidth)
        .map(e => e.className)""")
    assert over == [], f"за правым краем экрана: {over}"


def test_empty_and_offline_states(page, base_url):
    mock.Handler.jobs_cards = []
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector("text=новых вакансий нет")
    urllib.request.urlopen(base_url + "/__mock/offline?on=1").read()
    try:
        page.wait_for_selector("text=нет связи с релеем", timeout=20000)
    finally:
        urllib.request.urlopen(base_url + "/__mock/offline?on=0").read()


def test_mode_and_cap_are_sent_and_shown(page, base_url):
    mock.Handler.jobs_cards = []
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector("#settings")
    posts = []
    page.on("request", lambda r: posts.append(r.post_data) if r.method == "POST" else None)
    page.click('#settings button[data-mode="quiet"]')
    page.wait_for_timeout(300)
    import json
    settings_posts = [json.loads(p) for p in posts if p and '"mode"' in p]
    assert settings_posts[-1] == {"mode": "quiet"}
    page.wait_for_selector('#settings button[data-mode="quiet"].on')
    page.fill("#push-cap", "6")
    page.dispatch_event("#push-cap", "change")
    page.wait_for_timeout(300)
    cap_posts = [json.loads(p) for p in posts if p and '"push_cap"' in p]
    assert cap_posts[-1] == {"push_cap": 6}


def test_status_card_hidden_while_ask_pending(page, base_url):
    status_card = {"id": "status:a", "kind": "status", "version": "1", "rank": 1,
                   "payload": {"title": "QA", "company": "Acme", "url": "", "stage": "в работе"}}
    mock.Handler.jobs_cards = [status_card, ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    assert page.locator('[data-card-id="status:a"]').count() == 0
    mock.Handler.jobs_cards = [status_card]
    page.reload()
    page.wait_for_selector('[data-card-id="status:a"]')


def test_choice_label_with_delimiter_round_trips(page, base_url):
    card = {"id": "ask:b", "kind": "ask", "version": "sha1", "rank": 1,
            "payload": {"title": "QA", "company": "Acme", "items": [
                {"label": "A::B", "kind": "choice", "options": ["x::y"], "required": True},
            ]}}
    mock.Handler.jobs_cards = [card]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:b"]')
    send = page.locator('[data-card-id="ask:b"] [data-action="answers"]')
    page.click('[data-card-id="ask:b"] button[data-label="A::B"][data-option="x::y"]')
    assert not send.is_disabled()
    posts = []
    page.on("request", lambda r: posts.append(r.post_data) if r.method == "POST" else None)
    send.click()
    page.wait_for_timeout(300)
    import json
    decide_posts = [p for p in posts if p and '"action":"answers"' in p]
    assert json.loads(decide_posts[0])["data"] == {"A::B": "x::y"}


def test_ask_card_locks_textarea_while_sending(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'потому что круто')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')

    held = {}
    page.route("**/jobs/decide", lambda route: held.setdefault("route", route))
    page.click('[data-card-id="ask:a"] [data-action="answers"]')
    page.wait_for_timeout(50)
    assert page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').is_disabled()
    held["route"].fulfill(status=200, body='{"ok": true}')
    page.wait_for_timeout(100)
    assert not page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').is_disabled()


def test_decide_pending_survives_visibilitychange(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'потому что круто')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')

    held = {}
    page.route("**/jobs/decide", lambda route: held.setdefault("route", route))
    page.click('[data-card-id="ask:a"] [data-action="answers"]')
    page.wait_for_timeout(50)
    choice = page.locator('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    assert choice.is_disabled()

    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(300)
    assert page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').input_value() == 'потому что круто'
    assert choice.is_disabled()

    held["route"].fulfill(status=200, body='{"ok": true}')
    page.wait_for_timeout(150)
    assert not choice.is_disabled()


def test_poll_preserves_unsent_answers(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'черновик ответа')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    send = page.locator('[data-card-id="ask:a"] [data-action="answers"]')
    assert not send.is_disabled()

    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(300)
    assert page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').input_value() == 'черновик ответа'
    assert not send.is_disabled()


def test_failed_stop_keeps_answers_disabled_when_required_blank(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    send = page.locator('[data-card-id="ask:a"] [data-action="answers"]')
    assert send.is_disabled()
    page.route("**/jobs/decide", lambda route: route.fulfill(status=503, body="{}"))
    page.click('[data-card-id="ask:a"] [data-action="stop"]')
    page.wait_for_timeout(300)
    assert send.is_disabled()


def test_offline_disables_choice_buttons_and_restores(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    choice = page.locator('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    assert not choice.is_disabled()
    urllib.request.urlopen(base_url + "/__mock/offline?on=1").read()
    try:
        page.wait_for_selector("text=нет связи с релеем", timeout=20000)
        assert choice.is_disabled()
    finally:
        urllib.request.urlopen(base_url + "/__mock/offline?on=0").read()
    page.wait_for_selector("text=нет связи с релеем", state="detached", timeout=20000)
    assert not choice.is_disabled()


def test_ask_textarea_font_matches_page(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    fonts = page.evaluate("""() => {
        const ta = document.querySelector('[data-card-id="ask:a"] textarea[name="Почему вы?"]');
        return [getComputedStyle(ta).fontFamily, getComputedStyle(document.body).fontFamily];
    }""")
    assert fonts[0] == fonts[1]


def test_ask_textarea_font_size_16px(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    size = page.evaluate("""() => getComputedStyle(document.querySelector('[data-card-id="ask:a"] textarea')).fontSize""")
    assert size == "16px"


def test_browser_act_stays_disabled_after_decide_error(page, base_url):
    job_card = {"id": "job:a", "kind": "job", "version": "v1", "rank": 1, "hot": True,
                "payload": {"title": "QA", "company": "Acme", "location": "", "salary": "", "why": "",
                            "path": "qa", "url": "", "engine": "chrome"}}
    mock.Handler.jobs_cards = [job_card]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="job:a"]')
    page.route("**/jobs/decide", lambda route: route.fulfill(status=503, body="{}"))
    page.click('[data-card-id="job:a"] button[data-action="yes"]')
    page.wait_for_timeout(300)
    assert page.locator('[data-card-id="job:a"] button[data-engine="browser-act"]').is_disabled()


def test_status_stage_updates_without_version_change(page, base_url):
    status_card = {"id": "status:b", "kind": "status", "version": "1", "rank": 1,
                   "payload": {"title": "QA", "company": "Acme", "url": "", "stage": "в работе"}}
    mock.Handler.jobs_cards = [status_card, ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="status:b"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'черновик ответа')
    status_card["payload"]["stage"] = "отправлено"
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function("document.querySelector('[data-card-id=\"status:b\"] .stage')?.textContent === 'отправлено'", timeout=10000)
    assert page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').input_value() == 'черновик ответа'


def test_choice_selection_is_marked(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    yes = page.locator('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    no = page.locator('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="No"]')
    assert yes.get_attribute("aria-pressed") == "true"
    assert "on" in (yes.get_attribute("class") or "")
    assert no.get_attribute("aria-pressed") == "false"
    assert "on" not in (no.get_attribute("class") or "")


def test_pending_post_survives_offline_lock(page, base_url):
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'потому что круто')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    held = {}
    page.route("**/jobs/decide", lambda route: held.setdefault("route", route))
    page.click('[data-card-id="ask:a"] [data-action="answers"]')
    page.wait_for_timeout(100)
    urllib.request.urlopen(base_url + "/__mock/offline?on=1").read()
    try:
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(300)
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_selector("text=нет связи с релеем", timeout=20000)
        held["route"].fulfill(status=503, body="{}")
        page.wait_for_timeout(400)
        assert page.locator('[data-card-id="ask:a"] [data-action="answers"]').is_disabled()
        assert page.locator('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]').is_disabled()
    finally:
        urllib.request.urlopen(base_url + "/__mock/offline?on=0").read()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector("text=нет связи с релеем", state="detached", timeout=20000)


def test_cards_use_response_now_even_when_zero(page, base_url):
    page.route("**/jobs/cards",
               lambda route: route.fulfill(status=200, content_type="application/json",
                                           body='{"cards": [], "mode": "push", "push_cap": 4, "laptop_seen": 0, "now": 0}'))
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector("text=новых вакансий нет")
    assert page.locator("#laptop-banner").is_hidden()
    assert "09:00" in page.locator("#empty").inner_text()


def test_ask_payload_updates_while_answers_button_disabled(page, base_url):
    # F14: у ask кнопка «отправить ответы» штатно disabled, пока пусто обязательное
    # поле. Это не незавершённый POST, поэтому обновлённый payload с теми же id и
    # version обязан дорисоваться, а введённый ответ — сохраниться.
    card = {"id": "ask:c", "kind": "ask", "version": "sha1", "rank": 1,
            "payload": {"title": "QA инженер", "company": "Acme", "items": [
                {"label": "Почему вы?", "kind": "text", "required": True, "draft": ""},
                {"label": "Готовы к офферу?", "kind": "yesno", "required": True},
            ]}}
    mock.Handler.jobs_cards = [card]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:c"]')
    page.fill('[data-card-id="ask:c"] textarea[name="Почему вы?"]', 'черновик ответа')
    send = page.locator('[data-card-id="ask:c"] [data-action="answers"]')
    assert send.is_disabled()
    card["payload"]["title"] = "QA инженер (ночная смена)"
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function(
        "document.querySelector('[data-card-id=\"ask:c\"] h3')?.textContent === 'QA инженер (ночная смена)'",
        timeout=10000)
    assert page.locator('[data-card-id="ask:c"] textarea[name="Почему вы?"]').input_value() == 'черновик ответа'
    assert send.is_disabled()


def test_offline_unlock_keeps_pending_card_locked(page, base_url):
    # Находка судьи: снятие offline lock включало кнопки карточки, у которой
    # POST /jobs/decide ещё не ответил. Второе решение могло опередить первое.
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'потому что круто')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')

    held = {}
    page.route("**/jobs/decide", lambda route: held.setdefault("route", route))
    page.click('[data-card-id="ask:a"] [data-action="answers"]')
    page.wait_for_timeout(50)
    choice = page.locator('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    send = page.locator('[data-card-id="ask:a"] [data-action="answers"]')
    assert choice.is_disabled()

    urllib.request.urlopen(base_url + "/__mock/offline?on=1").read()
    page.wait_for_selector("text=нет связи с релеем", timeout=20000)
    urllib.request.urlopen(base_url + "/__mock/offline?on=0").read()
    page.wait_for_selector("text=нет связи с релеем", state="hidden", timeout=20000)
    page.wait_for_timeout(200)
    assert choice.is_disabled(), "снятие offline lock не должно включать карточку с незавершённым POST"
    assert send.is_disabled(), "кнопка ответов не должна включаться, пока висит POST"

    held["route"].fulfill(status=200, body='{"ok": true}')
    page.wait_for_timeout(150)
    assert not choice.is_disabled()


def test_offline_unlock_keeps_pending_stop_card_locked(page, base_url):
    # F1 (стоп): удерживаем POST «стоп» при заполненных обязательных полях,
    # два offline-опроса, восстановление GET — кнопка ответов остаётся disabled.
    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'потому что круто')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    send = page.locator('[data-card-id="ask:a"] [data-action="answers"]')
    assert not send.is_disabled()

    held = {}
    page.route("**/jobs/decide", lambda route: held.setdefault("route", route))
    page.click('[data-card-id="ask:a"] [data-action="stop"]')
    page.wait_for_timeout(100)
    assert send.is_disabled()
    urllib.request.urlopen(base_url + "/__mock/offline?on=1").read()
    try:
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(300)
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_selector("text=нет связи с релеем", timeout=20000)
        urllib.request.urlopen(base_url + "/__mock/offline?on=0").read()
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_selector("text=нет связи с релеем", state="detached", timeout=20000)
        page.wait_for_timeout(200)
        assert send.is_disabled(), "снятие offline lock включило ответы при висящем POST стоп"
    finally:
        try:
            urllib.request.urlopen(base_url + "/__mock/offline?on=0").read()
        except Exception:
            pass
    held["route"].fulfill(status=200, body='{"ok": true}')
    page.wait_for_timeout(200)


def test_proto_label_value_survives_poll_and_send(page, base_url):
    # F2: метка __proto__ не должна теряться при опросе и отправке.
    card = {"id": "ask:proto", "kind": "ask", "version": "sha1", "rank": 1,
            "payload": {"title": "QA", "company": "Acme", "items": [
                {"label": "__proto__", "kind": "text", "required": True, "draft": ""},
            ]}}
    mock.Handler.jobs_cards = [card]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:proto"]')
    page.fill('[data-card-id="ask:proto"] textarea', 'ответ')
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(300)
    assert page.locator('[data-card-id="ask:proto"] textarea').input_value() == 'ответ'
    posts = []
    page.on("request", lambda r: posts.append(r.post_data) if r.method == "POST" else None)
    page.click('[data-card-id="ask:proto"] [data-action="answers"]')
    page.wait_for_timeout(300)
    import json
    decide_posts = [p for p in posts if p and '"action":"answers"' in p]
    assert decide_posts, "POST /jobs/decide не ушёл"
    raw = json.loads(decide_posts[0])["data"]
    assert raw.get("__proto__") == 'ответ', f"значение __proto__ потеряно: {raw!r}"


def test_pending_post_blocks_new_version_card(page, base_url):
    # F4: POST ask:a v1 висит, опрос отдаёт ту же карточку версии v2 —
    # кнопки v2 отключены, пока не ответит исходный POST.
    import copy

    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'потому что круто')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')

    held = {}
    page.route("**/jobs/decide", lambda route: held.setdefault("route", route))
    page.click('[data-card-id="ask:a"] [data-action="answers"]')
    page.wait_for_timeout(50)
    assert page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').is_disabled()

    new = copy.deepcopy(ASK_CARD)
    new["version"] = "sha2"
    mock.Handler.jobs_cards = [new]
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    # F4: свежая версия не применяется раньше ответа исходного POST — в DOM
    # остаётся заблокированный узел v1, второе решение уйти не может.
    page.wait_for_timeout(300)
    node = page.locator('[data-card-id="ask:a"]')
    assert node.get_attribute("data-version") == "sha1"
    assert page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').is_disabled()
    choice = page.locator('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')
    assert choice.is_disabled(), "v2 включилась раньше ответа на POST v1"

    held["route"].fulfill(status=200, body='{"ok": true}')
    # Ответ v1 применён: опрос подтянул v2, кнопки снова живы.
    page.wait_for_function(
        "document.querySelector('[data-card-id=\"ask:a\"]')?.dataset.version === 'sha2'",
        timeout=10000)
    assert not page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').is_disabled()


def test_pending_post_blocks_card_after_empty_poll(page, base_url):
    # F5: POST ask:a v1 висит, опрос вернул пустой список, затем ask:a v2 —
    # действия отключены до ответа исходного POST.
    import copy

    mock.Handler.jobs_cards = [ASK_CARD]
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector('[data-card-id="ask:a"]')
    page.fill('[data-card-id="ask:a"] textarea[name="Почему вы?"]', 'потому что круто')
    page.click('[data-card-id="ask:a"] button[data-label="Готовы к офферу?"][data-option="Yes"]')

    held = {}
    page.route("**/jobs/decide", lambda route: held.setdefault("route", route))
    page.click('[data-card-id="ask:a"] [data-action="answers"]')
    page.wait_for_timeout(50)
    assert page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').is_disabled()

    mock.Handler.jobs_cards = []
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector('[data-card-id="ask:a"]', state="detached", timeout=10000)

    new = copy.deepcopy(ASK_CARD)
    new["version"] = "sha2"
    mock.Handler.jobs_cards = [new]
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector('[data-card-id="ask:a"]', timeout=10000)
    assert page.locator('[data-card-id="ask:a"] textarea[name="Почему вы?"]').is_disabled()
    assert page.locator('[data-card-id="ask:a"] [data-action="answers"]').is_disabled()

    held["route"].fulfill(status=200, body='{"ok": true}')
    page.wait_for_timeout(300)


def test_fractional_push_cap_is_not_sent(page, base_url):
    # F6: ручной ввод 2.5 не уходит на релей, показана ошибка, значение возвращено.
    mock.Handler.jobs_cards = []
    page.goto(base_url + "/jobs.html")
    page.wait_for_selector("#push-cap")
    assert page.locator("#push-cap").input_value() == "4"
    posts = []
    page.on("request", lambda r: posts.append(r.post_data) if r.method == "POST" else None)
    page.fill("#push-cap", "2.5")
    page.dispatch_event("#push-cap", "change")
    page.wait_for_timeout(300)
    import json
    cap_posts = [p for p in posts if p and '"push_cap"' in p]
    assert cap_posts == [], f"дробный потолок ушёл на релей: {cap_posts}"
    assert "целое" in page.locator("#settings-msg").inner_text()
    assert page.locator("#push-cap").input_value() == "4"
