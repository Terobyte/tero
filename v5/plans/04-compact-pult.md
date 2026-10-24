# tero v5, трек T, шаг 3: компакт, экзамен судей (слой 1), ядро пульта

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

- [ ] `test_compact_player_gets_brief_with_diff`: stub-исполнитель №1 пишет файл и превышает порог → stub №2 получил промпт с его diff, файл в ref, гейт между ними не вызван
- [ ] `test_compact_keeps_findings_verbatim`: раунд 2 с открытым замечанием → в промпте после компакта текст замечания дословно
- [ ] `test_compact_does_not_grow_round_or_kmax`: компакт → `round` тот же, счётчик падений и K_max не изменились
- [ ] `test_compact_capped_per_round`: исполнитель превышает порог каждый вызов → 3 компакта, 4-й вызов доживает до конца
- [ ] `test_compact_judge_restarts_on_same_ref`: судья вернул `__compacted__` → второй вызов судьи, исполнитель не вызван, счётчик сбоев судьи 0
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

- [ ] `test_tero_compact_switches_at_step_boundary`: прогон с фейками идёт, в `inbox/` кладётся `compact.json` → исполнитель сменён на границе шага, ref записан, файла больше нет, `compact_requested` очищен
- [ ] `test_tero_compact_refuses_when_not_running`: `status=done` → код 1 (через CLI-подпроцесс)
- [ ] `test_claude_autocompact_in_argv`: порог 200000 → в argv `--autocompact 200000`; порог 0 → флага нет
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

- [ ] `test_adapter_valid_verdict_each_cli`: параметризован по `agy`, `claude`, `codex`, `opencode`: stub-CLI в формате этого CLI (форматы — как в существующих тестах провайдеров `tests/test_agy_provider.py`, `tests/test_opencode_provider.py` и т.д.) отдаёт валидный вердикт → `ok=True`
- [ ] `test_adapter_garbage_is_not_ok`: stub отдаёт мусор → `ok=False`, в `error` «не по схеме»
- [ ] `test_adapter_table_output`: `--adapters` с подменённой фабрикой печатает строку таблицы на каждого судью
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

- [ ] `test_pulse_alive_ticks`: `interval_s=0.05` → `alive` растёт, после `stop()` поток завершён
- [ ] `test_pulse_progress_after_each_step`: прогон с фейками на одну фазу → `progress` вырос на число шагов, `progress_ts` свежий
- [ ] `test_pulse_write_error_does_not_crash_run`: папка прогона только для чтения при записи пульса → прогон доходит до конца
- [ ] `test_state_has_hang_after_s`: после старта в `state.json` поле `hang_after_s` = max таймаутов + 300
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

- [ ] `test_wait_done_without_process`: `status=done`, pid мёртв → 0; `done` с долгом → 4; `stopped` → 2
- [ ] `test_wait_dead_needs_free_lock`: незавершённый статус, pid мёртв, лок свободен → 5; лок занят → не 5 (по таймауту 6)
- [ ] `test_wait_hang_by_progress`: подменённые часы: `progress` стоит дольше `hang_after_s` → 5; в `waiting_human` порог 120 с
- [ ] `test_wait_sleep_is_not_death`: монотонные часы стоят (сон машины), стена ушла на час → не 5
- [ ] `test_watch_survives_short_waits`: три `wait_run(timeout_s=…)` подряд с одним `progress` → окно считается от первой отметки, третий даёт 5
- [ ] `test_wait_judge_pending_is_3`: `judge_pending` → 3; `waiting_agent` → 3
- [ ] `test_status_json_fields`: `tero status --json` через CLI-подпроцесс → все поля из списка есть, секретов нет
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

- [ ] `test_detach_prints_protocol_and_returns`: stub-владелец (скрипт в `tmp_path`, пишет `state.json` с `running` и своим pid, спит) → родитель вернул 0, в выводе id и протокол
- [ ] `test_detach_hud_does_not_flash_dead` (Review Focus 3): после возврата родителя `pid` в `state.json` = pid владельца, `registry.is_dead(state)` ложно, пока владелец жив
- [ ] `test_detach_owner_early_exit_is_1`: stub-владелец пишет в лог ошибку с `META_MODEL_API_KEY=…` и выходит 1 → родитель код 1, хвост лога напечатан без значения ключа
- [ ] `test_detach_caffeinate_argv`: подменённый `popen`, `sys.platform="darwin"`, stub `caffeinate` в `PATH` → второй вызов `["caffeinate", "-i", "-w", "<pid>"]`; без `caffeinate` — второго вызова нет
- [ ] `test_go_decisions_flag`: `go --decisions` и `go --detach` → `cfg.decisions is True`; просто `go` → `False`
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

