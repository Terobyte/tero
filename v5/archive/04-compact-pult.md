# tero v5, трек T, шаг 3: компакт, экзамен судей (слой 1), ядро пульта

> **Архив.** Фазы 1–8 этого плана сданы. Фазы 9–15 перенесены без изменений по сути в
> `v5/plans/06-pult-decisions.md`.

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Это хвост шага 2б (T8c, слой 1 T9b —
фазы 1–4, перенесены из `v5/plans/03-resilience-agy.md` без изменений) и шаг 3 (T10, T11a/b/c, T12 —
фазы 5–15) из `v5/tero-master-plan.md`. Спека — `v5/tero-resilience-spec.md` §7 и §5 (слой 1),
`v5/tero-master-spec.md` часть B «Ядро tero» (блок «Единые решения» главнее частей A/B/C).

Уже сделано: `state.json` с enum статуса, `stream.jsonl`, HUD и реестр `~/.tero/registry/`
(`src/hud/registry.py`: `pid_is_alive`, `is_dead`), лок репо `src/loop/lock.py` (`acquire(repo)`, flock на
`<repo>/.tero/run.lock`), снимки `refs/tero/<run_id>/...` (`src/loop/snapshot.py`), `_rollback_with_snapshot`,
`judge_pending` (сейчас — выход с кодом 3), `tero review`, `tero resume` (только с `judge_pending`),
виды ошибок провайдера, убийство по тишине, провайдер `agy` и его pre-flight.

Главный принцип шага: **прогон не умирает молча и не выбрасывает работу; любой стоп — точка решения,
которую может закрыть человек, агент или автопилот.**

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Абзацы выше описывают цель всего шага из 15 фаз, а не требования к текущей фазе. Поведение, которое
делают следующие фазы, в текущей фазе не делать и не требовать: его отсутствие — не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** В песочнице судьи
перечисление процессов (`psutil`) запрещено, поэтому `tests/test_process_guard.py` и всё, что зависит от
`ProcessGuard.snapshot_pids`, там падает при исправном коде. Падение этих тестов в песочнице — не находка.
`src/process_guard.py` и `tests/test_process_guard.py` в этом шаге не меняются. Настоящих бинарей `agy`,
`claude`, `codex`, `opencode`, `caffeinate` в песочнице может не быть: тесты шага их не требуют, только stub-скрипты.

**Старые тесты, которые фаза явно называет в «Меняются старые тесты», переписываются под новое
поведение — это не ослабление набора.** Остальные старые тесты не правятся. Если чек-лист фазы
конфликтует со старым тестом, которого нет в её списке, — старое поведение сохраняется под флагом
(см. `Config.decisions` ниже), а не ломается.

**Точки решения включаются флагом.** `Config.decisions: bool = False`. При `False` поведение прежнее:
`_halt` откатывает фазу и пишет `stopped`, `judge_pending` выходит с кодом 3, — все старые тесты
остаются зелёными без правок. Новое поведение фаз 8–14 работает только при `decisions=True`; тесты
этих фаз ставят флаг явно. CLI: `tero go --decisions` включает, `tero go --detach` включает всегда.

Правила для всех фаз:

- Каждый сбой сначала воспроизводится тестом, красный запуск виден, потом код.
- Настоящих LLM и настоящих CLI в тестах нет: исполнитель, судья, тестер — stub-скрипты в `tmp_path`
  (подставляются через ключ `command` конфига провайдера или через `PATH`) или фейки из `tests/loop/fakes.py`.
  Репо — одноразовые, в `tmp_path`.
- **Ни один тест не запускает настоящий `agy`** (`~/.local/bin/agy` открывает вход Google в браузере).
  Прогоны `run()` в тестах не передают `agy_preflight`; stub `agy` кладётся в `PATH` раньше настоящего.
- Ожидания в тестах — не настоящие: паузы, дедлайны, пороги и часы подменяются (модульные константы,
  как `agents.HEARTBEAT_S`, параметры конструктора или функция-часы `clock=`). Ни один тест не спит
  дольше 5 секунд.
