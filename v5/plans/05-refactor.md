# tero v5: рефакторинг перед точками решения — god files, мёртвый код, дубли

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Это не новая функциональность: восемь фаз
разбирают то, что накопилось за шаги 1–3 (`v5/archive/`), чтобы план `v5/plans/06-pult-decisions.md`
ложился в небольшие модули, а не дописывал `src/loop/phase.py` (сейчас 2004 строки, класс `_PhaseRunner` —
около 1450) и `src/loop/run.py` (`run()` — 334 строки, `resume()` — 311, половина у них общая).

Исключение — фаза 1: она закрывает настоящий дефект, найденный при архивации. Исполнитель фазы
«Счётчик контекста» сам отметил `[x] реализовать` во всех 15 фазах плана 04 (сама `mark_phase_done`
ставит галочки только своей фазе, это проверено). Если исполнитель отметит все чекбоксы чужой фазы,
`Phase.done` станет истиной и tero её пропустит; если вставит строки выше своей фазы, `mark_phase_done`
(она ставит `[x]` по `start_line`/`end_line`, снятым на старте) отметит не те строки. Фаза 1 идёт первой,
чтобы этот рефакторинг уже шёл под защитой.

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Абзацы выше описывают цель всего плана из 8 фаз, а не требования к текущей фазе. Перенос, который
делают следующие фазы, в текущей фазе не делать и не требовать: его отсутствие — не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** В песочнице судьи
перечисление процессов (`psutil`) запрещено, поэтому `tests/test_process_guard.py` и всё, что зависит от
`ProcessGuard.snapshot_pids`, там падает при исправном коде. Падение этих тестов в песочнице — не находка.
`src/process_guard.py` и `tests/test_process_guard.py` в этом плане не меняются.

Правила для всех фаз:

- **Поведение не меняется** (кроме фазы 1). Тексты в выводе, поля `state.json`, события `stream.jsonl` и
  ledger, их порядок, коды выхода, права создаваемых файлов — те же. Весь старый набор тестов зелёный
  без правок, кроме тестов, которые фаза явно называет в «Меняются старые тесты». Остальные старые тесты
  не правятся, не удаляются, не ослабляются и не помечаются `skip`.
- **Тесты самого плана — не «старые тесты».** Файлы, которые создают фазы этого плана
  (`tests/test_structure.py`, `tests/loop/test_plan_guard.py`, `tests/test_fsio.py`,
  `tests/loop/test_suite_guard.py`, `tests/loop/test_phase_modules.py`, `tests/loop/test_run_modules.py`),
  следующие фазы дополняют своими тестами. Опустить число в `FILE_CEILINGS`/`FUNC_CEILINGS` или удалить
  строку оттуда — обязанность фазы, а не правка старого теста. **Поднять** потолок или добавить в словари
  новый ключ — находка.
- **Потолки опускает каждая фаза.** Фаза, которая укоротила файл или функцию из словарей больше чем на 20
  строк, опускает их потолок до новой длины (иначе падает `test_ceilings_are_tight`). Функция, ставшая
  ≤ 120 строк, удаляется из `FUNC_CEILINGS`; файл, ставший ≤ 400, — из `FILE_CEILINGS`.
- **Импорты тестов — контракт.** Всё, что тесты импортируют из модуля (`from src.loop.run import
  rounds_word`, `from src.loop.phase import run_phase, PhaseOutcome`, `from src.menu import _fallback_menu`,
  `from src.cli_entry import build_parser`, …), остаётся импортируемым оттуда же: перенесённое имя
  реэкспортируется из старого модуля и стоит в его `__all__`.
