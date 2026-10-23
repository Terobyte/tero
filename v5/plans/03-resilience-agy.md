# tero v5, трек T, шаг 2б: устойчивость вызовов, компакт, провайдер agy, экзамен судей (слой 1)

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Это задачи T8, T8c, T9a и слой 1 T9b из
`v5/tero-master-plan.md`; спека — `v5/tero-resilience-spec.md` §3, §4, §5 (слой 1), §7 и `v5/tero-master-spec.md`
(блок «Единые решения» главнее частей A/B/C). Шаг 2а (`v5/plans/02-snapshots-judge-pending.md`) уже сделан:
снимки `refs/tero/<run_id>/...` (`snapshot.take_snapshot`, `tree_of`, `diff_for`, `restore_snapshot`),
`_rollback_with_snapshot`, `judge_pending` после трёх сбоев судьи, `tero review`, `tero resume` с судьи,
раунды-продолжения после падения исполнителя, K_max.

Главный принцип шага: **вызов роли не умирает молча и не висит вечно.** Зависший CLI убивается по тишине,
сетевой сбой пережидается с потолком, протухший вход останавливает сразу с понятным текстом, длинный
контекст исполнителя меняется на свежий вызов без потери работы, а Gemini работает через `agy`.

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Абзацы выше описывают цель всего шага из 11 фаз, а не требования к текущей фазе. Поведение, которое
делают следующие фазы, в текущей фазе не делать и не требовать: его отсутствие — не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** В песочнице судьи
перечисление процессов (`psutil`) запрещено, поэтому `tests/test_process_guard.py` и всё, что зависит от
`ProcessGuard.snapshot_pids`, там падает при исправном коде. Падение этих тестов в песочнице — не находка.
`src/process_guard.py` и `tests/test_process_guard.py` в этом шаге не меняются. Настоящих бинарей `agy`,
`claude`, `codex`, `opencode` в песочнице может не быть: тесты шага их не требуют, только stub-скрипты.

**Старые тесты, которые фаза явно называет в «Меняются старые тесты», переписываются под новое
поведение — это не ослабление набора.** Остальные старые тесты не правятся.

Правила для всех фаз:

- Каждый сбой сначала воспроизводится тестом, красный запуск виден, потом код.
- Настоящих LLM и настоящих CLI в тестах нет: исполнитель, судья, тестер — stub-скрипты в `tmp_path`
  (подставляются через ключ `command` конфига провайдера или через `PATH`) или фейки из `tests/loop/fakes.py`.
  Репо — одноразовые, в `tmp_path`.
- Ожидания в тестах — не настоящие: паузы, дедлайны и пороги подменяются (модульные константы, как
  `agents.HEARTBEAT_S`, или параметры конструктора). Ни один тест шага не спит дольше 5 секунд.
- Тесты: `PYTHONPATH=. python3 -m pytest -q` из корня. Подпроцессы CLI в коде и тестах —
  `[sys.executable, "-m", "src.cli_entry", ...]`, не `tero` из `PATH`.
- Запись `state.json`/`stream.jsonl` никогда не роняет прогон. Без HUD tero работает как раньше.
- Единый enum статуса: `starting | running | waiting_human | waiting_agent | waiting_limit | judge_pending | done | stopped`.
- Номера строк условные; ищи семантические точки («после возврата исполнителя», «вход в судью»).
- Новых зависимостей не добавлять. Папку `docs/` не трогать.
- Никаких сессий CLI: ни `--continue`, ни `--conversation`, ни `-s`. Каждый вызов роли — новый процесс.
- Каждое событие в `stream.jsonl`, которое добавляет этот шаг, несёт `phase`, `round`, `role` и `text`.
  Каждое изменение статуса пишется и в `state.json`, и в `stream.jsonl`, и в `ledger.jsonl`, одним
  вызовом-помощником из шага 2а.
- Секреты (`META_MODEL_API_KEY`, токены) не попадают ни в тексты ошибок, ни в `stream.jsonl`, ни в ledger.

## Виды ошибок провайдера

