# tero v5, хвост трека T и окно HUD: stop, resume после краша, guide/notify, баги, HUD

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Фазы 1–3 — T12 из `v5/tero-master-plan.md`
(перенесены без изменений из плана 06, его фазы 1–5 сданы, план в `v5/archive/`). Фазы 4–6 — баги из
`tero-v5-logs/parked-bugs.md` (B5, B6, B2–B4). Фазы 7–9 — окно HUD (T3 + T4b из мастер-плана), спека
`docs/superpowers/specs/2026-10-22-tero-hud-design.md` (разделы «Окно», «Как HUD читает файлы», «Как HUD
понимает умер»; читать можно, менять `docs/` нельзя). Спека — `v5/tero-master-spec.md` часть B и
`v5/tero-resilience-spec.md` §7.

**Номера фаз — по этому плану, 1–9:** 1 `tero stop` во время шага, 2 `resume` после краша, 3 `guide` и
`notify`, 4 гейт называет зависший тест, 5 квота codex по потоку, 6 отчёт игрока без эха схемы, 7 модель
экрана HUD, 8 окно HUD, 9 одно окно на машину.
Уже сделано (план 06): `resume` со снимка остановленного прогона (`src/loop/resume_step.py`), точки
решения, действия и бюджет, ожидание/аренда/автопилот (`decision_step.py`, `autopilot.py`), `judge_pending`
как живое ожидание, сторож read-only (`write_guard.py`), эскалация на одном раунде, гейт по затронутым
тестам (`gate_scope.py`).

**Код.** Где что лежит и куда класть новый код — `v5/code-map.md`, читать до начала каждой фазы. Главное:
- **на потолке (ни строки сверху без выноса):** `decision_step.py`, `autopilot.py`, `judge_step.py` (400/400),
  `session.py` (399/400), `agents.py` (409), `src/providers/codex.py` (540), `subprocess_runner.py` (403);
  **почти:** `phase.py` (758/772), `run.py` (416/421), `menu.py`, `config.py`; `src/cli_entry.py` ≤ 300 строк
  (`tests/loop/test_run_modules.py`). Цифры в `v5/code-map.md` устарели — верь `wc -l`. Нужно место — сначала
  вынести соседний код в новый модуль с повторным экспортом старых имён;
- потолки в `tests/test_structure.py` не поднимать и новых ключей не добавлять; понизить потолок, когда
  файл похудел на 20+ строк, — обязательно (иначе красный `test_ceilings_are_tight`);
- новые модули не импортируют `phase`, `run`, `cli_entry`; функции не длиннее 120 строк;
- переходы статуса — только через `src/loop/transition.py`, запись файлов — только через `src/fsio.py`;
- точки подмены из `v5/code-map.md` и повторный экспорт старых имён сохраняются.

Главный принцип: **прогон не умирает молча и не выбрасывает работу; любой стоп — точка решения.**

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Абзацы выше описывают цель всего плана из 9 фаз, а не требования к текущей фазе. Поведение, которое
делают следующие фазы, в текущей фазе не делать и не требовать: его отсутствие — не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** В песочнице судьи
перечисление процессов (`psutil`) запрещено, поэтому `tests/test_process_guard.py` и всё, что зависит от
`ProcessGuard.snapshot_pids`, там падает при исправном коде. Падение этих тестов в песочнице — не находка. То же для всего, что зависит от `registry.pid_is_alive` /
`current_pid_started` и `ProcessGuard` (убийство процессов): новые тесты, которым нужна «живость» pid,
подменяют `pid_is_alive` через monkeypatch; тест, который всё же требует psutil и падает только в песочнице, —
не находка.
`src/process_guard.py` и `tests/test_process_guard.py` в этом шаге не меняются. Настоящих бинарей `agy`,
`claude`, `codex`, `opencode`, `caffeinate` в песочнице может не быть: тесты шага их не требуют, только stub-скрипты.

