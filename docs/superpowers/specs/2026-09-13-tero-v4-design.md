# Tero v4 — Design Spec

**Дата:** 2026-09-13
**Статус:** черновик, правки после сверки с провайдерами
**Подход:** A — новое ядро `src/loop/` рядом со старым, затем удаление старого ядра

Канон ролей (совпадает с текущим `src/config.py` / меню):

| Роль | Провайдер | Модель |
|---|---|---|
| Player | `muse` | `muse-spark-1.3` |
| Judge | `codex` | `gpt-5.6-terra` (reasoning `max`) |
| Tester | `gemini` | `gemini-3.8-flash` |
| Escalation | `claude` | `opus` → `claude-opus-5` |

Живые провайдеры: `muse`, `claude`, `codex`, `gemini`, `opencode`, `cursor`. `zai` и `kilo` сняты, алиасов нет.

---

## 1. Цель

Один цикл вместо набора режимов и ручек:

```
player → judge → (tester) → player → judge → … → SHIP
```

Ядро детерминированное: модели работают, а решения принимают код, exit-коды тестов, `git` и JSON-схемы. Вердикты и готовность фазы больше не выводятся регулярками из прозы модели. Извлечение JSON из финального текста — только fallback для провайдеров без native schema (см. 5.3).

### Почему (по ревью текущего кода)

- Готовность фазы решают регулярки по тексту модели (`batch_executor.py:103-316`, `feedback.py`). Двусмысленный ответ ревьюера считается пройденным (`feedback.py:291-293`).
- Оркестратор сам тестов не запускает: «Verification» — просто секция текста в ответе player'а.
- В цикле нет git: нет чекпоинта, судья не видит diff, откатиться нечем.
- Проваленная фаза пропускается, и следующие фазы строятся на сломанном коде (`batch_executor.py:599-618`).
- `--code-review` в batch-режиме ничего не делает: ревью живёт только в `CoachPlayerSession.run()`, который CLI не вызывает.
- Около 2,3k строк недостижимого кода, ещё около 440 строк в неиспользуемом `run()`.
- Batch-путь не пишет историю, поэтому качество и стоимость не измерить.

---

## 2. Решения

### 2.1 Утверждены с пользователем

| # | Решение |
|---|---|
| D1 | Цикл player → judge → (tester) → player → judge, пока судья не даст SHIP |
| D2 | Судья получает контракт фазы, **карту** от player'а (файлы, что/зачем, откуда начать), `git diff -W` и сырой вывод тестов. Самооценку player'а («тесты прошли», «готово») судья не получает |
| D3 | Судья отвечает JSON. Для каждой находки он сам решает `route: tester \| player`. Оркестратор исполняет маршрут, судья агентов не запускает |
| D4 | Tester запускается только после NO-SHIP и только по находкам с `route: tester`. Можно дешёвую модель |
| D5 | Команда тестов определяется автоматически (pytest / npm / cargo / go), переопределяется в `.g3/config.yaml` или флагом |
| D6 | Фазы = заголовки `##` в плане. Если заголовков нет, каждый чекбокс — отдельная фаза. Автогруппировка по ключевым словам удаляется |
| D7 | Ветка `tero/<план>`, коммит на каждую фазу с SHIP. Main не трогается |
| D8 | Застряли → одна эскалация player'а на модель из конфига → снова застряли → откат фазы на чекпоинт, стоп всего плана, причина в журнал |
| D9 | Интерактивное меню остаётся. Главное в нём — провайдер и модель для каждой роли |
| D10 | Удаляются: горячие клавиши (`runtime_controls`), `tero ldb`, fallback-цепочки, мёртвые модули, `CoachPlayerSession.run()`, regex-парсинг вердиктов, расписание 5/2/3, файлы «bug-proof» тестов |
| D11 | Новые горячие клавиши — отдельный спек после v4 |

### 2.2 Добавлены в этом спеке (можно оспорить на ревью)