- **Точки подмены в тестах — контракт.** Тесты подменяют:
  `src.loop.phase.run_all`, `src.loop.phase.rollback`, `src.loop.run.run`,
  `src.loop.run.load_provider_configs` (его читает `agy_preflight_check`), `src.cli_entry.sys.exit`,
  `src.cli_entry.run_go`, `Path.replace` (`tests/loop/test_plan.py`) и `os.replace`
  (`tests/loop/test_state.py`), атрибуты модулей `src.loop.snapshot` (`take_snapshot`, `restore_snapshot`,
  `tree_of`), `src.loop.agents` (`RETRY_DELAYS_S`, `RETRY_DEADLINE_S`, `HEARTBEAT_S`, `_now`, `asyncio`),
  `src.menu` (`questionary`, `QUESTIONARY_AVAILABLE`, `_save_global_default`),
  `src.providers.free_models.list_models`. Код, который их вызывает, ищет их во время вызова через тот же
  модуль, что и сейчас. Поэтому гейт (`_gate`, вызов `run_all`) и откат (`_rollback_with_snapshot`, вызов
  `rollback`) остаются в `src/loop/phase.py`; `agy_preflight_check` остаётся в `src/loop/run.py`; снимки
  зовутся как `snapshot_mod.take_snapshot(...)`; `run_go` импортирует `src.loop.run.run` внутри функции,
  как сейчас. Перед переносом любой функции найди её имя в `tests/` (`grep -rn "setattr" tests`).
- **Без циклов импорта.** Новые модули (`suite_guard.py`, `phase_ctx.py`, `*_step.py`, `findings.py`,
  `transition.py`, `session.py`, `preflight.py`, `report.py`, `src/commands/*`) не импортируют
  `src.loop.phase`, `src.loop.run` и `src.cli_entry`. Общие данные приходят через `PhaseContext` (фаза 6),
  соседние шаги и функции, которые обязаны остаться в `phase.py`, — колбэками в конструкторе.
- Перенос — это перенос: тело функции переезжает как есть, переименования только там, где фаза их
  называет. Обёртка, которая после переноса никем не вызывается, удаляется в той же фазе. Попутно ничего
  не «улучшать»: найденный по ходу баг — отдельной строкой в ответе исполнителя, не правкой.
- Настоящих LLM и CLI в тестах нет, ни один тест не спит дольше 5 секунд, новых зависимостей нет.
  Тесты: `PYTHONPATH=. python3 -m pytest -q` из корня. Подпроцессы CLI —
  `[sys.executable, "-m", "src.cli_entry", ...]`.
- Номера строк условные; ищи по именам функций.
- Папку `docs/` не трогать.

## План правит только tero

Менять `src/loop/phase.py`; тесты в `tests/loop/test_plan_guard.py`.

- `_PhaseRunner` создаётся заново на каждую фазу; на входе он запоминает байты файла плана
  (`plan_path`), если файл существует и лежит внутри репо. При `start_with_judge` (resume) и
  отслеживаемом плане байты берутся из `git show <base>:<путь плана>`, а не с диска: в ref от старого
  прогона правка плана может уже лежать. Нет файла или план вне репо — проверки нет, не исключение.
- Проверка «файл плана отличается от запомненного → вернуть байты, событие `plan_restored` в ledger и
  `stream.jsonl` (`phase`, `round`, `role`, `text` «<роль> менял план, вернул»)» стоит:
  - у исполнителя — в `_raw_player_call` сразу после `await self.agents.run_role(...)`, **и на ветке
    исключения тоже**, до любого снимка (снимки раунда снимаются внутри `_raw_player_call`, в `_player`
    и в `_record_compact`; в них план должен попасть уже чистым);
  - у тестера — в `_tester` сразу после `run_role`, до `_check_reply` и `_revert_non_test` (иначе
    `_revert_non_test` вернёт план молча, а при упавшем тестере не вернёт вовсе);
  - у судьи — сразу после `run_role` судьи (провайдер без `read_only` может писать в дерево).
- Единственная запись в план — `mark_phase_done` при сдаче фазы.
- Вне этой фазы: перенос кода фазы (фазы 5–7).