Менять `src/errors.py`, `src/loop/agents.py`; тесты в `tests/loop/test_resilience_misc.py`.

- `classify_error(exc: BaseException | None, text: str) -> str` в `src/errors.py`, один из
  `auth | quota | network | timeout | stall | other`. Порядок проверки: тип исключения, потом текст
  (без учёта регистра). Таблица образцов — модульная константа рядом с функцией:
  - `timeout`: `asyncio.TimeoutError`/`TimeoutError` (но не `ProviderStalled`, см. ниже).
  - `stall`: `ProviderStalled` (новый класс в `src/errors.py`, наследник `ProviderError`).
  - `auth`: «unauthorized», «401», «403», «not logged in», «please log in», «login required»,
    «authentication», «invalid api key», «token expired», «credentials».
  - `quota`: «quota», «usage limit», «insufficient credits», «billing», «exceeded your current».
  - `network`: `ConnectionError`, «econnreset», «etimedout», «econnrefused», «connection reset»,
    «connection refused», «network», «502», «503», «504», «429», «rate limit», «temporarily unavailable».
  - иначе `other`.
- `run_role` при неудаче возвращает `{"__unavailable__": True, "error": ..., "kind": <вид>}`; ключ `kind`
  добавляется, остальное как было.
- Текст в консоли и в `_halt_text`: для `timeout` — «`<role>` не уложился в таймаут `<N>` с», для `stall` —
  «`<role>`: нет вывода `<N>` с, процесс убит»; «провайдер недоступен» — только для `network` и `other`.
  `last_error` для таймаута — не пустая строка и не «TimeoutError», а этот же текст.
- Вне этой фазы: паузы и повторы (фаза 3), убийство по тишине (фаза 2). Повторы в `_call` в этой фазе
  не меняются.

- [ ] `test_classify_error_table`: по одному примеру на каждый вид (тип и текст) → ожидаемый вид; текст «Unauthorized» и «HTTP 401» → `auth`, «ECONNRESET» → `network`
- [ ] `test_timeout_is_not_provider_unavailable`: stub-провайдер висит дольше таймаута роли → `kind=timeout`, в выводе нет «провайдер недоступен», есть «не уложился в таймаут»
- [ ] реализовать

## Убийство по тишине: stall_timeout_s

Менять `src/config.py` (`Config`), `src/providers/subprocess_runner.py`, провайдеры, `src/loop/run.py`;
тесты в `tests/loop/test_resilience_misc.py`.

- `Config.stall_timeout_s: int = 900` (0 — выключено). Передаётся всем провайдерам, у которых
  `streams = True` (атрибут класса): `claude`, `codex`, `opencode`, `muse` (через opencode), `cursor`, `agy`
  (фаза 6). Провайдер с `streams = False` его не получает — для него только общий таймаут роли.
  Ключ `stall_timeout_s` в разделе `providers` конфига (как сейчас у opencode) главнее общего.
- `SubprocessExit` получает поле `stalled: bool`. Провайдер, увидев `stalled=True`, бросает
  `ProviderStalled(f"нет вывода {N} с")`, а не возвращает обрезанный текст и не бросает общую ошибку.
- `LiveAgents._call` на `ProviderStalled` и на таймаут **не повторяет** вызов: возвращает неудачу сразу
  (`kind=stall|timeout`). Дальше решает фаза, как для любого падения роли в шаге 2а (исполнитель →
  раунд-продолжение, судья → повтор на том же ref и `judge_pending`).
- Вне этой фазы: паузы при сетевых ошибках (фаза 3).

- [ ] `test_stall_kills_every_streaming_provider`: параметризован по `claude`, `codex`, `opencode`, `cursor`: stub-CLI печатает одну строку и спит → за ≈`stall_timeout_s` (в тесте 0.5) провайдер бросает `ProviderStalled`, процесс stub мёртв
- [ ] `test_stall_not_retried`: stub-провайдер бросает `ProviderStalled` → `_call` вызвал провайдер один раз, `kind=stall`
- [ ] `test_non_streaming_provider_gets_no_stall`: провайдер с `streams = False` не получает `stall_timeout` (проверка аргументов фабрики или конфига)
- [ ] реализовать

