# tero v5: план реализации (HUD + ядро пульта + отказоустойчивость + agy + телефон)

> **Статус на 2026-10-24.** Сделано: T0, T1, T2, T4a, T5–T8, T8c, T9a, T9b слой 1, T10, T11a частично (inbox,
> `tero answer`) и рефакторинг (план 05) — планы в `v5/archive/`. Дальше: `v5/plans/06-pult-decisions.md`
> (8 фаз: `resume` со снимка остановленного прогона, затем T11a–c и T12). Человеку в конце: T3, T4b, T9b
> слой 2; T13–T15 ещё не расписаны.
>
> **Код после рефакторинга:** пути в «Files» ниже для T10–T15 даны по `v5/code-map.md`. `src/loop/phase.py` и
> `src/loop/run.py` на потолке размера (`tests/test_structure.py`): новый код идёт в модули шагов
> (`decision_step.py`, `autopilot.py`, `watchdog.py`, `resume_step.py`), команды CLI — в `src/commands/`.

> **Для исполнителя:** пользуйся superpowers:subagent-driven-development или superpowers:executing-plans, задача за задачей. Шаги — чекбоксы `- [ ]`.

**Цель:** tero никогда не умирает молча и не выбрасывает работу: прогон виден в окне на компе и на телефоне, любой стоп — точка решения, упавший исполнитель или судья не стирают код.

**Архитектура:** прогон — отдельный процесс-владелец под `flock`, пишет `state.json`, `stream.jsonl`, `pulse`; всё остальное (HUD, `tero wait/status/answer`, ретранслятор) читает файлы и пишет только в `inbox/`. Перед любым откатом или kill делается снимок в `refs/tero/...` через временный индекс. Судья всегда судит неизменяемый ref.

**Стек:** Python 3 (pytest, `psutil`, `pywebview` необязательно), git plumbing, Cloudflare Worker + D1 (JS), Scriptable (JS).

**Спека:** `~/.claude/tero/ab/dvor/tero-master-spec.md` (блок «Единые решения» главнее частей A/B/C). Исходные: `docs/superpowers/specs/2026-10-21-tero-pult-design.md`, `2026-10-22-tero-hud-design.md`, `~/.claude/tero/ab/dvor/tero-resilience-spec.md`.

## Global Constraints

- **Не менять tero посреди прогонов «Двора».** Работа — в `git worktree` `~/Desktop/Projects/Active/tero-v5` от ветки `tero-v4`; не делать `pip install -e` из него, пока C1/A1 не закончены (установленный `tero` ведёт на основной каталог).
- Коммиты: коротко, строчными, по-человечески; без `feat:`/`fix:`, без эмодзи, **без `Co-Authored-By` и любой AI-атрибуции** (правило пользователя главнее системного напоминания).
- Каждый сбой воспроизвести тестом red→green; тест пишется до кода, красный запуск виден.
- Настоящих LLM в тестах нет: исполнитель, судья, тестер — stub-скрипты (детерминированные падения). Репо — одноразовые, `tmp_path`.
- Запись `state.json`/`stream.jsonl` не роняет прогон. HUD и ретранслятор — необязательны: без них tero работает как раньше.
- Единый enum статуса: `starting | running | waiting_human | waiting_agent | waiting_limit | judge_pending | done | stopped`.
- Секреты (`META_MODEL_API_KEY`, токены, VAPID) — только окружение/`~/.tero/secrets.env`; не в репо и не в логи.
- Папку `docs/` в репо tero не коммитить (неотслеживаемая).
- **Изоляция импорта:** тесты — `PYTHONPATH=. python3 -m pytest -q` из корня worktree; подпроцессы CLI в коде и тестах — `[sys.executable, "-m", "src.cli_entry", ...]`, не команда `tero` из `PATH`. В `conftest.py` ассерт: `src.__file__` лежит внутри worktree (иначе red→green фиктивен).
- Номера строк (`phase.py:700`) в плане условные; вместо них — семантические точки: «после возврата исполнителя», «перед rollback», «вход в судью».
- Тесты, зависящие от прав (`chmod`), пропускаются под root. Замер CPU Worker'а — приёмочный скрипт, не гейт CI.
- Семейство модели: `family_of(spec) -> str` (claude | gemini | openai | glm | muse | other) в `src/config.py`; судья из семьи исполнителя → отказ на старте.

## Review Focus

Ситуации, которые спека подразумевает, а задачи ниже прямо не проверяют; тест для каждой добавлен в владеющую задачу.