**Старые тесты, которые фаза явно называет в «Меняются старые тесты», переписываются под новое
поведение — это не ослабление набора.** Остальные старые тесты не правятся. Если чек-лист фазы
конфликтует со старым тестом, которого нет в её списке, — старое поведение сохраняется под флагом
(см. `Config.decisions` ниже), а не ломается.

**Точки решения включаются флагом.** `Config.decisions: bool = False`. При `False` поведение прежнее:
`_halt` откатывает фазу и пишет `stopped`, `judge_pending` выходит с кодом 3, — все старые тесты
остаются зелёными без правок. Новое поведение фаз этого плана работает только при `decisions=True`; тесты
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
- «Номер фазы» в id вопроса, трейлере `Tero-Phase`, долге и `budget` — `phase.index + 1` (как в консоли:
  первая фаза — 1). В именах снимков `phase-NN-roundNN` — `phase.index`, как сейчас (первая фаза — `phase-00`).
- Номера строк условные; ищи семантические точки («после возврата исполнителя», «вход в судью», «`_halt`»).
- Новых зависимостей не добавлять. Папку `docs/` не трогать.
- Никаких сессий CLI: ни `--continue`, ни `--conversation`, ни `-s`. Каждый вызов роли — новый процесс.
- Каждое событие в `stream.jsonl`, которое добавляет этот шаг, несёт `phase`, `round`, `role` и `text`.
  Каждое изменение статуса пишется и в `state.json`, и в `stream.jsonl`, и в `ledger.jsonl`, одним
  вызовом из `src/loop/transition.py` (`phase_transition`, `resume_transition`).
- Секреты (`META_MODEL_API_KEY`, токены) не попадают ни в тексты ошибок, ни в `stream.jsonl`, ни в ledger,
  ни в `inbox/`, ни в вывод `status`.
- Ретранслятора (Cloudflare) в этом шаге нет: только протокол `RelayClient` и реализации `NoopRelay`
  (по умолчанию) и `FakeRelay` (для тестов). Настоящий клиент — шаг 4.


## tero stop во время шага

**Где и как (сверено с кодом, главнее текста ниже при расхождении):**
- `decision_step.py` и `autopilot.py` на потолке: сначала вынести `_skip_to_debt`/`resolve`
  (`decision_step.py` ~312–400) в `src/loop/decision_resolve.py` и/или `autopilot_answer`/`_inbox_autopilot`
  (`autopilot.py` ~337–400) в `src/loop/autopilot_answer.py`, старые имена — повторный экспорт. `stop.json` в
  ожидании читать в `autopilot.wait_answer` (`inbox.py` сейчас явно пропускает `stop.json` — так и оставить).
- Флаг — `watchdog.stop_requested(run_dir)`. Проверка — в начале каждого шага раунда в `_PhaseRunner` и в
  `DecisionStep.halt` (там причина подменяется на `user_stop`). `phase.py` может вырасти ≤ 6 строк (потолок 772).
- После флага `LiveAgents` не повторяет вызов (ветки повтора other/network в `agents.py` ~225–262). `agents.py`
  на потолке: место освободить выносом `with_schema`/`extract_json` в `src/loop/role_report.py` (его же
  использует фаза 6). В тестах `LiveAgents(retry_wait_s=0)`.
- Убийство: `ProcessGuard().snapshot_pids()` → `os.killpg(pid, SIGTERM)`, через 5 с `SIGKILL` (шаги идут с
  `start_new_session=True`, pgid = pid). `test_stop_midstep_kills_fast` psutil-зависим (см. преамбулу).
- `.git/index.lock` после убийства шага удаляется под `owner_lock` без условий: git во владельце идёт только из
  главного потока.
- Ретранслятор в этой фазе не опрашивается (у `RelayClient` нет метода для стопа) — строку про relay ниже
  игнорировать.

Создать `src/loop/watchdog.py` (сторожевой поток), `src/commands/stop_cmd.py` (команда `tero stop`),
`tests/loop/test_stop_midstep.py`; менять `src/loop/pulse.py`, `src/loop/decision_step.py`, в
`src/cli_entry.py` — только подпарсер и строка диспетчера; `src/loop/phase.py` не растёт.

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