## Пауза при сетевой ошибке, стоп при входе и квоте

Менять `src/loop/agents.py` (`_call`), `src/loop/phase.py` (обработка неудачи роли);
тесты в `tests/loop/test_resilience_misc.py`.

- Модульные константы в `agents.py` (подменяются в тестах): `RETRY_DELAYS_S = (60, 120, 300, 600, 3600)`,
  `RETRY_DEADLINE_S = 7200`.
- По виду ошибки:
  - `network` → повтор с паузами из `RETRY_DELAYS_S` по очереди; не больше `len(RETRY_DELAYS_S)` повторов;
    повтор не начинается, если общее время вызова с учётом следующей паузы превысит `RETRY_DEADLINE_S`.
    Перед паузой — строка «`<role>`: сеть, повтор `<i>`/`<n>` через `<мин>` мин» и событие `retry`
    (`attempt`, `wait_s`, `error`) в `stream.jsonl`. Статус в `state.json` на время паузы не меняется.
  - `auth`, `quota` → без повторов, сразу неудача.
  - `timeout`, `stall` → без повторов (фаза 2).
  - `other` → как сейчас: один повтор через `retry_wait_s`. Поэтому старые тесты с `retry_wait_s=0` не меняются.
- Фаза решает только по полю `kind` из ответа `run_role`; текст `error` она заново не классифицирует.
  Нет `kind` (фейки в старых тестах, например `{"__unavailable__": True, "error": "quota exceeded"}` в
  `test_halt_writes_stopped_with_reason[provider_unavailable]`) → `other`, поведение прежнее.
- `auth`/`quota` в фазе:
  - у исполнителя и тестера → `_halt("provider_auth")` / `_halt("provider_quota")` (со снимком, как любой
    `_halt`), без раунда-продолжения. Текст: «`<provider>`: нужен вход (`<error>`)» / «`<provider>`: квота
    или лимит (`<error>`)».
  - у судьи → сразу `judge_pending` (не ждать трёх сбоев), `reason=judge_auth|judge_quota`, дерево и ref
    нетронуты, текст как выше + «`tero resume` после входа».
- Вне этой фазы: `NEEDS_USER` (фаза 4), pre-flight `agy` (фаза 7).

- [ ] `test_network_error_backoff_schedule`: stub падает с «ECONNRESET» 3 раза, потом отвечает → 4 вызова, паузы ровно первые три из `RETRY_DELAYS_S` (подменённый `asyncio.sleep` записывает их), успех
- [ ] `test_network_retries_capped`: stub падает всегда → вызовов `len(RETRY_DELAYS_S)+1`, `kind=network`
- [ ] `test_network_retries_stop_at_deadline`: `RETRY_DEADLINE_S` меньше суммы пауз → повторов меньше, общее время не превышает дедлайн (часы подменены)
- [ ] `test_auth_error_stops_without_waiting`: «401 Unauthorized» → один вызов, ни одной паузы, у исполнителя `stopped` с `provider_auth`, ref есть
- [ ] `test_judge_quota_goes_judge_pending_at_once`: судья падает с «quota» → `judge_pending` после первого же сбоя, `reason=judge_quota`, исполнитель вызван один раз
- [ ] реализовать

## NEEDS_USER без эскалации

Менять `src/loop/phase.py` (`_apply_verdict`, `_after_judge`, `_judge`), `src/loop/prompts.py`;
тесты в `tests/loop/test_resilience_misc.py`.

- Только когда эскалация не настроена (`cfg.escalation_provider` пуст, это и есть `--no-escalation`).
  С эскалацией поведение прежнее: `_halt("user")`.