- [x] `test_player_ticking_future_phase_is_reverted`: план из двух фаз, stub-исполнитель фазы 1 пишет код и ставит `[x]` во всех чекбоксах плана → фаза 1 сдана, у фазы 2 на диске `[ ]`, `plan_restored` с `role=player` в ledger; повторный `run()` на ветке прогона запускает фазу 2, а не пишет «делать нечего»
- [x] `test_player_inserting_lines_above_phase`: исполнитель фазы 2 вставил строку в начало плана → после сдачи `[x]` стоят ровно у чекбоксов фазы 2, вставленной строки нет
- [x] `test_player_crash_snapshot_has_clean_plan`: исполнитель правит план и бросает исключение → `git show <snapshot_ref>:plan.md` равен исходному плану
- [x] `test_tester_editing_plan_is_reverted`: тестер правит план → план на диске исходный, `plan_restored` с `role=tester` в ledger
- [x] `test_plan_outside_repo_untouched`: план вне репо → событий `plan_restored` нет, фаза сдана как раньше
- [x] реализовать

## Страж структуры: потолки размеров

Создать `tests/test_structure.py`. В `src/` в этой фазе ничего не меняется.

- `FILE_CEILINGS: dict[str, int]` (ключ — путь от корня, `"src/loop/phase.py"`) — для каждого файла
  `src/**/*.py` длиннее 400 строк потолок равен его длине на момент фазы (сейчас это `src/loop/phase.py`,
  `src/loop/run.py`, `src/menu.py`, `src/providers/codex.py`, `src/cli_entry.py`, `src/config.py`,
  `src/loop/agents.py`, `src/providers/subprocess_runner.py`). Любой другой файл — не длиннее 400 строк.
- `FUNC_CEILINGS: dict[str, int]` — то же для функций и методов уровня модуля и класса длиннее 120 строк
  (по `ast`: `end_lineno - lineno + 1`), ключ `"src/loop/run.py::run"`: сейчас `src/loop/run.py::run`,
  `src/loop/run.py::resume`, `src/providers/subprocess_runner.py::run_subprocess_jsonl`,
  `src/loop/phase.py::_judge`. Вложенные функции ключей не имеют и всегда должны быть ≤ 120.
- Сообщение об ошибке называет файл (функцию), длину и потолок.
- Вне этой фазы: любые переносы кода.

- [x] `test_file_sizes_within_ceilings`: каждый файл не длиннее своего потолка или 400
- [x] `test_function_sizes_within_ceilings`: каждая функция (обход `ast.walk`, вложенные тоже) не длиннее своего потолка или 120
- [x] `test_ceilings_are_tight`: каждый потолок не больше фактической длины + 20, каждый ключ словарей существует (иначе сжатый файл держит старый потолок)
- [x] `test_every_src_module_imports`: `importlib.import_module` для каждого модуля `src/` проходит
- [x] реализовать

## Мёртвый код

Менять `src/providers/`, `src/config.py`, `src/loop/git.py`, `src/relay/iface.py`, `src/loop/phase.py`;
тесты в `tests/test_structure.py`.

- `src/providers/gemini.py` удалить: `create_provider("gemini")` уже идёт в `agy` с предупреждением,
  `GeminiProvider` в `src/` нигде не создаётся. Пункт меню «Gemini (Google CLI)» и алиас `gemini` не трогать.
- Удалить: `src/config.py::_load_defaults_section`, `src/loop/git.py::restore_tree` (вызывается только из
  теста; восстановление дерева делает `snapshot.restore_snapshot`), неиспользуемый импорт `field` в
  `src/relay/iface.py`, члены `_Next.INVALID` и `_Next.UNAVAILABLE` в `src/loop/phase.py` (на них ссылается
  только комментарий у `_after_judge` — поправить его).
- **Не мёртвые, не удалять** (нужны плану 06, HUD или окну T4b): `src/loop/inbox.py::Inbox`,
  `src/loop/decision.py` (`Decision`, `actions_for`), `src/relay/iface.py` (`RelayClient`, `NoopRelay`,
  `FakeRelay`), `Status.WAITING_HUMAN|WAITING_AGENT|WAITING_LIMIT`, `src/hud/tail.py::Tail`,
  `src/hud/registry.py` (`list_live`, `is_dead`), заглушка `from src.hud import view  # noqa: F401` в
  `src/hud/app.py` (окно T4b, его делает человек).