- Тесты: `PYTHONPATH=. python3 -m pytest -q` из корня. Подпроцессы CLI в коде и тестах —
  `[sys.executable, "-m", "src.cli_entry", ...]`, не `tero` из `PATH`.
- Запись `state.json`/`stream.jsonl`/`pulse` никогда не роняет прогон. Без HUD tero работает как раньше.
- Единый enum статуса: `starting | running | waiting_human | waiting_agent | waiting_limit | judge_pending | done | stopped`.
- `state.json` пишет только процесс-владелец (атомарно: временный файл + rename, как сейчас `RunState`).
  Все остальные команды (`wait`, `status`, `answer`, `stop`, `compact`) только читают его и пишут в
  `<run_dir>/inbox/`.
- Внутри владельца один `threading.Lock` на запись `state.json` и git-операции.
- Номера строк условные; ищи семантические точки («после возврата исполнителя», «вход в судью», «`_halt`»).
- Новых зависимостей не добавлять. Папку `docs/` не трогать.
- Никаких сессий CLI: ни `--continue`, ни `--conversation`, ни `-s`. Каждый вызов роли — новый процесс.
- Каждое событие в `stream.jsonl`, которое добавляет этот шаг, несёт `phase`, `round`, `role` и `text`.
  Каждое изменение статуса пишется и в `state.json`, и в `stream.jsonl`, и в `ledger.jsonl`, одним
  вызовом-помощником из шага 2а.
- Секреты (`META_MODEL_API_KEY`, токены) не попадают ни в тексты ошибок, ни в `stream.jsonl`, ни в ledger,
  ни в `inbox/`, ни в вывод `status`.
- Ретранслятора (Cloudflare) в этом шаге нет: только протокол `RelayClient` и реализации `NoopRelay`
  (по умолчанию) и `FakeRelay` (для тестов). Настоящий клиент — шаг 4.

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
- Вне этой фазы: что фаза делает с `__compacted__` (фаза 2), команда `tero compact` (фаза 3).

- [x] `test_should_compact`: порог, ноль = выключено, `requested` побеждает
- [x] `test_run_role_stops_at_threshold`: stub-провайдер растит `last_context_tokens` по шагам → после шага, где порог превышен, чтение прекращено, возврат `__compacted__` с числом токенов
- [x] `test_opencode_and_agy_report_tokens`: stub-потоки `opencode` и `agy` → `last_context_tokens` равен числу из последнего события
- [x] реализовать

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
- Вне этой фазы: `tero compact`, `--autocompact` у `claude` (фаза 3).

- [x] `test_compact_player_gets_brief_with_diff`: stub-исполнитель №1 пишет файл и превышает порог → stub №2 получил промпт с его diff, файл в ref, гейт между ними не вызван
- [x] `test_compact_keeps_findings_verbatim`: раунд 2 с открытым замечанием → в промпте после компакта текст замечания дословно
- [x] `test_compact_does_not_grow_round_or_kmax`: компакт → `round` тот же, счётчик падений и K_max не изменились
- [x] `test_compact_capped_per_round`: исполнитель превышает порог каждый вызов → 3 компакта, 4-й вызов доживает до конца
- [x] `test_compact_judge_restarts_on_same_ref`: судья вернул `__compacted__` → второй вызов судьи, исполнитель не вызван, счётчик сбоев судьи 0
- [x] реализовать

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

- [x] `test_tero_compact_switches_at_step_boundary`: прогон с фейками идёт, в `inbox/` кладётся `compact.json` → исполнитель сменён на границе шага, ref записан, файла больше нет, `compact_requested` очищен
- [x] `test_tero_compact_refuses_when_not_running`: `status=done` → код 1 (через CLI-подпроцесс)
- [x] `test_claude_autocompact_in_argv`: порог 200000 → в argv `--autocompact 200000`; порог 0 → флага нет
- [x] реализовать

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

