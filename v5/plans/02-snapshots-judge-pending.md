# tero v5, трек T, шаг 2: снимки работы, judge_pending, исполнитель со снимка

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Это задачи T5, T6, T7 из
`v5/tero-master-plan.md`; спека — `v5/tero-resilience-spec.md` §0–2 и `v5/tero-master-spec.md`
(блок «Единые решения» главнее частей A/B/C). Шаг 1 (`v5/plans/01-state-stream-hud.md`) уже сделан:
`state.json` (`RunState`), `stream.jsonl` (`Stream.emit`), лок прогона, `family_of`.

Главный принцип шага: **работа исполнителя никогда не выбрасывается.** Сейчас каждый `_halt` и стоп по
секретам в `_ship` делают `git.rollback` (`reset --hard` + `clean -fd`) — всё написанное стирается.
После шага: перед любым откатом лежит снимок в `refs/tero/...`, упавший судья не стирает код,
упавший исполнитель передаёт работу следующему.

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Абзац выше описывает цель всего шага из 10 фаз, а не требования к текущей фазе. Поведение, которое
делают следующие фазы (снимок после исполнителя, сверка дерева на входе в судью, `judge_pending`,
исполнитель со снимка, K_max), в текущей фазе не делать и не требовать: его отсутствие — не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** В песочнице судьи
перечисление процессов (`psutil`) запрещено, поэтому `tests/test_process_guard.py` и всё, что зависит от
`ProcessGuard.snapshot_pids`, там падает при исправном коде. Падение этих тестов в песочнице — не находка.
`src/process_guard.py` и `tests/test_process_guard.py` в этом шаге не меняются.

**Старые тесты, которые фаза явно называет в «Меняются старые тесты», переписываются под новое
поведение — это не ослабление набора.** Остальные старые тесты не правятся.

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
- Новых зависимостей не добавлять.
- Папку `docs/` не трогать.
- `git.rollback` остаётся низкоуровневым; логику снимка в него не встраивать.
- Снимок никогда не трогает рабочий индекс и рабочее дерево: только временный `GIT_INDEX_FILE`.
- Судья остаётся слепым (без диффа и отчёта исполнителя в промпте, см. `judge_prompt`) и читает
  рабочее дерево. Неизменяемость обеспечивается тем, что перед входом в судью дерево равно дереву снимка
  (сверка дерева с `<ref>^{tree}`), а не тем, что судья читает diff.
- Каждое событие в `stream.jsonl`, которое добавляет этот шаг, несёт `phase`, `round`, `role` и `text`.
  Каждое изменение статуса пишется и в `state.json`, и в `stream.jsonl`, и в `ledger.jsonl`, одним вызовом-помощником.

## take_snapshot

Создать `src/loop/snapshot.py`, `tests/loop/test_snapshot.py`.

- `take_snapshot(repo: Path, base: str, ref: str, deny: Iterable[str]) -> str` — возвращает sha коммита.
  Через временный `GIT_INDEX_FILE` (в `.git/`, удаляется в `finally`): `read-tree base` → `add -A` по tracked и
  новым не игнорируемым файлам → `rm --cached` для путей из deny → `write-tree` → `commit-tree -p base` →
  `update-ref <ref> <sha>`.
- Имя ref: `refs/tero/<run_id>/phase-NN-roundKK` (с `run_id`, чтобы прогоны не перетирали друг друга);
  строит его `snapshot.ref_name(run_id, phase_index, round)`.
- deny по умолчанию: `git.SECRET_PATTERNS` + `node_modules/` + `.tero/`; сопоставление — через `git.is_secret_path`
  и префиксы каталогов.

- [x] `test_snapshot_contains_tracked_and_new_files`: изменённый tracked, новый файл, удалённый файл → в `git ls-tree -r <ref>` ровно ожидаемое
- [x] `test_snapshot_excludes_deny_list`: `.env`, `key.pem`, `node_modules/x.js`, `.tero/state.json` в дереве → их нет в `git ls-tree -r <ref>`
- [x] `test_snapshot_survives_stale_index_lock`: создан `.git/index.lock` → снимок всё равно создан, `index.lock` и рабочий индекс (`git diff --cached`) не тронуты
- [x] `test_snapshot_leaves_worktree_alone`: после снимка `git status --porcelain` тот же, что до
- [x] реализовать `take_snapshot` и `ref_name`