- Меняются старые тесты:
  - `tests/test_gemini_provider.py` удаляется целиком;
  - `tests/test_bugs_providers.py`: строка импорта `from src.providers.gemini import …`, `_make_gemini`,
    `test_b45_gemini_*`, `test_b52_gemini_*`;
  - `tests/test_effort.py`: строка импорта `GeminiProvider` и `test_gemini_command_ignores_effort`
    (`test_gemini_has_no_effort_flag` проверяет `provider_supports_effort` и **остаётся**);
  - `tests/test_hunt_providers.py`, `tests/test_hunt_misc.py`: строки импорта `src.providers.gemini` и
    только тесты, которые создают `GeminiProvider`/`GeminiConfig`;
  - `tests/loop/test_git.py`: `restore_tree` убирается из импорта, `test_write_tree_and_restore`
    переписывается без него (дерево возвращается через `git read-tree` + `git checkout-index -a -f`), его
    проверка `write_tree` остаётся.
  Тест `test_gemini_routes_to_agy` остаётся.
- Вне этой фазы: дубли (фаза 4), переносы из `phase.py` и `run.py`.

- [x] `test_gemini_module_gone`: `importlib.util.find_spec("src.providers.gemini") is None`, `create_provider("gemini")` → `AgyProvider`
- [x] `test_no_unreferenced_private_functions`: каждая функция или метод уровня модуля/класса с именем `_x` в `src/` упоминается в `src/` ещё хотя бы раз кроме своего `def` (по `ast`, имена `__dunder__` не считаются); список исключений в тесте пуст
- [x] `test_no_unused_imports`: по `ast` каждое имя из `import`/`from … import` в `src/` используется в своём модуле или стоит в его `__all__`; не считаются `from __future__ import …`, импорты внутри `if TYPE_CHECKING:` и строки с `# noqa: F401`
- [x] реализовать

## Один атомарный ввод-вывод и одна папка прогона в CLI

Создать `src/fsio.py`, `tests/test_fsio.py`; менять `src/loop/state.py`, `src/loop/pulse.py`,
`src/loop/wait.py`, `src/loop/plan.py`, `src/loop/inbox.py`, `src/loop/detach.py`, `src/hud/registry.py`,
`src/menu.py`, `src/cli_entry.py`.

- Сейчас «временный файл + rename» написан семь раз (`RunState.update`, `Pulse`, `wait._write_watch`,
  `plan._atomic_write`, `Registry.register`, `menu._save_global_default`, `inbox.write_inbox_file`),
  чтение JSON-словаря с проверкой типа — трижды (`RunState.read`, `wait._read_json`,
  `detach._read_state`).
- `src/fsio.py`:
  - `atomic_write_text(path, text, *, newline=None, encoding="utf-8") -> None`: временный файл
    `mkstemp` в той же папке; права — как у существующего файла, а нового — `0o666 & ~umask` (`mkstemp`
    сам даёт 0600, это поменяло бы права `plan.md` и `config.yaml`); переименование через
    `Path(tmp).replace(path)` — так срабатывают обе подмены в тестах (`Path.replace` и `os.replace`);
    при ошибке временный файл удалён, исключение наружу.
  - `atomic_write_json(path, data, *, ensure_ascii=True) -> None`: байты `json.dumps` каждого места
    остаются прежними (`pulse` пишет ASCII, `state.json` и `inbox` — `ensure_ascii=False`).
  - `read_json_dict(path) -> dict | None`: нет файла, битый JSON, не словарь → `None`.
- Все семь мест пишут через `fsio`, три чтения читают через `read_json_dict`. Кто сейчас не бросает
  (`RunState.update`, `Pulse`, `Registry.register`, `wait._write_watch`, `inbox`), тот и дальше не бросает:
  ловит исключение у себя, как сейчас; `plan` и `menu` бросают, как сейчас. `plan.mark_phase_done` передаёт
  `newline=""` (CRLF-план остаётся CRLF) и ту же кодировку, что у `plan._parse` при чтении.