- [x] `test_stop_midstep_kills_fast`: stub-исполнитель `sleep 600`, `STOP_POLL_S=0.1` → `stop.json` → прогон `stopped` меньше чем за 5 с, процесс stub мёртв, снимок есть
- [x] `test_stop_removes_stale_index_lock`: убитый шаг оставил `.git/index.lock` → после стопа его нет, откат прошёл
- [x] `test_stop_json_answers_decision_point`: `decisions=True`, `waiting_human`, появился `stop.json` → итог `stopped`, причина `user_stop`
- [x] `test_stop_cli_refuses_when_not_running`: `status=done` → код 1 (через CLI-подпроцесс)
- [x] реализовать

## tero resume после краша

**Где и как (сверено с кодом, главнее текста ниже при расхождении):**
- «Фаза 1» в тексте ниже — это нумерация плана 06: читать как «`resume` со снимка из плана 06
  (`resume_step.py`, `_stopped_phases`)», «отказы фазы 1» — отказы `resume_refusal`.
- `session.py` на потолке: сначала вынести `_STOPPED_RESUME_REASONS`, `resume_refusal`, `_resume_config`,
  `_cfg_from_state_fields` в `src/loop/resume_rules.py`, в `session.py` — повторный экспорт (их импортируют
  `run.py` и `tests/loop/test_resume_stopped.py`).
- В `run.resume` ветвление по статусу (stopped / judge_pending) заменить одним вызовом
  `resume_step.dispatch(...)`; ветка краша — в `resume_step.py`. `force` — kwarg `resume(..., force=False)`;
  `run.py` может вырасти ≤ 5 строк (потолок 421).
- Поля «чекпоинт» в `state.json` нет. Откат при краше — к HEAD ветки `tero/<slug>` (коммиты бывают только при
  сдаче фазы): `git reset --hard HEAD && git clean -fd` (кроме `.tero/`), чекбоксы плана — из HEAD.
- «Завис» = `pid_is_alive` и `time.time() - pulse.progress_ts >= state.hang_after_s` (для `running`) или
  `_WAITING_THRESHOLD_S` (`waiting_*`): вынести как `wait.is_hung(state, pulse, now)` и использовать и в
  `wait_run`, и в `resume`. В тесте `pid_is_alive` подменён, stub-владелец запущен с `start_new_session=True`,
  `progress_ts` в прошлом.
- Сдача фазы: `phase_shipped(repo, state.base, run_id, n)`; тесты фазы — с `decisions=True` (трейлер пишется
  только при нём).
- `crash_resumes` сбрасывается в 0 при смене `phase_index`; отказ — `reason="crash_loop"`, текст «фаза N падает
  при каждом запуске».

Создать `tests/loop/test_resume_crash.py`; менять `src/loop/resume_step.py` (создан планом 06),
`src/loop/session.py` (`_resume_config`), в `src/cli_entry.py` — только флаг `--force`; `src/loop/run.py`
не растёт.

- `tero resume` смотрит статус: `judge_pending` при `decisions=False` — как сейчас (шаг 2а); `stopped` со
  снимком — как в фазе 1; любой незавершённый статус с мёртвым или зависшим владельцем — восстановление
  после краша; `done` и отказы фазы 1 — как сейчас: текст «resume для `done` пока не умеет», код 1 (его
  проверяет `tests/loop/test_resume_judge.py`, текст не менять).
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

- [x] `test_crash_between_tick_and_commit`: чекбоксы отмечены, коммита с трейлером нет → откат, фаза заново, чекбоксы `[ ]`
- [x] `test_crash_after_commit_before_state`: коммит с трейлером есть, `state.json` на старой фазе → фаза засчитана, идём к следующей
- [x] `test_crash_in_decision_point_restores_question`: дерево совпало с `tree_sha` → `waiting_human`, тот же id и текст
- [x] `test_resume_kills_hung_owner`: stub-владелец жив, `progress` стоит дольше порога → убит, лок взят; не завис → «прогон жив», код 1
- [x] `test_third_crash_resume_refused`: третий `resume` подряд → `stopped`; `--force` проходит
- [x] реализовать

