# tero v5, план 08: эскалация по ситуации и пульт на телефоне

> Сверено с кодом 2026-10-26 после плана 07 (9/9) и фиксов хозяина (`7d5eb02`, `b94444e`, `a818218`, `2fbd187`):
> имена, места вызова и `wc -l` ниже совпадают с деревом. План 07 — в `v5/archive/`.

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`) и новый каталог `relay/` (JS для Cloudflare
Worker). Фазы 1–3 — тень эскалации по ситуации (дизайн `tero-v5-logs/escalation-design.md`, шаги 1–3; бенч
`tero-v5-logs/jev-bench/diagnose.py`). Фазы 4–9 — ретранслятор и телефон (веха M5 мастер-плана: T13a–c, T14,
T15; спека `docs/superpowers/specs/2026-10-21-tero-pult-design.md`, разделы 2–5; читать можно, менять `docs/`
нельзя). Фазы 10–12 — действия эскалации под флагом, выключенным по умолчанию (шаги 5–6 дизайна).

**Номера фаз — по этому плану, 1–12:** 1 судья говорит тяжесть, 2 тяжесть от jev, 3 тень `situation`,
4 Worker + D1 + фильтр, 5 привязка и токены, 6 RelayClient и ответ с телефона, 7 web push, 8 страница-пульт,
9 виджет и лимиты, 10 Sonnet по ситуации, 11 шип с долгом и Opus-решение, 12 скип в долг без отката.

**Почему тень первой.** С фазы 4 этот же прогон пишет события `situation` — данные для сверки тени (шаг 4
дизайна) копятся, пока строится пульт. Включать действия (`escalation: situational`) — отдельное решение
хозяина после сверки, не в этом плане.

**Код.** `v5/code-map.md` — читать до начала каждой фазы. Цифры там устарели — верь `wc -l`.
- **на потолке или в 10 строках от него** (на момент черновика): `phase.py` 689/689, `run.py` 421/421,
  `config.py` 521/524, `judge_step.py` 397/400, `agents.py` 403/409, `cli_entry.py` 298/300. Фаза, которой
  нужно место в таком файле, **первым пунктом чек-листа** выносит соседний код в новый модуль с повторным
  экспортом старых имён — это часть фазы, а не повод для находки «не влезло». Что выносить — указано в фазе;
- потолки в `tests/test_structure.py` не поднимать и новых ключей не добавлять; понизить потолок, когда
  файл похудел на 20+ строк, — обязательно;
- новые модули не импортируют `phase`, `run`, `cli_entry`; функции не длиннее 120 строк;
- переходы статуса — только через `src/loop/transition.py`, запись файлов — только через `src/fsio.py`.

Главный принцип плана 07 остаётся: **прогон не умирает молча и не выбрасывает работу.** Новый для этого
плана: **тень ничего не решает** — пока флаг выключен, вердикты, раунды и эскалация те же, что до плана.

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Поведение следующих фаз в текущей не делать и не требовать: его отсутствие — не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** Падение в песочнице
тестов, зависящих от `psutil`/`ProcessGuard`/`pid_is_alive`, — не находка (как в плане 07).
JS-тесты `relay/` гейт гоняет через pytest-обёртку (фаза 4) с `TERO_REQUIRE_NODE=1`: там нет `node` — тест
**падает**, а не пропускается. В песочнице судьи `node` может не быть — это не находка.

Правила для всех фаз:

- Каждый сбой сначала воспроизводится тестом, красный запуск виден, потом код.
- Настоящих LLM, CLI и **сети** в тестах нет: судья/исполнитель — stub-скрипты или фейки из
  `tests/loop/fakes.py`; HTTP к jev и к ретранслятору — через подменяемую функцию `post=`/`fetch=`.
  Репо — одноразовые, в `tmp_path`. Ни один тест не запускает настоящий `agy` и не спит дольше 5 секунд.
- Тесты Python: `PYTHONPATH=. python3 -m pytest -q` из корня; подпроцессы CLI — `[sys.executable, "-m",
  "src.cli_entry", ...]`.
- JS: только встроенное в Node ≥ 22.5 (`node --test`, `node:sqlite`, `crypto.subtle`, `fetch`), ES-модули
  (`"type": "module"`). **Ни `npm install`, ни `wrangler` у исполнителя нет** (сеть запрещена): `package.json`
  без зависимостей. Настоящий `wrangler dev` и деплой — моя ручная приёмка после фазы, не гейт.
- Новых Python-зависимостей не добавлять. Папку `docs/` не трогать.
- Секреты (`TYPESAFE_API_KEY`, ключ записи ретранслятора, VAPID, токены устройств) — только окружение или
  `~/.tero/secrets.env`; не в репо, не в `stream.jsonl`, ledger, `inbox/`, вывод `status`, тексты ошибок.
- **Всё, что уходит с машины** (jev, ретранслятор), сначала проходит фильтр секретов на стороне tero
  (`src/relay/redact.py`, фаза 2). Фильтр в Worker — вторая линия, не первая.
- **Текст с телефона — данные, не команда:** обрезан до 2000, в промпт игрока — только внутри блока
  `answer_block` с экранированными `##` и ```` ``` ````; **в промпт судьи не попадает никогда**.