- [x] `test_adapter_valid_verdict_each_cli`: параметризован по `agy`, `claude`, `codex`, `opencode`: stub-CLI в формате этого CLI (форматы — как в существующих тестах провайдеров `tests/test_agy_provider.py`, `tests/test_opencode_provider.py` и т.д.) отдаёт валидный вердикт → `ok=True`
- [x] `test_adapter_garbage_is_not_ok`: stub отдаёт мусор → `ok=False`, в `error` «не по схеме»
- [x] `test_adapter_table_output`: `--adapters` с подменённой фабрикой печатает строку таблицы на каждого судью
- [x] реализовать

## Пульс владельца

Создать `src/loop/pulse.py`, `tests/loop/test_pulse.py`; менять `src/loop/run.py`, `src/loop/phase.py`.

- Файл `<run_dir>/pulse` (JSON, атомарная запись): `{"alive": <unix ts>, "progress": <int>, "progress_ts": <unix ts>}`.
- `Pulse(run_dir, interval_s=30)`: отдельный `threading.Thread(daemon=True)` (не asyncio-задача: гейт и git
  блокируют event loop), раз в `interval_s` обновляет `alive`. `pulse.progress()` — счётчик +1 и
  `progress_ts`; зовётся главным циклом после каждого **завершённого** шага (вызов исполнителя, гейт,
  судья, тестер, коммит фазы). `stop()` останавливает поток.
- Владелец (`run()` и `resume()`) запускает пульс после записи `starting` и останавливает в `finally`.
- В `state.json` владелец один раз пишет `hang_after_s` = наибольший из `player_timeout_s`,
  `judge_timeout_s`, `tester_timeout_s`, `test_timeout_s`, `stall_timeout_s` + 300.
- Вне этой фазы: кто и как читает пульс (`tero wait`, фаза 6).

- [x] `test_pulse_alive_ticks`: `interval_s=0.05` → `alive` растёт, после `stop()` поток завершён
- [x] `test_pulse_progress_after_each_step`: прогон с фейками на одну фазу → `progress` вырос на число шагов, `progress_ts` свежий
- [x] `test_pulse_write_error_does_not_crash_run`: папка прогона только для чтения при записи пульса → прогон доходит до конца
- [x] `test_state_has_hang_after_s`: после старта в `state.json` поле `hang_after_s` = max таймаутов + 300
- [x] реализовать

## tero wait и tero status

Создать `src/loop/wait.py`, `tests/loop/test_wait.py`; менять `src/cli_entry.py`; тесты CLI в `tests/test_cli.py`.

- `wait_run(run_dir, repo, *, timeout_s=None, clock=time.monotonic, sleep=time.sleep, poll_s=2) -> int`.
  Коды: `0` готово (`done` без долга), `4` готово с долгом (`done`, `debt` непустой), `2` `stopped`,
  `3` нужен агент (`waiting_agent` и `judge_pending`), `5` мёртв или завис, `6` истёк `timeout_s`.
- Сначала статус: `done`/`stopped` → `0`/`4`/`2`, даже если процесса нет. `5` — только при незавершённом статусе.
- **Мёртв** = pid из `state.json` не жив (`registry.pid_is_alive(pid, pid_started)` ложно) **и** лок репо
  свободен (неблокирующая попытка `lock.acquire(repo)`, взятый лок сразу отпустить). Занятый лок при
  мёртвом pid — не мёртв.
- **Завис** = pid жив, но `progress` не менялся дольше `hang_after_s` (в `running`) или 120 с (в
  `waiting_*`) по монотонным часам, считая от отметки в `<run_dir>/watch`; или `alive` не менялся 600 с.
- Файл `watch`: `{"boot_id": ..., "progress": N, "seen_mono": <clock()>}` — когда `wait` впервые увидел
  текущее `progress`. Новый `wait` с тем же `boot_id` и тем же `progress` берёт старую отметку (цепочка
  коротких `wait --timeout` не обнуляет окно). Другой `boot_id` или `progress` → новая отметка.
  `boot_id` на macOS — `sysctl -n kern.bootsessionuuid`, иначе `/proc/sys/kernel/random/boot_id`, иначе `""`.
  `watch` пишет `wait`, не владелец (это не `state.json`).