## tero guide и tero notify

**Где и как:** ретранслятор для `notify` берётся из `guide_cmds.make_relay()` (по умолчанию `NoopRelay()`) —
точка подмены. `test_notify_noop_relay`: Noop — через CLI-подпроцесс, FakeRelay — вызовом обработчика в
процессе с подменённым `make_relay`. «Разделы трёх хозяев» = в выводе есть строки `Claude Code`, `opencode`,
`Muse`. `src/cli_entry.py` после всех подпарсеров ≤ 300 строк.

Создать `src/commands/guide_cmds.py` (`guide`, `notify`); менять `src/loop/detach.py` (один текст
протокола), в `src/cli_entry.py` — только подпарсеры и строки диспетчера; тесты в `tests/test_cli.py`.

- `tero guide` печатает `AGENT_PROTOCOL` (`src/loop/detach.py`) и раздел «как спать» для хозяев Claude Code,
  opencode, Muse (содержание — `v5/hosts-and-limits.md`, без ссылок на `docs/`). Код 0.
- `tero notify "текст" [-w] [--run]` → `relay.notify(scrub_secrets(текст))`; с `NoopRelay` печатает
  «ретранслятора нет, сообщение не ушло» и код 0 (не ошибка).
- `go --detach` печатает тот же `AGENT_PROTOCOL`, что `guide` (один источник текста).

- [x] `test_guide_prints_protocol_and_hosts`: вывод содержит коды `wait` 0/2/3/4/5/6 и разделы трёх хозяев
- [x] `test_notify_noop_relay`: без ретранслятора → текст «ретранслятора нет», код 0; секрет в тексте вычищен (через `FakeRelay`)
- [x] `test_detach_and_guide_same_protocol`: текст протокола в выводе `--detach` (stub-владелец) и `guide` совпадает
- [x] реализовать

## Гейт называет зависший тест

**Где и как:** `detect()` и `TestCommand` не меняются (их argv закреплён в `tests/loop/test_gates.py` и
`test_gate_scope.py`). `-o faulthandler_timeout=` добавляет функция `pytest_argv(argv)`, её зовёт `gates._run`;
тест проверяет argv, переданный в процесс. Таймаут с убийством группы — `Popen(start_new_session=True)` +
`os.killpg` вместо `subprocess.run(timeout=)`. Сведения о висяке (имя теста, ≤ 40 строк стека) дописываются в
**конец** `output` — в промпт идут последние 200 строк. Имя теста — функция `test_…` из строки стека
`File "…", line N in test_x`.

Менять `src/loop/gates.py` (и `src/loop/gate_scope.py`, если там собирается команда); тесты в
`tests/loop/test_gates.py` (или рядом с существующими тестами гейта).

Баг B5: зависший тест съедает весь таймаут гейта (900 с), в отчёте только точки прогресса — ни исполнитель,
ни человек не знают, какой тест висит. Тот же висяк блокирует SIGINT.

- Если команда гейта — pytest (`pytest` в argv или `-m pytest`) и в ней нет своего
  `faulthandler_timeout`, гейт добавляет `-o faulthandler_timeout=<GATE_TEST_HANG_S>` (модульная константа,
  по умолчанию 120). Для не-pytest команд argv не меняется.
- Когда гейт упал по таймауту или в выводе есть дамп faulthandler (`Timeout (0:`/`Thread 0x`), в
  `GateResult` и в текст для исполнителя попадает имя зависшего теста (строка `tests/...::имя`, если её
  можно достать из вывода) и последние ≤40 строк стека, а не только хвост точек.
- На таймауте гейт убивает группу процессов pytest (с xdist-воркерами), а не только родителя.