- Каждое новое событие в `stream.jsonl`/ledger несёт `phase`, `round`, `role`; запись событий не роняет прогон.
- Сеть никогда не держит `owner_lock` и не задерживает `stop` дольше одного таймаута.
- Номера строк условные; ищи семантические точки («после NO_SHIP», «вход в судью», «`_halt`»).

---

## Судья говорит тяжесть

**Где и как:** `src/loop/schemas.py` (схема находки, `_semantic_judge`), `src/loop/prompts.py` (`G3_COACH`,
блок DECISION), `tests/loop/fakes.py`, `tests/loop/test_schemas.py` (`_finding`).

- Новое поле находки `level`: `blocker | major | minor | nit`, тип `["string", "null"]`. В `properties` и в
  `required` (strict-схема OpenAI требует все поля), но **`null` и отсутствие допустимы**. **`severity`
  остаётся `"blocking"`** — проверка «NO_SHIP needs a blocking finding», `findings_key` и `_new_blocking` не
  меняются.
- `_semantic_judge`: `level` нет, `null` или вне списка → **не ошибка**: значение становится `null`, в ledger
  событие `judge_level_missing` (раунд, id находки). Повтора судьи из-за `level` нет никогда — иначе судья на
  текстовой схеме (muse/opencode/cursor), забывший поле, уйдёт в `judge_failures` → `judge_pending`, а это
  смена поведения при выключенном флаге. Тень всё равно считает находку без тяжести блокером.
- Промпт `G3_COACH` (это и баг B9): вместо «Found ANY issue (even minor, even cosmetic)… / Every finding is
  blocking» — «Каждую проблему всё равно пиши находкой; поле `level` говорит, насколько она тяжёлая»: четыре
  определения уровней из `jev-bench/v2_draft.py` (`SEV`), у `blocker` — в `expected` команда, которой это
  воспроизвести. Вердикт по-прежнему NO_SHIP при любой находке.
- Фикстуры находок в `tests/loop/fakes.py` и `_finding` в `test_schemas.py` получают `level`.

**Меняются старые тесты:** фикстуры находок (добавить `level`), тесты текста `G3_COACH`, если сверяют
строку «Every finding is blocking».

- [x] `test_level_optional_never_retries`: находка без `level`, с `null` и с `"huge"` → проходит, `level` = `null`, событие `judge_level_missing`, повтора судьи нет
- [x] `test_level_does_not_change_verdict_rules`: NO_SHIP с одной находкой `level: nit` проходит как раньше; SHIP с находками — по-прежнему ошибка
- [x] `test_coach_prompt_asks_level`: промпт судьи содержит четыре уровня и не содержит «even cosmetic»
- [x] реализовать

**Вне этой фазы:** jev, тень, любое изменение поведения по `level`.

## Тяжесть от jev

**Где и как:** новый `src/relay/redact.py` (фильтр секретов, им же потом пользуется `RelayClient`), новый
`src/loop/jev.py` (клиент), новый `src/loop/situation_step.py` (точка вызова, туда же пойдёт фаза 3).
`judge_step.py` 397/400: **первым пунктом** вынести из него соседний код в новый модуль (кандидат —
`_stop_judge_pending` и вспомогательное вокруг `DecisionStep`), чтобы вызов `situation_step` встал одной
строкой. `config.py` 521/524: **первым пунктом** вынести `_ENV_MAP` и разбор окружения в `src/config_env.py`
(с повторным экспортом) — новые ключи этого плана (`jev_enabled`, relay, `escalation`, `sonnet_max`,
`phone_text_answers`) ложатся уже туда/в `Config`.