- `tero wait [-w <repo>] [--run <папка>] [--timeout S]` → `sys.exit(код)`; печатает одну строку: статус и почему.
- `tero status [-w] [--run] [--json]`: фаза i/n, раунд, роль, провайдер, модель, статус, `doing`, открытый
  вопрос (`id`, текст, действия) или `null`, журнал решений, долг, возраст пульса в секундах, аренда агента
  (`null`, пока её нет), `push_ok` (`false`, пока ретранслятора нет). Без `--json` — те же поля строками.
- Вне этой фазы: `--detach` (фаза 7), открытые вопросы и аренда (фазы 8, 11) — поля есть, значения пустые.

- [x] `test_wait_done_without_process`: `status=done`, pid мёртв → 0; `done` с долгом → 4; `stopped` → 2
- [x] `test_wait_dead_needs_free_lock`: незавершённый статус, pid мёртв, лок свободен → 5; лок занят → не 5 (по таймауту 6)
- [x] `test_wait_hang_by_progress`: подменённые часы: `progress` стоит дольше `hang_after_s` → 5; в `waiting_human` порог 120 с
- [x] `test_wait_sleep_is_not_death`: монотонные часы стоят (сон машины), стена ушла на час → не 5
- [x] `test_watch_survives_short_waits`: три `wait_run(timeout_s=…)` подряд с одним `progress` → окно считается от первой отметки, третий даёт 5
- [x] `test_wait_judge_pending_is_3`: `judge_pending` → 3; `waiting_agent` → 3
- [x] `test_status_json_fields`: `tero status --json` через CLI-подпроцесс → все поля из списка есть, секретов нет
- [x] реализовать

## tero go --detach

Создать `src/loop/detach.py`, `tests/loop/test_detach.py`; менять `src/cli_entry.py`.

- `tero go --detach`: подразумевает `--no-menu` и `--decisions`. Родитель запускает владельца
  `[sys.executable, "-m", "src.cli_entry", "go", <те же аргументы без --detach>, "--owner"]` с
  `start_new_session=True`, stdin `/dev/null`, stdout/stderr в `<repo>/.tero/detach.log`.
- `--owner` — скрытый флаг: обычный `go`, но без меню и с `decisions=True`. `state.json` пишет только он
  (как сейчас пишет `run()`); родитель `state.json` не пишет и не правит.
- Родитель ждёт до `DETACH_READY_S = 120`: владелец написал `state.json` со своим pid и статусом
  `running`/`waiting_*` → печатает id прогона, путь к `state.json` и протокол агента (`AGENT_PROTOCOL`,
  константа-текст из спеки, часть B «Агент-хозяин», п.1–3) → код 0. Владелец вышел раньше → печатает
  хвост `detach.log` (через `scrub_secrets`) → код 1. Таймаут → «владелец не стартовал» → код 1.
- Сон: на macOS, если `caffeinate` есть в `PATH`, родитель запускает отдельно
  `caffeinate -i -w <pid владельца>` (тоже `start_new_session=True`), не через `exec` и не как обёртку
  владельца — pid владельца не меняется.
- Запуск подпроцессов — через параметр `popen=subprocess.Popen` (подменяется в тестах).
- Вне этой фазы: точки решения (фаза 9 и дальше); `--decisions` здесь только принимается и кладётся в `Config`.

