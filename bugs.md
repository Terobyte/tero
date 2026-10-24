# Баги, найденные в ревью 2026-10-24

Ветка `tero-v4`. Ревью покрывало весь `src/` (~7k строк); все 443 существующих
теста зелёные, ruff чистый — ниже только то, что тесты не ловили.
Для каждого бага есть красный тест; тест станет зелёным только при реальном фиксе.

Красные тесты:
- `tests/loop/test_bugs_review.py`
- `tests/test_bugs_review_providers.py`

## Сводка

| ID | Приоритет | Где | Суть | Статус |
|----|-----------|-----|------|--------|
| BUG-001 | P1 | `src/loop/run.py:167` | любое исключение посреди фазы = краш без отката | закрыт 2026-10-24, тест зелёный |
| BUG-002 | P1 | `src/loop/gates.py:145` | не-UTF-8 вывод тестов роняет запуск | закрыт 2026-10-24, тест зелёный |
| BUG-003 | P2 | `src/providers/cursor.py:97` | промпт в argv → E2BIG на Linux | закрыт 2026-10-24, тест зелёный |
| BUG-004 | P2 | `src/loop/plan.py:48` | `[x]` по устаревшим строкам плана | закрыт 2026-10-24, тест зелёный |
| BUG-005 | P3 | `src/loop/run.py:54` | tester обязателен даже без тестов | закрыт 2026-10-24, тест зелёный |
| BUG-006 | P3 | `src/loop/gates.py:155` | таймаут гейта оставляет жить внуков | закрыт 2026-10-24, тест зелёный |
| BUG-007 | P3 | `src/loop/phase.py:622` | откат тестера не трогает gitignored-пути | закрыт 2026-10-24, тест зелёный |
| BUG-008 | P4 | `src/providers/__init__.py:81` | codex-фабрика форсирует effort=max | открыт, спорный |
| BUG-009 | P4 | `src/config.py:178` | метка GEMINI-3-LITE вместо 3.1 | закрыт 2026-10-24, тест зелёный |

## Детали

### BUG-001 · P1 — необработанное исключение посреди фазы рушит `tero go` без отката

- **Где:** `src/loop/run.py:167` — цикл фаз ловит только `KeyboardInterrupt`/`CancelledError`.
- **Триггер:** игрок удаляет `plan.md` → `plan_path.read_bytes()` в `_ship`
  (`src/loop/phase.py:707`) бросает `FileNotFoundError`. Работает для любого
  неожиданного исключения в `run_phase`.
- **Симптом:** traceback, ни `rollback`, ни `run_end` в ledger, изменения игрока
  остаются застейдженными (`_ship` успевает сделать `git add -A`). Следующий
  запуск блокируется проверкой «dirty worktree», пользователь чинит репозиторий
  вручную — при том, что весь дизайн построен на гарантированном откате (`_halt`).
- **Фикс:** широкий `except Exception` вокруг цикла фаз: rollback + событие в
  ledger + `RunOutcome(status="stop")`.
- **Red-тест:** `tests/loop/test_bugs_review.py::test_unexpected_phase_exception_rolls_back_and_stops`
  (сейчас падает с `FileNotFoundError`).

### BUG-002 · P1 — красный гейт падает на не-UTF-8 выводе тестов

- **Где:** `src/loop/gates.py:145` — `subprocess.run(..., text=True)` декодирует
  вывод строго; `_run` ловит только `OSError`/`TimeoutExpired`.
- **Симптом:** тест печатает невалидный UTF-8 байт (бинарный дифф в ассерте,
  вывод инструмента в другой локали) → `UnicodeDecodeError` выходит из
  `run_all` и роняет весь запуск по тому же пути без отката, что и BUG-001.
- **Фикс:** `errors="replace"` — провайдеры в `subprocess_runner.py:153` уже так делают.
- **Red-тест:** `tests/loop/test_bugs_review.py::test_gate_output_with_invalid_utf8_returns_result_not_crash`

### BUG-003 · P2 — cursor передаёт весь промпт одним argv

- **Где:** `src/providers/cursor.py:97` — `cmd.append(prompt)`.
- **Симптом:** у tester-промпта это полный diff фазы (`stage_and_diff`).
  На Linux одиночный аргумент ограничен ~128 КБ (`MAX_ARG_STRLEN`) → exec
  падает с E2BIG, обе попытки исчерпываются, запуск встаёт по
  `provider_unavailable`. Бонус: промпт в argv виден в `ps`.
- **Фикс:** передавать промпт через stdin, как все остальные провайдеры;
  собственный комментарий `src/providers/gemini.py:57` вводит stdin именно
  из-за этого лимита.
- **Red-тест:** `tests/test_bugs_review_providers.py::test_cursor_prompt_travels_via_stdin_not_argv`

### BUG-004 · P2 — `[x]` расставляются по устаревшим номерам строк, план не защищён

- **Где:** `src/loop/plan.py:48` (`mark_phase_done`) — номера строк из парсинга
  на старте прогона; файл плана не входит ни в `_GUARDED_NAMES`, ни в
  suite-пути, промпт игрока его не запрещает.
