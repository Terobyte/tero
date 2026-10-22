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

## state.json, enum статуса, порядок старта

Создать `src/loop/state.py`, `src/loop/lock.py`, `tests/loop/test_state.py`, `tests/loop/test_lock.py`.
Менять `src/loop/run.py` (порядок старта), `src/loop/git.py` (`exclude_tero` раньше), `src/loop/phase.py`
(каждый `_halt` пишет `status=stopped` + `reason` + текст в `state.json`), `src/config.py` (`family_of`).

Интерфейсы:

- `class Status(str, Enum)` со значениями enum выше.
- `class RunState`: `path: Path`, `update(**fields) -> None` — атомарно (temp + rename), под `threading.Lock`;
  ошибка записи → одно предупреждение, не исключение. `read(path) -> dict | None`.
- Поля `state.json`: `status, reason, text, pid, pid_started, updated_at, plan, phases[{name,state}], phase, round,
  phase_started, role, provider, model, step_started, verdict{verdict,blocking,first{issue,file,line}}, tester,
  gate, snapshot_ref, base, doing`.
- `lock.acquire(repo) -> Lock | None` — неблокирующий `flock` на `<repo>/.tero/run.lock`.
- `config.family_of(spec) -> str` — `claude | gemini | openai | glm | muse | other`.

Порядок в `run()`: git-репо → `exclude_tero()` → `flock .tero/run.lock` → папка прогона + `state.json: starting`
→ проверки (дерево, провайдеры, план, тесты) с обновлением `doing` → `ledger.jsonl`.

- [ ] `test_update_is_atomic_and_never_raises`: папка недоступна → `update` не бросает, предупреждение один раз (пропуск под root)
- [ ] `test_fresh_clone_starts_without_dirty_tree`: клон без `.tero/` в exclude → `run()` доходит до `status=running`, а не «грязное дерево»
- [ ] реализовать `RunState`, `Status` и порядок старта в `run()`
- [ ] `test_two_owners_second_exits`: второй владелец того же репо не берёт лок и выходит
- [ ] `test_halt_writes_stopped_with_reason` для `user|stuck|fuse|provider_unavailable|secrets`
- [ ] `test_judge_same_family_as_player_rejected`: `family_of(judge) == family_of(player)` → отказ на старте с понятным текстом, семья видна в выводе
- [ ] провал старта (грязное дерево) → `status=stopped`, `reason`, код 1, папки нет в `history()`

## stream.jsonl и точки записи в phase.py

Создать `src/loop/stream.py`, `tests/loop/test_stream.py`. Менять `src/loop/phase.py`, `src/loop/agents.py`
(`_collect_text`/`_activity`), `src/loop/gates.py`.

Интерфейс: `Stream(path).emit(type, **fields)` дописывает целую `\n`-терминированную JSON-строку и не падает.
Типы: `tool|text|gate_start|gate|verdict|tester|ship|stop|start_step`.

- [ ] тест: `emit` пишет целые строки; конкурентный читатель (по смещению) не видит обрезанной строки, недописанную откладывает
- [ ] тест на `phase.py` со stub-провайдерами: после фазы в `stream.jsonl` есть `start_step`, `tool`, `gate_start`, `gate`, `verdict`, `ship` в правильном порядке
- [ ] тест: в `state.json` лежит вердикт из провалидированного JSON судьи (`_judge`), а не из прозы
- [ ] подключить `emit`/`state.update` там, где сейчас `ledger.event` или `print` статуса; вывод тестов по ходу в поток не стримить

## правило запуска HUD, хвост, реестр

Это T4a: только логика и тесты. Окно (`view.html`, вёрстка по макетам) — T4b, принимается человеком
позже; здесь достаточно, чтобы `tero hud --run <папка>` запускался и падал без `pywebview` с подсказкой.

Создать `src/hud/__init__.py`, `src/hud/app.py`, `src/hud/tail.py`, `src/hud/registry.py`,
`tests/hud/__init__.py`, `tests/hud/test_tail.py`, `tests/hud/test_launch_rule.py`, `tests/hud/test_registry.py`.
Менять `src/cli_entry.py` (`tero hud`, `--no-hud`), `src/config.py` (`hud: true`), `pyproject.toml`
(extra `hud = ["pywebview"]`).

Интерфейсы:

- `hud_should_launch(env, platform, flags, cfg) -> tuple[bool, str]`, правило по порядку: `--no-hud`/`hud: false` → нет;
  не macOS → нет; `SSH_CONNECTION` или `SSH_TTY` → нет; нет `pywebview` (`importlib.util.find_spec`) → нет + подсказка.
- `Registry` в `~/.tero/registry/<run_id>.json` (pid, pid_started, путь к `state.json`); пишут все запуски.
- `Tail(path).read_new() -> list[dict]` — по смещению; недописанную строку откладывает, битую пропускает,
  при усечении файла читает с нуля.

- [ ] `test_tail_*`: недописанная строка, битая строка, усечение файла
- [ ] `test_launch_rule_*`: по тесту на каждую ветку правила
- [ ] «прогон оборвался»: `status in (starting, running)` и нет процесса с этим `pid` и тем же `pid_started`; тест на подменённом `psutil`
- [ ] `tero go` запускает `tero hud --run <папка>` через `Popen(start_new_session=True, stdin=DEVNULL, stdout/stderr → .tero/hud.log)` и не ждёт; тест: `Popen` бросает → прогон идёт
- [ ] `test_registry_lists_live_runs_only`: при чтении реестр удаляет файлы мёртвых процессов (`pid_exists` + `pid_started`), свой файл удаляется в `finally` `run()`; тест на остатках после `kill -9`
