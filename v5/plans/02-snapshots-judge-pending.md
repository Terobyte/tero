# tero v5, трек T, шаг 2: снимки работы, judge_pending, исполнитель со снимка

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Это задачи T5, T6, T7 из
`v5/tero-master-plan.md`; спека — `v5/tero-resilience-spec.md` §0–2 и `v5/tero-master-spec.md`
(блок «Единые решения» главнее частей A/B/C). Шаг 1 (`v5/plans/01-state-stream-hud.md`) уже сделан:
`state.json` (`RunState`), `stream.jsonl` (`Stream.emit`), лок прогона, `family_of`.

Главный принцип шага: **работа исполнителя никогда не выбрасывается.** Сейчас каждый `_halt` и стоп по
секретам в `_ship` делают `git.rollback` (`reset --hard` + `clean -fd`) — всё написанное стирается.
После шага: перед любым откатом лежит снимок в `refs/tero/...`, упавший судья не стирает код,
упавший исполнитель передаёт работу следующему.

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
  (сверка `write-tree` с `<ref>^{tree}`), а не тем, что судья читает diff.
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

- [ ] `test_snapshot_contains_tracked_and_new_files`: изменённый tracked, новый файл, удалённый файл → в `git ls-tree -r <ref>` ровно ожидаемое
- [ ] `test_snapshot_excludes_deny_list`: `.env`, `key.pem`, `node_modules/x.js`, `.tero/state.json` в дереве → их нет в `git ls-tree -r <ref>`
- [ ] `test_snapshot_survives_stale_index_lock`: создан `.git/index.lock` → снимок всё равно создан, `index.lock` и рабочий индекс (`git diff --cached`) не тронуты
- [ ] `test_snapshot_leaves_worktree_alone`: после снимка `git status --porcelain` тот же, что до
- [ ] реализовать `take_snapshot` и `ref_name`

## diff_for и проверка ref

Менять `src/loop/snapshot.py`, `tests/loop/test_snapshot.py`.

- `diff_for(repo, base, ref) -> str` — `git diff base ref` (без рабочего дерева).
- `verify(repo, ref) -> bool` — `git cat-file -e <ref>^{tree}`; снимок считается сделанным только после `verify`.
- `restore_snapshot(repo, ref)` — дерево и индекс становятся деревом ref, HEAD не двигается (через `git.restore_tree(<ref>^{tree})`).

- [ ] `test_diff_for_ignores_worktree`: после снимка дерево изменено ещё раз → `diff_for` показывает только то, что в ref
- [ ] `test_restore_snapshot_brings_back_work`: снимок → `rollback` → `restore_snapshot` → файлы снова на диске, HEAD = base
- [ ] реализовать `diff_for`, `verify`, `restore_snapshot`

## Откат только со снимком

Менять `src/loop/phase.py`, тесты в `tests/loop/test_phase_snapshot.py`.

- `PhaseRunner._rollback_with_snapshot(reason) -> str | None`: снимок → `verify` → только потом `git.rollback`.
  Снимок упал или не прошёл `verify` → отката нет, дерево как есть, `_halt` пишет `stopped` с текстом
  «снимок не сделан, дерево не тронуто» и `reason` исходной остановки.
- Все вызовы `rollback` в `phase.py` (`_halt`, стоп по секретам в `_ship`) идут через него. При стопе по
  секретам deny-list и так вырезает секреты из снимка.
- В `state.json`: `snapshot_ref`, `base`; в `stream.jsonl` событие `snapshot` (`ref`, `base`, `reason`); в ledger — `snapshot`.
- Текст остановок в `_halt_text` меняется с «→ откат фазы» на «→ откат фазы, работа в `<ref>`».

- [ ] `test_halt_takes_snapshot_before_rollback`: stub-исполнитель пишет файл, фаза останавливается по `fuse` → ref есть, файл в ref, дерево откатано
- [ ] `test_no_rollback_without_snapshot`: `take_snapshot` подменён на исключение → `rollback` не вызван, файл исполнителя на диске, `status=stopped` с текстом про снимок
- [ ] `test_secrets_stop_snapshot_has_no_secrets`: исполнитель создал `.env` и код → стоп `secrets`, в ref есть код и нет `.env`
- [ ] `test_snapshot_ref_in_state_stream_ledger`: после остановки `snapshot_ref` и `base` в `state.json`, событие `snapshot` в `stream.jsonl` и ledger
- [ ] подключить `_rollback_with_snapshot` во все места отката