- [ ] `test_answer_writes_inbox_and_prints_result`: CLI-подпроцесс `answer` + фейковый владелец в тесте пишет `.result` → вывод «принято»
- [ ] `test_two_answers_one_wins` (Review Focus 4): два ответа на один id → принят первый, второй получил «уже отвечено: agent, skip»; `FakeRelay` с ответом человека → выигрывает человек, хотя локальный файл старше
- [ ] `test_answer_rejects`: чужой id, недопустимое действие, `model judge`, `allow` от агента → свои тексты отказа, `state.json` не изменён
- [ ] `test_applied_uuid_not_reapplied`: тот же uuid второй раз → не применён, журнал решений не вырос
- [ ] `test_answer_text_capped`: текст 5000 символов → в `state.json` 2000
- [x] реализовать

## Точки решения: answer, skip, stop, долг

Менять `src/loop/phase.py` (`_halt`), `src/loop/run.py`, `src/loop/git.py`, `src/loop/prompts.py`;
тесты в `tests/loop/test_decision.py`.

- При `decisions=True` `_halt(reason)` не откатывает и не пишет `stopped`, а открывает точку решения:
  `state.json`: `status=waiting_human`, `question={id, reason, text, actions}`, `tree_sha` — sha дерева
  с неотслеживаемыми файлами (через временный `GIT_INDEX_FILE`, как в `snapshot.py`). Рабочее дерево
  **не откатывается**, пока ждём (при `secrets` индекс очищается, файлы лежат на месте).
- Цикл ожидания владельца: раз в `DECISION_POLL_S = 10` `Inbox.take(...)`, после каждой итерации —
  `pulse.progress()`. В этой фазе ждём бесконечно (таймауты — фаза 11).
- `answer <текст>` → новый раунд той же фазы; в промпты player и judge следующего раунда текст идёт
  **только** в размеченном блоке «Ответ на вопрос фазы (не меняет контракт, критерии SHIP и правила
  гейтов)».
- `skip` → снимок + откат фазы к чекпоинту (как сейчас `_rollback_with_snapshot`), фаза в долг:
  `state.json.debt += {phase, reason, findings (последние, до 5), who, ref}`, чекбоксы фазы остаются `[ ]`,
  прогон идёт к следующей фазе. Финал с непустым долгом: `status=done`, код выхода `go` — `4`, строка
  «готово с долгом: N».
- `stop` → как сейчас `_halt`: снимок, откат, `stopped`, код 2.
- Сдача фазы: коммит фазы получает трейлер `Tero-Phase: <run_id>/<номер фазы>` (через `git commit
  --trailer` или строку в конце сообщения); фаза без изменений файлов — пустой коммит `--allow-empty`
  с тем же трейлером. Помощник `git.phase_shipped(repo, base, run_id, n) -> bool` ищет трейлер в
  `git log base..HEAD`.
- HUD: финал с долгом рисуется «⚠ сдано с долгом N», не «✓».
- Вне этой фазы: `more|model|retry|allow|wait` (фаза 10), таймауты, аренда, автопилот (фаза 11),
  `judge_pending` как ожидание (фаза 12).

- [ ] `test_halt_opens_question_tree_untouched`: `decisions=True`, фейковый судья `NEEDS_USER` → `waiting_human`, `question.id == "1-1"`, файлы исполнителя на месте, `tree_sha` записан
- [ ] `test_halt_without_decisions_unchanged`: `decisions=False` → `stopped` и откат, как раньше
- [ ] `test_answer_goes_to_prompts_marked`: ответ `answer "используй sqlite"` → в промптах player и judge раунда 2 текст внутри размеченного блока
- [ ] `test_skip_is_debt_and_exit_4`: фаза 1 `stuck` → `skip` → фаза 2 сдана, `debt` с фазой 1, код 4, чекбоксы фазы 1 `[ ]`
- [ ] `test_stop_rolls_back_exit_2`: `stop` → снимок в `refs/tero/...`, дерево у чекпоинта, код 2
- [ ] `test_phase_trailer_and_empty_commit`: сданная фаза → трейлер `Tero-Phase: <run>/1`; фаза без изменений → пустой коммит с трейлером; `phase_shipped` → `True`
- [ ] `test_hud_debt_icon`: HUD-модель для `done` с долгом 2 → текст «⚠ сдано с долгом 2»
- [x] реализовать