- [x] `test_gate_adds_faulthandler_for_pytest`: команда `python -m pytest -q` → в argv есть `-o faulthandler_timeout=`; команда `make test` — без изменений; своё значение в команде не перебивается
- [x] `test_gate_reports_hung_test_name`: `GATE_TEST_HANG_S=1`, `timeout_s=2`, stub-тест спит 4 с → таймаут гейта, красный, в конце `output` имя теста и строка стека
- [x] `test_gate_timeout_kills_process_group`: stub-команда порождает дочерний `sleep`, таймаут гейта подменён → после гейта дочерний процесс мёртв
- [x] реализовать

## Квота codex распознаётся по потоку

**Где и как:** `codex.py` (540) и `subprocess_runner.py` (403) на потолке. `raise_for_exit(..., errors=)` уже умеет
события потока (так делает opencode). В `codex.py` копить сообщения событий `error`/`turn.failed` и передавать
`raise_for_exit("codex", rc, stderr, errors=self._stream_errors[-10:])`; место освободить выносом
`_adapt_codex_event`/`_adapt_command_item` в `src/providers/codex_events.py` (codex.py похудел на 20+ строк →
понизить его потолок). Фильтр шума `rmcp::`/`AuthRequired` — новая функция в `src/errors.py`, в
`subprocess_runner.py` — замена строки, без роста. Проверка «без повторов»: на уровне `LiveAgents` (один вызов
stub, `_last_kind == "quota"`) и `run_phase` с `FakeAgents` → `stop_reason == "provider_quota"`.

Менять `src/errors.py` (`classify_error`) и место, где провайдер передаёт текст ошибки (`src/providers/codex.py`
или `src/providers/subprocess_runner.py`); тесты в `tests/test_errors.py` и тестах провайдера codex.

Баг B6: codex пишет в поток событий `You've hit your usage limit…`, а в текст ошибки выхода попадает только
stderr чужого MCP-сервера (`rmcp … AuthRequired`). Итог: вид `other`/`network`, ~10 мин холостых повторов,
финал `judge_pending` вместо `provider_quota`.

- Провайдер при ненулевом выходе передаёт в классификацию ещё и последние сообщения об ошибке из своего
  потока (события ошибки codex, ≤10 последних, ≤2000 симв.).
- Строки шума MCP (`rmcp::`, `AuthRequired` от MCP-серверов) выкидываются из текста ошибки до классификации
  и до показа человеку.
- Секреты в тексте ошибки по-прежнему вычищаются (`scrub_secrets`).

- [x] `test_codex_quota_in_stream_is_quota`: stub-codex пишет событие ошибки `You’ve hit your usage limit…` в поток, в stderr только `rmcp … AuthRequired`, выход 1 → вид `quota`, прогон уходит в `provider_quota` без повторов «сеть»
- [x] `test_rmcp_noise_stripped_from_error_text`: текст ошибки, который видит человек, не содержит строк `rmcp::`
- [x] `test_classify_error_old_markers_unchanged`: прежние маркеры auth/quota/network классифицируются как раньше
- [x] реализовать

## Отчёт игрока без эха схемы

**Где и как:** нативная схема у `codex`, `claude`, `agy` (передают `output_schema`); `opencode`, `cursor`, `muse`
получают пример. Сигнатура `with_schema(prompt, schema, provider)`. Пример отчёта игрока — `status: done` с
непустым `changes` (иначе его отвергнет семантическая проверка, см. `tests/loop/test_player_repair.py`). Если
фаза 1 уже вынесла `with_schema` в `src/loop/role_report.py` — менять там.

Менять `src/loop/agents.py` (`with_schema`, повтор через `repair_prompt` — сначала вынести эти куски в
новый модуль `src/loop/role_report.py`, `agents.py` не растёт), `src/loop/prompts.py` (`repair_prompt`), место
печати `✎` (HUD/detach-вывод); тесты в `tests/loop/test_agents.py` и рядом.

Баги B2–B4: opencode игнорирует `output_schema`, схема идёт в промпт текстом; слабая модель после долгого хода
копирует саму JSON Schema вместо отчёта. Повтор при этом не виден в `stream.jsonl`, а финальный JSON-отчёт
печатается как реплика `✎ {...}`.