## Снимок при выходе исполнителя

Менять `src/loop/phase.py` (`_player`), `src/loop/agents.py` (если нужно вернуть признак «процесс мёртв»).

- Снимок делается после **любого** возврата исполнителя: успех, `blocked`, ответ не по схеме, таймаут,
  исключение провайдера. Только после того как `LiveAgents._call` отработал `guard.kill_new_processes`:
  к этому моменту процессы исполнителя мертвы, снимок фиксирует всё, что он успел записать.
- ref этого раунда перезаписывается снимком при входе в судью (следующая фаза), отдельного имени не нужно.

- [ ] `test_snapshot_after_player_timeout`: stub-исполнитель пишет файлы и висит → таймаут → в ref есть все записанные файлы
- [ ] `test_snapshot_after_player_ok`: обычный раунд → после исполнителя ref раунда существует, `snapshot_ref` в `state.json`
- [ ] реализовать

## Вход в судью по снимку

Менять `src/loop/phase.py` (`_judge`).

- На входе в судью: снимок раунда (`take_snapshot` + `verify`) → `snapshot_ref` в `state.json` → сверка
  `write_tree(repo) == <ref>^{tree}` (иначе `restore_snapshot`) → вызов судьи.
- Проверка «судья изменил дерево» сравнивает с деревом ref, а не с `tree_before`; при изменении — `restore_snapshot(ref)`.
- `state.json` во время судьи: `role=judge`, `provider`, `model`, `step_started`, `snapshot_ref`, всё до вызова.

- [ ] `test_judge_sees_snapshot_tree`: дерево перед судьёй совпадает с `<ref>^{tree}`; stub-судья проверяет это сам и пишет в свой ответ
- [ ] `test_judge_tamper_restores_snapshot`: stub-судья портит файл → после судьи дерево = дерево ref, событие `judge_tamper` в ledger
- [ ] реализовать

## judge_pending после трёх сбоев судьи

Менять `src/loop/phase.py` (`run`, `_judge`, `_halt`), `src/loop/run.py`, тесты в `tests/loop/test_judge_pending.py`.

- Сбой судьи = провайдер недоступен, таймаут или ответ не по схеме (после встроенной попытки починки в `agents`).
- При сбое судья вызывается **снова на том же ref**, исполнитель не запускается, раунд не растёт,
  эскалация исполнителя не включается (сейчас `_Next.INVALID` → `_no_progress()` эскалирует исполнителя — это убрать).
- Валидный вердикт обнуляет счётчик сбоев. 3 сбоя подряд → `status=judge_pending`, отката нет, дерево и ref
  нетронуты, `reason=judge_failed`, `text`: «код готов, не принят: `<ref>`; `tero review`, `tero resume`».
- `PhaseOutcome.status="judge_pending"`; `run()` прекращает прогон, `RunOutcome(status="judge_pending", exit_code=3)`.
  Лок отпускается (живое ожидание — это T11, не здесь).
- Модель судьи в прогоне не меняется.
- Событие `judge_failed` (`attempt`, `error`) в `stream.jsonl` и ledger на каждый сбой.

- [ ] `test_garbage_judge_three_times_goes_judge_pending`: stub-судья трижды отвечает мусором → `status=judge_pending`, ref есть, дерево = дерево ref, код выхода 3, не `stuck`
- [ ] `test_judge_retry_does_not_rerun_player`: stub-судья: мусор, затем SHIP → исполнитель вызван один раз, фаза сдана
- [ ] `test_judge_unavailable_three_times_no_rollback`: провайдер судьи недоступен трижды → `judge_pending`, файлы исполнителя на диске
- [ ] `test_judge_failure_does_not_escalate_player`: мусорный ответ судьи не включает эскалацию
- [ ] реализовать

## tero review

Менять `src/cli_entry.py`; создать `tests/test_cli_review.py`.

- `tero review [-w <repo>] [--run <папка>]` — по умолчанию последний прогон из `.tero/runs`. Печатает статус,
  фазу, раунд, `snapshot_ref`, `base`, `git diff --stat` и полный `diff_for(base, ref)`. Только чтение.
- Нет `snapshot_ref` в `state.json` → понятный текст и код 1.