- CLI: «`--run` или последний прогон в `.tero/runs`, иначе «Прогонов нет.»» повторён шесть раз (`wait`,
  `status`, `answer`, `compact`, `review`, `resume`) → одна функция `resolve_run_dir(args) -> Path | None`
  вместе с `_latest_run_dir` в `src/commands/__init__.py` (пакет создаётся здесь); `cli_entry.py`
  импортирует её оттуда. Потолок `src/cli_entry.py` опустить.
- Меняются старые тесты: нет.
- Вне этой фазы: перенос обработчиков CLI (фаза 8), переносы из `phase.py` и `run.py`.

- [x] `test_atomic_write_failure_keeps_old`: `os.replace` подменён на исключение → старое содержимое на месте, `*.tmp` в папке нет; то же с подменённым `Path.replace`
- [x] `test_atomic_write_keeps_mode_and_crlf`: файл 0644 с `\r\n` → после записи с `newline=""` права 0644, байты `\r\n` на месте; новый файл — `0o666 & ~umask`
- [x] `test_atomic_write_utf8_under_c_locale`: подпроцесс с `LC_ALL=C`, `PYTHONUTF8=0` пишет кириллицу → файл в UTF-8
- [x] `test_read_json_dict_cases`: нет файла, мусор, список, словарь → `None`, `None`, `None`, словарь
- [x] `test_mkstemp_only_in_fsio`: `tempfile.mkstemp`/`NamedTemporaryFile` в `src/` встречается только в `src/fsio.py`, `src/loop/snapshot.py` (временный индекс) и `src/providers/codex.py` (файлы инструкций и схемы)
- [x] реализовать

## phase.py: защита набора тестов в suite_guard.py

Создать `src/loop/suite_guard.py`, `tests/loop/test_suite_guard.py`; менять `src/loop/phase.py`.

- В `suite_guard.py` переезжают: константы `SUITE_CONFIG_NAMES`, `SUITE_HOOK_NAMES`, `AGENT_CONFIG_DIRS`,
  `AGENT_INSTRUCTION_NAMES` и производные `_GUARDED_*`, `_AGENT_DIRS_FOLDED`, `_SUITE_HOOKS_FOLDED`,
  `_PRUNE_DIRS`; функции `is_test_path`, `_suite_paths`, `_remove_guarded`, `_strip_unsafe_ancestors`,
  `_base_guarded_entries`, `_follow_link`, `_chain_end`, `_resolve_in_repo`, `_walk_target`,
  `_snapshot_guarded`; тела методов `_protect_tests`, `_inside_repo`, `_revert_non_test`, `_revert_path`
  как функции с явными аргументами (`repo`, снимок охраняемого, состояние фазы). В `_PhaseRunner`
  остаются тонкие методы с теми же именами, которые зовут `suite_guard`.
- `_PhaseRunner.guarded`, `.link_targets`, `.suite_hashes` остаются атрибутами `_PhaseRunner` до конца плана.
- `src/loop/phase.py` реэкспортирует `is_test_path` и константы (`__all__`).
- Меняются старые тесты: нет.
- Вне этой фазы: компакт, тестер, судья (фазы 6–7).

- [x] `test_suite_guard_imports_no_phase`: по `ast` `src/loop/suite_guard.py` не импортирует `src.loop.phase` и `src.loop.run`
- [x] `test_is_test_path_same_object`: `src.loop.phase.is_test_path is src.loop.suite_guard.is_test_path`
- [x] `test_snapshot_guarded_direct`: прямой вызов `suite_guard._snapshot_guarded` на репо в `tmp_path` с `conftest.py`, `AGENTS.md` и симлинком на папку → те же записи, что в `_PhaseRunner(...).guarded`
- [x] реализовать

## phase.py: контекст фазы, компакт и тестер в свои модули

Создать `src/loop/phase_ctx.py`, `src/loop/compact_step.py`, `src/loop/tester_step.py`,
`tests/loop/test_phase_modules.py`; менять `src/loop/phase.py`.

