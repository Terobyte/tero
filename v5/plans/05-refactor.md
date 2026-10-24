# tero v5: рефакторинг перед точками решения — god files, мёртвый код, дубли

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Это не новая функциональность: восемь фаз
разбирают то, что накопилось за шаги 1–3 (`v5/archive/`), чтобы план `v5/plans/06-pult-decisions.md`
ложился в небольшие модули, а не дописывал `src/loop/phase.py` (сейчас 2001 строка, класс `_PhaseRunner` —
около 1450) и `src/loop/run.py` (`run()` — 334 строки, `resume()` — 311, половина у них общая).

Исключение — фаза 1: она закрывает настоящий дефект, найденный при архивации. Исполнитель фазы
«Счётчик контекста» сам отметил `[x] реализовать` во всех 15 фазах плана 04 (сама `mark_phase_done`
ставит галочки только своей фазе, это проверено). Если исполнитель отметит все чекбоксы чужой фазы,
`Phase.done` станет истиной и tero её пропустит. Фаза 1 идёт первой, чтобы этот рефакторинг уже шёл
под защитой.

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Абзацы выше описывают цель всего плана из 8 фаз, а не требования к текущей фазе. Перенос, который
делают следующие фазы, в текущей фазе не делать и не требовать: его отсутствие — не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** В песочнице судьи
перечисление процессов (`psutil`) запрещено, поэтому `tests/test_process_guard.py` и всё, что зависит от
`ProcessGuard.snapshot_pids`, там падает при исправном коде. Падение этих тестов в песочнице — не находка.
`src/process_guard.py` и `tests/test_process_guard.py` в этом плане не меняются.

Правила для всех фаз:

- **Поведение не меняется** (кроме фазы 1). Тексты в выводе, поля `state.json`, события `stream.jsonl` и
  ledger, коды выхода — те же. Весь старый набор тестов зелёный без правок, кроме тестов, которые фаза
  явно называет в «Меняются старые тесты». Остальные старые тесты не правятся, не удаляются, не
  ослабляются и не помечаются `skip`.
- **Импорты тестов — контракт.** Всё, что тесты импортируют из модуля (`from src.loop.run import
  rounds_word`, `from src.menu import _fallback_menu`, `from src.cli_entry import build_parser`, …),
  остаётся импортируемым оттуда же: перенесённое имя реэкспортируется из старого модуля.
- **Точки подмены в тестах — контракт.** Тесты подменяют `src.loop.phase.run_all`, `src.loop.phase.rollback`,
  `src.loop.run.run`, `src.cli_entry.sys.exit`, `src.cli_entry.run_go`, атрибуты модулей
  `src.loop.snapshot` (`take_snapshot`, `restore_snapshot`, `tree_of`), `src.loop.agents` (`RETRY_DELAYS_S`,
  `RETRY_DEADLINE_S`, `HEARTBEAT_S`, `_now`, `asyncio`), `src.menu` (`questionary`, `QUESTIONARY_AVAILABLE`,
  `_save_global_default`), `src.providers.free_models.list_models`. Код, который их вызывает, ищет их
  во время вызова через тот же модуль, что и сейчас. Поэтому гейт (`_gate`, вызов `run_all`) и откат
  (`_rollback_with_snapshot`, вызов `rollback`) остаются в `src/loop/phase.py`, снимки зовутся как
  `snapshot_mod.take_snapshot(...)`, а `run_go` импортирует `src.loop.run.run` внутри функции, как сейчас.
- **Без циклов импорта.** Новые модули шагов (`src/loop/*_step.py`, `suite_guard.py`, `findings.py`)
  не импортируют `src.loop.phase` и `src.loop.run`. Общие данные приходят через `PhaseContext` (фаза 6).
- Перенос — это перенос: тело функции переезжает как есть, переименования только там, где фаза их
  называет. Попутно ничего не «улучшать»: найденный по ходу баг — отдельной строкой в ответе
  исполнителя, не правкой.
- Настоящих LLM и CLI в тестах нет, ни один тест не спит дольше 5 секунд, новых зависимостей нет.
  Тесты: `PYTHONPATH=. python3 -m pytest -q` из корня. Подпроцессы CLI —
  `[sys.executable, "-m", "src.cli_entry", ...]`.