- Без эскалации `NEEDS_USER` не останавливает фазу:
  1. первый `NEEDS_USER` → переспрос: тот же судья, тот же ref, в промпт судьи добавлен блок
     «Ты спросил: `<user_question>`. Человека нет. Реши сам: SHIP или NO_SHIP с замечаниями. NEEDS_USER
     снова не принимается.»;
  2. второй подряд → свежий вызов судьи с обычным промптом, без блока;
  3. третий подряд → `judge_pending`, `reason=needs_user`, `text` «нужен человек: `<user_question>`;
     `tero review`, `tero resume`». Отката нет, дерево и ref нетронуты.
- Исполнитель между этими вызовами не запускается, раунд не растёт, эскалация не включается.
- Счётчик `NEEDS_USER` свой, не общий со счётчиком сбоев судьи; любой другой валидный вердикт его обнуляет.
- Событие `needs_user` (`attempt`, `question`) в `stream.jsonl` и ledger на каждый `NEEDS_USER`.
- Меняются старые тесты: `tests/loop/test_phase.py::test_halt_writes_stopped_with_reason[user]` — он
  гоняет `NEEDS_USER` с `escalation_provider=""`. Для случая `user` в `cfg_kwargs` ставится
  `escalation_provider="codex"`, `escalation_model=""`; остальные случаи параметризации не меняются.
  `test_needs_user_rolls_back` и `tests/loop/test_hunt_loop.py` идут с эскалацией по умолчанию
  (`Config.escalation_provider="claude"`) и не меняются.

- [ ] `test_needs_user_reask_then_fresh_then_pending`: судья трижды `NEEDS_USER` → 3 вызова судьи, во 2-м промпте блок переспроса с вопросом, в 3-м его нет, итог `judge_pending` `needs_user`, исполнитель вызван один раз
- [ ] `test_needs_user_then_ship`: `NEEDS_USER`, затем SHIP → фаза сдана без нового вызова исполнителя
- [ ] `test_needs_user_with_escalation_unchanged`: эскалация настроена → `NEEDS_USER` даёт прежний стоп `user`
- [ ] реализовать

## Ключи config.yaml вне defaults

Менять `src/config.py`; тесты в `tests/loop/test_config.py`.

- Файл `config.yaml` (bundled, `~/.tero/`, проектный) с ключами верхнего уровня, кроме `defaults` и
  `providers`, → одно предупреждение `UserWarning` на файл: «`<путь>`: ключи вне `defaults:` не читаются:
  `[...]`». Ключи с именами полей `Config` в этом списке помечаются «→ перенеси в `defaults:`».
- Значения таких ключей по-прежнему не применяются.

- [ ] `test_top_level_keys_warn`: `player_provider: codex` на верхнем уровне → предупреждение с путём и подсказкой, `Config.player_provider` не изменился
- [ ] `test_known_sections_no_warning`: только `defaults` и `providers` → предупреждений нет
- [ ] реализовать

## Провайдер agy

Создать `src/providers/agy.py`, `tests/test_agy_provider.py`; менять `src/providers/__init__.py`, `src/config.py`.

Факты (сняты с `agy` 1.4.5 2026-10-23, опирайся на них):
- `agy -p <prompt> --model <m> --output-format stream-json --print-timeout <N>s` печатает NDJSON:
  - `{"event":"init","conversation_id":...,"init":{"model":...,"permission_mode":...}}`
  - `{"event":"step_update","step_update":{"step_index":i,"state":"ACTIVE|DONE","step_type":"agent_response","text_delta":"..."}}`
  - `{"event":"step_update","step_update":{"step_type":"tool","state":"ACTIVE","tool_name":"write_file","tool_info":{"name":...,"parameters":{...}}}}`
  - у `DONE`-шагов бывает `"usage":{"input_tokens":N,"output_tokens":N,...}`
  - последняя строка `{"event":"result","result":{"status":"SUCCESS|ERROR","response":"...","structured_output":{...},"error":"...","usage":{...}}}`
- С `--json-schema '<schema>'` готовый объект лежит в `result.structured_output`.
- Ошибка (например, неизвестная модель): код возврата 1, текст в stderr и `result.status="ERROR"`, `result.error`.
- `--mode plan`: модель не пишет в рабочий каталог (план кладёт в свой каталог `~/.gemini/...`).
- Без флагов `permission_mode` уже `always-proceed`.