| # | Решение | Зачем |
|---|---|---|
| S1 | Вердикт `NEEDS_USER` у судьи | Единственный путь «стоп по неясности». Player не может остановить план сам |
| S2 | Судья помечает находки прошлого раунда `resolved` / `still_open` | Детерминированное определение «нет прогресса» без сравнения формулировок |
| S3 | Аварийный предохранитель `max_rounds` (по умолчанию 15) на фазу | Защита от судьи, который бесконечно находит новые мелочи. Штатный стоп — по-прежнему «нет прогресса». Это новое поле, не duel-`max_rounds=3` |
| S4 | Advisory-находки никогда не держат цикл | Против бесконечных придирок |
| S5 | Старт только с чистым деревом и зелёными тестами | Чекпоинты и RED-проверка иначе врут. Нет тестов → старт разрешён, см. 5.2 |
| S6 | Защита дерева от судьи: снапшот до и сверка после | Native read-only есть только у Codex и Claude; Muse/Cursor/Gemini/OpenCode игнорируют флаг. Решает сверка |
| S7 | Хэши файлов tester'а. Player не может их править | Player не чинит тест вместо кода |
| S8 | Новая зависимость `jsonschema` | Валидация ответов вместо самописных проверок |
| S9 | `.g3/` добавляется в `.git/info/exclude` целевого репо | Журнал не попадает в diff и коммиты; `.gitignore` пользователя не трогается |
| S10 | Гейта «только разрешённые файлы» нет | Scope проверяет судья; план не обязан перечислять файлы. Секреты — отдельно, S13 |
| S11 | Исходный сьют на checkpoint: удаление тестового файла → restore + `NO_PROGRESS`. Правка существующих тестов разрешена и уходит судье как `suite_delta` | Нельзя «позеленить» фазу, выкинув тесты. Контракт фазы может требовать правки тестов |
| S12 | Неизвестный провайдер в yaml/env/CLI (в т.ч. старые `zai`/`kilo`) → отказ старта, код 1. Автозамены нет | После сноса провайдеров конфиг не молчит |
| S13 | Перед коммитом — deny-list секретов; судья видит `git status --short` отдельно от diff | S10 не означает «коммитим `.env`» |

---

## 3. Поток выполнения

### 3.1 Старт рана

```
tero go
  │
  ├─ меню (или --no-menu) → Config
  ├─ провайдер каждой роли ∈ {muse, claude, codex, gemini, opencode, cursor}
  │     иначе отказ, код 1, имя в сообщении                          (S12)
  ├─ проверить провайдеров всех ролей (check_ready)
  ├─ определить команду тестов                         (gates.detect)
  ├─ git: репо есть? дерево чистое?                     (S5)
  │     грязное на ветке tero/* → в меню предложить откат незавершённой фазы
  │     грязное в другом месте → отказ с объяснением
  ├─ checkout/создать ветку tero/<slug плана>          (D7; существует → продолжаем на ней)
  ├─ тесты зелёные на старте? нет → отказ              (S5)
  ├─ .g3/ → .git/info/exclude                          (S9)
  └─ создать .g3/runs/<run-id>/
```

### 3.2 Фаза