- Конфиг: `jev_enabled: bool = False`, ключ — `TYPESAFE_API_KEY` из окружения.
- `redact(text, secret_paths)`: вырезать `sk-…`, `AKIA…`, `-----BEGIN…`, hex ≥ 32 и base64 ≥ 40 **без `/`**
  (иначе съедаются пути) в любом месте строки; путь из `git.secret_paths` → только имя файла.
- Запрос — как `body()` в `jev-bench/v2_draft.py`: `state` (фаза, раунд, сколько раундов потрачено, сколько
  закрыто, `open_findings` — `redact(problem)[:600]`), вопросы `s0…sN` с критериями `SEV`. Вопрос
  `polishing` не задавать. Адрес `https://api.typesafe.ai/v1/systemone`, модель `jev-latest`.
- Находок больше 8 → несколько вызовов по 8. Ответы сортировать **по номеру** (`int(k[1:])`), не строкой.
- Находки с `route: discard_test` в jev не идут — тот же список, что берёт `_new_blocking`; порядок
  `sev[i]` ↔ `findings[i]` по этому списку.
- Итог — событие ledger `jev_sev` (`sev: [[level, conf], …]`, `tok`), в `stream.jsonl` — короткая строка.
  Таймаут **на всю тяжесть раунда** — 20 с (не на пакет); вызов идёт до проверки `stop` следующего шага, без
  `owner_lock`.
- **Сбой jev не трогает цикл:** нет ключа, сеть, не-200, мусор, таймаут → событие `jev_sev` с `error`
  (без ключа и без тела ответа), раунд идёт дальше. `jev_enabled=False` → вызова нет вовсе.

- [x] `test_redact`: токен посреди 500-символьной строки вырезан; `src/loop/phase.py` и `relay/src/worker.js` целы; путь из `secret_paths` → имя файла
- [x] `test_jev_body_matches_bench`: тело запроса = как в бенче, без `polishing`, `problem` прошёл `redact` и обрезан до 600
- [x] `test_jev_splits_over_8_and_sorts_numerically`: 11 находок → два вызова; `s10` попадает на своё место
- [x] `test_jev_skips_discard_test`: находка `discard_test` в запрос не идёт, `sev` выровнен по остальным
- [x] `test_jev_failure_does_not_block`: `post=` бросает / отдаёт 402 / спит дольше общего таймаута → событие с `error`, ключа в событии нет, фаза продолжается
- [x] `test_jev_off_by_default`: без флага `post=` не зовётся
- [x] вынос из `judge_step.py` и `config.py` сделан, старые имена импортируются, `test_structure` зелёный
- [x] реализовать

**Вне этой фазы:** `diagnose`, событие `situation`.

## Тень: событие situation

**Где и как:** перенести `jev-bench/diagnose.py` (функции `diagnose`, `_diagnose`, `_known_blocker`,
`_blockers`, `_rated_blockers`, `Cfg`, константы) в `src/loop/situation.py` **без изменения логики**; тесты
`jev-bench/tests/test_diagnose.py` без CLI и `simulate` (24 из 26) — в `tests/loop/test_situation.py`. Вызов
— из `situation_step.py`.

- Место вызова — **внутри `_apply_verdict` сразу после принятого NO_SHIP**, до `findings.stalled()` → STOP и
  до ветки `in_continuation`: раунд, который кончается стопом, тоже получает событие.
- История фазы для `diagnose` — в `state.json` под ключом `situation[<номер фазы>]`
  (`rounds`, `opus_round`, `owner_round`, `sonnet_n`), через `fsio`. **Не в `_State` раннера**: `more`/`model`
  создают новый раннер с чистым `_State()` (phase.py, `self.state = _State()`), а история должна пережить
  продолжение. На каждый NO_SHIP — `{round, n_findings, resolved, still_open, sev, judge_level, player}`.
  - `n_findings` — длина списка без `discard_test`; `resolved`/`still_open` — из `previous` судьи;
    `judge_level` — `level` из **сырого** ответа судьи (в `OpenFinding` его нет);
  - `player` — `sonnet`, если **этот** раунд играл `escalation_provider`, иначе `muse`. В режиме `round` флаг
    `escalated` не сбрасывается — брать фактического игрока раунда (из события `player_start`/роли), а не флаг.
  - `opus_round`, `owner_round` пока всегда `null`, `sonnet_n` — счётчик по фактическим раундам.