Требования:
- `AgyConfig(command="agy", default_model="gemini-3.8-flash-high", print_timeout_s=3600, stall_timeout_s=...)`,
  `AgyProvider.streams = True`. Вывод сообщений — тем же способом, что у `opencode.py` (чтобы
  `normalize_message` и `_activity` в `agents.py` работали без изменений): текст из `text_delta`, вызовы
  инструментов — `tool_name` + `parameters`.
- argv: `[command, "-p", prompt, "--model", m, "--output-format", "stream-json", "--print-timeout",
  f"{print_timeout_s}s", "--disable-slash-commands"]` + `--json-schema <json>` если есть схема +
  `--effort <e>` если задан (`low|medium|high|xhigh|max`). Судья (`read_only=True`) → `--mode plan`;
  исполнитель и тестер → `--dangerously-skip-permissions`. `stdin` — `/dev/null`.
- Финальный текст: `json.dumps(structured_output)`, если он есть, иначе `response`. `status != "SUCCESS"`
  или код возврата ≠ 0 → `ProviderError(result.error или хвост stderr)`. Невалидный ответ судьи — обычный
  сбой судьи из шага 2а, без отдельного тихого повтора.
- Модель без суффикса уровня (`gemini-3.8-flash`) → `gemini-3.8-flash-high`.
- Не больше одного вызова `agy` одновременно в процессе: модульный `asyncio.Lock`.
- `create_provider("agy")` → `AgyProvider`; `create_provider("gemini")` → тоже `AgyProvider` с одним
  предупреждением «gemini CLI больше не используется, вызываю agy». `src/providers/gemini.py` и его тесты не трогать.
- `src/config.py`: `"agy"` в `KNOWN_PROVIDERS`; `family_of("agy:<model>")` — по модели, без модели → `gemini`.
- Вне этой фазы: pre-flight и проверка `agy models` (фаза 7).

- [ ] `test_agy_argv_judge_and_player`: argv судьи содержит `--mode plan` и не содержит `--dangerously-skip-permissions`; у исполнителя наоборот; `--print-timeout` с единицей (`3600s`)
- [ ] `test_agy_structured_output_is_final_text`: stub `agy` печатает поток из фактов выше → финальный текст = `structured_output`, строки инструментов видны через `_activity`
- [ ] `test_agy_error_result_raises`: stub печатает `result.status=ERROR` и выходит с 1 → `ProviderError` с текстом `error`
- [ ] `test_agy_garbage_is_judge_failure`: stub без `result` и без JSON → `run_role("judge")` даёт `__invalid__`
- [ ] `test_agy_one_call_at_a_time`: два параллельных вызова stub `agy` (stub пишет время старта/конца) → интервалы не пересекаются
- [ ] `test_gemini_routes_to_agy`: `create_provider("gemini")` → `AgyProvider`, предупреждение есть
- [ ] реализовать

## agy: проверка перед очередью

Менять `src/loop/run.py` (проверки на старте), `src/providers/agy.py`; тесты в `tests/test_agy_provider.py`.

- Если хоть одна роль идёт через `agy` (или `gemini`), до первой фазы, в блоке проверок провайдеров:
  1. `agy models` (таймаут 30 с): первый столбец строк — имена; модели роли нет в списке → предупреждение
     «`agy`: модели `<m>` нет в `agy models`», прогон идёт.
  2. `agy -p "ок" --model <m> --output-format json --print-timeout 60s` (таймаут процесса 90 с, `stdin=/dev/null`):
     `status != "SUCCESS"`, код ≠ 0 или таймаут → стоп до очереди: `status=stopped`, текст
     «`agy` не отвечает: `<error>`. Войди: запусти `agy` в терминале», код выхода 1. Не зависание.
- Вне этой фазы: компакт (фазы 8–10).