## Действия more, model, retry, allow, wait и бюджет

Менять `src/loop/phase.py`, `src/loop/decision.py`; тесты в `tests/loop/test_decision.py`.

- `more` → ещё 5 раундов, в которых детектор «нет прогресса» не останавливает фазу; после пятого —
  точка решения с причиной `fuse`. Эскалацию не отменяет.
- `model <player|tester> <провайдер[:модель]>` → роль сменена до конца фазы, со следующего раунда; модель
  должна быть известна (`KNOWN_PROVIDERS`), иначе отказ при приёме. `model judge` отклонён ещё в фазе 8.
- `retry` → повторить вызов провайдера (тот же раунд); после `secrets` — снимок, откат, фаза заново, а
  пути, вызвавшие стоп, идут в промпт player блоком «эти файлы не создавать».
- `allow <путь>` (только человек) → путь в `state.json.allowed_secrets`, проверка секретов его
  пропускает до конца прогона; после — сдача заново (гейт и судья на том же дереве).
- `wait` → `status=waiting_limit`, пауза до известного времени сброса (если вид ошибки его дал), иначе
  5 → 15 → 30 мин (`LIMIT_WAITS_S`, подменяется), потом повтор вызова; после третьей неудачи — снова
  точка решения. `waiting_limit` не считается в `human_timeout`.
- Бюджет продолжений: `more`, `retry`, `wait`, `model` от `agent`/`autopilot` — общий бюджет 3 на фазу в
  `state.json.budget[<фаза>]`, переживает `resume`. Исчерпан → в вопросе остаются только `skip|stop`, другие
  действия отклоняются «бюджет исчерпан». У `human` бюджета нет.
- Вне этой фазы: таймауты ожидания, аренда, автопилот (фаза 11).

- [ ] `test_more_gives_five_rounds_without_stuck`: фейк без прогресса → `more` → 5 раундов без `stuck`, потом вопрос `fuse`
- [ ] `test_model_switches_next_round`: `model player opencode:x` → раунд 2 исполнитель `opencode:x`; неизвестный провайдер отклонён
- [ ] `test_secrets_allow_and_retry`: `secrets` + `allow .env.example` от человека → сдача; `retry` → откат, в промпте player «не создавать: .env»
- [ ] `test_wait_backoff_then_question`: провайдер `quota` три раза при подменённых паузах → `waiting_limit` трижды, потом новый вопрос
- [ ] `test_budget_three_per_phase`: агент `retry` ×3 принято, 4-й отклонён «бюджет исчерпан»; человек — без ограничения
- [x] реализовать

## Ожидание, аренда агента, автопилот

Создать `src/loop/autopilot.py`, `tests/loop/test_autopilot.py`; менять `src/loop/phase.py`,
`src/loop/wait.py`, `src/config.py`.

- `Config.human_timeout_s = 900`, `Config.agent_timeout_s = 1800`, `Config.autopilot_grace_s = 900`,
  `Config.wake_command: str = ""`. Все подменяются в тестах на доли секунды.
- Ожидание: `waiting_human` → через `human_timeout_s` без ответа `waiting_agent` (вопрос **остаётся
  открытым** для человека) → аренда агента истекла → `relay.notify("агента нет, решу сам через N мин")` →
  через `autopilot_grace_s` автопилот. Нет ретранслятора (`NoopRelay`) и нет живой аренды → автопилот через
  600 с после открытия вопроса.
- Аренда: `<run_dir>/lease` = `{"until": <unix ts>, "pid": <pid сессии агента>}`. Пишет `tero wait`
  (не владелец): пока ждёт — `now + 120`, продлевая раз в 30 с; при выходе с 3 — `now + agent_timeout_s`;
  с 6 — `now + 300`. pid сессии агента — родитель `wait` (`os.getppid()`), либо `--agent-pid`. pid мёртв →
  аренда недействительна сразу. Принятый ответ агента продлевает аренду на `agent_timeout_s`.
- `wake_command` (если задан): срабатывает только когда аренда **истекла**, один раз на вопрос; плейсхолдеры
  `{run_dir}`, `{question_id}`; запуск через параметр `popen` в новой сессии; выдаёт аренду на
  `agent_timeout_s` (pid = pid запущенного процесса). Действия агента, разбуженного так: только
  `retry|skip|stop` (в inbox-файле `"woken": true`, остальное отклоняется).