```
checkpoint = HEAD
round = 0, escalated = false, open_findings = []
suite_hashes = хэши тестовых файлов и конфигов тестов на checkpoint   (S11)
protected = {}   # path → sha256 файлов tester'а этой фазы

loop:
  round += 1
  если round > max_rounds → ROLLBACK + STOP("fuse")                 (S3)

  1. PLAYER (свежая сессия; после эскалации — модель эскалации)
       вход:  контракт фазы, git diff --stat от checkpoint,
              блокирующие находки (с путями тестов tester'а),
              advisory, suite_delta прошлого раунда,
              вывод красного гейта (если был)
       выход: PlayerReport (JSON)

  2. ЗАЩИТА ТЕСТОВ
       2a. хэши protected (tester) совпадают? нет → восстановить,     (S7)
           NO_PROGRESS, следующий раунд
           (player получает замечание; гейт и судья не вызываются)
       2b. тестовый файл с checkpoint удалён? → восстановить,         (S11)
           NO_PROGRESS, следующий раунд
       2c. тестовый файл с checkpoint изменён? → suite_delta = пути
           (не восстанавливать; решит судья)

  3. ГЕЙТ ТЕСТОВ: run_all()
       tree_now = git write-tree (после player, без add чужого)
       красный →
            есть fingerprint и он == прошлый красный fingerprint фазы?
              да → NO_PROGRESS
            иначе tree_now == tree_at_last_red?
              да → NO_PROGRESS          # нет отпечатка — сравниваем дерево
            запомнить fingerprint (если есть) и tree_at_last_red
            следующий раунд, player получает сырой вывод
            (судья не вызывается)
       зелёный → дальше

  4. git add -A; tree_before = git write-tree
     JUDGE (свежая сессия)
       вход:  контракт, PlayerReport как карта,
              git diff --cached -W checkpoint,
              git status --short,                                    (S13)
              suite_delta, вывод тестов, open_findings с id
       выход: JudgeVerdict (JSON)
     git add -A; tree_after = git write-tree
     tree_after != tree_before → восстановить дерево до tree_before,
                                  записать в журнал                   (S6)

  5. по вердикту:
       SHIP       → отметить чекбоксы секции [x]
                    deny-list секретов не пуст? → ROLLBACK + STOP("secrets")
                    иначе git commit → следующая фаза                (S13)
       NEEDS_USER → ROLLBACK + STOP(user_question)
       NO_SHIP    →
          применить discard_test: удалить protected-файл, снять защиту;
              находка остаётся открытой и уходит player'у текстом
          stall = open_findings непусты
                  и ни одна previous не помечена resolved
          если stall:
              NO_PROGRESS()            # только side effect: escalate или STOP
              если фаза остановлена → выход
              # после первой эскалации поток продолжается ниже
          open_findings = still_open из прошлых + новые blocking
                          (новым id F<n> присваивает оркестратор)
          среди НОВЫХ blocking есть route=tester и tester включён → 6
          иначе → следующий раунд

  6. TESTER (свежая сессия)
       вход:  находки с route=tester, контракт, diff
       выход: TesterReport (JSON)
     для каждого теста:
       путь не похож на тестовый → откатить файл, находка уходит player'у текстом
       run_files([путь]) exit 0 (зелёный) → удалить файл, находка уходит текстом
       красный → записать sha256 файла в protected
     изменения tester'а вне тестовых путей → откатить
     → следующий раунд

NO_PROGRESS() — только эскалация или стоп, набор находок не меняет:
  не escalated и эскалация настроена → escalated = true
      (player до конца фазы работает на модели эскалации), вернуть управление
  иначе → ROLLBACK + STOP("stuck")

Вызывать из шагов 2, 3, 5 и при сбое судьи (5.3).
После эскалации те же условия stall / тот же красный отпечаток / то же дерево
снова вызывают NO_PROGRESS и уже останавливают фазу (D8).

ROLLBACK = git reset --hard checkpoint && git clean -fd
```

Tester пишет тесты поверх текущих изменений player'а, фаза не откатывается. Чекбоксы плана коммитятся вместе с кодом фазы, если план лежит внутри репо.

Бесконечные *новые* blocking при закрытых старых режет `max_rounds` (S3), не `NO_PROGRESS`. Advisory цикл не держит (S4).

### 3.3 Продолжение после остановки

- Ветка `tero/<slug>` существует → checkout на неё.
- Фазы, у которых все чекбоксы `[x]`, пропускаются.
- Ctrl+C: остановить агента, убить порождённые процессы, **дерево не откатывать** (пусть можно посмотреть), в журнал `interrupted`. На следующем старте грязное дерево на ветке `tero/*` → меню предлагает откат (с подтверждением), в `--no-menu` печатается команда отката и выполнение прекращается.

---

## 4. Контракты данных

Все три роли в финальном сообщении возвращают JSON. Схемы лежат в `src/loop/schemas.py`. Кроме схемы, оркестратор проверяет смысловые правила.

### 4.1 PlayerReport

```json
{
  "status": "done",
  "changes": [
    { "file": "src/parse.py", "what": "пустая строка возвращает []", "why": "шаг 2 контракта" }
  ],
  "start_here": ["src/parse.py:40"],
  "blocked_reason": ""
}
```

- `status`: `done` | `blocked`. При `blocked` цикл идёт дальше: гейт, потом судья, который видит `blocked_reason` и может вернуть `NEEDS_USER`.
- Поля «тесты прошли» в схеме нет — это факт, который устанавливает оркестратор.
- Для судьи это **карта**: навигация, а не доказательство. Промпт судьи говорит об этом явно.

### 4.2 JudgeVerdict