- [ ] `test_agy_preflight_ok`: stub отвечает SUCCESS → прогон стартует, `agy` вызван до исполнителя
- [ ] `test_agy_preflight_no_login_stops`: stub печатает ERROR «not logged in» → `stopped`, код 1, текст с «Войди», исполнитель не вызван
- [ ] `test_agy_preflight_hang_stops`: stub висит → стоп по таймауту (подменён на 1 с), не зависание
- [ ] `test_agy_unknown_model_warns`: модели нет в `agy models` → предупреждение, прогон идёт
- [ ] реализовать

## Счётчик контекста

Создать `src/loop/compact.py`, `tests/loop/test_compact.py`; менять `src/config.py`, провайдеры, `src/loop/agents.py`.

- Провайдер держит `last_context_tokens: int` (0, если не знает) и обновляет по мере потока:
  `opencode`/`muse` — `step_finish.part.tokens.input`; `codex` — из уже существующего учёта токенов;
  `agy` — `usage.input_tokens` из `DONE`-шагов; `claude` — `input_tokens + cache_read_input_tokens` из `usage`.
- `Config.compact_player_at_tokens: int = 0` и `Config.compact_judge_at_tokens: int = 0` (0 — выключено,
  по умолчанию выключено; включать только между прогонами). Значения пишутся в ledger событием
  `compact_config` в начале прогона.
- `compact.should_compact(tokens: int, at_tokens: int, requested: bool) -> bool`: `requested` или
  (`at_tokens > 0` и `tokens >= at_tokens`).
- `muse` отдаёт `last_context_tokens` своего внутреннего opencode-провайдера.
- `LiveAgents.run_role` принимает `compact_check: Callable[[int], bool] | None`. После каждого сообщения
  провайдера (это и есть «граница шага») зовёт `compact_check(provider.last_context_tokens)`; `True` → прекращает чтение потока, процессы убиваются
  как обычно (`guard.kill_new_processes`), возврат `{"__compacted__": True, "tokens": N}`.
- Вне этой фазы: что фаза делает с `__compacted__` (фаза 9), команда `tero compact` (фаза 10).

- [ ] `test_should_compact`: порог, ноль = выключено, `requested` побеждает
- [ ] `test_run_role_stops_at_threshold`: stub-провайдер растит `last_context_tokens` по шагам → после шага, где порог превышен, чтение прекращено, возврат `__compacted__` с числом токенов
- [ ] `test_opencode_and_agy_report_tokens`: stub-потоки `opencode` и `agy` → `last_context_tokens` равен числу из последнего события
- [ ] реализовать

## Мягкая смена сессии исполнителя

Менять `src/loop/phase.py` (`_player`, `run`), `src/loop/prompts.py`; тесты в `tests/loop/test_compact.py`.

- Исполнитель вернул `__compacted__` → снимок (как после любого возврата исполнителя, шаг 2а) → событие
  `compact` (`role`, `tokens`, `ref`) в `stream.jsonl` и ledger → **новый вызов исполнителя в том же раунде**
  с промптом: обычный `player_prompt` + блок «часть работы уже сделана» с `git diff base..ref` (тот же,
  что у раунда-продолжения шага 2а, с той же обрезкой 20 000 символов) + открытые замечания судьи
  дословно (как они уже идут в `player_prompt`).
- Гейт и судья на снимке компакта не запускаются: следующий шаг — исполнитель.
- Компакт не считается ни раундом, ни падением исполнителя (правило «два падения подряд»), ни раундом
  для K_max. Раунд-счётчик не растёт.
- Не больше 3 компактов за раунд; после третьего `compact_check` в этом раунде не передаётся (исполнитель
  доживает вызов как раньше).
- Судья: `__compacted__` у судьи → новый вызов судьи на том же ref, не считается сбоем судьи; не больше
  одного компакта судьи на раунд.
- Вне этой фазы: `tero compact`, `--autocompact` у `claude` (фаза 10).