- Номера строк условные; ищи по именам функций.
- Папку `docs/` не трогать.

## План правит только tero

Менять `src/loop/phase.py`; тесты в `tests/loop/test_plan_guard.py`.

- На входе фазы владелец запоминает байты файла плана (`plan_path`), если он лежит внутри репо.
- Сразу после возврата исполнителя и тестера (до снимка раунда и до гейта): файл плана отличается
  от запомненного → байты возвращаются, событие `plan_restored` в ledger и `stream.jsonl` (`phase`,
  `round`, `role`, `text` «исполнитель менял план, вернул»). Прогон продолжается как обычно.
- Единственная запись в план — `mark_phase_done` при сдаче фазы; после неё запомненные байты
  обновляются (следующая фаза сравнивает с планом, где уже стоят `[x]` этой фазы).
- План вне репо — проверки нет, поведение прежнее.
- Вне этой фазы: перенос кода фазы (фазы 4–7).

- [ ] `test_player_ticking_future_phase_is_reverted`: план из двух фаз, stub-исполнитель фазы 1 пишет код и ставит `[x]` во всех чекбоксах плана → фаза 1 сдана, у фазы 2 чекбоксы `[ ]`, исполнитель вызван и во второй фазе, `plan_restored` в ledger
- [ ] `test_player_editing_plan_text_is_reverted`: исполнитель дописал строку в план → в коммите фазы и на диске её нет, `[x]` у сданной фазы стоят
- [ ] `test_tester_editing_plan_is_reverted`: то же для тестера
- [ ] `test_plan_outside_repo_untouched`: план вне репо → событий `plan_restored` нет, фаза сдана как раньше
- [ ] реализовать

## Страж структуры: потолки размеров

Создать `tests/test_structure.py`. В `src/` в этой фазе ничего не меняется.

- `FILE_CEILINGS: dict[str, int]` — для каждого файла `src/**/*.py` длиннее 400 строк потолок равен его
  длине на момент фазы (сейчас это `src/loop/phase.py`, `src/loop/run.py`, `src/menu.py`,
  `src/providers/codex.py`, `src/cli_entry.py`, `src/config.py`, `src/loop/agents.py`,
  `src/providers/subprocess_runner.py`). Любой другой файл — не длиннее 400 строк.
- `FUNC_CEILINGS: dict[str, int]` (ключ `"<путь>::<имя>"`) — то же для функций и методов длиннее 120
  строк (по `ast`: `end_lineno - lineno + 1`): сейчас `run.py::run`, `run.py::resume`,
  `subprocess_runner.py::run_subprocess_jsonl`, `phase.py::_judge`. Любая другая функция — не длиннее 120.
- Потолки только опускаются. Следующие фазы опускают свои числа или удаляют строки из словарей;
  поднять потолок или добавить в словарь новый файл — находка.
- Сообщение об ошибке называет файл (функцию), длину и потолок.
- Вне этой фазы: любые переносы кода.

- [ ] `test_file_sizes_within_ceilings`: каждый файл не длиннее своего потолка или 400
- [ ] `test_function_sizes_within_ceilings`: каждая функция не длиннее своего потолка или 120
- [ ] `test_ceilings_are_tight`: каждый потолок не больше фактической длины + 20, каждый ключ словарей существует (иначе сжатый файл держит старый потолок)
- [ ] `test_every_src_module_imports`: `importlib.import_module` для каждого модуля `src/` проходит
- [ ] реализовать

## Мёртвый код

Менять `src/providers/`, `src/config.py`, `src/loop/git.py`, `src/relay/iface.py`, `src/loop/phase.py`;
тесты в `tests/test_structure.py`.

- `src/providers/gemini.py` удалить: `create_provider("gemini")` уже идёт в `agy` с предупреждением,
  `GeminiProvider` в `src/` нигде не создаётся. Ссылки на модуль в `src/` (если есть, например в
  `src/providers/effort.py`) убрать. Пункт меню «Gemini (Google CLI)» и алиас `gemini` не трогать.
