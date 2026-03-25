# GLM-5 `/compact` — Root Cause And Fix Plan

Date: 2026-03-19

## TL;DR

Проблема не в одной точке.

1. В `tero` нет доведённого до конца пользовательского manual-compact пути.
   В коде есть только `ESC=compact` через `RuntimeControls`, но `CoachPlayerSession.run()` не применяет этот флаг перед следующим player turn.
2. Для `ccg`/`glm-5` уже есть только auto-compaction внутри Claude Agent SDK.
   Это полезно для переполнения контекста, но не заменяет явный `/compact`.
3. В `ai-cli-proxy-api` путь `/v1/responses/compact` физически не поддержан для `iflow`.
   Если GLM идёт через `iflow`, executor сразу возвращает `501 NotImplemented`.
4. Тестовая база вокруг continuation/compaction недоверифицируется:
   в Python-части есть async-тесты на compaction flow, но в `pyproject.toml` нет `pytest-asyncio`.

Итого: основной root cause в продукте — нет единого, завершённого manual compaction flow; отдельный hard blocker для GLM через proxy — `iflow` не умеет `responses/compact`.

## Что подтверждает root cause

### 1. Manual compact поднимается, но не исполняется

- `src/runtime_controls.py:16-24` трактует standalone `ESC` как `"compact"`.
- `src/runtime_controls.py:330-401` хранит `_compact_requested` и выставляет его в `apply_pending()`.
- `src/coach_player.py:360-423` вызывает `self._runtime.apply_pending(self)`, но дальше сразу строит `player_prompt` и запускает `_run_turn()` без проверки `self._runtime.compact_requested`.

Следствие: UI уже обещает compact (`[ESC=compact]`), но session loop не доводит это действие до реального сжатия контекста.

### 2. Для GLM-5 есть только auto-compact, а не user-invoked compact

- `src/providers/ccg.py:22-40` регистрирует `PreCompact` hook.
- `src/providers/ccg.py:76-91` вычисляет `autoCompactThreshold` для моделей вроде GLM-5 с меньшим окном контекста.
- `src/config.py:250-257` задаёт для `glm-5` окно `98_000`.

Следствие: `ccg` умеет автоматическое сжатие внутри Claude SDK, но в приложении нет завершённого явного `/compact`-сценария.

### 3. Planned continuation/compact flow есть в тестах и дизайне, но отсутствует в runtime

- `tests/test_continuation_agent.py:113-163` уже ожидает `_run_with_continuation()` и compaction retry при высоком token usage.
- В `src/coach_player.py` есть только вызов `getattr(self, "_run_with_continuation", self._run_turn)` на `src/coach_player.py:568-576`, но определения `_run_with_continuation()` в файле нет.
- Дизайн в `docs/superpowers/specs/2026-03-17-runtime-controls-design.md` прямо описывает, что compact-флаг должен проверяться перед следующим player turn.

Следствие: функциональность была задумана и частично обвязана тестами/дизайном, но не интегрирована в рабочий цикл.

### 4. GLM через proxy-path падает ещё раньше: `iflow` не поддерживает compact endpoint

- `ai-cli-proxy-api/sdk/api/handlers/openai/openai_responses_handlers.go:95-127` принимает `/v1/responses/compact` и форвардит его как `Alt="responses/compact"`.
- `ai-cli-proxy-api/internal/runtime/executor/iflow_executor.go:74-77` и `:177-180` немедленно возвращает `"/responses/compact not supported"`.
- Отдельного теста на `iflow` compact-ветку нет: repo search по `*test.go` не нашёл ни одного случая с `iflow` + `compact`.

Следствие: если GLM маршрутизируется через `iflow`, manual compaction сломан на уровне executor-а независимо от app-layer логики.

### 5. Тестовая защита неполная

- `pyproject.toml:5-12` содержит только `pyyaml>=6.0`; async test dependency отсутствует.
- Запуск `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_ccg_compact_hook.py tests/test_context_manager.py tests/test_continuation_agent.py` падает не по бизнес-логике, а потому что async tests cannot run without plugin.

Следствие: зона compaction/continuation сейчас хуже защищена регресс-тестами, чем должна.

## Почему это особенно заметно на GLM-5

- `ccg`/`glm-5` является дефолтным путём во многих местах (`src/config.py`, `src/runtime_controls.py`).
- Окно контекста у `glm-5` меньше, чем у Claude-family (`98k` против `200k`), поэтому необходимость manual compact возникает чаще.
- Пользователь видит в UI обещание compaction, но для дефолтной модели это обещание не доведено до работающего сценария.

## Настоящий root cause

### Primary

В проекте нет единого compaction contract-а между UI, session loop и provider-ами.

Сейчас есть три разрозненных куска:

- UI-сигнал (`ESC=compact`)
- provider-level auto-compact для `ccg`
- proxy-level `/responses/compact`

Но нет общей orchestration logic, которая гарантирует: "пользователь запросил compact -> бот сжал контекст -> следующий turn продолжился с корректным summary/fallback".

### Secondary

`iflow` рассматривает `responses/compact` как unsupported feature, а не как capability, которую можно эмулировать fallback-ом.

## План решения

### Phase 1. Свести manual compaction к одному contract-у

Цель: один entrypoint для compact, независимо от того, trigger пришёл из `/compact`, `ESC`, continuation retry или proxy endpoint.