- Событие ledger `situation` (`situation`, `action`, `reason`, `round`) и строка в `stream.jsonl`.
  **Поведение не меняется:** результат никто не читает.
- Сбой `diagnose` (исключение) → событие `situation` с `error`, раунд идёт дальше.
- Пишется при `jev_enabled` и без него (тогда тяжесть только от судьи).

- [ ] `test_situation_matches_bench`: 24 перенесённых теста бенча зелёные
- [ ] `test_situation_event_after_no_ship`: фейковый прогон NO_SHIP→NO_SHIP→SHIP → два события `situation`; вердикты, раунды и итоговые статусы совпадают с тем же прогоном при подменённом `situation_step` = no-op (сравнение в одном тесте, а не со «снимком до плана»)
- [ ] `test_situation_on_stalled_stop`: раунд, где `stalled()` даёт STOP, всё равно пишет `situation`
- [ ] `test_history_survives_more`: после `more` новый раннер видит историю прошлых раундов фазы
- [ ] `test_player_is_actual_not_flag`: режим `round`, эскалация на r5 → r5 `sonnet`, r1–r4 `muse`; повтор после `more` на игроке плана → `muse`
- [ ] `test_situation_crash_is_event`: подменённый `diagnose` бросает → событие с `error`, фаза сдаётся как обычно
- [ ] реализовать

**Вне этой фазы:** любое действие по `situation`, Opus, шип с долгом.

## Worker, D1 и фильтр

**Где и как:** создать `relay/src/worker.js`, `relay/src/filter.js`, `relay/schema.sql`, `relay/wrangler.toml`
(без секретов, `account_id` пустой), `relay/package.json` (`"type": "module"`, без зависимостей),
`relay/test/*.test.js`, `relay/test/fake_d1.js`, `relay/bench.js`; обёртка `tests/relay/test_worker_js.py`;
`src/loop/gate_scope.py` — `relay/**` и `widget/**` отображаются на обёртку.

- `fake_d1.js` — тонкая обёртка над `node:sqlite` `DatabaseSync(":memory:")` с интерфейсом D1
  (`prepare().bind().run()/first()/all()`, `batch`), **выполняет настоящий `relay/schema.sql`**. Не разбор
  SQL регулярками.
- Обёртка: `node --test` с **явным списком** файлов `relay/test/*.test.js` (и `widget/*.test.js`, когда
  появятся). Нет `node` в `PATH` и не задан `TERO_REQUIRE_NODE` → skip; задан → fail. Поиск `node` — `PATH`,
  затем `~/.nvm/versions/node/*/bin/node` (прогон с `--detach` может не видеть nvm в `PATH`).
- Маршруты: `POST /state` (ключ записи tero, состояние по `run_id`+`machine_id`), `GET /state` (токен
  устройства или read-токен), `GET /runs` — активные прогоны, `POST /answer` (токен устройства: id вопроса +
  допустимое действие + текст ≤ 2000; `phone_text_answers: false` из состояния → текст отвергается **здесь**,
  не только на странице), `GET /answers` (ключ записи; **не удаляет**), `POST /answers/ack` (ключ записи,
  uuid — удалить после применения). Каждому ответу — uuid и время получения ретранслятором; `who: "human"`
  ставит Worker по токену устройства.
- Фильтр на приёме (вторая линия, те же правила, что `redact.py`): секреты в любом месте строки; блоки кода и
  `diff --git` не принимать; находка ≤ 500 символов. Только список полей из спеки (раздел 3); лишние
  отбрасываются.
- `relay/bench.js`: 1000 `POST /state` на фейке, печатает среднее CPU; критерий — < 10 мс на запрос.