## diff_for и проверка ref

Менять `src/loop/snapshot.py`, `tests/loop/test_snapshot.py`.

- `diff_for(repo, base, ref) -> str` — `git diff base ref` (без рабочего дерева).
- `verify(repo, ref) -> bool` — `git cat-file -e <ref>^{tree}`; снимок считается сделанным только после `verify`.
- `restore_snapshot(repo, ref)` — дерево и индекс становятся деревом ref, HEAD не двигается (через `git.restore_tree(<ref>^{tree})`).

- [x] `test_diff_for_ignores_worktree`: после снимка дерево изменено ещё раз → `diff_for` показывает только то, что в ref
- [x] `test_restore_snapshot_brings_back_work`: снимок → `rollback` → `restore_snapshot` → файлы снова на диске, HEAD = base
- [x] реализовать `diff_for`, `verify`, `restore_snapshot`

## Откат только со снимком

Менять `src/loop/phase.py`, тесты в `tests/loop/test_phase_snapshot.py`.

- `PhaseRunner._rollback_with_snapshot(reason) -> str | None`: снимок → `verify` → только потом `git.rollback`.
  Снимок упал или не прошёл `verify` → отката нет, дерево как есть, `_halt` пишет `stopped` с текстом
  «снимок не сделан, дерево не тронуто» и `reason` исходной остановки.
- Все вызовы `rollback` в `phase.py` (`_halt`, стоп по секретам в `_ship`) идут через него. При стопе по
  секретам deny-list и так вырезает секреты из снимка.
- В `state.json`: `snapshot_ref`, `base`; в `stream.jsonl` событие `snapshot` (`ref`, `base`, `reason`); в ledger — `snapshot`.
- Текст остановок в `_halt_text` меняется с «→ откат фазы» на «→ откат фазы, работа в `<ref>`».
- Вне этой фазы: снимок после возврата исполнителя (фаза 4), сверка дерева с ref на входе в судью (фаза 5),
  `judge_pending` и счётчик сбоев судьи (фаза 6), новый исполнитель со снимка и K_max (фазы 9–10).
  Здесь сбой судьи или исполнителя по-прежнему ведёт в `_halt`, но уже со снимком.

- [x] `test_halt_takes_snapshot_before_rollback`: stub-исполнитель пишет файл, фаза останавливается по `fuse` → ref есть, файл в ref, дерево откатано
- [x] `test_no_rollback_without_snapshot`: `take_snapshot` подменён на исключение → `rollback` не вызван, файл исполнителя на диске, `status=stopped` с текстом про снимок
- [x] `test_secrets_stop_snapshot_has_no_secrets`: исполнитель создал `.env` и код → стоп `secrets`, в ref есть код и нет `.env`
- [x] `test_snapshot_ref_in_state_stream_ledger`: после остановки `snapshot_ref` и `base` в `state.json`, событие `snapshot` в `stream.jsonl` и ledger
- [x] подключить `_rollback_with_snapshot` во все места отката

## Снимок при выходе исполнителя

Менять `src/loop/phase.py` (`_player`), `src/loop/agents.py` (если нужно вернуть признак «процесс мёртв»).

- Снимок делается после **любого** возврата исполнителя: успех, `blocked`, ответ не по схеме, таймаут,
  исключение провайдера. Только после того как `LiveAgents._call` отработал `guard.kill_new_processes`:
  к этому моменту процессы исполнителя мертвы, снимок фиксирует всё, что он успел записать.
- ref этого раунда перезаписывается снимком при входе в судью (следующая фаза), отдельного имени не нужно.
- Вне этой фазы: сверка дерева на входе в судью (фаза 5), `judge_pending` (фаза 6), новый исполнитель
  после падения вместо `_halt` (фаза 9), любые изменения `ProcessGuard` и `LiveAgents._call`. Что процессы
  исполнителя мертвы после `guard.kill_new_processes`, в этой фазе принимается как данность.