- `with_schema()` для провайдеров без нативной схемы кладёт в конец промпта **заполненный пример отчёта**
  (валидный по схеме) и список полей одной строкой; сама JSON Schema в промпт не попадает (валидатор её
  по-прежнему использует). Провайдеры с нативной схемой — как сейчас.
- Ответ, который похож на эхо схемы (объект с `"type": "object"` и `"properties"`/`"required"` на верхнем
  уровне), — отдельная ошибка; `repair_prompt` в этом случае говорит прямо: «ты прислал схему, а не отчёт»
  и показывает пример.
- Каждый повтор роли по `repair_prompt` пишет событие `repair` в `stream.jsonl` (`phase`, `round`, `role`,
  `text` с кратким списком ошибок валидации, без секретов), как сейчас пишется `kick`.
- Финальное сообщение роли, которое разобралось как отчёт (или эхо схемы), не печатается как `✎`-реплика.

- [x] `test_with_schema_shows_example_not_schema`: промпт для провайдера без нативной схемы не содержит `"additionalProperties"`, содержит пример, который проходит `validate_role`
- [x] `test_schema_echo_gets_specific_repair`: stub-игрок первым ответом присылает схему, вторым — отчёт → повтор с текстом «схему, а не отчёт», итог — отчёт принят
- [x] `test_repair_emits_stream_event`: после повтора в `stream.jsonl` есть событие `repair` с `role` и `round`
- [x] `test_final_report_not_printed_as_text`: `LiveAgents(verbose=True)` с фейковым провайдером через `factory=`, capsys → нет `✎ {"status"`
- [x] реализовать

## Модель экрана HUD

**Где и как (главнее текста ниже):**
- **Первым делом:** автофикстура в `tests/conftest.py` подменяет `src.loop.session.maybe_launch_hud` на no-op для
  всех тестов, кроме `tests/hud/test_launch_rule.py` (иначе после фазы 8 каждый тест с `run()` откроет настоящее
  окно). Правка `conftest.py` здесь разрешена.
- В `state.json` нет списка фаз. Владелец пишет `phases=[{"title", "done"}]` и `total_phases` один раз — в
  существующем вызове `_update(state, status=RUNNING…)` в `run.py` (≤ 421 строки) и в resume. Модель: `done` или
  индекс < `phase_index` → done, == `phase_index` → active, остальное → todo, номер в `state.debt[].phase` →
  долг. Это единственная правка вне `src/hud` и `conftest.py`.
- `alive = lambda s: registry.pid_is_alive(s.get("pid"), s.get("pid_started"))`; незавершённость статуса
  проверяет сам `build_view` (`registry.is_dead` смотрит только `starting`/`running` — не использовать).
- `waiting_human`/`waiting_agent`/`waiting_limit` → иконка ❓.

Создать `src/hud/model.py`, `tests/hud/test_model.py`; `src/hud/tail.py` и `src/hud/registry.py` — только
вызовы, без смены поведения. Окна в этой фазе нет: только чистая функция «файлы прогона → что рисовать».

Уже есть: правило запуска и `maybe_launch_hud` (`src/hud/app.py`), хвост `stream.jsonl` по смещению
(`src/hud/tail.py`), реестр и `pid_is_alive`/`is_dead` (`src/hud/registry.py`).

- `build_view(state: dict | None, events: list[dict], now: float, alive: Callable) -> dict` (JSON-готовый
  словарь, без объектов). Поля как минимум:
  - `sectors`: по фазе плана `{title, color}` — `done` зелёный, `active` жёлтый, `todo` красный, долг
    (`debt`) — оранжевый; нет фаз (статус `starting`) → пустой список, круг серый;
  - `icon`: 🛠 player, ⚖️ judge, 🧪 tester, ⏱ гейт, ❓ стоп/вопрос/«оборвался», ⏸ `judge_pending`, ✓ `done`;
  - `header`: «Фаза N/M · раунд R · MM:SS» (время от `phase_started`), `role_line`: «player · <модель>»;
  - `doing`: последний `text` текущей роли из хвоста, иначе последний `tool`; на гейте — «⏱ тесты идут M:SS»
    от последнего `gate_start`; на стопе — причина из `state.json`; на открытом вопросе — текст вопроса;
  - `last_event_age_s` — сколько секунд с последнего события в `stream.jsonl`;
  - `verdict`: последний вердикт (SHIP/NO_SHIP, число блокирующих, первая находка `файл:строка`);
  - `debt_line` — существующая `debt_line()` из `app.py`, `done_text` — `hud_done_text()`;
  - `tabs`: события, разложенные по `all/player/judge/tester/gate`, каждый хвост ≤ `TAB_LIMIT` (константа, 2000).