- `phase_ctx.py`:
  - `@dataclass PhaseContext` с общими для шагов полями: `phase`, `total_phases`, `repo`, `plan_path`,
    `preamble`, `cfg`, `agents`, `test_command`, `tester_enabled`, `ledger`, `run_dir`, `state_io`,
    `stream`, `pulse`, `checkpoint`, `contract`, `suite_hashes`, `guarded`, `link_targets`, `st`, и
    изменяемым `role` (сейчас `_PhaseRunner._role`, на нём держатся `emit` и `update_state`);
  - методы, которые сейчас у `_PhaseRunner` общие для всех шагов: `emit` (`_emit`), `update_state`,
    `progress`, `tag`, `round_file`, `check_reply`, `provider_text`, `invalid`, `provider_error`,
    `player_identity`;
  - типы и константы, без которых шаги иначе импортировали бы `phase`: `_Next`, `PhaseOutcome`,
    `OpenFinding`, `ProtectedTest`, `_State` (новое имя `PhaseState`), `K_MAX`, `_JUDGE_MAX_FAILURES`,
    `_NEEDS_USER_MAX_ATTEMPTS`, и помощники `_stream_text`, `_tail`, `_dump`.
  - `phase.py` реэкспортирует `PhaseOutcome`, `_Next`, `K_MAX` и `_State` (алиас `PhaseState`) через
    `__all__`. В `phase.py` остаются `UNAVAILABLE_PLAYER` и `_continuation_block`.
- `compact_step.py`: класс `CompactStep(ctx)` с методами из `_PhaseRunner`: `_player_compact_failed`,
  `_compact_file`, `_take_requested_compact`, `_claude_autocompacts`, `_clear_compact_requested`,
  `_player_compact_check`, `_judge_compact_check`, `_record_compact`. Чистые `should_compact` и `Compacted`
  остаются в `src/loop/compact.py`.
- `tester_step.py`: класс `TesterStep(ctx)` с `_tester` и `_note_finding`; `suite_guard` он зовёт сам,
  обёртки `_inside_repo`, `_revert_non_test`, `_revert_path` в `_PhaseRunner` после этого удаляются.
- `_PhaseRunner` создаёт `PhaseContext` один раз и держит шаги полями. Шагу, которому нужен сосед или
  функция, обязанная остаться в `phase.py`, они передаются колбэком в конструкторе.
- Меняются старые тесты: нет.
- Вне этой фазы: судья, K_max, переходы статуса (фаза 7).

- [x] `test_step_modules_import_no_phase`: по `ast` `phase_ctx.py`, `compact_step.py`, `tester_step.py` не импортируют `src.loop.phase` и `src.loop.run`
- [x] `test_reexports_same_objects`: `src.loop.phase._State is src.loop.phase_ctx.PhaseState`, `src.loop.phase.PhaseOutcome is src.loop.phase_ctx.PhaseOutcome`
- [x] `test_emit_fields_unchanged`: `PhaseContext.emit("gate", ...)` с подменённым `Stream` → в записи есть `phase`, `round`, `role`, `text`, секрет из окружения (длиннее 8 символов) вычищен
- [x] реализовать

## phase.py: судья в свой модуль, переходы статуса в одном месте

Создать `src/loop/judge_step.py`, `src/loop/judge_prompt.py` (если `judge_step.py` выходит длиннее 400
строк), `src/loop/findings.py`, `src/loop/transition.py`; менять `src/loop/phase.py`, `src/loop/run.py`;
тесты в `tests/loop/test_phase_modules.py`.

- `judge_step.py`: класс `JudgeStep(ctx, compact, rollback_with_snapshot)` с `_snapshot_before_judge`,
  `_judge`, `_judge_failed`, `_stop_judge_pending`, `_apply_verdict`, `_apply_verdict_continuation`,
  `_new_blocking`, `_clean_retry`, `_discard_tests`, `_print_verdict`. `CompactStep` и
  `_rollback_with_snapshot` (он остаётся в `phase.py` ради подмены `phase.rollback`) приходят
  аргументами. `_judge` (181 строка) режется на части не длиннее 120 строк по смыслу: сборка промпта,
  вызов с повторами, разбор сбоя, применение вердикта; строка `src/loop/phase.py::_judge` удаляется из
  `FUNC_CEILINGS`. Сборку промпта судьи можно вынести в `judge_prompt.py`.