- Удалить: `src/config.py::_load_defaults_section`, `src/loop/git.py::restore_tree` (вызывается только из
  теста; восстановление дерева делает `snapshot.restore_snapshot`), неиспользуемый импорт `field` в
  `src/relay/iface.py`, члены `_Next.INVALID` и `_Next.UNAVAILABLE` в `src/loop/phase.py`, если на них
  нет ссылок (комментарий у `_after_judge` поправить).
- **Не мёртвые, не удалять** (нужны плану 06 или HUD): `src/loop/inbox.py::Inbox`, `src/loop/decision.py`
  (`Decision`, `actions_for`), `src/relay/iface.py` (`RelayClient`, `NoopRelay`, `FakeRelay`),
  `Status.WAITING_HUMAN|WAITING_AGENT|WAITING_LIMIT`, `src/hud/tail.py::Tail`,
  `src/hud/registry.py` (`list_live`, `is_dead`).
- Меняются старые тесты: `tests/test_gemini_provider.py` удаляется целиком; в `tests/test_bugs_providers.py`
  (`_make_gemini`, `test_b45_gemini_*`, `test_b52_gemini_*`), `tests/test_effort.py`
  (`test_gemini_has_no_effort_flag`, `test_gemini_command_ignores_effort`), `tests/test_hunt_providers.py`
  и `tests/test_hunt_misc.py` удаляются только тесты, которые создают `GeminiProvider`/`GeminiConfig` или
  импортируют `src.providers.gemini`; в `tests/loop/test_git.py` — только тест `restore_tree`. Тест
  `test_gemini_routes_to_agy` остаётся.
- Потолки в `tests/test_structure.py` опустить под новые длины.
- Вне этой фазы: дубли (фаза 4), переносы из `phase.py` и `run.py`.

- [ ] `test_gemini_module_gone`: `importlib.util.find_spec("src.providers.gemini") is None`, `create_provider("gemini")` → `AgyProvider`
- [ ] `test_no_unreferenced_private_functions`: каждая функция или метод уровня модуля/класса с именем `_x` в `src/` упоминается в `src/` ещё хотя бы раз кроме своего `def` (по `ast`, имена `__dunder__` не считаются); список исключений в тесте пуст
- [ ] `test_no_unused_imports`: по `ast` каждое имя из `import`/`from … import` в `src/` используется в своём модуле или стоит в `__all__` (реэкспорт помечается `__all__`)
- [ ] реализовать

## Один атомарный ввод-вывод и одна папка прогона в CLI

Создать `src/fsio.py`, `tests/test_fsio.py`; менять `src/loop/state.py`, `src/loop/pulse.py`,
`src/loop/wait.py`, `src/loop/plan.py`, `src/loop/inbox.py`, `src/loop/detach.py`, `src/hud/registry.py`,
`src/menu.py`, `src/cli_entry.py`.

- Сейчас «временный файл + rename» написан семь раз (`RunState.update`, `Pulse`, `wait._write_watch`,
  `plan._atomic_write`, `Registry`, `menu._save_global_default`, `inbox.write_inbox_file`), чтение
  JSON-словаря с проверкой типа — дважды (`wait._read_json`, `detach._read_state`).
- `src/fsio.py`: `atomic_write_text(path, text, *, newline=None) -> None` (временный файл `mkstemp` в той
  же папке, `encoding="utf-8"`, `os.replace`; при ошибке временный файл удалён, исключение наружу),
  `atomic_write_json(path, data) -> None` (`ensure_ascii=False`), `read_json_dict(path) -> dict | None`
  (нет файла, битый JSON, не словарь → `None`).
- Все семь мест пишут через `fsio`. Кто сейчас не бросает (`RunState.update`, `Pulse`, `inbox`), тот и
  дальше не бросает: ловит исключение у себя, как сейчас. `plan.mark_phase_done` передаёт `newline=""`
  (CRLF-план остаётся CRLF).
- CLI: «`--run` или последний прогон в `.tero/runs`, иначе «Прогонов нет.»» повторён в `wait`, `status`,
  `answer`, `compact`, `review` → одна функция `_resolve_run_dir(args) -> Path | None` в `src/cli_entry.py`.
- Меняются старые тесты: нет.
- Вне этой фазы: переносы из `phase.py` и `run.py`.