- [ ] `filter.test.js`: токен посреди 500-символьной находки вырезан; относительный путь цел; блок ```` ``` ```` и `diff --git` отброшены
- [ ] `routes.test.js`: без токена → 401; read-токен не может `POST /answer`; ответ длиннее 2000 → 413; текст при `phone_text_answers: false` → 422; лишнее поле состояния отброшено; `GET /answers` дважды отдаёт тот же ответ, после `ack` — нет
- [ ] `schema.test.js`: `fake_d1` поднимает `schema.sql` без ошибок
- [ ] `test_worker_js.py`: падает, если JS-тест красный; с `TERO_REQUIRE_NODE=1` и пустым `PATH` — падает, без него — skip
- [ ] `test_gate_scope_relay`: изменён только `relay/src/worker.js` → `select_tests` включает обёртку
- [ ] реализовать

**Вне этой фазы:** привязка, пуши, cron, клиент на стороне tero.

## Привязка и токены

**Где и как:** `relay/src/pair.js`, `src/commands/relay_cmds.py` (`tero relay setup`, `tero pair`).
`cli_entry.py` 298/300: **первым пунктом** вынести регистрацию подпарсеров guide/answer-команд в
`src/commands/parsers.py` (функция `add_*_parsers(sub)`), новые подпарсеры — туда же.

- `tero pair`: одноразовый код — 8 символов из алфавита без похожих букв, 15 мин, сгорает при обмене;
  Worker после 5 неверных кодов с одного IP за 15 мин отвечает 429. Печатает ссылку; QR **не делаем** в этом
  плане (кодировщик без зависимостей — отдельная работа), ссылку хозяин открывает сам. `--revoke
  <устройство>`, `--reset`.
- Worker: обмен кода на токен устройства; токен хранится хешем (SHA-256); read-токен для виджета — отдельный.
  Отозванный токен → 401 с телом `{"error": "unpaired"}` (страница покажет «привязка недействительна»).
- `tero relay setup`: генерирует ключ записи (`secrets.token_urlsafe`) и **VAPID-ключи через `node`**
  (`relay/src/vapid_keygen.js`, `crypto.subtle.generateKey` P-256 → JWK; в stdlib Python P-256 нет), пишет в
  `~/.tero/secrets.env` (права 600) и адрес в `~/.tero/config.yaml`; `wrangler login`, `wrangler secret put`
  и деплой — **печатает команды для человека**, сам не запускает.

- [ ] `pair.test.js`: код живёт 15 мин (подменённые часы), второй обмен того же кода → 410; 6-й неверный код → 429; `--revoke` одного устройства не трогает другое; отозванный → 401 `unpaired`
- [ ] `test_pair_cmd`: `tero pair` с фейковым `fetch=` печатает ссылку, ключ записи не печатается
- [ ] `test_relay_setup`: с фейковым `node` пишет `secrets.env` с правами 600, секреты не в stdout
- [ ] вынос подпарсеров, `cli_entry.py` ≤ 300, старые команды работают
- [ ] реализовать

## RelayClient и ответ с телефона

**Где и как:** `src/relay/client.py` (реальный `RelayClient`), `src/relay/iface.py` (`make_relay(cfg)`
переезжает сюда из `guide_cmds.py`, там остаётся повторный экспорт), `PhaseContext` получает поле `relay`
(создаётся один раз на прогон), `relay/src/cron.js`.

Сейчас (проверено): `Inbox.take` уже зовёт `relay.fetch_answers()` и берёт `who == "human"`, но все три
`DecisionStep(...)` — `phase.py` (`_decider`), `judge_step._stop_judge_pending` (или куда его вынесла фаза 2),
`resume_wait.py` — создаются без relay, то есть всегда `NoopRelay`; `push_state` не зовёт никто.

- Все три места передают `ctx.relay` в `DecisionStep`. `phase.py` 689/689: правка в `_decider` — замена
  аргумента в той же строке, без новых строк; если не влезает — `_decider` выносится в `decision_step.py`.
- **Ответ с телефона идёт в памяти** через `fetch_answers`, файлом в `inbox/` не кладётся. Локальные файлы
  `inbox/` — `who: agent`, если `human` без подписи (`src/loop/human_key.py`: HMAC ключом `~/.tero/human.key`,
  привязан к id прогона; подписывает только `tero answer --human` на TTY) — фикс B7 уже сделан, этот путь не трогать. После успешного `_apply` —
  `relay.ack(uuid)`; отклонённый (бюджет, не тот вопрос) ответ тоже `ack` с причиной, чтобы не крутился.
- Текст из relay в `take` проходит тот же `_cap`, что локальный; `action == "text"` при
  `phone_text_answers: false` отклоняется и в tero.
- `push_state`: из `transition.phase_transition` после записи `state.json` — в фоновом потоке-демоне с
  очередью из одного (новое состояние вытесняет неотправленное), никогда под `owner_lock`. Пульс раз в 5 мин
  — тот же поток. Ответы опрашиваются только в `waiting_*`, раз в 10 с (уже есть в ожидании).
- Перед отправкой состояние проходит `redact` (фаза 2). Сбой сети не роняет прогон, таймаут 5 с; в `status`
  — «нет связи с ретранслятором», если последняя отправка не удалась.
- Cron раз в 5 мин: пульс старше 15 мин при незавершённом статусе → **один** пуш «нет связи с машиной»
  (отправку пуша фаза 7 подставит; здесь — запись `stale_notified` и вызов заглушки `notify()`), отметка
  `stale_notified` до возврата пульса. `done`/`stopped` tero отправляет всегда.

- [ ] `test_relay_reaches_all_decision_steps`: во всех трёх местах `DecisionStep.relay is ctx.relay`
- [ ] `test_phone_answer_applied_in_memory`: фейковый relay отдаёт ответ `who: human` → применён, файла в `inbox/` нет, `ack` вызван
- [ ] `test_inbox_file_human_is_agent`: файл в `inbox/` с `"who": "human"` без подписи → считается агентом; ответ relay `who: human` → человек (защита B7 не сломана)
- [ ] `test_phone_text_capped_and_not_in_judge`: текст 5000 символов → обрезан; в промпте судьи текста нет; `##` экранирован в промпте игрока
- [ ] `test_relay_client_pushes_on_status_change` и `test_relay_down_does_not_stop_run` (фейковый `fetch=`, отправка не под `owner_lock`)
- [ ] `cron.test.js`: пульс 16 мин назад → один `notify`; второй cron без нового пульса → нет
- [ ] реализовать