- Снимок не удался (`take_snapshot` бросил или `verify` ложь) → событие `snapshot_failed` в ledger и
  `stream.jsonl`, `snapshot_ref` в `state.json` очищается (не остаётся ref прошлого раунда); раунд идёт дальше.

- [x] `test_snapshot_after_player_timeout`: stub-исполнитель пишет файлы и висит → таймаут → в ref есть все записанные файлы
- [x] `test_snapshot_after_player_ok`: обычный раунд → после исполнителя ref раунда существует, `snapshot_ref` в `state.json`
- [x] `test_failed_player_snapshot_clears_ref`: `take_snapshot` бросает во 2-м раунде → `snapshot_failed` в ledger, в `state.json` нет ref 1-го раунда
- [x] реализовать

## Вход в судью по снимку

Менять `src/loop/phase.py` (`_judge`), `src/loop/snapshot.py`.

- На входе в судью: снимок раунда (`take_snapshot` + `verify`) → `snapshot_ref` в `state.json` → сверка
  дерева с `<ref>^{tree}` (иначе `restore_snapshot`) → вызов судьи.
- Сверка — **тем же фильтром, что и снимок**: `snapshot.tree_of(repo, base, deny) -> str` (тот же временный
  индекс, что в `take_snapshot`, без `commit-tree`/`update-ref`; `take_snapshot` переиспользует его).
  Не `git.write_tree`: тот видит файлы из deny (`.env`, `*secret*`, `node_modules/`), которых в ref нет,
  и сверка не сходилась бы никогда. Файлы из deny сверка не видит и не удаляет.
- Проверка «судья изменил дерево» после судьи — тоже `tree_of(...)` против дерева ref, а не `tree_before`;
  при изменении — `restore_snapshot(ref)` и событие `judge_tamper`.
- `state.json` во время судьи: `role=judge`, `provider`, `model`, `step_started`, `snapshot_ref`, всё до вызова.
- Вне этой фазы: повтор судьи при сбое и `judge_pending` (фаза 6), новый исполнитель со снимка (фаза 9).

- [x] `test_judge_sees_snapshot_tree`: дерево перед судьёй совпадает с `<ref>^{tree}`; stub-судья проверяет это сам и пишет в свой ответ
- [x] `test_judge_tamper_restores_snapshot`: stub-судья портит файл → после судьи дерево = дерево ref, событие `judge_tamper` в ledger
- [x] `test_untracked_secret_is_not_tamper`: исполнитель создал `.env` и код → на входе в судью и после него нет `restore` и `judge_tamper`, `.env` на диске; стоп `secrets` из фазы 3 работает как раньше
- [x] `test_state_before_judge_call`: stub-судья читает `state.json` в момент вызова → `role=judge`, `provider`, `model`, `step_started`, `snapshot_ref` = ref раунда
- [x] реализовать

## judge_pending после трёх сбоев судьи

Менять `src/loop/phase.py` (`run`, `_judge`, `_halt`), `src/loop/run.py`, тесты в `tests/loop/test_judge_pending.py`.

- Сбой судьи = провайдер недоступен, таймаут или ответ не по схеме (после встроенной попытки починки в `agents`).
- При сбое судья вызывается **снова на том же ref**, исполнитель не запускается, раунд не растёт,
  эскалация исполнителя не включается (сейчас `_Next.INVALID` → `_no_progress()` эскалирует исполнителя — это убрать;
  `_Next.UNAVAILABLE` судьи сейчас → `_halt("provider_unavailable")` — это тоже заменить повтором).
- Валидный вердикт обнуляет счётчик сбоев. 3 сбоя подряд → `status=judge_pending`, отката нет, дерево и ref
  нетронуты, `reason=judge_failed`, `text`: «код готов, не принят: `<ref>`; `tero review`, `tero resume`».