- [ ] `test_atomic_write_failure_keeps_old`: `os.replace` подменён на исключение → старое содержимое на месте, `*.tmp` в папке нет
- [ ] `test_atomic_write_keeps_crlf`: `newline=""` и текст с `\r\n` → байты на диске те же
- [ ] `test_atomic_write_utf8_under_c_locale`: подпроцесс с `LC_ALL=C`, `PYTHONUTF8=0` пишет кириллицу → файл в UTF-8
- [ ] `test_read_json_dict_cases`: нет файла, мусор, список, словарь → `None`, `None`, `None`, словарь
- [ ] `test_mkstemp_only_in_fsio`: `tempfile.mkstemp`/`NamedTemporaryFile` в `src/` встречается только в `src/fsio.py`, `src/loop/snapshot.py` (временный индекс) и `src/providers/codex.py` (файлы инструкций и схемы)
- [ ] реализовать

## phase.py: защита набора тестов в suite_guard.py

Создать `src/loop/suite_guard.py`, `tests/loop/test_suite_guard.py`; менять `src/loop/phase.py`.

- В `suite_guard.py` переезжают: константы `SUITE_CONFIG_NAMES`, `SUITE_HOOK_NAMES`, `AGENT_CONFIG_DIRS`,
  `AGENT_INSTRUCTION_NAMES` и производные `_GUARDED_*`, `_AGENT_DIRS_FOLDED`, `_SUITE_HOOKS_FOLDED`,
  `_PRUNE_DIRS`; функции `is_test_path`, `_suite_paths`, `_remove_guarded`, `_strip_unsafe_ancestors`,
  `_base_guarded_entries`, `_follow_link`, `_chain_end`, `_resolve_in_repo`, `_walk_target`,
  `_snapshot_guarded`; тела методов `_protect_tests`, `_inside_repo`, `_revert_non_test`, `_revert_path`
  как функции с явными аргументами (`repo`, снимок охраняемого, состояние фазы). В `_PhaseRunner`
  остаются тонкие методы с теми же именами, которые зовут `suite_guard`.
- `src/loop/phase.py` реэкспортирует `is_test_path` и константы (`__all__`).
- `suite_guard.py` не импортирует `src.loop.phase`.
- Потолок `src/loop/phase.py` опустить под новую длину.
- Меняются старые тесты: нет.
- Вне этой фазы: компакт, тестер, судья (фазы 6–7).

- [ ] `test_suite_guard_imports_no_phase`: по `ast` `src/loop/suite_guard.py` не импортирует `src.loop.phase` и `src.loop.run`
- [ ] `test_is_test_path_same_object`: `src.loop.phase.is_test_path is src.loop.suite_guard.is_test_path`
- [ ] `test_snapshot_guarded_direct`: прямой вызов `suite_guard._snapshot_guarded` на репо в `tmp_path` с `conftest.py`, `AGENTS.md` и симлинком на папку → те же записи, что видит фаза (сравнить с `_PhaseRunner(...).guarded`)
- [ ] реализовать

## phase.py: контекст фазы, компакт и тестер в свои модули

Создать `src/loop/phase_ctx.py`, `src/loop/compact_step.py`, `src/loop/tester_step.py`,
`tests/loop/test_phase_modules.py`; менять `src/loop/phase.py`.

- `phase_ctx.py`: `@dataclass PhaseContext` с общими для шагов полями (`phase`, `total_phases`, `repo`,
  `plan_path`, `preamble`, `cfg`, `agents`, `test_command`, `ledger`, `run_dir`, `state_io`, `stream`,
  `pulse`, `checkpoint`, `contract`, `st`) и методами, которые сейчас у `_PhaseRunner` общие для всех
  шагов: `emit` (`_emit`), `update_state`, `progress`, `tag`, `round_file`, `check_reply`, `provider_text`,
  `invalid`, `provider_error`, `player_identity`. Сюда же переезжают `_State` (новое имя `PhaseState`,
  `_State` остаётся алиасом в `phase.py`), `OpenFinding`, `ProtectedTest`, `_stream_text`, `_tail`, `_dump`.