## Web push

**Где и как:** `relay/src/push.js` (Web Crypto, VAPID P-256, JWT ES256 — подпись **сырые r‖s, 64 байта**,
`aud` = origin endpoint, `exp` ≤ 24 ч; шифрование `aes128gcm` по RFC 8291), подключение к `notify()` из cron
и к событиям «новый вопрос / done / stopped».

- Эфемерный ключ и salt — параметры функции (по умолчанию случайные), чтобы тест подставил вектор.
- Пуш ≤ 200 символов, без находок целиком; одна причина не чаще раза в 30 мин (по `run_id`+причине в D1).
- Ответ push-сервиса пишется в D1 (`push_log`: код, время); 404/410 → подписка мертва. Поле `push_ok` Worker
  возвращает в ответе на `POST /state`; tero сохраняет его, `tero status` при `false` пишет
  «пуш-канал сломан — перепривяжи».

- [ ] `push.test.js`: шифрование с ключами и salt из RFC 8291 Appendix A даёт **байт в байт** тело из RFC
- [ ] `push.test.js`: JWT — 64-байтная подпись, проверяется публичным ключом через `subtle.verify`
- [ ] `push.test.js`: 410 → подписка мёртвая, запись в `push_log`; вторая та же причина за 30 мин → пуша нет
- [ ] `test_status_push_broken`: `push_ok: false` в ответе relay → строка в `tero status`
- [ ] реализовать

## Страница-пульт

**Где и как:** `relay/public/index.html`, `relay/public/sw.js`; манифест генерирует Worker (код в `start_url`).

- Требования — T14 мастер-плана: открытый вопрос первым; список прогонов с переключением; список моделей для
  `model` без `judge`; «уже отвечено», если вопрос закрыт; журнал последних событий; долг и история фазы;
  `requestPermission()` только по кнопке; `phone_text_answers: false` → кнопки `more|skip|retry|stop` без
  текста; `stop` с подтверждением; 401 `unpaired` → «привязка недействительна».
- Без токена страница показывает только экран привязки (данные — только с токеном, Worker отвечает 401).

- [ ] `page.test.js`: по фиктивному состоянию HTML содержит открытый вопрос раньше статуса; моделей `judge` в списке нет; закрытый вопрос → «уже отвечено»; без токена — экран привязки, без данных
- [ ] реализовать

**Ручная приёмка фаз 4–8 (не гейт):** я — `wrangler dev` и деплой; хозяин — айфон: привязка Safari →
PWA, «Включить пуши», пуш пришёл, ответ дошёл, «нет связи» один раз.

## Виджет и лимиты