```json
{
  "verdict": "NO_SHIP",
  "previous": [ { "id": "F1", "state": "resolved" }, { "id": "F2", "state": "still_open" } ],
  "findings": [
    { "severity": "blocking", "route": "tester",
      "file": "src/parse.py", "line": 42,
      "problem": "parse('') бросает IndexError", "expected": "возвращает []" },
    { "severity": "advisory", "route": "player",
      "file": "src/util.py", "line": null,
      "problem": "дублирование хелперов", "expected": "" }
  ],
  "user_question": ""
}
```

Смысловые правила (нарушение считается невалидным ответом, см. 5.3):
- `SHIP` → в `findings` нет `blocking`, и все `previous` помечены `resolved`.
- `NO_SHIP` → хотя бы один `blocking` в `findings` или хотя бы один `still_open` в `previous`.
- `NEEDS_USER` → `user_question` не пустой.
- `previous` покрывает все id, которые оркестратор передал в `open_findings`.
- `still_open` находки прошлого раунда остаются открытыми; новые `blocking` добавляются с новыми id `F<n>`.
- `route`: `tester` | `player` | `discard_test`.
- `route: tester`, когда тесты недоступны или не умеют скоуп → оркестратор меняет маршрут на `player`.
- `route: discard_test` допустим только для id из `open_findings`, у которого есть protected-файл. Оркестратор удаляет файл и снимает защиту; находка остаётся открытой и уходит player'у текстом («тест снят, чини код»). Нужен, когда tester написал неверный RED.

### 4.3 TesterReport

```json
{
  "tests": [ { "finding_id": "F1", "path": "tests/test_parse.py", "name": "test_parse_empty_string" } ],
  "skipped": [ { "finding_id": "F3", "reason": "поведение UI, автотестом не воспроизводится" } ]
}
```

- Один новый файл на находку, чтобы RED-проверка и защита работали по файлам.
- Тестовые пути: `tests/`, `test/`, `__tests__/`, `test_*.py`, `*_test.py`, `*_test.go`, `*.test.{ts,tsx,js}`, `*.spec.{ts,tsx,js}`.
- Конфиги сьюта (для S11): `pytest.ini`, `conftest.py`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`. Их удаление/порча на checkpoint считается порчей сьюта.

---

## 5. Модули

Новое ядро — `src/loop/`. Каждый модуль имеет одну задачу и тестируется отдельно, без моков сессии.

| Модуль | Отвечает за | Интерфейс (суть) | Зависит от |
|---|---|---|---|
| `plan.py` | Разбор плана на фазы, запись чекбоксов | `load_phases(path) -> list[Phase]`; `mark_phase_done(path, phase)` | — |
| `schemas.py` | Схемы и смысловая валидация | `PLAYER`, `JUDGE`, `TESTER`; `validate(role, data, ctx) -> list[str]` | `jsonschema` |
| `prompts.py` | Системные промпты трёх ролей и сборка входов | `player_prompt(...)`, `judge_prompt(...)`, `tester_prompt(...)` | `plan`, `schemas` |
| `gates.py` | Поиск и запуск тестов | `detect(repo) -> TestCommand \| None`; `run_all()`; `run_files(paths)`; `fingerprint(result)` | subprocess |
| `git.py` | Все git-операции | `ensure_clean`, `checkout_run_branch`, `head`, `stage_and_diff(base)`, `write_tree`, `restore_tree`, `file_hashes`, `rollback(base)`, `commit(msg)`, `exclude_g3`, `status_short` | subprocess `git` |
| `agents.py` | Вызов роли и получение валидного JSON | `run_role(role, provider, model, system, prompt, schema, timeout, ctx) -> dict` | `providers`, `schemas`, `process_guard` |
| `phase.py` | Раунды одной фазы (раздел 3.2) | `run_phase(phase, deps) -> PhaseOutcome` | всё выше, `ledger` |
| `run.py` | Старт, цикл по фазам, стоп | `run(config) -> RunOutcome` | `phase`, `git`, `gates`, `ledger` |
| `ledger.py` | Журнал и артефакты, история | `Ledger(run_dir).event(kind, **data)`, `.artifact(path, content)`; `history(repo, limit)` | — |

`phase.py` получает зависимости явно (`deps` с `agents`, `gates`, `git`, `ledger`), поэтому интеграционные тесты подставляют `FakeAgents` со скриптом ответов и работают с настоящим git во временном репо.

### 5.1 `plan.py`

- Преамбула до первого `##` — общий контекст плана, передаётся в каждую фазу.
- Секция `##` = `Phase(index, title, body, checkboxes)`. `body` — весь текст секции, включая чекбоксы: это и есть контракт фазы.
- Заголовков нет → каждый чекбокс становится фазой с `title = текст чекбокса`.
- Фаза выполнена, если все её чекбоксы `[x]`. Секция без чекбоксов не считается фазой (это заметки).
- `mark_phase_done` меняет только строки чекбоксов своей секции, запись атомарная (temp-файл + rename).