- [x] `test_detach_prints_protocol_and_returns`: stub-владелец (скрипт в `tmp_path`, пишет `state.json` с `running` и своим pid, спит) → родитель вернул 0, в выводе id и протокол
- [x] `test_detach_hud_does_not_flash_dead` (Review Focus 3): после возврата родителя `pid` в `state.json` = pid владельца, `registry.is_dead(state)` ложно, пока владелец жив
- [x] `test_detach_owner_early_exit_is_1`: stub-владелец пишет в лог ошибку с `META_MODEL_API_KEY=…` и выходит 1 → родитель код 1, хвост лога напечатан без значения ключа
- [x] `test_detach_caffeinate_argv`: подменённый `popen`, `sys.platform="darwin"`, stub `caffeinate` в `PATH` → второй вызов `["caffeinate", "-i", "-w", "<pid>"]`; без `caffeinate` — второго вызова нет
- [x] `test_go_decisions_flag`: `go --decisions` и `go --detach` → `cfg.decisions is True`; просто `go` → `False`
- [x] реализовать

## inbox, вопрос и tero answer

Создать `src/loop/inbox.py`, `src/loop/decision.py`, `src/relay/__init__.py`, `src/relay/iface.py`,
`tests/loop/test_inbox.py`; менять `src/cli_entry.py`.

- `src/relay/iface.py`: `Answer(question_id, action, text, who, why, uuid)`; протокол `RelayClient`:
  `push_state(state) -> bool`, `fetch_answers(question_id) -> list[Answer]`, `notify(text) -> bool`.
  `NoopRelay` (ничего не шлёт, ответов нет, `push_state`/`notify` → `False`) и `FakeRelay` (списки в памяти).
- `Decision(id, reason, question, actions)`; `id = "<номер фазы>-<номер точки решения в фазе>"`.
  Действия по причине (таблица спеки, часть B «Точки решения вместо стопов»):
  `user` → `answer|skip|stop`; `stuck`, `fuse` → `more|model|skip|stop`;
  `provider_unavailable`/`provider_auth`/`provider_quota` → `retry|wait|model|stop`;
  `secrets` → `allow|retry|skip|stop`; `judge_pending` → `retry|skip|stop`.
- `tero answer <id> <действие> [текст] --why "…" [--human] [-w] [--run]`: пишет
  `inbox/<uuid>.json` атомарно (`{"uuid","question_id","action","text","why","who","ts"}`; `who` = `agent`,
  либо `human` при `--human` и stdin-TTY), ждёт `inbox/<uuid>.result` до 60 с и печатает его;
  нет результата → «владелец не ответил» и код 1. Без `--why` — ошибка разбора аргументов.
- Владелец: `Inbox.take(question_id, relay) -> Answer | None` — перед приёмом локального ответа синхронно
  `relay.fetch_answers(id)`: есть ответ человека → выигрывает он. Первый принятый выигрывает, остальные
  получают `.result` «отклонено: уже отвечено: <кто, что>». Чужой id → «отклонено: вопрос <id> не открыт».
  Действие не из списка вопроса → «отклонено: действие недопустимо». `model judge …` → «отклонено: судью
  менять нельзя». `allow` не от человека → «отклонено: allow только человек». Текст `answer` длиннее 2000
  символов обрезается.
- Принятие пишется **одной** записью `state.json`: эффект, `applied_uuids` += uuid, запись в журнал
  `decisions` (`who`, `action`, `why`, `ts`, `question_id`); после неё — `inbox/<uuid>.result` «принято».
  UUID из `applied_uuids` повторно не применяется.
- Вне этой фазы: кто открывает вопрос и что делают действия (фазы 9–10); ожидание и аренда (фаза 11).

- [x] `test_answer_writes_inbox_and_prints_result`: CLI-подпроцесс `answer` + фейковый владелец в тесте пишет `.result` → вывод «принято»
- [x] `test_two_answers_one_wins` (Review Focus 4): два ответа на один id → принят первый, второй получил «уже отвечено: agent, skip`; `FakeRelay` с ответом человека → выигрывает человек, хотя локальный файл старше
- [x] `test_answer_rejects`: чужой id, недопустимое действие, `model judge`, `allow` от агента → свои тексты отказа, `state.json` не изменён
- [x] `test_applied_uuid_not_reapplied`: тот же uuid второй раз → не применён, журнал решений не вырос
- [x] `test_answer_text_capped`: текст 5000 символов → в `state.json` 2000
- [x] реализовать

