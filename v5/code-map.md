# Карта кода tero после рефакторинга (план 05, сдан 2026-10-24)

Где что лежит в `src/` и куда класть новый код. Планы v5 ссылаются сюда вместо старых путей вида
«менять `src/loop/phase.py`». Ищи код по смыслу, номера строк не важны.

## Правила структуры (их проверяет `tests/test_structure.py`)

- Потолки размера: файл не больше 400 строк, функция не больше 120. Исключения перечислены в
  `FILE_CEILINGS` / `FUNC_CEILINGS` и **только понижаются**. Поднять потолок или добавить ключ — находка.
- Если файл из `FILE_CEILINGS` стал меньше потолка на 20 строк и больше, потолок понижается до нового
  размера в той же фазе: иначе упадёт `test_ceilings_are_tight`. Это единственная разрешённая правка
  `tests/test_structure.py` без отдельного разрешения в фазе.
- Сейчас на потолке: `src/loop/phase.py` (849), `src/loop/run.py` (447). Новый код туда **не
  дописывается**. Если в них нужен вызов нового шага, освободи место: вынеси соседний код в модуль шага.
- Нет приватных функций без ссылок и неиспользуемых импортов.
- Новые модули не импортируют `src.loop.phase`, `src.loop.run`, `src.cli_entry` (без циклов). Шаг фазы
  получает `PhaseContext` и нужные колбэки в конструкторе, как `JudgeStep`.
- Атомарная запись файлов — только через `src/fsio.py` (`atomic_write_text`, `atomic_write_json`,
  `read_json_dict`). `mkstemp` в других местах запрещён (`tests/test_fsio.py`).
- Переход статуса пишется в `state.json`, `stream.jsonl` и `ledger.jsonl` одним вызовом из
  `src/loop/transition.py`: `phase_transition(ctx, ...)` внутри фазы, `resume_transition(...)` в `resume`.
  Своих помощников переходов не заводить (`test_transitions_in_one_module`).

## Точки подмены в тестах (контракт, сигнатуры не менять)

`src.loop.phase.run_all`, `src.loop.phase.rollback`, `src.loop.run.run`,
`src.loop.run.load_provider_configs`, `src.cli_entry.sys.exit`, `src.cli_entry.run_go`,
`src.loop.session.maybe_launch_hud`, `Path.replace`, `os.replace`, атрибуты `src.loop.snapshot`,
`agents.RETRY_DELAYS_S`, `agents.HEARTBEAT_S`. Повторный экспорт старых имён из `phase.py`, `run.py`,
`cli_entry.py` (`__all__`) сохраняется.

## Одна фаза плана

| Модуль | Что там |
|---|---|
| `src/loop/phase.py` | `run_phase` и `_PhaseRunner`: оркестратор раунда «исполнитель → защита тестов → гейт → судья → тестер». Здесь же `_rollback_with_snapshot`, `_ship`, `_halt_text`, `_write_stopped`, `_halt`, снимок после исполнителя, детектор «нет прогресса» |
| `src/loop/phase_ctx.py` | `PhaseContext` (входы, IO, состояние фазы; `emit`, `update_state`, `progress`, `check_reply`, `provider_error`), `PhaseState`, `PhaseOutcome`, `OpenFinding`, `_Next`, `K_MAX` |
| `src/loop/judge_step.py` | `JudgeStep`: снимок на входе в судью, вызов судьи с повторами, `NEEDS_USER`, три сбоя → `judge_pending`, применение вердикта, K_max и чистая попытка |
| `src/loop/judge_prompt.py` | промпт судьи, проверка контекста, вывод вердикта в `state.json` и консоль |
| `src/loop/findings.py` | чистые проверки над `PhaseState`: продолжение со снимка, «замечания не уменьшились», застой |
| `src/loop/compact_step.py` | `CompactStep`: порог контекста, запросы `inbox/compact.json`, смена сессии роли |
| `src/loop/tester_step.py` | `TesterStep`: красные тесты на находки судьи для тестера |
| `src/loop/suite_guard.py` | защита набора тестов и конфигов агентов от исполнителя |
| `src/loop/transition.py` | переходы статуса (см. выше) |
| `src/loop/prompts.py` | промпты исполнителя, судьи, тестера; `needs_user_reask_block` |
| `src/loop/agents.py` | вызов роли, разбор JSON, валидация, одна починка |
| `src/loop/snapshot.py` | снимки `refs/tero/<run>/phase-NN-roundNN` через временный индекс, `diff_for` |
| `src/loop/git.py` | git-операции прогона, `rollback`, `force_checkout_run_branch` |