- **Симптом:** игрок редактирует/сдвигает строки плана посреди фазы →
  `mark_phase_done` отмечает не те строки: чекбоксы фазы остаются `[ ]`
  или, при другом сдвиге, отмечается чужая фаза — при возобновлении фазы
  молча пропускаются. Удаление плана — это краш BUG-001.
- **Фикс:** сопоставлять чекбоксы по содержимому, а не по номерам строк
  (или защищать план как guarded-файл).
- **Red-тест:** `tests/loop/test_bugs_review.py::test_plan_edited_midphase_marks_own_checkboxes`

### BUG-005 · P3 — готовность tester-провайдера требуется даже без тестов

- **Где:** `src/loop/run.py:54` — `check_ready` прогоняет все роли безусловно.
- **Симптом:** без тестовой команды `tester_enabled=False` и tester никогда не
  вызывается (маршрут judge подменяется на player, `src/loop/phase.py:526`),
  но запуск всё равно падает с «tester provider (gemini) not ready».
- **Фикс:** проверять tester только когда есть тестовая команда со scoped-режимом.
- **Red-тест:** `tests/loop/test_bugs_review.py::test_run_without_tests_skips_tester_readiness`

### BUG-006 · P3 — таймаут гейта не убивает процессы-внуки

- **Где:** `src/loop/gates.py:155` — на `TimeoutExpired` `subprocess.run`
  SIGKILL-ит только прямой потомок.
- **Симптом:** сервер/демон, порождённый тестами, переживает таймаут и остаётся
  висеть (держит порты/файлы). Провайдеры решают это через
  `start_new_session=True` + `killpg` (`subprocess_runner.py:113`), гейт — нет.
- **Фикс:** `start_new_session=True` + `os.killpg` по таймауту.
- **Red-тест:** `tests/loop/test_bugs_review.py::test_gate_timeout_kills_grandchildren`
  (внук пишет маркер-файл, если пережил килл).

### BUG-007 · P3 — откат тестера не трогает gitignored-пути

- **Где:** `src/loop/phase.py:622` (`_revert_non_test`) — перебирает
  `git ls-files -co --exclude-standard`, ignored-пути в список не попадают.
- **Симптом:** tester может изменить/создать файл в gitignored-пути (вплоть до
  `.env`) — изменение переживает и откат нетестовых файлов, и `rollback`
  (`clean -fd` без `-x` ignored не трогает).
- **Фикс:** снапшотить ignored-пути перед ходом тестера и восстанавливать, как
  это уже делает механизм guarded-конфигов.
- **Red-тест:** `tests/loop/test_bugs_review.py::test_tester_cannot_modify_ignored_paths`

### BUG-008 · P4 — codex-фабрика форсирует `model_reasoning_effort="max"` (спорный)

- **Где:** `src/providers/__init__.py:81` — дефолт фабрики `"max"`, тогда как
  `CodexConfig` (`src/providers/codex.py:43`) документирует `""` = «наследовать
  из `~/.codex/config.toml`».
- **Симптом:** любой codex без явного `model_reasoning_effort` в config.yaml
  получает `-c model_reasoning_effort=max`, молча перекрывая config.toml
  пользователя.
- **Оговорка:** комментарий в `src/menu.py:24` называет дефолт фабрики «max»
  намеренным. Фиксировать тестом не стали — сначала решить, что целевое поведение.
- **Red-тест:** нет.

### BUG-009 · P4 — метка модели `GEMINI-3-LITE` вместо `GEMINI-3.1-LITE`

- **Где:** `src/config.py:178` — ветка `"gemini-3-flash"` затеняет
  `gemini-3.1-flash-lite` (copy-paste); соседний `gemini-3.5-flash-lite`
  сохраняет версию → `GEMINI-3.5-LITE`.
- **Red-тест:** `tests/test_bugs_review_providers.py::test_short_model_name_gemini_3_1_flash_lite`

## Проверено и НЕ баг (чтобы не переоткрывать)

- `subprocess.run(timeout=...)` после килла **не виснет** на пайпах: POSIX-ветка
  не перечитывает вывод, `run_all` возвращается ровно по таймауту
  (проверено экспериментом). Осталась только утечка внуков — BUG-006.
- Неотслеженные (untracked) файлы игрока, удалённые тестером, **восстанавливаются
  корректно**: `write_tree` стейджит их до хода тестера, поэтому они остаются
  видны `ls-files -c` и `_revert_path` возвращает их из дерева.

## Запаркованные заметки после фикса (advisory от судьи, не блокеры)

- Ctrl-C во время прогона гейта больше не убивает прямой дочерний процесс
  (`_run` на Popen без kill в interrupt-пути; до фикса внуки всё равно
  утекали — см. BUG-006).
- `_snapshot_ignored` читает все gitignored-файлы в память на каждый ход
  тестера; prune покрывает только `.git/.tero/node_modules` — репозиторий с
  тяжёлым ignored-артефактами (venv, build) платит за это.
- Игнорируемые symlink в снапшот не попадают: заменённый тестером symlink
  удаляется без восстановления исходной ссылки.
- `ruff check .` имеет 14 предсуществующих ошибок в старых тестовых файлах
  (`tests/loop/test_agents.py`, `test_gemini_provider.py`, `test_hunt_procs.py`,
  `test_menu_smoke.py`, `test_opencode_provider.py`, `test_subprocess_runner.py`) —
  вне этого фикса.