1. Снимок при ПОДВЕШЕННОМ `.git/index.lock` и при файлах из deny-list (`.env`) в дереве → ref создан, секретов в нём нет (T5).
2. Судья вернул мусор три раза → `judge_pending`, дерево и ref нетронуты, `resume` запускает только судью (T6).
3. `kill -9` владельца во время `--detach`: HUD не показывает «оборвался» по pid родителя; `resume` не делает двойника (T10, T12).
4. Два ответа на один вопрос одновременно (телефон и агент) → принят один, второй получает «уже отвечено» (T9).
5. Первый `tero go` в свежем клоне без `.tero/` в exclude: старт не падает на «грязное дерево» (T1).

---

## Порядок исполнения: сначала всё, что делает tero, человек в конце

Номера задач не меняются, меняется очередь. Правило: всё, что проверяется тестами или скриптом, идёт в **треке T** (tero/агенты, без человека). Всё, что требует глаз, руки, телефон или аккаунт, откладывается в **трек Ч** и собирается в один блок в конце.

**Трек T (без человека), по порядку:**
1. T0 (замеры CLI агентом) · T1 · T2 · T4a (тесты; окно 4b пишется, но не принимается).
2. T5 · T6 · T7 · T8 · T8c (устойчивость, компакт) · T9a (код `agy`, тесты на stub'ах, реальный pre-flight) · T9b слой 1 (адаптеры).
3. T10 · рефакторинг (план 05, сдан) · T12a `resume` со снимка остановленного прогона · T11a/b/c · T12 (ядро пульта).
4. T13a/b/c (Worker на `wrangler dev`, без деплоя) · T14 и T15 (код, тесты; PWA проверяется скриншотом из playwright).
5. Гейт между шагами: полный `PYTHONPATH=. python3 -m pytest -q` + судья (не Muse).

**Трек Ч (человек), один блок в конце, по порядку:**
1. T3 спайк фокуса (до него окно HUD открывается свёрнутым в фоне: безопасный запасной вариант из T3) + приёмка T4b по списку части A.
2. T9b слой 2: разбор расхождений в метках (два прохода) и утверждение порога годности. Запуск экзамена только после окончания «Двора».
3. `wrangler login`, деплой Worker'а, `tero relay setup`, `tero pair` (аккаунт Cloudflare, один раз).
4. Чек-лист T14 на айфоне и установка виджета T15 в Scriptable.

Что нельзя вынести в конец: ничего из трека Ч не блокирует трек T, потому что T3 имеет запасной вариант, а деплой Worker'а нужен только для реального телефона.

---

## Веха M0. Исследование (без кода)

### Task 0: записка «как спать и как будить»

**Files:** Create: `docs/notes/hosts-and-limits.md` (в worktree, не коммитить `docs/`: положить в `~/.claude/tero/ab/dvor/`).

- [ ] Замерить для Claude Code, opencode, Muse: фоновая команда будит агента по завершении? максимальная длина одного вызова? можно ли разбудить сессию снаружи (`--resume`)? Результат — по абзацу на хозяина.
- [ ] Для каждого провайдера (`claude`, `codex`, `opencode`, `agy`, `muse`) записать: что CLI сообщает о лимитах/токенах/времени сброса; чего нет — «неизвестно».
- [ ] Записать, что `agy models` на 2026-10-22 даёт `gemini-3.8-flash-*`, `claude-sonnet-4-6`, `claude-opus-4-6-thinking`, `gpt-oss-120b-medium` (Gemini 3.1 Pro нет).
- **Готово, когда** записка лежит и из неё выбран `wake_command` по умолчанию для каждого хозяина.

---

## Веха M1. Ядро состояния и HUD (раунд 0)

### Task 1: `state.json`, enum статуса, порядок старта

**Files:**
- Create: `src/loop/state.py`, `src/loop/lock.py` (минимум: `acquire(repo) -> Lock | None`, `flock` на `<repo>/.tero/run.lock`), `tests/loop/test_state.py`, `tests/loop/test_lock.py`
- Modify: `src/loop/run.py` (порядок старта), `src/loop/git.py` (`exclude_tero` раньше), `src/loop/phase.py` (каждый `_halt` пишет `status=stopped` + `reason` + текст в `state.json`), `src/config.py` (`family_of`)

**Interfaces:**
- Produces: `class Status(str, Enum)` с значениями enum выше; `class RunState` с `path: Path`, `update(**fields) -> None` (атомарно: temp+rename, под `threading.Lock`, ошибка записи → одно предупреждение, не исключение), `read(path) -> dict | None`.
- Поля `state.json`: `status, reason, text, pid, pid_started, updated_at, plan, phases[{name,state}], phase, round, phase_started, role, provider, model, step_started, verdict{verdict,blocking,first{issue,file,line}}, tester, gate, snapshot_ref, base, doing`.

- [ ] Тест `test_update_is_atomic_and_never_raises` (папка недоступна → `update` не бросает, предупреждение один раз; пропуск под root).
- [ ] Тест `test_fresh_clone_starts_without_dirty_tree`: клон без exclude → `run()` доходит до `status=running`, не «грязное дерево» (Review Focus 5). Запустить: красный.
- [ ] Реализовать `RunState`, `Status`; в `run()` порядок: git-репо → `exclude_tero()` → `flock .tero/run.lock` → папка прогона + `state.json: starting` → проверки (дерево, провайдеры, план, тесты) с обновлением `doing` → `ledger.jsonl`.
- [ ] Тест `test_two_owners_second_exits` (лок репо, T10 его только расширяет). Тест `test_halt_writes_stopped_with_reason` для `user|stuck|fuse|provider_unavailable|secrets`: HUD позже не примет стоп за краш.
- [ ] Тест `test_judge_same_family_as_player_rejected`: `family_of(judge)==family_of(player)` → отказ на старте с понятным текстом; в `status` семья видна.
- [ ] Тест: провал старта (грязное дерево) → `status=stopped`, `reason`, код 1, папки нет в `history()`.
- [ ] `pytest -q` зелёный; коммит `state.json и порядок старта`.

### Task 2: `stream.jsonl` и точки записи в `phase.py`

**Files:** Create `src/loop/stream.py`, `tests/loop/test_stream.py`; Modify `src/loop/phase.py`, `src/loop/agents.py` (`_collect_text`/`_activity`), `src/loop/gates.py`.

**Interfaces:** `Stream(path).emit(type, **fields)` — дописывает целую строку `\n`-терминированную, не падает. Типы: `tool|text|gate_start|gate|verdict|tester|ship|stop|start_step`.

- [ ] Тест: `emit` пишет целые строки; конкурентный читатель не видит обрезанной строки (читает по смещению, недописанную откладывает).
- [ ] Тест на `phase.py` со stub-провайдерами: после фазы в `stream.jsonl` есть `start_step`, `tool`, `gate_start`, `gate`, `verdict`, `ship` в правильном порядке; в `state.json` — вердикт из провалидированного JSON судьи (`_judge`).
- [ ] Подключить `emit`/`state.update` в местах, где сейчас `ledger.event` или `print` статуса. Вывод тестов по ходу не стримить.
- [ ] Зелёный; коммит `stream.jsonl и снимки состояния по ходу фазы`.

### Task 3: спайк — окно HUD без кражи фокуса (timebox 1 рабочий день)

**Files:** Create `spikes/hud_focus.py` (вне пакета).

- [ ] Открыть окно `pywebview` поверх всех, пока в другом приложении печатается текст; проверить, что ни один символ не потерян.
- [ ] Не держит → `pyobjc`: accessory-приложение без иконки в доке, non-activating panel. Не держит за день → решение: окно открывается свёрнутым в фоне (записать в `hud/README`), идти дальше.
- **Готово, когда** выбран один из двух способов открытия и записан в коммите спайка.

### Task 4a: правило запуска, хвост, реестр (тесты) · Task 4b: окно (ручная приёмка)

**Files:**
- Create: `src/hud/__init__.py`, `src/hud/app.py`, `src/hud/view.html` (один самодостаточный HTML, тёмная тема), `src/hud/tail.py` (чтение хвоста по смещению), `src/hud/registry.py`, `tests/hud/test_tail.py`, `tests/hud/test_launch_rule.py`, `tests/hud/test_registry.py`
- Modify: `src/cli_entry.py` (`tero hud`, `--no-hud`), `src/config.py` (`hud: true`), `pyproject.toml` (extra `hud = ["pywebview"]`)

**Interfaces:**
- `hud_should_launch(env, platform, flags, cfg) -> tuple[bool, str]` — правило по порядку: `--no-hud`/`hud:false` → нет; не macOS → нет; `SSH_CONNECTION|SSH_TTY` → нет; нет `pywebview` (через `importlib.util.find_spec`) → нет + подсказка.
- `Registry` в `~/.tero/registry/<run_id>.json` (pid, pid_started, путь к `state.json`); пишут все запуски; HUD берёт «+1 прогон» оттуда.
- `Tail(path).read_new() -> list[dict]` — по смещению, недописанную строку откладывает, битую пропускает, при усечении файла читает с нуля.

- [ ] Тесты `test_tail_*` (недописанная строка, битая, усечение), `test_launch_rule_*` (каждая ветка), `test_registry_lists_live_runs_only`.
- [ ] «Умер»: `status in (starting, running)` и нет процесса с `pid` + тем же `pid_started` → «прогон оборвался». Тест на подменённом `psutil`.
- [ ] `tero go` запускает `tero hud --run <папка>` через `Popen(start_new_session=True, stdin=DEVNULL, stdout/stderr → .tero/hud.log)` и не ждёт; ошибка запуска не влияет на прогон (тест: `Popen` бросает → прогон идёт).
- [ ] Реестр: при чтении удаляет файлы мёртвых процессов (`pid_exists` + `pid_started`); свой файл удаляется в `finally` `run()`. Тест на `kill -9`-остатках.
- [ ] **4b.** Вёрстка по макетам `hud-collapsed.html`/`hud-expanded.html`: круг-секторы фаз, иконка момента (🛠⚖️🧪⏱❓✓), «Фаза N/M · раунд · время», роль и модель, «что делает» из хвоста `stream.jsonl`, «последнее событие N с назад». Статусы и поля, которых ещё нет (`judge_pending`, долг), рисуются заглушкой по неизвестному статусу (серый); их иконки добавляют T6 (⏸ + ref) и T11 (⚠ сдано с долгом N) вместе с тестом на поле.
- [ ] Одно окно на машину (lock-файл в `~/.tero/`), второй запуск переключает окно на свой прогон.
- [ ] Ручная приёмка по списку части A (1–9; п.8 работает за счёт `_halt` из T1); у процесса HUD нет слушающих сокетов (`lsof -i -P -p <pid>`).
- [ ] Коммит `hud: окно прогона`.

---

## Веха M2. Снимки и устойчивость судьи/исполнителя

### Task 5: снимок работы

**Files:** Create `src/loop/snapshot.py`, `tests/loop/test_snapshot.py`; Modify `src/loop/phase.py` (порядок: kill исполнителя → подтвердить смерть pid → `take_snapshot` → только потом `git.rollback`). `git.py:rollback` остаётся низкоуровневым, бизнес-логику снимка в него не встраиваем.

**Interfaces:**
- `take_snapshot(repo: Path, base: str, phase: int, round: int, deny: list[str]) -> str` — возвращает имя ref `refs/tero/phase-N-roundK`. Через временный `GIT_INDEX_FILE`: `git add -A` по tracked+новым не игнорируемым (без deny-list), `write-tree`, `commit-tree` (родитель = base), `update-ref`.
- `diff_for(repo, base, ref) -> str`.

- [ ] Тест `test_snapshot_survives_stale_index_lock` (создать `.git/index.lock`; снимок всё равно создан; рабочий индекс не тронут) — Review Focus 1.
- [ ] Тест `test_snapshot_excludes_deny_list`: `.env`, `*.pem`, `node_modules/` в дереве → `git cat-file -p <ref>^{tree}` не содержит их.
- [ ] Тест: в `phase.py` откат без снимка невозможен (обёртка `rollback_with_snapshot`; `take_snapshot` подменён на падение → откат не выполнен).
- [ ] Тест: снимок делается после смерти pid исполнителя (stub-исполнитель пишет файлы до kill; в ref — всё).
- [ ] Реализовать; ref и `base` пишутся в `state.json` и ledger. Снимок: при выходе исполнителя (любом), входе в судью, перед откатом/kill. Перед снимком — убедиться, что процесс исполнителя мёртв.
- [ ] Зелёный; коммит `снимок работы в refs/tero перед откатом`.

### Task 6: `judge_pending` и судья по ref

**Files:** Modify `src/loop/phase.py` (`_judge`), `src/loop/run.py`, `src/cli_entry.py` (`tero resume`, `tero review`); Create `tests/loop/test_judge_pending.py`.

**Interfaces:** `judge(repo, base, ref) -> Verdict` читает только `diff_for(repo, base, ref)`, дерево не читает. 3 подряд сбоя (таймаут, мусор, не по схеме) → `status=judge_pending`, откат не делается. Модель судьи в прогоне не меняется.
**Объём (по критике):** в M2 `judge_pending` — состояние в `state.json` + завершение процесса с понятным кодом; `tero resume` и `tero review` работают по нему. В T11 то же состояние становится живым ожиданием (процесс держит лок, `wait`=3, автопилот зовёт судью); контракт `state.json` не меняется, переписывается только цикл ожидания. HUD: иконка ⏸ + ref добавляется здесь.

- [ ] Тест `test_garbage_judge_three_times_goes_judge_pending` (Review Focus 2): stub-судья отвечает мусором; после 3 → `judge_pending`, ref есть, дерево без изменений, код выхода понятный, не `stuck`.
- [ ] Тест `test_resume_runs_only_judge`: `tero resume` на `judge_pending` не вызывает исполнителя; судья получает diff из ref, даже если дерево менялось после падения.
- [ ] Реализовать вход `tero resume` как единую точку восстановления (по статусу); `tero review` печатает ref и diff.
- [ ] Зелёный; коммит `judge_pending: код не выбрасывается при падении судьи`.

### Task 7: новый исполнитель со снимка, K_max

**Files:** Modify `src/loop/phase.py`, `src/loop/prompts.py`; Create `tests/loop/test_player_resume.py`.

- [ ] Тест: stub-исполнитель №1 убит на середине → в промпте stub-исполнителя №2 лежит `git diff base..ref` первого (проверяем промпт и `git cat-file -p ref`); сессия новая (нет `--continue`).
- [ ] Тест: тесты на сыром аварийном снимке не запускаются до раунда нового исполнителя.
- [ ] Тест: «замечания не уменьшились» = `blocking` не стал меньше И множество `(file, line)` не сократилось (текст замечаний не сравниваем: LLM перефразирует). После K_max=2 таких раундов — чистая попытка, старый ref остаётся.
- [ ] Реализовать. Коммит `новый исполнитель стартует со снимка`.

### Task 8: прочая устойчивость

**Files:** Modify `src/loop/agents.py`, `src/providers/subprocess_runner.py`, `src/config.py`, `src/errors.py`; Create `tests/loop/test_resilience_misc.py`.

- [ ] Тест+код `stall_timeout_s` (нет вывода N минут → kill); для провайдеров без потока не применяется (флаг `streams = False` у провайдера).
- [ ] Тест+код пауза 1→2→5→10→60 мин с `max_retries` и общим дедлайном 2 ч; auth/quota-ошибки → немедленный стоп.
- [ ] Тест: `TimeoutError` не печатается как «провайдер недоступен».
- [ ] Тест: `--no-escalation` + `NEEDS_USER` → 1 переспрос, 1 свежий судья, третий → `judge_pending` «нужен человек» (нет цикла).
- [ ] Тест: ключи `config.yaml` вне `defaults:` → предупреждение.
- [ ] Коммит по пункту на тест.

---

### T8c. Автокомпакт и принудительный компакт (добавлено 2026-10-22)

Спека: `tero-resilience-spec.md` §7. Файлы: `src/loop/compact.py` (счётчик контекста по `stream.jsonl`, решение «пора»),
команда `tero compact [--role player|judge]`, поле `compact_requested` в `state.json`, проброс `--autocompact N` в провайдер `claude`.
Механика: на границе шага снимок (T5) и новый вызов роли с брифом (контракт + `git diff base..ref` + замечания судьи дословно).
Тесты на stub-провайдерах: порог превышен, `tero compact` во время вызова, замечания дословно, раунд K_max не растёт, `--autocompact` в argv.
Зависит от T1, T2, T5, T7. Трек T. В T0 добавить замер: есть ли у `opencode` нативный compact (`--attach`/server API).

## Веха M3. Провайдер `agy`

### Task 9a: провайдер `agy`

**Files:** Create `src/providers/agy.py`, `tests/test_agy_provider.py`; Modify `src/cli_entry.py`/`src/config.py` (проверить, что `--judge провайдер:модель` жив в v4; нет — завести флаг и ключ конфига с тестом), `src/providers/__init__.py` (`create_provider`: `agy`; `gemini` → алиас на `agy` с предупреждением), `src/providers/gemini.py` (удалить или оставить тонким алиасом).

**Interfaces:** `AgyProvider.run(prompt, schema, role)`: `agy -p <prompt> --model <m> --json-schema <schema> --output-format json --print-timeout <N>s`; судья — режим без записи, исполнитель/тестер — `--dangerously-skip-permissions`. Невалидный JSON = сбой судьи (T6). Не более одного вызова `agy` одновременно в прогоне. `streams = False` (T8).

- [ ] Тест на stub-бинаре `agy` (скрипт в `tmp_path`, `PATH`): валидный JSON → вердикт; мусор → сбой; формат `--print-timeout` с единицей (`900s`).
- [ ] Pre-flight: `agy -p "ок"` до очереди; нет входа/токен протух → стоп с понятным текстом, не зависание.
- [ ] Дефолтная модель для судьи: `gemini-3.8-flash-high` (из `agy models`); имя проверять `agy models` на старте, нет в списке → предупреждение.
- [ ] Ручной тест готовности: `tero go --judge agy:gemini-3.8-flash-high` на малом плане возвращает валидный вердикт.
- [ ] Коммит `провайдер agy`.

### Task 9b: экзамен судей через `Provider`

**Files:** Create `tools/judge_exam/run.py`, `tools/judge_exam/cases/` (25–30 диффов + `labels.json`), `tests/test_judge_exam_adapter.py`.

- [ ] Слой 1: каждый CLI (`agy`, `claude`, `codex`, `opencode`) отвечает валидным вердиктом на тривиальном диффе (тест на stub'ах).
- [ ] Слой 2: качество. Кейсы — явные баги, тонкие, чистые; часть вне «Двора» (holdout). Метки — два независимых прохода, расхождения разбирает человек. Порог годности записан в `labels.json` ДО замеров.
- [ ] Метрики: найденные баги, ложные отклонения, ответы не по схеме, время. Вывод — таблица.
- [ ] Коммит `экзамен судей`.

---

## Веха M4. Ядро пульта

### Task 10: владелец, `--detach`, пульс, `wait`, `status`

**Files:** Create `src/loop/lock.py`, `src/loop/pulse.py`, `src/loop/wait.py`, `tests/loop/test_lock.py`, `test_pulse.py`, `test_wait.py`; Modify `src/cli_entry.py` (`go --detach`, `wait`, `status`, `stop`, `guide`), `src/loop/run.py`. После рефакторинга обработчики команд — в `src/commands/`, общий старт и цикл фаз — в `src/loop/session.py`.

**Interfaces:**
- `acquire(run_dir) -> Lock | None` (`flock` неблокирующий; pid, `pid_started` в `state.json`).
- Поток пульса: `alive` раз в 30 с; `progress` после завершённого шага. Отдельный `threading.Thread`.
- `tero wait [--timeout S]` коды: `0` готово, `4` с долгом, `2` остановлен, `3` нужен агент (`waiting_agent` **и `judge_pending`**), `5` мёртв/завис, `6` таймаут.
- Один `threading.Lock` на `state.json` и git. Сторожевой поток при `stop` сам шлёт `SIGTERM`→`SIGKILL` группе процессов шага (`process_guard.py`; главный поток в это время заблокирован в ожидании подпроцесса), а откат со снимком делает главный поток после возврата, увидев флаг отмены.
- `--detach`: финальный процесс-владелец пишет `state.json` после форка; сон удерживается фоновым `caffeinate -i -w <pid владельца>` (не `exec`: `caffeinate cmd` запускает cmd дочерним процессом и меняет pid); родитель `state.json` не трогает.
- `watch`: когда `wait` впервые увидел текущее `progress` (id загрузки системы + монотонное время), чтобы цепочка коротких `wait --timeout` не обнуляла окно зависания.
- `tero notify "текст"` и часовой отчёт агента (флаг в конфиге, по умолчанию выкл.) — команда в CLI, отправка через клиент ретранслятора (`NoopRelay`, пока M5 нет).

- [ ] Тест `test_two_owners_second_exits` (flock).
- [ ] Тест `test_detach_hud_does_not_flash_dead` (Review Focus 3): после detach `pid` в `state.json` = pid владельца; проверка «умер» HUD не срабатывает.
- [ ] Тесты `wait` на подменённых часах (монотонное время): мёртв → 5 только при незавершённом статусе; завис по `progress`; сон машины (часы стоят) не смерть; `done`/`stopped` → 0/4/2 даже без процесса.
- [ ] Тесты `watch`: цепочка коротких `wait` не сбрасывает окно зависания; «мёртв» = pid нет И flock свободен (неблокирующая проверка), занятый лок при отсутствии pid — не мёртв.
- [ ] `status --json`: фаза, раунд, роль, статус, открытый вопрос, журнал, долг, возраст пульса, аренда агента, жив ли пуш-канал.
- [ ] Коммит по подзадачам.

### Task 11a: точки решения + inbox + answer · 11b: ожидание, аренда, wake_command · 11c: автопилот, бюджет, долг, трейлер

**Files:** Create `src/loop/decision.py` и `src/loop/inbox.py` (сделаны), `src/loop/decision_step.py` (сюда переезжают `_halt`, `_halt_text`, `_write_stopped` из `phase.py`; каждый `_halt` → точка решения), `src/loop/autopilot.py`, `tests/loop/test_decision.py`, `test_inbox.py`, `test_autopilot.py`; Modify `src/loop/session.py` (`run_phases`: долг, код 4), `src/loop/judge_step.py` (`judge_pending` как ожидание), `src/loop/wait.py` (аренда), `src/loop/git.py` (трейлер `Tero-Phase`, пустой коммит).

**Interfaces:**
- `RelayClient` (протокол в `src/relay/iface.py`): `push_state(state)`, `fetch_answers(question_id) -> list[Answer]`, `notify(text)`; в M4 есть `NoopRelay` и `FakeRelay` для тестов, реальный клиент — T13.
- `Decision(id="<фаза>-<номер>", question, actions)`; действия: `answer|more|model|retry|allow|wait|skip|stop` по таблице пульта.
- `inbox/<uuid>.json` → владелец применяет в одной атомарной записи `state.json` (эффект + бюджет + UUID), затем `inbox/<uuid>.result`.
- Бюджет 3 на фазу (агент/автопилот), человек без бюджета. `model judge` отклоняется на приёме; `model` для `player|tester` — со следующего раунда.
- Автопилот: `skip`, но вторая подряд проблемная фаза → `stop`; для `judge_pending` — только «позвать судью снова» (в бюджет 3), потом `skip` в долг; «принять вручную»/«отклонить» — только человек.
- Дерево не откатывается, пока ждём; перед `skip/stop/retry-после-secrets` — снимок (T5).

- [ ] Тест `test_two_answers_one_wins` (Review Focus 4): телефон и агент ответили одновременно → принят первый, второй получает «уже отвечено: <кто, что>»; перед приёмом локального ответа владелец опрашивает `FakeRelay` — человек выигрывает.
- [ ] `judge_pending` становится живым ожиданием (процесс держит лок, `wait`=3); поле долга в `state.json` + иконка HUD «⚠ сдано с долгом N» с тестом.
- [ ] Тесты: `answer` попадает в промпт только в размеченном блоке ≤2000 симв.; `more` даёт 5 раундов без стопа по «нет прогресса»; `secrets` + `allow` (только человек) + `retry` (пути в промпт «не создавать»); `model judge` отклонён; автопилот не пропускает фазы цепочкой.
- [ ] Тест: сдача фазы только по трейлеру `Tero-Phase: <run>/<N>`; пустая фаза — пустой коммит.
- [ ] Тест ожидания: `waiting_human` → через `human_timeout` `waiting_agent` (вопрос остаётся открытым) → аренда истекла → пуш-предупреждение → автопилот через 15 мин (10 без ретранслятора и агента).
- [ ] Аренда агента (`lease`) хранит pid сессии агента; смерть pid → досрочное истечение; `wake_command` с фиксированным промптом, действия только `retry|skip|stop` с `--why`, один раз на вопрос.
- [ ] Коммит по подзадачам.

### Task 12a: `resume` со снимка остановленного прогона (добавлено 2026-10-24, идёт перед T11)

Любой стоп сейчас кладёт работу исполнителя в снимок и откатывает дерево, а `tero resume` умеет только
`judge_pending`. 12a продолжает остановленный прогон с уже написанного кода: дерево из снимка → судья по
снимку → исполнитель продолжает с этого кода. Действует без флага `decisions`; точки решения T11 потом
делают то же без выхода из процесса.

**Files:** Modify `src/loop/session.py` (`_resume_config`), `src/cli_entry.py` (`run_resume`, `--answer`), `src/loop/prompts.py` (размеченный блок ответа — его же берёт `answer` в T11a); Create `src/loop/resume_step.py`, `tests/loop/test_resume_stopped.py`.

- [ ] `stopped` со снимком и причиной `user|stuck|fuse|provider_*` → `resume`: судья первым по ref, дальше исполнитель с блоком продолжения; ещё `max_rounds` раундов, номера и снимки старых раундов не затираются.
- [ ] Причина `user` требует `--answer`; ответ — только в размеченном блоке промптов судьи и исполнителя.
- [ ] Отказы: нет снимка, `secrets`, `done` (прежний текст). Сбой настройки возвращает прежний `stopped`.
- [ ] Коммит.

### Task 12: `resume` после краша, `stop` во время шага

**Files:** Modify `src/loop/resume_step.py` (из 12a), `src/loop/session.py`, `src/loop/pulse.py`, `src/loop/decision_step.py`, `src/cli_entry.py` (только подпарсеры и флаг `--force`); Create `src/loop/watchdog.py`, `src/commands/stop_cmd.py`, `src/commands/guide_cmds.py`, `tests/loop/test_resume_crash.py`, `test_stop_midstep.py`. `src/loop/run.py` не растёт.

- [ ] Тесты: краш между тиком плана и коммитом; краш между коммитом и записью состояния; краш в точке решения (дерево совпало с sha → ожидание восстановлено, тот же id и вопрос); зависший владелец (pid жив, условия «завис» выполнены → kill группы, взять лок); pid жив, но не завис → отказ «прогон жив».
- [ ] Счётчик крашей: на третьем `resume` отказ, `stopped`; `resume --force` обходит.
- [ ] `stop` во время шага: сторожевой поток (inbox раз в 5 с, ретранслятор раз в 60 с) убивает группу процессов шага сам, взводит флаг; главный поток после возврата делает снимок и откат. Тест: stub-исполнитель спит 10 мин, `stop` останавливает за секунды.
- [ ] `tero guide` печатает протокол агента и раздел «как спать» из T0.
- [ ] Коммит по подзадачам.

---

## Веха M5. Ретранслятор и телефон

### Task 13a: Worker + D1 + фильтр · 13b: pair и токены · 13c: cron, пуш, `RelayClient`

**Files:** Create `relay/src/worker.js`, `relay/schema.sql`, `relay/wrangler.toml`, `relay/test/*.test.js`, `src/relay/client.py`, `tests/test_relay_client.py`, `src/commands/relay_cmds.py` (`tero relay setup`, `tero pair`); Modify `src/cli_entry.py` (только подпарсеры).

- [ ] Тесты на `wrangler dev` (cron через `--test-scheduled`): состояние по ключу `run_id`+`machine_id`; чтение только по токену (read-токен виджета отдельно); ответы: id + допустимое действие + текст; приём с лимитом длины.
- [ ] Фильтр секретов на отправке: подстроки `sk-`, `AKIA`, `-----BEGIN`, длинные hex/base64 в любом месте; без блоков кода и диффов; пути секретов — только имя файла. Тест: токен посреди 500-символьной находки вырезан.
- [ ] `tero pair`: QR/ссылка, код 15 мин, одноразовый; токены на устройство, хеш в D1, `--revoke`, `--reset`.
- [ ] Cron раз в 5 мин: пульс старше 15 мин при незавершённом статусе → один пуш «нет связи», отметка `stale_notified`.
- [ ] Замер CPU цепочки «принять → отфильтровать → пуш» < 10 мс — приёмочный скрипт `relay/bench.js`, не гейт CI; не влезает — менять библиотеку, не тариф.
- [ ] `tero relay setup`: ключ записи и VAPID в `~/.tero/secrets.env`, `wrangler secret`, адрес в `~/.tero/config.yaml`. `wrangler login` делает Temirlan.
- [ ] **13c.** Реальный `RelayClient` вместо `NoopRelay`: push состояния при изменении статуса/фазы/вопроса, пульс раз в 5 мин, опрос ответов раз в 10 с только в `waiting_*`; `tero notify`; endpoint списка активных прогонов (по `run_id`). Тесты на `wrangler dev`.
- [ ] Коммит по подзадачам.

### Task 14: Web push и страница-пульт

**Files:** Create `relay/src/push.js` (Web Crypto, VAPID P-256, `aud`=origin, `exp`≤24 ч), `relay/public/index.html`, `manifest` (генерится Worker'ом, код в `start_url`), `relay/public/sw.js`.

- [ ] Тест: 404/410 от push-сервиса → подписка мертва, `tero status` показывает «пуш-канал сломан — перепривяжи».
- [ ] PWA: список активных прогонов с переключением; список моделей для `model` (без `judge`); «уже отвечено», журнал решений, долг, история, состояние пуш-канала; корень всегда показывает открытый вопрос первым; `requestPermission()` только по кнопке «Включить пуши» из режима standalone; `phone_text_answers: false` по умолчанию (кнопки `more|skip|retry|stop`); `stop` с подтверждением; пуш ≤200 симв.; повторы одной причины не чаще раза в 30 мин.
- [ ] Ручной чек-лист на айфоне (привязка Safari→PWA, кнопка пушей, пуш пришёл, ответ дошёл, «нет связи» один раз).
- [ ] Коммит.

### Task 15: виджет Scriptable и лимиты

**Files:** Create `widget/tero.js`; Modify `src/loop/state.py` (`limits`), `src/providers/*` (что CLI сообщает), страницу-пульт.

- [ ] Виджет читает read-токен из `args.widgetParameter` (в коде токена нет); блокировка: 2–3 строки; домашний: подробнее, тап → Safari.
- [ ] Лимиты в `status` и на странице: что CLI сообщает (T0), остальное «неизвестно».
- [ ] Коммит.

---

## Self-review

- **Покрытие спеки:** HUD (T1–T4), «Единые решения» 1–15 (1: T1; 2: T6; 3: T5; 4–5: T11; 6: T1; 7–8: T10; 9: T11; 10: T13–14; 11: T4; 12: порядок вех; 13: T4 без кнопок; 14: T4; 15: T3), часть C (T5–T9), пульт (T10–T15), лимиты (T0, T15).
- **Расхождения порядка:** M1 (состояние+HUD) → M2 (снимки) → M4 (пульт) → M5; `agy` (M3) не зависит от M4 и идёт после M2.
- **Гейты:** между вехами — полный `pytest -q` и ручная приёмка M1 (HUD).
- **Вне плана:** параллельные прогоны, очки/браслет, Live Activity, сквозное шифрование.