- `compact_step.py`: класс `CompactStep(ctx)` с методами из `_PhaseRunner`: `_player_compact_failed`,
  `_compact_file`, `_take_requested_compact`, `_claude_autocompacts`, `_clear_compact_requested`,
  `_player_compact_check`, `_judge_compact_check`, `_record_compact`. Чистые `should_compact` и `Compacted`
  остаются в `src/loop/compact.py`.
- `tester_step.py`: класс `TesterStep(ctx)` с `_tester` и `_note_finding`.
- `_PhaseRunner` создаёт `PhaseContext` один раз и держит шаги полями; его методы-обёртки с прежними
  именами допустимы только там, где их зовёт другой код `phase.py`.
- Новые модули не импортируют `src.loop.phase`.
- Потолок `src/loop/phase.py` опустить под новую длину (ожидаемо ≤ 1300).
- Меняются старые тесты: нет.
- Вне этой фазы: судья, K_max, переходы статуса (фаза 7).

- [ ] `test_step_modules_import_no_phase`: по `ast` `phase_ctx.py`, `compact_step.py`, `tester_step.py` не импортируют `src.loop.phase` и `src.loop.run`
- [ ] `test_state_alias`: `src.loop.phase._State is src.loop.phase_ctx.PhaseState`
- [ ] `test_emit_fields_unchanged`: `PhaseContext.emit("gate", ...)` с подменённым `Stream` → в записи есть `phase`, `round`, `role`, `text`, секрет из окружения вычищен
- [ ] реализовать

## phase.py: судья и K_max в свои модули, один переход статуса

Создать `src/loop/judge_step.py`, `src/loop/findings.py`, `src/loop/transition.py`; менять
`src/loop/phase.py`, `src/loop/run.py`; тесты в `tests/loop/test_phase_modules.py`.

- `judge_step.py`: класс `JudgeStep(ctx)` с `_snapshot_before_judge`, `_judge`, `_judge_failed`,
  `_stop_judge_pending`, `_apply_verdict`, `_apply_verdict_continuation`. `_judge` (181 строка) режется на
  части не длиннее 120 строк по смыслу: сборка промпта, вызов с повторами, разбор сбоя, применение
  вердикта. Строка `phase.py::_judge` удаляется из `FUNC_CEILINGS`.
- `findings.py`: чистые функции над `PhaseState` — `findings_key`, `not_shrunk`, `stalled`, `new_blocking`,
  `no_progress`, `in_continuation` (из `_findings_key`, `_not_shrunk`, `_stalled`, `_new_blocking`,
  `_no_progress`, `_in_continuation`). `_clean_retry` и `_discard_tests` зовут git и остаются у шага судьи.
- `transition.py`: одна функция `write_status_transition(state_io, stream, ledger, *, status, **fields)`.
  Сейчас переход пишется двумя копиями — `_PhaseRunner._write_status_transition` и вложенная
  `_write_status_transition` в `run.resume`; обе зовут новую функцию.
- `phase.py` остаётся оркестратором: `run_phase`, `_PhaseRunner.run`, `_after_judge`, `_player`,
  `_raw_player_call`, `_gate`, `_same_red`, `_snapshot_after_player`, `_rollback_with_snapshot`, `_ship`,
  `_halt`, `_halt_text`, `_write_stopped` и проверка плана из фазы 1.
- Потолок `src/loop/phase.py` опустить под новую длину, длина не больше 800 строк.
- Меняются старые тесты: нет.
- Вне этой фазы: `run.py` кроме перехода статуса (фаза 8).

- [ ] `test_judge_modules_import_no_phase`: по `ast` `judge_step.py`, `findings.py`, `transition.py` не импортируют `src.loop.phase` и `src.loop.run`
- [ ] `test_one_status_transition`: по `ast` во всём `src/` ровно одно определение `write_status_transition` и ни одного `_write_status_transition`
- [ ] `test_findings_pure`: `findings_key` и `not_shrunk` на двух списках замечаний с тем же `(file, line)` и другим текстом → «не уменьшились» (как `test_kmax_rephrased_findings_count_as_flat`, но без прогона)
- [ ] `test_phase_py_is_orchestrator`: `src/loop/phase.py` ≤ 800 строк, в `FUNC_CEILINGS` нет ключей из `phase.py`
- [ ] реализовать