Сделать:

1. Добавить единый service-layer API, например в `src/context_manager.py` или новом `src/compaction.py`:
   - `build_manual_compact_summary(messages) -> str`
   - `build_manual_compact_prompt(summary, current_step, feedback) -> str`
   - `maybe_compact_for_next_turn(...) -> CompactResult`
2. Разделить два типа compaction:
   - native compaction: provider умеет compact сам
   - fallback compaction: мы строим summary и продолжаем следующий turn с этим summary
3. Зафиксировать capability matrix:
   - `ccg`: native auto-compact mid-turn + fallback manual compact
   - `codex`: native `/responses/compact` там, где executor умеет
   - `iflow`: fallback compact, пока native endpoint не реализован

Ожидаемый результат: manual compact больше не зависит от частного поведения одного provider-а.

### Phase 2. Доделать compact flow в `CoachPlayerSession`

Сделать:

1. В `src/coach_player.py` перед каждым player turn:
   - читать `self._runtime.compact_requested`
   - вызывать `self._runtime.clear_compact()`
   - если есть `self._last_turn_result`, подменять обычный `player_prompt` на compact continuation prompt
2. Хранить `self._last_turn_result` после каждого успешного player turn.
3. Реально реализовать `_run_with_continuation()`:
   - первый запуск обычный
   - если нет нужных completion markers или контекст переполнен, делать compact-summary retry
   - использовать общий compaction service из Phase 1
4. Убедиться, что compact работает и в step-loop, и в review/fix loop.

Ожидаемый результат: user-triggered compact начнёт работать в основном runtime, а не только существовать в виде флага.

### Phase 3. Добавить настоящий `/compact` user entrypoint

Сейчас slash-command parser в `src/` не найден.

Сделать:

1. Выбрать canonical UX:
   - `/compact` как текстовая команда
   - `ESC=compact` как hotkey
   - оба варианта на один backend flow
2. Если продукт реально принимает текстовые команды, добавить parser/router для slash-команд.
3. `/compact` не должен просто уходить в модель как user text; он должен интерпретироваться приложением и вызывать manual compaction flow.

Ожидаемый результат: `/compact` становится feature приложения, а не надеждой на то, что модель “сама поймёт”.

### Phase 4. Закрыть GLM/iFlow blocker в proxy

Сделать:

1. В `ai-cli-proxy-api/internal/runtime/executor/iflow_executor.go` заменить `501 NotImplemented` на fallback execution path.
2. Fallback path для `iflow`:
   - принимать `responses/compact`
   - строить summarization request в обычный `chat/completions`
   - возвращать OpenAI-compatible payload вида `response.compaction`
3. Если payload содержит не всё, что нужно для lossless compaction, документировать минимально поддерживаемый контракт.
4. Добавить capability flag в executor layer, чтобы handler мог логировать `native` vs `emulated`.

Ожидаемый результат: GLM через proxy-path перестанет быть исключением и начнёт compact-иться хотя бы через эмуляцию.

### Phase 5. Закрыть тестовые дыры

Сделать:

1. Добавить `pytest-asyncio` в dev/test dependencies.
2. Починить и прогнать:
   - `tests/test_ccg_compact_hook.py`
   - `tests/test_context_manager.py`
   - `tests/test_continuation_agent.py`
3. Добавить новые Python tests:
   - `ESC` -> `compact_requested=True` -> следующий player turn использует compact prompt
   - `compact_requested=True` при `self._last_turn_result is None` не падает
   - `_run_with_continuation()` реально использует compact summary, а не уходит в бесконечный retry
4. Добавить Go tests:
   - `iflow` executor compact fallback вместо `501`
   - handler `/v1/responses/compact` + `iflow` integration
   - GLM alias/model routing не теряет compact-поведение

Ожидаемый результат: compaction станет защищён end-to-end тестами, а не только дизайном и полуготовыми unit tests.

## Порядок внедрения

1. Починить app-layer manual compaction в `src/`.
2. Реализовать `_run_with_continuation()` и общий compaction service.
3. Добавить slash-command entrypoint `/compact`.
4. Потом закрыть `iflow` compact fallback в proxy.
5. После этого добить тесты и прогнать оба контура.

Такой порядок даёт самый быстрый пользовательский эффект: default path (`ccg`/`glm-5`) начнёт работать раньше, даже если proxy-path ещё не закончен.

## Acceptance Criteria

- `ESC=compact` реально меняет следующий player turn на compact continuation flow.
- `/compact` не уходит в модель как обычный текст, а обрабатывается приложением.
- Для default `ccg`/`glm-5` manual compact работает без зависимости от autoCompactThreshold.
- Для `iflow` вызов `/v1/responses/compact` больше не возвращает `501`.
- Async tests на compaction запускаются в CI/локально.
- У нас есть хотя бы один end-to-end test на GLM compact path.

## Verification Notes

Проверено во время расследования:

- `go test ./sdk/api/handlers/openai -run Compact` -> `ok`
- `go test ./internal/runtime/executor -run CompactPassthrough` -> `ok`
- Python async tests на compaction сейчас невалидно запускаются без async plugin

## Рекомендуемый следующий шаг

Начать с app-layer фикса в `src/coach_player.py` и `src/context_manager.py`, потому что это убирает основную пользовательскую боль для default `glm-5` path быстрее всего.