- `PhaseOutcome.status="judge_pending"`; `run()` прекращает прогон, `RunOutcome(status="judge_pending", exit_code=3)`.
  Лок отпускается (живое ожидание — это T11, не здесь).
- Модель судьи в прогоне не меняется.
- Событие `judge_failed` (`attempt`, `error`) в `stream.jsonl` и ledger на каждый сбой.
- Меняются старые тесты: `tests/loop/test_phase.py::test_invalid_judge_is_logged_with_errors_and_raw` — сейчас
  ждёт `stuck` и «эскалация» после двух мусорных ответов судьи. Логирование `invalid` (`errors`, `raw`,
  `judge.invalid.txt`) остаётся и проверяется как раньше; ожидание `stuck`/«эскалация» заменяется на
  повтор судьи без нового вызова исполнителя. Любой другой старый тест, который ждёт `stuck`, эскалацию или
  `provider_unavailable` от сбоя **судьи**, переписывается так же.

- [x] `test_garbage_judge_three_times_goes_judge_pending`: stub-судья трижды отвечает мусором → `status=judge_pending`, ref есть, дерево = дерево ref, код выхода 3, не `stuck`
- [x] `test_judge_retry_does_not_rerun_player`: stub-судья: мусор, затем SHIP → исполнитель вызван один раз, фаза сдана
- [x] `test_judge_unavailable_three_times_no_rollback`: провайдер судьи недоступен трижды → `judge_pending`, файлы исполнителя на диске
- [x] `test_judge_failure_does_not_escalate_player`: мусорный ответ судьи не включает эскалацию
- [x] реализовать

## tero review

Менять `src/cli_entry.py`; создать `tests/test_cli_review.py`.

- `tero review [-w <repo>] [--run <папка>]` — по умолчанию последний прогон из `.tero/runs`. Печатает статус,
  фазу, раунд, `snapshot_ref`, `base`, `git diff --stat` и полный `diff_for(base, ref)`. Только чтение.
- Нет `snapshot_ref` в `state.json` → понятный текст и код 1.

- [x] `test_review_prints_ref_and_diff`: прогон в `judge_pending` (подготовленный `state.json` + ref) → вывод содержит ref, base и строку из diff
- [x] `test_review_without_snapshot`: `state.json` без ref → код 1, текст «снимка нет»
- [x] реализовать команду

## tero resume с судьи

Менять `src/cli_entry.py`, `src/loop/run.py`, `src/loop/phase.py`; тесты в `tests/loop/test_resume_judge.py`.

- `tero resume [-w <repo>] [--run <папка>]` — единая точка восстановления, решает по `status` из `state.json`.
  В этом шаге поддержан только `judge_pending`; любой другой статус → текст «resume для `<status>` пока не умеет» и код 1.
- Конфиг: `run()` при старте пишет в `state.json` поле `config` — поля `Config`, кроме ключей и токенов
  (имена с `key`, `token`, `secret`). `resume` собирает `Config` из него; флаги провайдеров в CLI не нужны.
  Старый `state.json` без `config` → «прогон старше resume, запусти `tero go`» и код 1.
- `resume` продолжает **ту же папку прогона** (тот же `run_id`, `ledger.jsonl` и `stream.jsonl` дописываются),
  ветку прогона `tero/<slug>` и план; лок репо берётся как в `run()`.
- `judge_pending`: если дерево не совпадает с ref (`tree_of` из фазы 5), сначала снимок текущего дерева в
  `refs/tero/<run_id>/before-resume` → `restore_snapshot(ref)` → фаза продолжается **с судьи** на том же
  раунде: исполнитель не вызывается, гейт не перезапускается (ref уже прошёл гейт до `judge_pending`),
  `checkpoint` — `base` из `state.json`, открытые замечания прошлых раундов не восстанавливаются → дальше
  обычный цикл и следующие фазы. Точка входа — параметр `PhaseRunner` (например, `start_with_judge=True`).
- Судья получает тот код, что в ref, даже если дерево менялось после остановки.
- Тесты зовут функцию resume из `run.py` с фейками из `tests/loop/fakes.py`; через CLI-подпроцесс проверяется
  только отказ.