## run.py и cli_entry.py: общий старт, общий цикл фаз, команды отдельно

Создать `src/loop/session.py`, `src/loop/preflight.py`, `src/loop/report.py`, `src/commands/__init__.py`,
`src/commands/runs.py`, `src/commands/inbox_cmds.py`; менять `src/loop/run.py`, `src/cli_entry.py`;
тесты в `tests/loop/test_run_modules.py`.

- `run()` и `resume()` повторяют друг друга: лок, реестр, пульс, HUD, `finally` с `unregister` и
  освобождением, и цикл «следующая несданная фаза → `run_phase` → сводка». Общее выносится:
  `session.py` — контекстный менеджер `RunSession` (лок, `Registry.register/unregister`, `Pulse`
  start/stop, запуск HUD) и `run_phases(...)` — один цикл фаз для обоих. В `run()` и `resume()`
  остаётся только своё: порядок старта и проверки у `run`, восстановление `judge_pending` у `resume`.
- `preflight.py`: `_same_family_as_judge`, `_validate_providers`, `_roles`, `_agy_roles`,
  `agy_preflight_check`, вызов `check_free_models`. `report.py`: `rounds_word`, `phase_summary_line`,
  `_print_phase_summary`, `slugify`. `src/loop/run.py` реэкспортирует `RunOutcome`, `run`, `resume`,
  `rounds_word`, `phase_summary_line`, `agy_preflight_check`, `slugify`.
- `cli_entry.py`: обработчики `run_history`, `run_wait`, `run_status`, `run_review` → `src/commands/runs.py`;
  `run_answer`, `run_compact` (+ `_answer_wait_s`, `_answer_poll_s`) → `src/commands/inbox_cmds.py`.
  В `cli_entry.py` остаются `build_parser`, `main`, `run_go`, `prepare_go_config`, `_config_from_args`,
  `_resolve_run_dir`, `run_resume` и реэкспорт перенесённых обработчиков. `python -m src.cli_entry` и
  `tero` работают как раньше.
- Строки `run.py::run` и `run.py::resume` удаляются из `FUNC_CEILINGS`. Потолки: `src/loop/run.py`
  ≤ 450, `src/cli_entry.py` ≤ 250.
- Меняются старые тесты: нет.
- Вне этой фазы: `menu.py`, `config.py`, провайдеры (их потолки остаются как есть).

- [ ] `test_run_and_resume_share_loop`: по `ast` `run_phase` вызывается в `src/loop/` ровно в одном месте — в `session.run_phases`
- [ ] `test_cli_help_lists_commands`: подпроцесс `python -m src.cli_entry --help` → в выводе `go`, `history`, `hud`, `wait`, `status`, `answer`, `compact`, `review`, `resume`
- [ ] `test_run_reexports`: все имена из списка выше импортируются из `src.loop.run`, `run_review` — из `src.cli_entry`
- [ ] `test_run_py_sizes`: `src/loop/run.py` ≤ 450, `src/cli_entry.py` ≤ 250, ни одна функция в `src/loop/` не длиннее 120 строк
- [ ] реализовать

---

Карта модулей после плана (для плана 06 — где искать код):

| Что | Где |
|---|---|
| Оркестратор фазы, `_halt`, `_ship`, гейт, откат, проверка плана | `src/loop/phase.py` |
| Общие поля и помощники шага, `PhaseState` | `src/loop/phase_ctx.py` |
| Судья, вход в судью, `judge_pending` | `src/loop/judge_step.py` |
| Замечания, K_max | `src/loop/findings.py` |
| Тестер | `src/loop/tester_step.py` |
| Компакт роли и `inbox/compact.json` | `src/loop/compact_step.py` |
| Защита набора тестов | `src/loop/suite_guard.py` |
| Переход статуса (state + stream + ledger) | `src/loop/transition.py` |
| Лок, реестр, пульс, HUD, цикл фаз | `src/loop/session.py` |
| Проверки на старте | `src/loop/preflight.py` |
| Команды CLI кроме `go`/`resume` | `src/commands/` |
| Атомарная запись и чтение JSON | `src/fsio.py` |