- Автопилот (`autopilot.choose(decision, state) -> Answer`): по таблице спеки (`user`/`stuck`/`fuse`/`secrets` →
  `skip`; провайдер → `wait`, после исчерпания бюджета `skip`), бюджет 3, `who="autopilot"`. **Не
  цепочкой:** если фаза, начатая сразу после пропуска автопилотом, тоже дошла до точки решения —
  `stop` и `relay.notify("остановил: две фазы подряд не идут")`. Человек и агент так не ограничены.
- Повторы без шума: точки решения по одной причине подряд — не больше одного `notify` в 30 мин.
- Вне этой фазы: `judge_pending` (фаза 12), `stop` во время шага (фаза 13).

- [ ] `test_human_timeout_to_waiting_agent`: подменённые таймауты → `waiting_human` → `waiting_agent`, вопрос открыт, `wait_run` вернул 3
- [ ] `test_lease_written_by_wait`: `wait_run` пишет `lease` с `until` и pid; мёртвый pid → аренда недействительна
- [ ] `test_autopilot_after_lease_expired`: нет ответа, аренда истекла → `notify` в `FakeRelay`, через grace `skip` от `autopilot` в журнале
- [ ] `test_autopilot_no_relay_no_agent_10min`: `NoopRelay`, аренды нет → автопилот по таймеру 600 с (подменён)
- [ ] `test_autopilot_not_chain`: фаза 1 пропущена автопилотом, фаза 2 тоже в точке решения → `stop`, notify «две фазы подряд»
- [ ] `test_wake_command_once_when_lease_expired`: `wake_command` со stub-скриптом → запущен один раз с подставленными `{run_dir}` и `{question_id}`; при живой аренде не запущен; `woken`-ответ `more` отклонён
- [x] реализовать

## judge_pending как живое ожидание

Менять `src/loop/phase.py`, `src/loop/run.py`, `src/loop/autopilot.py`; тесты в `tests/loop/test_judge_pending.py`
(новые тесты в конец файла) и `tests/loop/test_autopilot.py`.

- При `decisions=True` три сбоя судьи → `status=judge_pending` и точка решения с причиной `judge_pending`
  (действия `retry|skip|stop`), процесс не выходит и держит лок; `wait` даёт 3.
- `retry` → новый вызов судьи на том же `snapshot_ref`, исполнитель не вызывается; от агента/автопилота —
  в бюджет 3.
- Автопилот для `judge_pending`: только `retry` (в бюджет), после исчерпания — `skip` в долг.
  «Принять вручную» и «отклонить» — только человек: действия `accept` и `reject` доступны только
  `who=human` (`accept` → фаза сдана с этим ref, `reject` → как `retry` исполнителя с новым раундом).
- При `decisions=False` поведение шага 2а не меняется (выход с кодом 3, `tero resume` с судьи).

- [ ] `test_judge_pending_waits_holding_lock`: судья трижды мусор, `decisions=True` → процесс жив, лок занят, `wait_run` → 3
- [ ] `test_judge_pending_retry_calls_judge_only`: `retry` → вызван только судья, на том же ref
- [ ] `test_autopilot_judge_pending_retry_then_skip`: автопилот: `retry` ×3, потом `skip` в долг
- [ ] `test_accept_reject_human_only`: `accept` от агента отклонён; от человека → фаза сдана с трейлером
- [x] реализовать

## tero stop во время шага

Создать `tests/loop/test_stop_midstep.py`; менять `src/loop/pulse.py`, `src/loop/phase.py`, `src/cli_entry.py`.

- `tero stop [-w] [--run]` → `inbox/stop.json` (`{"who", "ts"}`), печатает «стоп запрошен»; статус уже
  `done`/`stopped` → «прогон не идёт», код 1.
- Сторожевой поток рядом с пульсом (`Watchdog`, `threading.Thread`): раз в `STOP_POLL_S = 5` смотрит
  `inbox/stop.json` (и `relay`, раз в 60 с) даже в `running`. Увидел стоп → ставит флаг отмены и сам шлёт
  `SIGTERM` (через 5 с `SIGKILL`) группе процессов текущего шага (через тот же механизм, что
  `guard.kill_new_processes`; главный поток в это время заблокирован в ожидании подпроцесса). Откат не
  делает.
