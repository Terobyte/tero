# tero v5, трек T, шаг 1: состояние, поток, правило запуска HUD

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Это задачи T1, T2, T4a из
`v5/tero-master-plan.md`; спека — `v5/tero-master-spec.md` (блок «Единые решения» главнее частей A/B/C),
часть A — HUD, часть B — пульт. Записка о хозяевах и лимитах — `v5/hosts-and-limits.md`.

Правила для всех фаз:

- Каждый сбой сначала воспроизводится тестом, красный запуск виден, потом код.
- Настоящих LLM в тестах нет: исполнитель, судья, тестер — stub-скрипты или фейки из `tests/loop/fakes.py`.
  Репо — одноразовые, в `tmp_path`.
- Тесты: `PYTHONPATH=. python3 -m pytest -q` из корня. Подпроцессы CLI в коде и тестах —
  `[sys.executable, "-m", "src.cli_entry", ...]`, не `tero` из `PATH`.
- Запись `state.json`/`stream.jsonl` никогда не роняет прогон. Без HUD tero работает как раньше.
- Единый enum статуса: `starting | running | waiting_human | waiting_agent | waiting_limit | judge_pending | done | stopped`.
- Номера строк в спеке условные; ищи семантические точки («после возврата исполнителя», «перед rollback», «вход в судью»).
- Тесты, зависящие от прав (`chmod`), пропускаются под root.
- Новых зависимостей, кроме необязательного extra `hud = ["pywebview"]`, не добавлять.
- Папку `docs/` не трогать.

## Status и RunState

Создать `src/loop/state.py`, `tests/loop/test_state.py`.

- `class Status(str, Enum)` со значениями enum выше.
- `class RunState`: `path: Path`, `update(**fields) -> None` — атомарно (temp + rename), под `threading.Lock`; ошибка записи → одно предупреждение, не исключение. `read(path) -> dict | None`.
- Поля `state.json`: `status, reason, text, pid, pid_started, updated_at, plan, phases[{name,state}], phase, round, phase_started, role, provider, model, step_started, verdict{verdict,blocking,first{issue,file,line}}, tester, gate, snapshot_ref, base, doing`.

- [x] `test_update_is_atomic_and_never_raises`: папка недоступна → `update` не бросает, предупреждение один раз (пропуск под root)
- [x] реализовать `Status` и `RunState`

## Лок прогона

Создать `src/loop/lock.py`, `tests/loop/test_lock.py`.

- `lock.acquire(repo) -> Lock | None` — неблокирующий `flock` на `<repo>/.tero/run.lock`.

- [x] `test_two_owners_second_exits`: второй владелец того же репо не берёт лок и выходит
- [x] реализовать `acquire`

## family_of и запрет одной семьи

Менять `src/config.py`, тесты в `tests/test_config.py` (или рядом с существующими).

- `config.family_of(spec) -> str` — `claude | gemini | openai | glm | muse | other`.

- [x] `test_family_of_*` по значению на семью
- [x] `test_judge_same_family_as_player_rejected`: `family_of(judge) == family_of(player)` → отказ на старте с понятным текстом, семья видна в выводе
- [x] реализовать `family_of` и проверку на старте

## Порядок старта run()

Менять `src/loop/run.py`, `src/loop/git.py` (`exclude_tero` раньше).

Порядок в `run()`: git-репо → `exclude_tero()` → `flock .tero/run.lock` → папка прогона + `state.json: starting` → проверки (дерево, провайдеры, план, тесты) с обновлением `doing` → `ledger.jsonl`.

- [ ] `test_fresh_clone_starts_without_dirty_tree`: клон без `.tero/` в exclude → `run()` доходит до `status=running`, а не «грязное дерево»
- [ ] провал старта (грязное дерево) → `status=stopped`, `reason`, код 1, папки нет в `history()`
- [ ] поменять порядок старта

## Остановки пишут stopped

Менять `src/loop/phase.py`.

- [ ] `test_halt_writes_stopped_with_reason` для `user|stuck|fuse|provider_unavailable|secrets`
- [ ] каждый `_halt` пишет `status=stopped` + `reason` + текст в `state.json`

## Stream.emit

Создать `src/loop/stream.py`, `tests/loop/test_stream.py`.

Интерфейс: `Stream(path).emit(type, **fields)` дописывает целую `\n`-терминированную JSON-строку и не падает. Типы: `tool|text|gate_start|gate|verdict|tester|ship|stop|start_step`.

- [ ] тест: `emit` пишет целые строки; конкурентный читатель (по смещению) не видит обрезанной строки, недописанную откладывает
- [ ] реализовать `Stream`

## Запись потока в phase.py

Менять `src/loop/phase.py`, `src/loop/agents.py` (`_collect_text`/`_activity`), `src/loop/gates.py`.

- [ ] тест на `phase.py` со stub-провайдерами: после фазы в `stream.jsonl` есть `start_step`, `tool`, `gate_start`, `gate`, `verdict`, `ship` в правильном порядке
- [ ] тест: в `state.json` лежит вердикт из провалидированного JSON судьи (`_judge`), а не из прозы
- [ ] подключить `emit`/`state.update` там, где сейчас `ledger.event` или `print` статуса; вывод тестов по ходу в поток не стримить

## Tail

Создать `src/hud/__init__.py`, `src/hud/tail.py`, `tests/hud/__init__.py`, `tests/hud/test_tail.py`.

- `Tail(path).read_new() -> list[dict]` — по смещению; недописанную строку откладывает, битую пропускает, при усечении файла читает с нуля.

- [ ] `test_tail_*`: недописанная строка, битая строка, усечение файла
- [ ] реализовать `Tail`

## Правило запуска HUD

Создать `src/hud/app.py`, `tests/hud/test_launch_rule.py`. Менять `src/cli_entry.py` (`tero hud`, `--no-hud`), `src/config.py` (`hud: true`), `pyproject.toml` (extra `hud = ["pywebview"]`).

- `hud_should_launch(env, platform, flags, cfg) -> tuple[bool, str]`, правило по порядку: `--no-hud`/`hud: false` → нет; не macOS → нет; `SSH_CONNECTION` или `SSH_TTY` → нет; нет `pywebview` (`importlib.util.find_spec`) → нет + подсказка.
- Окно (`view.html`) — T4b, здесь не делать: `tero hud --run <папка>` без `pywebview` падает с подсказкой.

- [ ] `test_launch_rule_*`: по тесту на каждую ветку правила
- [ ] реализовать правило и команду `tero hud`

## Реестр запусков

Создать `src/hud/registry.py`, `tests/hud/test_registry.py`.

- `Registry` в `~/.tero/registry/<run_id>.json` (pid, pid_started, путь к `state.json`); пишут все запуски.

- [ ] «прогон оборвался»: `status in (starting, running)` и нет процесса с этим `pid` и тем же `pid_started`; тест на подменённом `psutil`
- [ ] `test_registry_lists_live_runs_only`: при чтении реестр удаляет файлы мёртвых процессов (`pid_exists` + `pid_started`), свой файл удаляется в `finally` `run()`; тест на остатках после `kill -9`
- [ ] реализовать `Registry`

## tero go запускает HUD

Менять `src/cli_entry.py` / `src/loop/run.py`.

- [ ] `tero go` запускает `tero hud --run <папка>` через `Popen(start_new_session=True, stdin=DEVNULL, stdout/stderr → .tero/hud.log)` и не ждёт; тест: `Popen` бросает → прогон идёт