- `findings.py`: чистые функции над `PhaseState` без ledger, вывода и записи в состояние —
  `findings_key`, `not_shrunk`, `stalled`, `in_continuation` (из `_findings_key`, `_not_shrunk`, `_stalled`,
  `_in_continuation`). `_no_progress` (пишет в ledger, ставит `escalated`) остаётся в `phase.py`.
- `transition.py`: обе нынешние копии записи перехода статуса переезжают **как есть**, без объединения
  (они расходятся порядком записи state/stream/ledger, видами событий и обработкой ошибок):
  `phase_transition(...)` — из `_PhaseRunner._write_status_transition`, `resume_transition(...)` — из
  вложенной `_write_status_transition` в `run.resume`. Объединение — не в этом плане.
- Обёртки compact- и judge-методов в `_PhaseRunner`, которые после переноса никем не вызываются, удаляются.
- `phase.py` остаётся оркестратором: `run_phase`, `_PhaseRunner.run`, `_after_judge`, `_player`,
  `_player_prompt`, `_raw_player_call`, `_gate`, `_same_red`, `_no_progress`, `_snapshot_after_player`,
  `_rollback_with_snapshot`, `_ship`, `_halt`, `_halt_text`, `_write_stopped`, проверка плана из фазы 1 и
  сборка контекста и шагов.
- Потолки `src/loop/phase.py`, `src/loop/run.py` и `src/loop/run.py::resume` опустить; `phase.py` — не
  длиннее 850 строк.
- Меняются старые тесты: нет.
- Вне этой фазы: остальной `run.py` (фаза 8).

- [ ] `test_judge_modules_import_no_phase`: по `ast` `judge_step.py`, `findings.py`, `transition.py` (и `judge_prompt.py`, если есть) не импортируют `src.loop.phase` и `src.loop.run`
- [ ] `test_transitions_in_one_module`: по `ast` во всём `src/` нет `_write_status_transition`, а `phase_transition` и `resume_transition` определены только в `src/loop/transition.py`
- [ ] `test_findings_pure`: `findings_key` и `not_shrunk` на двух списках замечаний с тем же `(file, line)` и другим текстом → «не уменьшились» (как `test_kmax_rephrased_findings_count_as_flat`, но без прогона)
- [ ] `test_phase_py_is_orchestrator`: `src/loop/phase.py` ≤ 850 строк, в `FUNC_CEILINGS` нет ключей из `phase.py`
- [ ] реализовать

## run.py и cli_entry.py: общий старт, общий цикл фаз, команды отдельно

Создать `src/loop/session.py`, `src/loop/preflight.py`, `src/loop/report.py`, `src/commands/runs.py`,
`src/commands/inbox_cmds.py`; менять `src/loop/run.py`, `src/cli_entry.py`; тесты в
`tests/loop/test_run_modules.py`.

- `session.py`:
  - `RunOutcome` переезжает сюда (`run.py` реэкспортирует);
  - контекстный менеджер `RunSession` — лок, `Registry.register/unregister`, `Pulse` start/stop, запуск
    HUD **флагом** (у `run` включён, у `resume` выключен, как сейчас);
  - `run_phases(...)` — один цикл «следующая несданная фаза → `run_phase` → сводка» для `run` и `resume`.
    Различия нынешних циклов сохраняются параметрами или колбэками: `KeyboardInterrupt`/`CancelledError`
    → `interrupted`/130 только у `run`; запись остановки (`state.update` у `run`, `resume_transition` у
    `resume`); финал `DONE` (`_end("ship")` со счётчиками у `run`, `run_end` без счётчиков у `resume`);
    начальный `shipped` (у `resume` — число уже сданных фаз); `start_with_judge`/`start_checkpoint`/
    `start_round` только для первой фазы `resume`; момент освобождения лока (на каждом выходе у `run`, в
    `finally` у `resume`); сбой `Pulse` (release + unregister + raise у `run`, `_restore_pending` у `resume`).