**Где и как:** `widget/tero.js` (Scriptable), `widget/tero.test.js`, `src/loop/state.py` (`limits`),
`src/providers/*` — только то, что CLI уже сообщает (у codex — токены и квота из потока, фаза 5 плана 07).

- Разметка — чистая функция `layout(state, family)`; вызовы API Scriptable (`ListWidget`, `Request`,
  `args`) — только под `if (typeof ListWidget !== "undefined")`, чтобы файл импортировался в node.
- Виджет читает read-токен из `args.widgetParameter`, в коде токена нет. Экран блокировки: 2–3 строки
  (`2/5 · р3` / `⚠ ответ` / `лимит 40%`); домашний — подробнее, тап → Safari.
- `limits` в `state.json`, `status` и на странице; чего CLI не сообщает — «неизвестно».

- [ ] `tero.test.js`: импорт в node без падения; `layout` по фиктивному состоянию даёт 3 строки; в файле нет строки, похожей на токен
- [ ] `test_limits_unknown_by_default`: провайдер без данных → `limits[p] == "неизвестно"`
- [ ] реализовать

**Вне этой фазы:** уровни шума пушей (спека §5) — отдельно, после живой недели с пультом.

## Sonnet по ситуации

**Где и как:** конфиг `escalation: "round" | "situational"` (по умолчанию `"round"`), `sonnet_max: 2`.
Валидация конфига: `situational` **требует `decisions: true`** (иначе ошибка конфига при старте) — без
`decisions` `DecisionStep.halt` идёт в `_old_halt`, точек решения нет. Модули: `situation_step.py`;
`phase.py` 689/689 — **первым пунктом** вынести `_no_progress` и связанное (phase.py ~595) в
`src/loop/progress_step.py`, чтобы ветка встала без роста `phase.py`.

- При `"situational"` после NO_SHIP `situation_step` возвращает игрока следующего раунда:
  `stuck` → `escalation_provider`/`escalation_model`; `unstuck`/`converging` → игрок плана.
  Одноразовый флаг `escalated` при `"situational"` не используется; лимит — `sonnet_n` из истории
  (`state.json`, фаза 3), по всей фазе, переживает `more`/`model`.
- `findings.stalled()` после судьи при `"situational"` не зовётся. Пути `_no_progress` **до судьи**
  (`protect_tests`, `_same_red`) — на старом механизме; ветка `escalate_at_round` при `"situational"`
  выключена, `grace_until` остаётся; путь продолжения (`in_continuation`) — без действий.
- Действия `polish`, `stuck_strong`, `mole`, `ask_owner` в этой фазе ведут себя как `converging`
  (событие пишется, действие — в фазе 11).
- `escalation_model` по умолчанию в конфиге — `"opus"`: для этого режима в шаблоне `.tero/config.yaml`
  явно `escalation: claude:sonnet`; в тесте — явно.

- [ ] `test_round_mode_unchanged`: `escalation: round` — фейковый прогон NO_SHIP×5 даёт те же раунды, игроков и статус, что при `situation_step` = no-op (одно сравнение в тесте)
- [ ] `test_situational_requires_decisions`: `situational` без `decisions` → ошибка конфига
- [ ] `test_stuck_then_sonnet_then_back`: фейковый судья держит `still_open` → раунд Sonnet; после `sonnet_max` — игрок плана
- [ ] `test_sonnet_max_survives_more`: 2 раунда Sonnet → `more` → новый раннер Sonnet не зовёт
- [ ] вынос `_no_progress`, `phase.py` не вырос
- [ ] реализовать

## Шип с долгом и Opus-решение

**Где и как:** `situation_step.py`, новые `src/loop/debt.py` и `src/loop/opus_decision.py`.

- **Долг:** `.tero/runs/<run>/debt.md` (человеку) и `state.json` → `debt: [{phase, round, finding_id, level,
  problem, why: polish|opus|skip}]` (машине; страница читает его). Запись — через `fsio`.
- `polish` → **шип с долгом**: открытые находки — в долг, затем фаза идёт **тем же путём, что SHIP**:
  `_Next.SHIP` → `_full_gate_before_ship` → `_ship` (проверка секретов, полный гейт, трейлер). Прямой коммит
  мимо этого пути запрещён. Гейт красный → не шип, раунд продолжается как обычно.