- «Оборвался»: статус незавершённый (`starting`/`running`/`waiting_*`/`judge_pending`) и `alive(state)` ложно
  (через `registry.pid_is_alive`, pid + время старта) → `icon` ❓, `doing` «прогон оборвался». Живой процесс с
  давним событием — не «оборвался», только `last_event_age_s` большой.
- `state.json` не прочитался → вызывающий передаёт прошлый снимок; `build_view(None, …)` → серый круг,
  «нет данных», не исключение.

- [ ] `test_sectors_colors_by_phase_status`: три фазы done/active/todo и одна в долге → зелёный, жёлтый, красный, оранжевый
- [ ] `test_icon_per_role_and_status`: player/judge/tester/gate/stopped/judge_pending/done/вопрос → свои иконки
- [ ] `test_doing_prefers_role_text_then_tool_then_gate_timer`: последний `text` роли; без него — `tool`; на гейте — таймер по подменённому `now`
- [ ] `test_dead_owner_shows_broken`: `running` + `alive` ложно → «прогон оборвался»; `alive` истинно + событие 20 мин назад → не оборвался, возраст 1200
- [ ] `test_none_state_is_grey_not_crash` и `test_tabs_capped`: `None` → серый круг; 5000 событий → вкладка ≤ `TAB_LIMIT`
- [ ] реализовать

## Окно HUD

**Где и как (сверено с pywebview 6.2.1, главнее текста ниже):**
- `webview.platforms.cocoa` при импорте ставит `setActivationPolicy_(0)`, а `first_show` всегда зовёт
  `activateIgnoringOtherApps_(YES)`; `focus=False` влияет только на `canBecomeKeyWindow`. Поэтому
  `no_focus_steal()` вызывается **после** `import webview.platforms.cocoa` и **до** `webview.start`: ставит
  `NSApplicationActivationPolicyAccessory` и глушит `activateIgnoringOtherApps_` у `BrowserView.app`. Тест —
  фейковый модуль `webview.platforms.cocoa`, порядок: импорт cocoa → `no_focus_steal` → `start`.
- `view.py` не импортирует `webview` на уровне модуля (`test_every_src_module_imports`). Фейковому `webview` в
  тестах ставить `__spec__ = importlib.machinery.ModuleSpec("webview", None)` (иначе `find_spec` бросает
  `ValueError`). `test_hud_main_no_webview_hint` подменяет `importlib.util.find_spec` → `None`.

Создать `src/hud/view.py` (окно), `src/hud/view.html` (одна самодостаточная страница), `tests/hud/test_view.py`;
менять `src/hud/app.py` (`main` вместо `NotImplementedError` зовёт `view.run`), `pyproject.toml` не трогать
(`hud = ["pywebview"]` уже есть). Макеты для глаза: `.superpowers/brainstorm/*/content/hud-collapsed.html`,
`hud-expanded.html` (в `.gitignore`, может не быть — тогда по спеке).

- Окно — `pywebview`: `webview.create_window(..., html=<текст view.html>, js_api=<мост>, frameless=True,
  on_top=True, focus=False, …)`, `webview.start(http_server=False)` — **никакого `url=` и локального сервера**:
  у процесса HUD нет слушающих сокетов. `view.html` без внешних ресурсов (ни `http://`, ни `https://`, ни
  `src=`/`href=` на файлы), CSS и JS внутри, тёмная тема.