- [x] `test_resume_runs_only_judge`: прогон в `judge_pending`, дерево изменено вручную → `resume` → stub-исполнитель не вызван, дерево перед судьёй = дерево ref, SHIP → фаза сдана коммитом
- [x] `test_resume_keeps_manual_changes_in_ref`: ручные изменения до `resume` лежат в `refs/tero/<run_id>/before-resume`
- [x] `test_resume_other_status_refuses`: `status=done` → код 1, понятный текст
- [x] реализовать

## Исполнитель со снимка

Менять `src/loop/phase.py` (`run`, `_player`), `src/loop/prompts.py` (`player_prompt`); тесты в `tests/loop/test_player_resume.py`.

- Исполнитель упал (провайдер недоступен, таймаут, исключение) → не `_halt` сразу: снимок (уже есть из фазы
  «Снимок при выходе исполнителя»), дерево не откатывается, следующий раунд — **новый вызов** исполнителя
  (новая сессия, никакого `--continue`) с блоком в промпте:
  «Часть работы уже сделана предыдущим исполнителем, вот `git diff base..ref`. Продолжай, не переписывай готовое.»
- Блок держится во всех следующих раундах фазы, пока не будет SHIP или чистой попытки (фаза 10). Такие раунды —
  «раунды-продолжения».
- Дифф в промпте режется до 20 000 символов с пометкой «обрезано, полный дифф: `git diff <base> <ref>`».
- Гейт (тесты) на сыром аварийном снимке не запускается: после падения следующий шаг — исполнитель, не гейт и не судья.
- Два падения исполнителя подряд (два раунда подряд, в каждом исполнитель упал) → `_halt("provider_unavailable")`
  (со снимком, как раньше по смыслу).
- Меняются старые тесты: те, где единственный ответ исполнителя — `__unavailable__` и ожидается
  `provider_unavailable` после одного падения (например, `tests/loop/test_phase.py::test_provider_error_is_logged`,
  строка про `provider_unavailable` в `tests/loop/test_run.py`), получают второе падение подряд; проверки
  `provider_error` и текста остаются.

- [x] `test_second_player_gets_first_diff`: stub-исполнитель №1 пишет файл и падает → в промпте stub-исполнителя №2 есть его diff; `git cat-file -p <ref>` содержит файл
- [x] `test_no_gate_on_crash_snapshot`: после падения исполнителя гейт не вызывается до следующего раунда исполнителя
- [x] `test_two_player_crashes_halt`: два падения подряд → `stopped`, `provider_unavailable`, ref есть
- [x] реализовать

## K_max: чистая попытка

Менять `src/loop/phase.py`; тесты в `tests/loop/test_player_resume.py`.

- Считаются только раунды-продолжения (фаза 9), дошедшие до вердикта судьи NO_SHIP.
- «Замечания не уменьшились» = число `blocking` не стало меньше **и** множество `(file, line)` не сократилось
  (текст замечаний не сравниваем: LLM перефразирует).
- После `K_max = 2` таких раундов подряд → чистая попытка: `_rollback_with_snapshot("clean_retry")` к `checkpoint`,
  открытые замечания судьи остаются, блок «часть работы уже сделана» из промпта убирается. Старый ref остаётся.
- В раунде-продолжении `_stalled`/`_no_progress` не проверяются: там решает счётчик K_max (иначе `stuck`
  наступил бы на первом же ровном раунде и K_max никогда не сработал бы). Вне раундов-продолжений
  `_stalled`/`_no_progress`/эскалация работают как раньше и не меняются.

- [ ] `test_kmax_two_flat_rounds_clean_retry`: падение исполнителя → два раунда-продолжения с тем же `(file, line)` → откат к checkpoint, ref остался, следующий промпт без блока продолжения, не `stuck`
- [ ] `test_kmax_progress_resets`: замечаний стало меньше → счётчик обнулён, отката нет
- [ ] `test_kmax_rephrased_findings_count_as_flat`: тот же `(file, line)`, другой текст → считается «не уменьшились»
- [ ] реализовать