- `stuck_strong`/`mole` → **один** вызов Opus-решения: роль `decider` через существующий раннер провайдера
  (`agents.py` не трогать; вызов собирает `opus_decision.py`), `claude:opus`, текстовый JSON-ответ
  `{decision: ship_with_debt|more|ask_owner, why}`, разбор как у судьи на текстовой схеме, таймаут 10 мин,
  `stop` во время вызова — убить процесс и выйти как обычный `stop`.
  - `ship_with_debt` → как `polish` (тот же путь), `why: opus`;
  - `more` → в историю `opus_round` = текущий раунд, играет игрок плана; повторный `stuck_strong`/`mole` →
    `ask_owner` (так уже решает `diagnose`);
  - `ask_owner` → `DecisionStep.halt("stuck")`: точка решения more/model/skip/stop; ответ `more` хозяина →
    `owner_round` в историю.
- Сбой Opus-вызова (таймаут, не-JSON, нет CLI) → `ask_owner`.

- [ ] `test_polish_ships_through_gate`: мелочи на r3 → фаза закоммичена через `_ship` (есть трейлер), в `debt.md` и `state.debt` находки; при красном гейте — не шип
- [ ] `test_opus_ship_with_debt`: фейковый Opus `ship_with_debt` → тот же путь, `why: opus`
- [ ] `test_opus_more_then_owner`: Opus `more` → ещё раунд; второй `mole` → точка решения `stuck`; ответ хозяина `more` → `owner_round`
- [ ] `test_opus_failure_asks_owner` и `test_stop_during_opus`
- [ ] реализовать

**Вне этой фазы:** пороги (`fix_budget`, `min_conf`, `mole_rounds`) не трогать — правятся по сверке тени.

## Скип в долг без отката

**Где и как:** `decision_resolve._skip_to_debt` (сейчас: снимок в ref + `locked_rollback`, работа фазы
выбрасывается).

Проверено: при `decisions: true` `halt("fuse")` уже открывает вопрос без отката; откат — только в `_old_halt`
и в ответах `skip`/`stop`. Поэтому меняется не фьюз, а `skip`.

- При `escalation: "situational"` ответ `skip` (от хозяина или агента-автопилота) на любую точку решения:
  открытые находки — в долг (`why: skip`), фаза идёт путём шипа из фазы 11. Красный полный гейт → старый
  `_skip_to_debt` (снимок + откат), в долг пометка «гейт красный, откат».
- `stop` и режим `"round"` — как сейчас.

- [ ] `test_skip_keeps_work_situational`: фьюз → вопрос → `skip` → изменения игрока закоммичены, долг записан
- [ ] `test_skip_red_gate_rolls_back`: тот же сценарий с красным гейтом → откат, ref есть, пометка в долге
- [ ] `test_skip_round_mode_rolls_back`: режим `round` — старое поведение
- [ ] реализовать

---

## Решения в этом черновике, которые принял я (Claude)

- Эскалация и пульт — один план (решение хозяина 2026-10-26); тень первой, чтобы прогон сам копил данные.
- `wrangler dev` из спеки заменён на `node --test` + фейк D1 на `node:sqlite`: у исполнителя нет сети и
  `npm install`. Настоящий Worker проверяю руками после фаз 4–8.
- `polishing` у jev не спрашиваем — бенч показал, что нужна только тяжесть.
- QR в `tero pair` выкинут (ссылка вместо него); уровни шума пушей — после недели живого пульта.
- Действия эскалации пишем в этом плане, но под флагом `round` по умолчанию; включение — после сверки тени
  (≥ 10 фаз с 2+ NO_SHIP), отдельным решением хозяина.
- Бывшая фаза «фьюз без отката» заменена на «скип в долг без отката»: фьюз при `decisions` уже не откатывает.
- Шаг 7 дизайна (уборка долга, потолок major) — не фаза кода, а правило после плана: смотрим `debt.md`.
- Перед прогоном (я, не исполнитель) — сделано 2026-10-26: B1 (`997f92a`), B2–B4 (план 07, фаза 6), B5–B6 (план 07,
  фазы 4–5), B7/B10 (`b94444e`, `a818218`), B11 (`2fbd187`), долг плана 07 (`7d5eb02`). B9 входит в фазу 1,
  B8 закрывается фазами 10–11. Новые модули, о которых стоит знать: `src/loop/crash_step.py` (сбой фазы → снимок +
  откат + стоп `crash`, вызов в `session._run_one`), `src/loop/human_key.py`, `src/loop/ignored_guard.py`.