## Прогон целиком

| Модуль | Что там |
|---|---|
| `src/loop/run.py` | `run` (`tero go`) и `resume` (`tero resume`), `_resume_phases`, `_restore_resume_tree`, `agy_preflight_check` |
| `src/loop/session.py` | общее у `run` и `resume`: `RunOutcome`, `RunSession` (лок, реестр, пульс, HUD только у `run`), `run_phases` — единственный цикл по фазам, `end_run`, `_resume_config` (что можно продолжить), `_refuse` |
| `src/loop/preflight.py` | проверки старта `tero go`: дерево, провайдеры, семья судьи, `agy` |
| `src/loop/report.py` | итоги фаз в консоль, `slugify` |
| `src/loop/state.py`, `stream.py`, `ledger.py`, `pulse.py`, `lock.py` | `state.json`, `stream.jsonl`, журнал, пульс владельца, лок репо |
| `src/loop/wait.py`, `detach.py` | `tero wait`/`status`, `go --detach` и `AGENT_PROTOCOL` |
| `src/loop/inbox.py`, `decision.py` | входящие ответы на точку решения, `Decision` и `actions_for` |
| `src/relay/iface.py` | протокол `RelayClient`, `NoopRelay`, `FakeRelay` |

## CLI

| Модуль | Что там |
|---|---|
| `src/cli_entry.py` | только парсер аргументов, `run_go`, `run_resume` и диспетчер команд (не больше 300 строк) |
| `src/commands/__init__.py` | `resolve_run_dir(args)` — папка прогона из `--run` или последняя |
| `src/commands/runs.py` | `history`, `wait`, `status`, `review` (только чтение) |
| `src/commands/inbox_cmds.py` | `answer`, `compact` (пишут в `inbox/`) |

Новая команда CLI: обработчик в `src/commands/<тема>.py`, в `cli_entry.py` только подпарсер и строка
диспетчера.

## Куда класть новый код плана 06

| Что | Куда |
|---|---|
| `resume` со снимка остановленного прогона | `src/loop/session.py` (`_resume_config`), шаг восстановления дерева — в `run.py` не растёт, вынести в новый `src/loop/resume_step.py` |
| точка решения вместо `_halt`, ожидание ответа, `answer`/`skip`/`stop`, долг | новый `src/loop/decision_step.py`; `_halt`, `_halt_text`, `_write_stopped` переезжают туда из `phase.py` |
| `more`/`model`/`retry`/`allow`/`wait`, бюджет | `src/loop/decision_step.py`, приём ответов — `src/loop/inbox.py` |
| таймауты ожидания, аренда, автопилот | новый `src/loop/autopilot.py`, аренду пишет `src/loop/wait.py` |
| `judge_pending` как ожидание | `src/loop/judge_step.py` (`_stop_judge_pending`) + `decision_step.py` |
| сторожевой поток `stop` | новый `src/loop/watchdog.py`; команда — `src/commands/inbox_cmds.py` |
| `resume` после краша | `src/loop/resume_step.py` |
| `guide`, `notify` | `src/commands/<тема>.py`, текст протокола — `src/loop/detach.py` |
| трейлер `Tero-Phase`, `phase_shipped` | `src/loop/git.py` |