### 5.2 `gates.py`

Порядок автодетекта (первое совпадение):

| Признак | `run_all` | `run_files` |
|---|---|---|
| `pyproject.toml` / `pytest.ini` / `conftest.py` / `tests/*.py` | `python -m pytest -q` | `python -m pytest -q {files}` |
| `package.json` со скриптом `test` | `npm test --silent` | `npm test --silent -- {files}` |
| `Cargo.toml` | `cargo test` | — |
| `go.mod` | `go test ./...` | — |

- Переопределение: `test_command` и `test_command_scoped` (с `{files}`) в конфиге или флаги `--test-command` / `--test-command-scoped`.
- Нет `run_files` → tester отключён для рана, `route: tester` превращается в `player` (раздел 4.2). Меню показывает это.
- Тесты не найдены совсем → гейт пропускается, tester отключён, судья видит «тесты не обнаружены». Старт разрешён, в меню и в журнале стоит предупреждение.
- `fingerprint`: детерминированный только для pytest — множество id упавших тестов из `FAILED …`. Для остальных `fingerprint` = `None`; «тот же красный» тогда определяется по неизменному `write-tree` с прошлого красного гейта (шаг 3.2.3). Хэш хвоста лога не используется: в нём таймстампы.
- Вывод в промптах обрезается до последних 200 строк; полный вывод идёт в артефакты.
- Таймаут `test_timeout_s` (по умолчанию 900). Таймаут считается красным гейтом.

### 5.3 `agents.py`

1. `provider.run(prompt, system_prompt, working_dir, model=..., output_schema=schema, read_only=...)`. Два **новых необязательных** аргумента. Протокол `AgentProvider` их не требует: кто не понимает — игнорирует. Codex: `read_only=True` мапится на уже существующий `disable_tools=True` (`-s read-only`).

   | Провайдер | `output_schema` | `read_only` (судья) |
   |---|---|---|
   | `claude` | `--json-schema` | запрет `Edit` / `Write` / `NotebookEdit` |
   | `codex` | `--output-schema <tmpfile>` | `-s read-only` |
   | `muse` | нет флага; JSON из финального текста | нет; всегда `--yolo` |
   | `cursor` | нет флага; JSON из финального `stream-json` | нет; всегда `--force --trust` |
   | `gemini` | нет | нет; `--yolo` |
   | `opencode` | нет | нет |

   Реальная гарантия, что судья не пишет в дерево, — сверка (S6), не флаги CLI. Дефолтный player — Muse: native schema нет, рабочий путь — шаг 2 (last JSON) + один ремонт.