- [ ] `test_review_prints_ref_and_diff`: прогон в `judge_pending` (подготовленный `state.json` + ref) → вывод содержит ref, base и строку из diff
- [ ] `test_review_without_snapshot`: `state.json` без ref → код 1, текст «снимка нет»
- [ ] реализовать команду

## tero resume с судьи

Менять `src/cli_entry.py`, `src/loop/run.py`, `src/loop/phase.py`; тесты в `tests/loop/test_resume_judge.py`.

- `tero resume [-w <repo>] [--run <папка>]` — единая точка восстановления, решает по `status` из `state.json`.
  В этом шаге поддержан только `judge_pending`; любой другой статус → текст «resume для `<status>` пока не умеет» и код 1.
- `judge_pending`: тот же конфиг, план и ветка прогона, лок репо → если дерево не совпадает с ref,
  сначала снимок текущего дерева в `refs/tero/<run_id>/before-resume` → `restore_snapshot(ref)` → фаза
  продолжается **с судьи** (исполнитель не вызывается) на том же раунде, `checkpoint` — `base` из `state.json` → дальше обычный цикл и следующие фазы.
- Судья получает тот код, что в ref, даже если дерево менялось после остановки.

- [ ] `test_resume_runs_only_judge`: прогон в `judge_pending`, дерево изменено вручную → `resume` → stub-исполнитель не вызван, дерево перед судьёй = дерево ref, SHIP → фаза сдана коммитом
- [ ] `test_resume_keeps_manual_changes_in_ref`: ручные изменения до `resume` лежат в `refs/tero/<run_id>/before-resume`
- [ ] `test_resume_other_status_refuses`: `status=done` → код 1, понятный текст
- [ ] реализовать

## Исполнитель со снимка

Менять `src/loop/phase.py` (`run`, `_player`), `src/loop/prompts.py` (`player_prompt`); тесты в `tests/loop/test_player_resume.py`.

- Исполнитель упал (провайдер недоступен, таймаут, исключение) → не `_halt` сразу: снимок (уже есть из фазы
  «Снимок при выходе исполнителя»), дерево не откатывается, следующий раунд — **новый вызов** исполнителя
  (новая сессия, никакого `--continue`) с блоком в промпте:
  «Часть работы уже сделана предыдущим исполнителем, вот `git diff base..ref`. Продолжай, не переписывай готовое.»
- Дифф в промпте режется до 20 000 символов с пометкой «обрезано, полный дифф: `git diff <base> <ref>`».
- Гейт (тесты) на сыром аварийном снимке не запускается: после падения следующий шаг — исполнитель, не гейт и не судья.
- Два падения исполнителя подряд → `_halt("provider_unavailable")` (со снимком, как раньше по смыслу).

- [ ] `test_second_player_gets_first_diff`: stub-исполнитель №1 пишет файл и падает → в промпте stub-исполнителя №2 есть его diff; `git cat-file -p <ref>` содержит файл
- [ ] `test_no_gate_on_crash_snapshot`: после падения исполнителя гейт не вызывается до следующего раунда исполнителя
- [ ] `test_two_player_crashes_halt`: два падения подряд → `stopped`, `provider_unavailable`, ref есть
- [ ] реализовать

## K_max: чистая попытка

Менять `src/loop/phase.py`; тесты в `tests/loop/test_player_resume.py`.

- Считаются только раунды-продолжения после падения исполнителя, дошедшие до судьи.
- «Замечания не уменьшились» = число `blocking` не стало меньше **и** множество `(file, line)` не сократилось
  (текст замечаний не сравниваем: LLM перефразирует).
- После `K_max = 2` таких раундов подряд → чистая попытка: `_rollback_with_snapshot("clean_retry")` к `checkpoint`,
  открытые замечания судьи остаются, блок «часть работы уже сделана» из промпта убирается. Старый ref остаётся.
- Счётчик чистых попыток не трогает существующие `_stalled`/`_no_progress`/эскалацию: это отдельная ветка.

- [ ] `test_kmax_two_flat_rounds_clean_retry`: два раунда-продолжения с тем же `(file, line)` → откат к checkpoint, ref остался, следующий промпт без блока продолжения
- [ ] `test_kmax_progress_resets`: замечаний стало меньше → счётчик обнулён, отката нет
- [ ] `test_kmax_rephrased_findings_count_as_flat`: тот же `(file, line)`, другой текст → считается «не уменьшились»
- [ ] реализовать