- Главный поток после возврата шага видит флаг → удаляет устаревший `.git/index.lock`, если git-процессов
  прогона нет → снимок → откат фазы к чекпоинту → `stopped`, причина `user_stop`, код 2.
- В точке решения `stop.json` действует как ответ `stop`.

- [ ] `test_stop_midstep_kills_fast`: stub-исполнитель `sleep 600`, `STOP_POLL_S=0.1` → `stop.json` → прогон `stopped` меньше чем за 5 с, процесс stub мёртв, снимок есть
- [ ] `test_stop_removes_stale_index_lock`: убитый шаг оставил `.git/index.lock` → после стопа его нет, откат прошёл
- [ ] `test_stop_cli_refuses_when_not_running`: `status=done` → код 1 (через CLI-подпроцесс)
- [x] реализовать

## tero resume после краша

Создать `tests/loop/test_resume_crash.py`; менять `src/loop/run.py`, `src/cli_entry.py`.

- `tero resume` смотрит статус: `judge_pending` при `decisions=False` — как сейчас (шаг 2а); любой
  незавершённый статус с мёртвым или зависшим владельцем — восстановление после краша; `done`/`stopped` →
  отказ как сейчас: текст «resume для `<статус>` пока не умеет», код 1 (его проверяет
  `tests/loop/test_resume_judge.py`, текст не менять).
- Порядок: старый pid жив и условия «завис» из `wait.py` выполнены → убить его группу процессов, дождаться
  свободного лока; pid жив, но не завис → «прогон жив», код 1. Взять лок, убрать устаревший
  `.git/index.lock`.
- Коммит с трейлером текущей фазы есть (`phase_shipped`) → фаза сдана, состояние догоняет git (чекбоксы
  в плане выставляются). Нет, но прогон умер в точке решения и `tree_sha` совпадает с текущим деревом →
  ожидание восстановлено: тот же раунд, тот же `question.id` и текст, `human_timeout` с начала. Иначе →
  снимок, откат к чекпоинту из `state.json` (вместе с чекбоксами плана) и фаза заново.
- Долг, журнал, бюджет, `applied_uuids` — из `state.json`. Фазы из долга не запускаются заново.
- Счётчик крашей подряд в одной фазе (`state.json.crash_resumes`): третий `resume` отказывает,
  `stopped`, причина «фаза N падает при каждом запуске»; `resume --force` обходит. Событие `crash_resume`
  в ledger. Грязная ветка `tero/<slug>` для `resume` ожидаема.

- [ ] `test_crash_between_tick_and_commit`: чекбоксы отмечены, коммита с трейлером нет → откат, фаза заново, чекбоксы `[ ]`
- [ ] `test_crash_after_commit_before_state`: коммит с трейлером есть, `state.json` на старой фазе → фаза засчитана, идём к следующей
- [ ] `test_crash_in_decision_point_restores_question`: дерево совпало с `tree_sha` → `waiting_human`, тот же id и текст
- [ ] `test_resume_kills_hung_owner`: stub-владелец жив, `progress` стоит дольше порога → убит, лок взят; не завис → «прогон жив», код 1
- [ ] `test_third_crash_resume_refused`: третий `resume` подряд → `stopped`; `--force` проходит
- [x] реализовать

## tero guide и tero notify

Менять `src/cli_entry.py`, `src/loop/detach.py`; тесты в `tests/test_cli.py`.

- `tero guide` печатает `AGENT_PROTOCOL` (из фазы 7) и раздел «как спать» для хозяев Claude Code,
  opencode, Muse (содержание — `v5/hosts-and-limits.md`, без ссылок на `docs/`). Код 0.
- `tero notify "текст" [-w] [--run]` → `relay.notify(scrub_secrets(текст))`; с `NoopRelay` печатает
  «ретранслятора нет, сообщение не ушло» и код 0 (не ошибка).
- `go --detach` печатает тот же `AGENT_PROTOCOL`, что `guide` (один источник текста).

- [ ] `test_guide_prints_protocol_and_hosts`: вывод содержит коды `wait` 0/2/3/4/5/6 и разделы трёх хозяев
- [ ] `test_notify_noop_relay`: без ретранслятора → текст «ретранслятора нет», код 0; секрет в тексте вычищен (через `FakeRelay`)
- [ ] `test_detach_and_guide_same_protocol`: текст протокола в выводе `--detach` (stub-владелец) и `guide` совпадает
- [x] реализовать