2. Извлечь JSON: сначала нативный структурированный результат, если провайдер его отдал; иначе последний JSON-объект из финального текста ассистента (в ```json-блоке или без него).
3. `schemas.validate`. Ошибка → **один** ремонтный вызов той же роли: «верни только JSON по схеме; твой прошлый ответ: …; ошибки: …». Работа в дереве при этом не повторяется.
4. Снова невалидно или таймаут:
   - player → идём дальше без карты (`PlayerReport` = «отчёт недоступен»); diff остаётся главным источником правды;
   - judge → раунд засчитывается как `NO_PROGRESS`;
   - tester → находки уходят player'у текстом.
5. Ошибка провайдера (auth, лимиты, сеть) → одна повторная попытка через 30 с → снова ошибка → ROLLBACK + STOP(`provider_unavailable: <имя>`).
6. До вызова снапшот PID, после — убить новые процессы (существующий `process_guard`).

Каждый вызов свежий: без продолжения сессий, без continuation-промптов и без компакции на уровне оркестратора.

### 5.4 Промпты (суть правил)

**Player:** выполнить контракт фазы минимальным изменением; можно запускать тесты для самопроверки; не коммитить; не менять файлы из `protected` и не удалять тестовые файлы с checkpoint; при неясности не выдумывать, а вернуть `status: blocked`; финальное сообщение — только JSON `PlayerReport`.

**Judge:** файлы не менять. Карта player'а — навигация, её утверждения надо проверить по diff и коду. Проверить соответствие контракту, регрессии, scope, `suite_delta` и `git status`. Ослабление/skip/`xfail` существующих тестов без пункта контракта — `blocking`. Можно читать любые файлы и запускать команды, которые ничего не меняют. `blocking` — только нарушение контракта, баг или регрессия; стиль и «можно лучше» — `advisory`. `route: tester` — если поведение воспроизводится автотестом. `route: discard_test` — если protected-тест не про ту находку. Для каждой находки из `open_findings` указать `resolved` / `still_open`. Финальное сообщение — только JSON `JudgeVerdict`.

**Tester:** на каждую находку написать один новый тестовый файл, который **падает на текущем коде именно по описанной причине**; не трогать нетестовые файлы; то, что автотестом не воспроизводится, — в `skipped`. Финальное сообщение — только JSON `TesterReport`.

### 5.5 `ledger.py`

```
.g3/runs/<YYYYMMDD-HHMMSS>-<slug>/
  ledger.jsonl                     одно событие на строку
  phase-02/round-03/
    player.prompt.md  player.raw.txt  player.json
    tests.log
    diff.patch
    judge.prompt.md   judge.raw.txt   judge.json
    tester.json
```

События: `run_start`, `phase_start`, `player`, `gate`, `judge`, `judge_tamper`, `tester`, `red_check`, `protected_violation`, `suite_violation`, `escalate`, `ship`, `rollback`, `stop`, `interrupted`, `run_end`.
Общие поля: `ts`, `phase`, `round`, `role`, `provider`, `model`, `duration_s`, `tokens_in`, `tokens_out` (если провайдер отдаёт), `exit_code`, `verdict`, `reason`.

`tero history` — таблица последних N ранов: дата, план, фаз SHIP/всего, раундов, эскалаций, причина остановки, длительность.

### 5.6 Коммит фазы

Сообщение по правилам пользователя: коротко, lowercase, без префиксов, без атрибуции. Формат: `<title фазы в lowercase, до 60 символов>`. Коммит содержит изменения фазы и отмеченные чекбоксы плана.

Deny-list (совпадение → не коммитить, `STOP("secrets")`): `.env`, `.env.*`, `*.pem`, `*.p12`, `id_rsa`, `id_ed25519`, `credentials.json`, `*secret*`, `*credentials*`. Это не scope-гейт (S10), а предохранитель.

---

## 6. Конфиг, меню, CLI

### 6.1 `Config` (все поля v4)

```python
plan_file: str = "requirements.md"
working_dir: str = "."
player_provider: str = "muse";         player_model: str = "muse-spark-1.3"
judge_provider: str = "codex";         judge_model: str = "gpt-5.6-terra"
tester_provider: str = "gemini";       tester_model: str = "gemini-3.8-flash"
escalation_provider: str = "claude";   escalation_model: str = "opus"   # пусто = без эскалации
test_command: str = "";                test_command_scoped: str = ""    # пусто = автодетект
player_timeout_s: int = 600;  judge_timeout_s: int = 600;  tester_timeout_s: int = 300;  test_timeout_s: int = 900
max_rounds: int = 15   # предохранитель раундов фазы; не старый duel max_rounds=3
verbose: bool = False
```

Провайдеры: `muse` | `claude` | `codex` | `gemini` | `opencode` | `cursor`.
Алиасы как сейчас в `_normalize_provider_name`: `spark`/`muse-code` → `muse`; `cursor-cli`/`cursor-headless`/`headless` → `cursor`. Алиасов `zai` / `kilo` нет.

Порядок разрешения прежний: defaults → `~/.g3/config.yaml` → `.g3/config.yaml` → env → CLI.
Неизвестное имя провайдера на любом слое → отказ старта, код 1, без подстановки дефолта (S12).

Уходят все поля `batch_*`, `coach_*`, `review_*`, `ldb_*`, `*_fallback_chain`, `chain_*`, `context_limit`, `compact_threshold`, `max_continuation_attempts`, `max_turns`, `autonomous`, а также поля duel-эпохи (`agent_a*`, `agent_b*`, `worktree_mode`, `run_*`, `judge`, `max_rounds` старого смысла, `timeout_s`, `ask_feedback`). Секция `providers:` в yaml (мультиаккаунты, `type`) остаётся.

### 6.2 Меню

```
▶   Запустить                                  ← сохраняет выбор в ~/.g3/config.yaml

─── роли ─────────────────────────────────
    Player:      muse (muse-spark-1.3)
    Judge:       codex (gpt-5.6-terra)
    Tester:      gemini (gemini-3.8-flash)
    Escalation:  claude (opus)                 ← можно «выкл»

─── план ─────────────────────────────────
    Файл плана:  plan.md   (фаз 6, готово 2)
    Тесты:       python -m pytest -q  (авто)   ← «не найдены» / «без скоупа: tester выкл»

─────────────────────────────────────────
✗   Выход
```

Выбор роли: провайдер, затем модель (пресеты + свой ID), как сейчас в `_questionary_select_provider_model`. Пресеты — текущие `MUSE_MODEL_PRESETS`, `CODEX_MODEL_PRESETS`, `GEMINI_MODEL_PRESETS`, `CURSOR_MODEL_PRESETS`, `CLAUDE_MODEL_PRESETS`, `OPENCODE_MODEL_PRESETS`. Fallback-меню без questionary — те же пункты.

### 6.3 CLI

```
tero go [-p PLAN] [-w DIR] [--no-menu] [-v]
        [--player P[:MODEL]] [--judge P[:MODEL]] [--tester P[:MODEL]]
        [--escalation P[:MODEL] | --no-escalation]
        [--test-command CMD] [--test-command-scoped CMD] [--max-rounds N]
tero history [-w DIR] [--limit N]
```

Коды выхода: `0` — все фазы SHIP; `1` — ошибка старта; `2` — стоп (stuck / fuse / needs_user / provider / secrets); `130` — Ctrl+C.

---

## 7. Живой вывод

Потоковый показ действий агента остаётся (урезанный `streaming.py`). Плюс одна строка на событие цикла:

```
[фаза 2/6 · раунд 3] player  muse(muse-spark-1.3)     → done, 3 файла
[фаза 2/6 · раунд 3] тесты   pytest                    → зелёные (41 passed)
[фаза 2/6 · раунд 3] judge   codex(gpt-5.6-terra)      → NO_SHIP: 2 blocking (1→tester), F1 resolved
[фаза 2/6 · раунд 3] tester  gemini(gemini-3.8-flash)  → 1 тест, красный ✓
[фаза 2/6]           SHIP за 4 раунда → commit a1b2c3d
```

---

## 8. Что удаляется

**Модули `src/`:** `batch_executor.py`, `coach_player.py`, `feedback.py`, `turn_runner.py`, `role_router.py`, `context_manager.py`, `runtime_controls.py`, `plan_tracker.py`, `prompts.py`, `learning/`, `orchestrator.py`, `duel.py`, `judge.py`, `bug_detector.py`, `state.py`, `worktree.py`, `utils/`, `personas/`, `ai/`, `ldb/`, `providers/chain.py`.

**Остаются (с правками):** `providers/*` включая уже существующие `muse.py` и `cursor.py` (кроме `chain.py`; добавить `output_schema` и `read_only` тем, у кого есть флаги), `config.py` (урезать), `menu.py` (переписать под 6.2), `cli_entry.py` (урезать), `process_guard.py`, `errors.py`, `constants.py` (урезать), `streaming.py` (урезать).

**Корень репо (чужое и старое):** `g3.py`, `bugs.md`, `bugs/`, `bugs_final.md`, `plan-bugfix-all.md`, `requirements.md` (план ldb), `config/settings.json`, `.env.example` (оба от CareerBot), `.superpowers/brainstorm/`, файл `tests/test_bug.py` с обратной кавычкой на конце имени.

**Тесты:** остаются провайдерные (`test_codex_large_prompt`, `test_codex_token_tracking`, `test_opencode_provider`, `test_gemini_provider`, `test_muse_provider`, `test_cursor_provider`, `test_subprocess_runner`, `test_message_normalization`, `test_legacy_provider_compat`), при необходимости с правками. Меню и CLI переписываются. Остальное удаляется вместе с кодом, который эти тесты проверяли.

**Зависимости `pyproject.toml`:** убрать `astroid`, `astunparse`, `astor`, `graphviz`; добавить `jsonschema`.

**Документы:** старые specs/plans в `docs/superpowers/` остаются как история. README переписывается (скилл `readme`): он сейчас построен вокруг ldb.

---

## 9. Тестирование v4

- **Юнит, без моков:** `plan` (секции, преамбула, без заголовков, секции без чекбоксов, атомарная запись), `schemas` (каждое смысловое правило из 4.2, включая `discard_test`), `gates` (детект по фикстурам-репо, pytest-fingerprint, `None` вне pytest), `git` (все операции на временном репо, deny-list), извлечение JSON в `agents` (fenced / bare / мусор вокруг / невалидный).
- **Интеграция `phase.py`** с `FakeAgents` (скриптованные ответы, которые правят файлы) на временном git-репо с настоящим pytest:
  1. SHIP с первого раунда → коммит, чекбоксы `[x]`.
  2. NO_SHIP → tester (красный тест) → player → SHIP; тест в коммите.
  3. Тест tester'а сразу зелёный → файл удалён, находка ушла текстом.
  4. Player меняет защищённый тест tester'а → файл восстановлен, раунд = `NO_PROGRESS`.
  5. Player удаляет тестовый файл с checkpoint → файл восстановлен, раунд = `NO_PROGRESS`.
  6. Судья меняет файл → дерево восстановлено, событие `judge_tamper`.
  7. Нет прогресса → эскалация → нет прогресса → откат на checkpoint, стоп, код выхода 2.
  8. Красный гейт с тем же fingerprint дважды → `NO_PROGRESS`.
  9. Два красных гейта подряд при неизменном дереве и `fingerprint is None` → `NO_PROGRESS`.
  10. `NEEDS_USER` → откат, стоп, вопрос в журнале.
  11. Невалидный JSON судьи → ремонтный вызов → валидный.
  12. `max_rounds` → откат, стоп `fuse`.
  13. `discard_test` → protected-файл удалён, находка ушла player'у.
  14. В индексе `.env` на SHIP → стоп `secrets`, коммита нет.
- **`run.py`:** грязное дерево → отказ; красные тесты на старте → отказ; `player_provider: zai` → отказ, код 1; продолжение на существующей ветке пропускает готовые фазы.
- **Ручной smoke:** игрушечный репо с планом из 3 фаз на реальных провайдерах (player muse/spark-1.3, judge codex/terra, tester gemini/3.8-flash).

---

## 10. Порядок реализации

0. **Спайк структурированного вывода** на живых CLI. Цель — в каком событии приходит финальный текст/JSON. Итог — заметка в этом спеке (уточнение таблицы 5.3).
   - `muse exec --json --yolo` — native schema нет; найти событие с финальным текстом.
   - `agent -p --force --trust --output-format stream-json` — то же для Cursor.
   - `claude -p --output-format stream-json --json-schema`.
   - `codex exec --json --output-schema`.
   ZAI/Kilo не спайкать: провайдеров нет.
1. `src/loop/`: `schemas` → `plan` → `gates` → `git` → `ledger` → `agents` → `phase` → `run`, у каждого модуля свои тесты.
2. Провайдеры: `output_schema` / `read_only` у Claude и Codex; у Muse/Cursor/Gemini/OpenCode — принять и проигнорировать.
3. Новые `config` / `menu` / `cli_entry` переключаются на `src/loop/run.py`.
4. Удаление по разделу 8, зависимости.
5. Ручной smoke (раздел 9).
6. README.

---

## 11. Вне скоупа

- Новые горячие клавиши во время рана (D11, отдельный спек).
- Параллельные фазы и worktree.
- Набор эталонных задач для сравнения конфигураций.
- Уведомления (Telegram) о стопе.
- Переименование `g3` → `tero` (пакет, `.g3/`, `~/.g3/`).
- Учёт стоимости в долларах: пока только токены, если провайдер их отдаёт.
- Обновление скилла tero v3 (`~/.claude/skills/tero`) под этот цикл — отдельная задача после посадки ядра.