- [ ] `test_compact_player_gets_brief_with_diff`: stub-исполнитель №1 пишет файл и превышает порог → stub №2 получил промпт с его diff, файл в ref, гейт между ними не вызван
- [ ] `test_compact_keeps_findings_verbatim`: раунд 2 с открытым замечанием → в промпте после компакта текст замечания дословно
- [ ] `test_compact_does_not_grow_round_or_kmax`: компакт → `round` тот же, счётчик падений и K_max не изменились
- [ ] `test_compact_capped_per_round`: исполнитель превышает порог каждый вызов → 3 компакта, 4-й вызов доживает до конца
- [ ] `test_compact_judge_restarts_on_same_ref`: судья вернул `__compacted__` → второй вызов судьи, исполнитель не вызван, счётчик сбоев судьи 0
- [ ] реализовать

## tero compact и autocompact у claude

Менять `src/cli_entry.py`, `src/loop/phase.py`, `src/loop/state.py`, `src/providers/claude_native.py`;
тесты в `tests/loop/test_compact.py`, `tests/test_cli.py`.

- `tero compact [-w <repo>] [--run <папка>] [--role player|judge]` (по умолчанию `player`, прогон — последний
  из `.tero/runs`): пишет файл `<run_dir>/inbox/compact.json` (`{"role": ..., "ts": ...}`) и печатает
  «запрошен компакт `<role>`». `state.json` команда не пишет (его пишет только владелец прогона).
  Статус прогона не `running` → текст «прогон не идёт» и код 1.
- Владелец: `compact_check` текущей роли видит файл → `requested=True`; в `state.json` ставится
  `compact_requested: "<role>"`, файл удаляется; поле очищается, когда стартовал новый вызов роли.
  Файл для другой роли не трогается до вызова этой роли.
- `claude`: если для роли `compact_*_at_tokens > 0`, в argv `--autocompact <N>` (N в пределах 100000–1000000,
  иначе предупреждение и без флага), и мягкая смена по порогу для этой роли не включается (компактит сам CLI);
  `tero compact` для `claude` работает как для всех. При 0 argv как сейчас.

- [ ] `test_tero_compact_switches_at_step_boundary`: прогон с фейками идёт, в `inbox/` кладётся `compact.json` → исполнитель сменён на границе шага, ref записан, файла больше нет, `compact_requested` очищен
- [ ] `test_tero_compact_refuses_when_not_running`: `status=done` → код 1 (через CLI-подпроцесс)
- [ ] `test_claude_autocompact_in_argv`: порог 200000 → в argv `--autocompact 200000`; порог 0 → флага нет
- [ ] реализовать

## Экзамен судей: слой 1 (адаптеры)

Создать `tools/__init__.py`, `tools/judge_exam/__init__.py`, `tools/judge_exam/run.py`,
`tests/test_judge_exam_adapter.py`.

- `exam_adapter(provider: str, model: str, factory=None) -> dict`: одноразовое репо в временной папке с
  тривиальным диффом (одна функция с очевидной правкой), вызов роли `judge` через `LiveAgents` и
  `judge_prompt` (те же классы `Provider`, что в прогоне) → `{"provider", "model", "ok", "verdict",
  "seconds", "error"}`. `ok` — ответ прошёл схему судьи.
- `python3 -m tools.judge_exam.run --adapters [--judge provider:model ...]` печатает таблицу
  `провайдер | модель | ok | вердикт | секунды | ошибка`. Настоящий запуск — только руками, не в гейте.
- Слой 2 (кейсы, метки, пороги) в этом шаге не делается: это трек человека.

- [ ] `test_adapter_valid_verdict_each_cli`: параметризован по `agy`, `claude`, `codex`, `opencode`: stub-CLI в формате этого CLI (форматы — как в существующих тестах провайдеров и фактах фазы «Провайдер agy») отдаёт валидный вердикт → `ok=True`
- [ ] `test_adapter_garbage_is_not_ok`: stub отдаёт мусор → `ok=False`, в `error` «не по схеме»
- [ ] `test_adapter_table_output`: `--adapters` с подменённой фабрикой печатает строку таблицы на каждого судью
- [ ] реализовать