- Без кражи фокуса (T3): до создания окна на macOS процесс ставит себе
  `NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)` (pyobjc, импорт
  внутри функции, без pyobjc — пропуск), окно показывается без `activate`. Это изолировано в одной функции
  `no_focus_steal()`, которую тест подменяет; ручная проверка «печатаю в другом окне — символы не теряются»
  остаётся человеку, судья её не требует.
- Мост `js_api`: `view()` → `build_view(...)` из фазы 7 по файлам прогона (`state.json` перечитывается целиком,
  при ошибке разбора — прошлый снимок; `stream.jsonl` — через `src/hud/tail.py`), `toggle()` свёрнутый/
  развёрнутый, `save_pos(x, y)`. Страница зовёт `view()` раз в секунду.
- Свёрнутый вид: круг-секторы с иконкой в центре, `header`, `role_line`, `doing`, «последнее событие N с назад».
  Развёрнутый: та же строка + список фаз с точками + вкладки лога `all/player/judge/tester/тесты` + внизу
  последний вердикт или причина стопа. Клик по кругу — разворот.
- Позиция и вид сохраняются в `~/.tero/hud.json` (через `src/fsio.py`; путь подменяется в тестах) и
  восстанавливаются при запуске.
- HUD никак не влияет на прогон: любая ошибка в `view.run` пишется в `.tero/hud.log` и процесс выходит;
  `tero go` об этом не знает (как сейчас).
- **Тесты не открывают настоящее окно:** фейковый модуль `webview` в `sys.modules` записывает аргументы
  `create_window`/`start`.

- [ ] `test_window_uses_inline_html_no_server`: фейковый `webview` → `create_window` получил `html=`, не `url=`; `frameless`, `on_top`, `focus=False`; `start(http_server=False)`
- [ ] `test_view_html_is_self_contained`: в `view.html` нет `http://`, `https://`, внешних `src=`/`href=`
- [ ] `test_bridge_view_reads_run_files`: временная папка прогона со `state.json` и `stream.jsonl` → `bridge.view()` отдаёт `header` и `doing`; битый `state.json` → прошлый снимок
- [ ] `test_no_focus_steal_order`: импорт `webview.platforms.cocoa` → `no_focus_steal` → `start`; после него `activateIgnoringOtherApps_` не активирует
- [ ] `test_position_and_mode_persist`: `save_pos` + `toggle` → `hud.json`; новый запуск читает их
- [ ] `test_hud_main_no_webview_hint`: без `webview` → подсказка установки, код 1 (как сейчас)
- [ ] реализовать

## Одно окно HUD на машину

Менять `src/hud/view.py`, `src/hud/app.py`; тесты в `tests/hud/test_single_window.py`.

- Окно держит `~/.tero/hud.lock` (`fcntl.flock`, неблокирующий; путь подменяется в тестах). Второй
  `tero hud --run <папка>` при занятом локе не открывает окно: атомарно пишет папку в `~/.tero/hud-target`
  и выходит с кодом 0. Окно раз в секунду смотрит `hud-target` и переключается на новый прогон.
- Лок отпущен (окно закрыто, процесс умер) → следующий `tero hud` открывает окно сам.
- В окне метка «+N прогон», если в реестре (`src/hud/registry.py`) есть живые прогоны кроме показанного;
  мост `switch(run_dir)` переключает.
- После конца прогона окно остаётся с итогом, пока не придёт новый `hud-target` или не закроют.

- [ ] `test_second_hud_writes_target_and_exits`: лок занят (держит тест) → `main` пишет `hud-target`, код 0, `create_window` не вызван
- [ ] `test_window_switches_on_target`: окно (фейковый `webview`, мост) видит новый `hud-target` → `view()` отдаёт данные нового прогона
- [ ] `test_free_lock_opens_window`: лок свободен → окно открыто; изнутри фейкового `start` видно, что `flock` занят
- [ ] `test_plus_n_label_from_registry`: два живых прогона в реестре (подменённый `src.hud.registry.pid_is_alive`) → метка «+1»
- [ ] реализовать