- `preflight.py`: `_same_family_as_judge`, `_validate_providers`, `_roles`, `_agy_roles`, вызов
  `check_free_models` и стартовые проверки `run()` до цикла фаз (`start_checks(cfg, repo, ...)`).
  `agy_preflight_check` остаётся в `run.py` (тест подменяет `src.loop.run.load_provider_configs`).
  Восстановление дерева у `resume` — отдельная функция в `run.py`.
- `report.py`: `rounds_word`, `phase_summary_line`, `_print_phase_summary`, `slugify`.
- `src/loop/run.py` реэкспортирует через `__all__`: `RunOutcome`, `rounds_word`, `phase_summary_line`,
  `slugify`.
- `cli_entry.py`: обработчики `run_history`, `run_wait`, `run_status`, `run_review` → `src/commands/runs.py`;
  `run_answer`, `run_compact`, `_answer_wait_s`, `_answer_poll_s` → `src/commands/inbox_cmds.py`. В
  `cli_entry.py` остаются `build_parser`, `main`, `run_go`, `prepare_go_config`, `_config_from_args`,
  `run_resume` и реэкспорт перенесённых обработчиков через `__all__`. `src/commands/*` не импортируют
  `src.cli_entry` (`resolve_run_dir` уже в `src/commands/__init__.py` с фазы 4). `python -m src.cli_entry`
  и `tero` работают как раньше.
- Строки `src/loop/run.py::run` и `src/loop/run.py::resume` удаляются из `FUNC_CEILINGS`. Потолки:
  `src/loop/run.py` ≤ 450, `src/cli_entry.py` ≤ 300.
- Меняются старые тесты: нет.
- Вне этой фазы: `menu.py`, `config.py`, провайдеры (их потолки остаются как есть).

- [ ] `test_run_and_resume_share_loop`: по `ast` `run_phase` вызывается в `src/loop/` ровно в одном месте — в `session.run_phases`
- [ ] `test_resume_does_not_launch_hud`: `resume` с подменённым запуском HUD → запуск не вызван; `run` → вызван один раз
- [ ] `test_cli_help_lists_commands`: подпроцесс `python -m src.cli_entry --help` → в выводе `go`, `history`, `hud`, `wait`, `status`, `answer`, `compact`, `review`, `resume`
- [ ] `test_run_reexports`: имена из списка выше импортируются из `src.loop.run`, `run_review` и `run_answer` — из `src.cli_entry`
- [ ] `test_run_py_sizes`: `src/loop/run.py` ≤ 450, `src/cli_entry.py` ≤ 300, ни одна функция в `src/loop/` и `src/commands/` не длиннее 120 строк
- [ ] реализовать

---

Карта модулей после плана (для плана 06 — где искать код):

| Что | Где |
|---|---|
| Оркестратор фазы, `_halt`, `_ship`, гейт, откат, `_no_progress`, проверка плана | `src/loop/phase.py` |
| Общие поля и помощники шага, `PhaseState`, `PhaseOutcome`, `_Next`, `K_MAX` | `src/loop/phase_ctx.py` |
| Судья, вход в судью, `judge_pending`, чистая попытка K_max | `src/loop/judge_step.py` (промпт — `judge_prompt.py`) |
| Сравнение замечаний для K_max | `src/loop/findings.py` |
| Тестер | `src/loop/tester_step.py` |
| Компакт роли и `inbox/compact.json` | `src/loop/compact_step.py` |
| Защита набора тестов | `src/loop/suite_guard.py` |
| Переходы статуса (state + stream + ledger) | `src/loop/transition.py` |
| `RunOutcome`, лок, реестр, пульс, HUD, цикл фаз | `src/loop/session.py` |
| Проверки на старте | `src/loop/preflight.py` (`agy_preflight_check` — в `run.py`) |
| Сводки фаз, `slugify` | `src/loop/report.py` |
| Команды CLI кроме `go`/`resume`, выбор папки прогона | `src/commands/` |
| Атомарная запись и чтение JSON | `src/fsio.py` |
