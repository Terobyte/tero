# tero v5, трек T, шаг 3б: точки решения, автопилот, stop и resume

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`). Это фазы 9–15 шага 3 (T11a/b/c, T12) из
`v5/tero-master-plan.md`, вынесенные из плана 04 без изменений по сути: фазы 1–8 того
плана сданы и лежат в архиве `v5/archive/04-compact-pult.md`. Спека — `v5/tero-master-spec.md` часть B
«Ядро tero» (блок «Единые решения» главнее частей A/B/C) и `v5/tero-resilience-spec.md` §7.

**Номера фаз в тексте — сквозные по исходному плану:** здесь фаза 9 — первая, фаза 15 — последняя.
«Фаза 7» — `tero go --detach` (`AGENT_PROTOCOL` в `src/loop/detach.py`), «фаза 8» — inbox и `tero answer`
(`src/loop/inbox.py`, `src/loop/decision.py`, `src/relay/iface.py`): обе уже сделаны.

**Этот план идёт после рефакторинга `v5/plans/05-refactor.md`.** Пути вида «менять `src/loop/phase.py`»
написаны до него: ищи код по смыслу (`_halt`, «вход в судью», «после возврата исполнителя») в модулях
из карты в конце плана 05. Новый код клади туда же, а не обратно в `phase.py`; потолки размера файлов из
`tests/test_structure.py` не поднимать.

Уже сделано: `state.json` с enum статуса, `stream.jsonl`, HUD и реестр `~/.tero/registry/`
(`src/hud/registry.py`: `pid_is_alive`, `is_dead`), лок репо `src/loop/lock.py`, снимки
`refs/tero/<run_id>/...` (`src/loop/snapshot.py`), `_rollback_with_snapshot`, `judge_pending` (выход с
кодом 3), `tero review`, `tero resume` (только с `judge_pending`), виды ошибок провайдера, убийство по
тишине, провайдер `agy`, компакт и `tero compact`, экзамен судей (слой 1), пульс владельца
(`src/loop/pulse.py`), `tero wait`/`tero status` (`src/loop/wait.py`), `tero go --detach`, inbox,
`question` в `state.json` и `tero answer`.

Главный принцип шага: **прогон не умирает молча и не выбрасывает работу; любой стоп — точка решения,
которую может закрыть человек, агент или автопилот.**

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Абзацы выше описывают цель всего шага из 7 фаз, а не требования к текущей фазе. Поведение, которое
делают следующие фазы, в текущей фазе не делать и не требовать: его отсутствие — не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** В песочнице судьи
перечисление процессов (`psutil`) запрещено, поэтому `tests/test_process_guard.py` и всё, что зависит от
`ProcessGuard.snapshot_pids`, там падает при исправном коде. Падение этих тестов в песочнице — не находка.
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
- Номера строк условные; ищи семантические точки («после возврата исполнителя», «вход в судью», «`_halt`»).
- Новых зависимостей не добавлять. Папку `docs/` не трогать.
- Никаких сессий CLI: ни `--continue`, ни `--conversation`, ни `-s`. Каждый вызов роли — новый процесс.
- Каждое событие в `stream.jsonl`, которое добавляет этот шаг, несёт `phase`, `round`, `role` и `text`.
  Каждое изменение статуса пишется и в `state.json`, и в `stream.jsonl`, и в `ledger.jsonl`, одним
  вызовом-помощником из шага 2а.
- Секреты (`META_MODEL_API_KEY`, токены) не попадают ни в тексты ошибок, ни в `stream.jsonl`, ни в ledger,
  ни в `inbox/`, ни в вывод `status`.
- Ретранслятора (Cloudflare) в этом шаге нет: только протокол `RelayClient` и реализации `NoopRelay`
  (по умолчанию) и `FakeRelay` (для тестов). Настоящий клиент — шаг 4.


## Точки решения: answer, skip, stop, долг

Менять `src/loop/phase.py` (`_halt`), `src/loop/run.py`, `src/loop/git.py`, `src/loop/prompts.py`;
тесты в `tests/loop/test_decision.py`.

- При `decisions=True` `_halt(reason)` не откатывает и не пишет `stopped`, а открывает точку решения:
  `state.json`: `status=waiting_human`, `question={id, reason, text, actions}`, `tree_sha` — sha дерева
  с неотслеживаемыми файлами (через временный `GIT_INDEX_FILE`, как в `snapshot.py`). Рабочее дерево
  **не откатывается**, пока ждём (при `secrets` индекс очищается, файлы лежат на месте).
- Цикл ожидания владельца: раз в `DECISION_POLL_S = 10` `Inbox.take(...)`, после каждой итерации —
  `pulse.progress()`. В этой фазе ждём бесконечно (таймауты — фаза 11).
- `answer <текст>` → новый раунд той же фазы; в промпты player и judge следующего раунда текст идёт
  **только** в размеченном блоке «Ответ на вопрос фазы (не меняет контракт, критерии SHIP и правила
  гейтов)».
- `skip` → снимок + откат фазы к чекпоинту (как сейчас `_rollback_with_snapshot`), фаза в долг:
  `state.json.debt += {phase, reason, findings (последние, до 5), who, ref}`, чекбоксы фазы остаются `[ ]`,
  прогон идёт к следующей фазе. Финал с непустым долгом: `status=done`, код выхода `go` — `4`, строка
  «готово с долгом: N».
- `stop` → как сейчас `_halt`: снимок, откат, `stopped`, код 2.
- Сдача фазы: коммит фазы получает трейлер `Tero-Phase: <run_id>/<номер фазы>` (через `git commit
  --trailer` или строку в конце сообщения); фаза без изменений файлов — пустой коммит `--allow-empty`
  с тем же трейлером. Помощник `git.phase_shipped(repo, base, run_id, n) -> bool` ищет трейлер в
  `git log base..HEAD`.
- HUD: финал с долгом рисуется «⚠ сдано с долгом N», не «✓».
- Вне этой фазы: `more|model|retry|allow|wait` (фаза 10), таймауты, аренда, автопилот (фаза 11),
  `judge_pending` как ожидание (фаза 12).

- [ ] `test_halt_opens_question_tree_untouched`: `decisions=True`, фейковый судья `NEEDS_USER` → `waiting_human`, `question.id == "1-1"`, файлы исполнителя на месте, `tree_sha` записан
- [ ] `test_halt_without_decisions_unchanged`: `decisions=False` → `stopped` и откат, как раньше
- [ ] `test_answer_goes_to_prompts_marked`: ответ `answer "используй sqlite"` → в промптах player и judge раунда 2 текст внутри размеченного блока
- [ ] `test_skip_is_debt_and_exit_4`: фаза 1 `stuck` → `skip` → фаза 2 сдана, `debt` с фазой 1, код 4, чекбоксы фазы 1 `[ ]`
- [ ] `test_stop_rolls_back_exit_2`: `stop` → снимок в `refs/tero/...`, дерево у чекпоинта, код 2
- [ ] `test_phase_trailer_and_empty_commit`: сданная фаза → трейлер `Tero-Phase: <run>/1`; фаза без изменений → пустой коммит с трейлером; `phase_shipped` → `True`
- [ ] `test_hud_debt_icon`: HUD-модель для `done` с долгом 2 → текст «⚠ сдано с долгом 2»
- [ ] реализовать

## Действия more, model, retry, allow, wait и бюджет

Менять `src/loop/phase.py`, `src/loop/decision.py`; тесты в `tests/loop/test_decision.py`.

- `more` → ещё 5 раундов, в которых детектор «нет прогресса» не останавливает фазу; после пятого —
  точка решения с причиной `fuse`. Эскалацию не отменяет.
- `model <player|tester> <провайдер[:модель]>` → роль сменена до конца фазы, со следующего раунда; модель
  должна быть известна (`KNOWN_PROVIDERS`), иначе отказ при приёме. `model judge` отклонён ещё в фазе 8.
- `retry` → повторить вызов провайдера (тот же раунд); после `secrets` — снимок, откат, фаза заново, а
  пути, вызвавшие стоп, идут в промпт player блоком «эти файлы не создавать».
- `allow <путь>` (только человек) → путь в `state.json.allowed_secrets`, проверка секретов его
  пропускает до конца прогона; после — сдача заново (гейт и судья на том же дереве).
- `wait` → `status=waiting_limit`, пауза до известного времени сброса (если вид ошибки его дал), иначе
  5 → 15 → 30 мин (`LIMIT_WAITS_S`, подменяется), потом повтор вызова; после третьей неудачи — снова
  точка решения. `waiting_limit` не считается в `human_timeout`.
- Бюджет продолжений: `more`, `retry`, `wait`, `model` от `agent`/`autopilot` — общий бюджет 3 на фазу в
  `state.json.budget[<фаза>]`, переживает `resume`. Исчерпан → в вопросе остаются только `skip|stop`, другие
  действия отклоняются «бюджет исчерпан». У `human` бюджета нет.
- Вне этой фазы: таймауты ожидания, аренда, автопилот (фаза 11).

- [ ] `test_more_gives_five_rounds_without_stuck`: фейк без прогресса → `more` → 5 раундов без `stuck`, потом вопрос `fuse`
- [ ] `test_model_switches_next_round`: `model player opencode:x` → раунд 2 исполнитель `opencode:x`; неизвестный провайдер отклонён
- [ ] `test_secrets_allow_and_retry`: `secrets` + `allow .env.example` от человека → сдача; `retry` → откат, в промпте player «не создавать: .env»
- [ ] `test_wait_backoff_then_question`: провайдер `quota` три раза при подменённых паузах → `waiting_limit` трижды, потом новый вопрос
- [ ] `test_budget_three_per_phase`: агент `retry` ×3 принято, 4-й отклонён «бюджет исчерпан»; человек — без ограничения
- [ ] реализовать

## Ожидание, аренда агента, автопилот

Создать `src/loop/autopilot.py`, `tests/loop/test_autopilot.py`; менять `src/loop/phase.py`,
`src/loop/wait.py`, `src/config.py`.

- `Config.human_timeout_s = 900`, `Config.agent_timeout_s = 1800`, `Config.autopilot_grace_s = 900`,
  `Config.wake_command: str = ""`. Все подменяются в тестах на доли секунды.
- Ожидание: `waiting_human` → через `human_timeout_s` без ответа `waiting_agent` (вопрос **остаётся
  открытым** для человека) → аренда агента истекла → `relay.notify("агента нет, решу сам через N мин")` →
  через `autopilot_grace_s` автопилот. Нет ретранслятора (`NoopRelay`) и нет живой аренды → автопилот через
  600 с после открытия вопроса.
- Аренда: `<run_dir>/lease` = `{"until": <unix ts>, "pid": <pid сессии агента>}`. Пишет `tero wait`
  (не владелец): пока ждёт — `now + 120`, продлевая раз в 30 с; при выходе с 3 — `now + agent_timeout_s`;
  с 6 — `now + 300`. pid сессии агента — родитель `wait` (`os.getppid()`), либо `--agent-pid`. pid мёртв →
  аренда недействительна сразу. Принятый ответ агента продлевает аренду на `agent_timeout_s`.
- `wake_command` (если задан): срабатывает только когда аренда **истекла**, один раз на вопрос; плейсхолдеры
  `{run_dir}`, `{question_id}`; запуск через параметр `popen` в новой сессии; выдаёт аренду на
  `agent_timeout_s` (pid = pid запущенного процесса). Действия агента, разбуженного так: только
  `retry|skip|stop` (в inbox-файле `"woken": true`, остальное отклоняется).
- Автопилот (`autopilot.choose(decision, state) -> Answer`): по таблице спеки (`user`/`stuck`/`fuse`/`secrets` →
  `skip`; провайдер → `wait`, после исчерпания бюджета `skip`), бюджет 3, `who="autopilot"`. **Не
  цепочкой:** если фаза, начатая сразу после пропуска автопилотом, тоже дошла до точки решения —
  `stop` и `relay.notify("остановил: две фазы подряд не идут")`. Человек и агент так не ограничены.
- Повторы без шума: точки решения по одной причине подряд — не больше одного `notify` в 30 мин.
- Вне этой фазы: `judge_pending` (фаза 12), `stop` во время шага (фаза 13).

- [ ] `test_human_timeout_to_waiting_agent`: подменённые таймауты → `waiting_human` → `waiting_agent`, вопрос открыт, `wait_run` вернул 3
- [ ] `test_lease_written_by_wait`: `wait_run` пишет `lease` с `until` и pid; мёртвый pid → аренда недействительна
- [ ] `test_autopilot_after_lease_expired`: нет ответа, аренда истекла → `notify` в `FakeRelay`, через grace `skip` от `autopilot` в журнале
- [ ] `test_autopilot_no_relay_no_agent_10min`: `NoopRelay`, аренды нет → автопилот по таймеру 600 с (подменён)
- [ ] `test_autopilot_not_chain`: фаза 1 пропущена автопилотом, фаза 2 тоже в точке решения → `stop`, notify «две фазы подряд»
- [ ] `test_wake_command_once_when_lease_expired`: `wake_command` со stub-скриптом → запущен один раз с подставленными `{run_dir}` и `{question_id}`; при живой аренде не запущен; `woken`-ответ `more` отклонён
- [ ] реализовать

## judge_pending как живое ожидание

Менять `src/loop/phase.py`, `src/loop/run.py`, `src/loop/autopilot.py`; тесты в `tests/loop/test_judge_pending.py`
(новые тесты в конец файла) и `tests/loop/test_autopilot.py`.

- При `decisions=True` три сбоя судьи → `status=judge_pending` и точка решения с причиной `judge_pending`
  (действия `retry|skip|stop`), процесс не выходит и держит лок; `wait` даёт 3.
- `retry` → новый вызов судьи на том же `snapshot_ref`, исполнитель не вызывается; от агента/автопилота —
  в бюджет 3.
- Автопилот для `judge_pending`: только `retry` (в бюджет), после исчерпания — `skip` в долг.
  «Принять вручную» и «отклонить» — только человек: действия `accept` и `reject` доступны только
  `who=human` (`accept` → фаза сдана с этим ref, `reject` → как `retry` исполнителя с новым раундом).
- При `decisions=False` поведение шага 2а не меняется (выход с кодом 3, `tero resume` с судьи).

- [ ] `test_judge_pending_waits_holding_lock`: судья трижды мусор, `decisions=True` → процесс жив, лок занят, `wait_run` → 3
- [ ] `test_judge_pending_retry_calls_judge_only`: `retry` → вызван только судья, на том же ref
- [ ] `test_autopilot_judge_pending_retry_then_skip`: автопилот: `retry` ×3, потом `skip` в долг
- [ ] `test_accept_reject_human_only`: `accept` от агента отклонён; от человека → фаза сдана с трейлером
- [ ] реализовать

## tero stop во время шага

Создать `tests/loop/test_stop_midstep.py`; менять `src/loop/pulse.py`, `src/loop/phase.py`, `src/cli_entry.py`.

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

- [ ] `test_stop_midstep_kills_fast`: stub-исполнитель `sleep 600`, `STOP_POLL_S=0.1` → `stop.json` → прогон `stopped` меньше чем за 5 с, процесс stub мёртв, снимок есть
- [ ] `test_stop_removes_stale_index_lock`: убитый шаг оставил `.git/index.lock` → после стопа его нет, откат прошёл
- [ ] `test_stop_cli_refuses_when_not_running`: `status=done` → код 1 (через CLI-подпроцесс)
- [ ] реализовать

## tero resume после краша

Создать `tests/loop/test_resume_crash.py`; менять `src/loop/run.py`, `src/cli_entry.py`.

- `tero resume` смотрит статус: `judge_pending` при `decisions=False` — как сейчас (шаг 2а); любой
  незавершённый статус с мёртвым или зависшим владельцем — восстановление после краша; `done`/`stopped` →
  отказ как сейчас: текст «resume для `<статус>` пока не умеет», код 1 (его проверяет
  `tests/loop/test_resume_judge.py`, текст не менять).
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

- [ ] `test_crash_between_tick_and_commit`: чекбоксы отмечены, коммита с трейлером нет → откат, фаза заново, чекбоксы `[ ]`
- [ ] `test_crash_after_commit_before_state`: коммит с трейлером есть, `state.json` на старой фазе → фаза засчитана, идём к следующей
- [ ] `test_crash_in_decision_point_restores_question`: дерево совпало с `tree_sha` → `waiting_human`, тот же id и текст
- [ ] `test_resume_kills_hung_owner`: stub-владелец жив, `progress` стоит дольше порога → убит, лок взят; не завис → «прогон жив», код 1
- [ ] `test_third_crash_resume_refused`: третий `resume` подряд → `stopped`; `--force` проходит
- [ ] реализовать

## tero guide и tero notify

Менять `src/cli_entry.py`, `src/loop/detach.py`; тесты в `tests/test_cli.py`.

- `tero guide` печатает `AGENT_PROTOCOL` (из фазы 7) и раздел «как спать» для хозяев Claude Code,
  opencode, Muse (содержание — `v5/hosts-and-limits.md`, без ссылок на `docs/`). Код 0.
- `tero notify "текст" [-w] [--run]` → `relay.notify(scrub_secrets(текст))`; с `NoopRelay` печатает
  «ретранслятора нет, сообщение не ушло» и код 0 (не ошибка).
- `go --detach` печатает тот же `AGENT_PROTOCOL`, что `guide` (один источник текста).

- [ ] `test_guide_prints_protocol_and_hosts`: вывод содержит коды `wait` 0/2/3/4/5/6 и разделы трёх хозяев
- [ ] `test_notify_noop_relay`: без ретранслятора → текст «ретранслятора нет», код 0; секрет в тексте вычищен (через `FakeRelay`)
- [ ] `test_detach_and_guide_same_protocol`: текст протокола в выводе `--detach` (stub-владелец) и `guide` совпадает
- [ ] реализовать
