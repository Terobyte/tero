# tero v5, план 09: пульт 2 — сообщение хозяину, wake, сторож, хозяин в логе

> Спека: `docs/superpowers/specs/2026-10-29-tero-pult-hud-host-design.md` (версия 0.5). Читать можно,
> менять `docs/` нельзя. Фазы 1–2 спеки сданы вручную (`ed33f97`, `85dac3e`, `d438bbb`): аренда с pid хозяина
> и подписью (`src/loop/lease.py`, `src/loop/host_id.py`), `agent` в модели, код 8, `ready`, `machine_id`,
> `/notify`, код 9 у `resume`, глобальные поля (`src/config_env.py`), проекции `src/hud/model_view.py`,
> `log` вместо `tabs`, `source.js` / `pult.js`, `phoneLight` (подмены 1, 3, 4).

Ты меняешь сам tero (Python ≥ 3.11, `src/`, тесты в `tests/`) и `relay/` (JS для Cloudflare Worker).
Пункты спеки → фазы этого плана: п. 4 «Сообщение хозяину» → фаза 1; п. 3 `wake` → фаза 2; п. 2 «Сторож и
`resume`» → фазы 3 (`resume --detach`, модель) и 4 (`tero watch`, команды, отметки); п. 5 «Хозяин в логе» →
фаза 5. Раздел спеки «Как это устроено» (правило писателей, подпись файлов прогона) действует во всех фазах.

**Номера фаз — по этому плану, 1–5:** 1 сообщение и поток опроса, 2 wake, 3 resume в фоне,
4 сторож, 5 хозяин в логе.

**Спайки спеки** (дефолтная команда пробуждения, формат транскрипта и `~/.claude/sessions/<pid>.json`)
хозяин не делал: строитель реализует оба пути на фейках, настоящий Claude Code в тестах не запускается.

**Код.** `v5/code-map.md` — читать до начала каждой фазы. Цифры там устарели — верь `wc -l`.
- потолки в `tests/test_structure.py` (по умолчанию 400 строк на файл, 120 на функцию). На потолке или
  близко: `src/config.py` 432/435, `src/loop/run.py` 420/421, `src/loop/inbox.py` 366/400,
  `src/hud/model.py` 359/400, `src/loop/autopilot.py` 355/400, `src/loop/wait.py` 350/400. Фаза, которой
  нужно место в таком файле, **первым пунктом чек-листа** выносит соседний код в новый модуль с повторным
  экспортом старых имён — это часть фазы, а не повод для находки «не влезло»;
- потолки не поднимать и новых ключей не добавлять; понизить потолок, когда файл похудел на 20+ строк, —
  обязательно (`test_ceilings_are_tight`);
- новые модули не импортируют `phase`, `run`, `cli_entry`;
- переходы статуса — только через `src/loop/transition.py`, запись файлов — только через `src/fsio.py`.

Главный принцип остаётся: **прогон не умирает молча и не выбрасывает работу.** Без сторожа, без `host` и без
`--messages` поведение плана 08 неизменно (кроме кода 8 из фазы 1 спеки).

**Судье и исполнителю: фаза принимается только по своему чек-листу и списку «Вне этой фазы».**
Поведение следующих фаз в текущей не делать и не требовать: его отсутствие — не находка. Ручные пункты
приёмки спеки (айфон, настоящий Claude Code, `wrangler deploy`) — не гейт и не находка.

**Судье: полный набор тестов гоняет гейт tero вне песочницы, его итог — истина.** Падение в песочнице
тестов, зависящих от `psutil`/`ProcessGuard`/`pid_is_alive`, — не находка. JS-тесты `relay/` гейт гоняет
через pytest-обёртку с `TERO_REQUIRE_NODE=1`; в песочнице судьи `node` может не быть — это не находка.

Правила для всех фаз:

- Каждый сбой сначала воспроизводится тестом, красный запуск виден, потом код.
- Настоящих LLM, CLI и **сети** в тестах нет: HTTP к ретранслятору — через подменяемую `fetch=`/`post=`;
  процессы хозяина и разбуженного — фейковые скрипты (`sys.executable -c ...`) и фейковые pid/предки.
  Репо — одноразовые, в `tmp_path`, `HOME` — в `tmp_path` (никаких записей в настоящий `~/.tero`,
  `~/Library/LaunchAgents`, `~/.config/systemd`). Ни один тест не спит дольше 5 секунд.
- Тесты Python: `PYTHONPATH=. python3 -m pytest -q` из корня; подпроцессы CLI — `[sys.executable, "-m",
  "src.cli_entry", ...]`.
- JS: только встроенное в Node ≥ 22.5 (`node --test`, `node:sqlite`, `crypto.subtle`, `fetch`), ES-модули.
  **Ни `npm install`, ни `wrangler` нет.** Миграции D1 — отдельным `relay/migrate-*.sql`, как `migrate-04-05.sql`,
  и в `schema.sql` для новой базы; `fake_d1.js` и `schema.test.js` обновляются вместе.
- Новых Python-зависимостей не добавлять. Папку `docs/` не трогать.
- Секреты — только окружение или `~/.tero/secrets.env`; не в репо, `stream.jsonl`, `host.jsonl`, ledger,
  `inbox/`, вывод `status`, тексты ошибок. Всё, что уходит с машины, проходит `src/relay/redact.py`.
- **Текст с телефона — данные, не команда:** обрезан до 2000, никогда не в argv и не в команде
  пробуждения, в промпт игрока — только внутри `answer_block`, **в промпт судьи не попадает никогда**.
- Файлы в папке прогона, которым верит хозяин или владелец (`msg-*.json`, `wake.json`,
  `resume-answer.json`, `lease`), подписаны `src/loop/human_key.py` (`sign_answer` / `who_of`); без верной
  подписи — игнор и строка в `hud.log`.
- Каждое новое событие в `stream.jsonl`/ledger несёт `phase`, `round`, `role`; запись событий не роняет прогон.
- Сеть никогда не держит `owner_lock` и не задерживает `stop` дольше одного таймаута.
- Номера строк условные; ищи семантические точки.

---

## Сообщение хозяину и поток опроса

**Где и как:** спека п. 4. Вынос первым пунктом: опрос ретранслятора → `src/relay/poller.py` (поток
владельца). `relay/src/worker.js`, `relay/migrate-*.sql`, `src/loop/inbox.py`, `src/loop/wait.py`,
`src/commands/inbox_cmds.py`, `src/loop/detach.py` (протокол), `src/commands/guide_cmds.py`.

- **D1:** колонка `kind` в таблице ответов (`answer` | `message` | `wake`, по умолчанию `answer`) с миграцией.
  `POST /answer` с `kind: message` или `wake` не требует `question_id`, только токен PWA, текст до 2000,
  прогон незавершённый (проверка `marks.owner_alive` — фаза 4; здесь «не `false`» всегда истинно).
  `GET /answers?run_id=…` отдаёт все виды с `kind`; старый фильтр по `question_id` остаётся.
  Глобальный `phone_text_answers: false` отклоняет `message` и в воркере (если флаг виден ему), и в tero.
- **Поток опроса** стартует в `run()` и во владельце `resume`, живёт до конца прогона, останавливается
  по `stop` за один таймаут. Раз в 10 с в `waiting_*`, раз в 30 с во время шага, немедленный GET при переходе
  в ожидание. Держит последний GET в памяти. `Inbox.take` получает адаптер, который отдаёт из кеша только
  `kind: answer` с этим `question_id`, чьих uuid нет в `applied_uuids` и среди квитированных этим
  процессом: `take` больше сам не ходит в сеть. Ответы квитирует `take`; ответы на закрытые вопросы поток
  квитирует сам с причиной «не тот вопрос». Сообщения поток кладёт подписанными в
  `inbox/msg-<uuid>.json` и квитирует. `kind: wake` в этой фазе: квитировать и записать в
  `state.decisions` `{kind: wake, reason: manual, result: "нет пробуждения (фаза 2)", at}` — заглушка.
- `_inbox_files` отбирает ответы по имени `<uuid>.json`, а не по отсутствию `question_id`.
- **Сообщение не попадает в промпт игрока** и ни на что в прогоне не влияет.
- **`tero wait --messages`** → код **7**, когда есть непрочитанное подписанное сообщение и статус не
  терминальный: печатает сообщения в stdout и помечает прочитанными атомарным переименованием в
  `msg-<uuid>.read.json`; печатает только те, что сам переименовал. Приоритет: 8 > 0/4/2 > 3 > 7 > 5 > 6.
  Выход с 7 продлевает аренду на `agent_timeout`, как 3. Без флага ничего нового.
- **`tero inbox [-w] [--run] [--all]`**: непрочитанные печатает и помечает так же; `--all` — история без пометки.
- Модель: `messages_waiting: n` (непрочитанные) во `view` и белых списках (`client.py`, `filter.js`);
  финальный `tero status` при непрочитанных пишет «непрочитанных: n».
- Протокол в `detach.py` и `tero guide`: код 7 («текст уже в выводе; на вопрос отвечай только если статус
  `waiting_agent`»), правка `test_detach_prints_protocol_and_returns`.
- Cron: ответы и сообщения терминальных прогонов старше 7 дней удаляются.
- `pult.js`: поле «сообщение хозяину» под карточкой, отправка `kind: message`, на отказ — текст отказа.

- [ ] вынос опроса в `src/relay/poller.py`, `inbox.py` не вырос
- [ ] `schema.test.js` / `fake_d1.js`: колонка `kind`, миграция применяется к базе 05; `routes.test.js`: `/answer` с `kind: message` без `question_id` → 200, без токена PWA → 401/403, текст 2001 символ → обрезан или отказ
- [ ] `test_poller_single_get_and_take_offline`: ответ пришёл между опросами → `take` берёт из кеша без сети, `ack` → повторный GET → ровно один приём; два GET на 10 с не бывает
- [ ] `test_poller_acks_stale_answers`: ответ на закрытый вопрос квитирован с причиной, не копится
- [ ] `test_message_written_signed_not_in_player_prompt`: сообщение → `inbox/msg-<uuid>.json` с подписью; промпт игрока следующего раунда его не содержит
- [ ] `test_wait_messages_code7_prints_and_renames`: код 7, текст в stdout, файл стал `.read.json`; второй `wait` 7 не даёт; два параллельных `wait` не печатают одно сообщение дважды
- [ ] `test_unsigned_msg_ignored`: подложенный `inbox/msg-x.json` без подписи — `wait --messages` и `tero inbox` его не печатают, строка в `hud.log`
- [ ] `test_wait_code_priority`: 8 раньше 7; 3 раньше 7; 7 раньше 5 и 6
- [ ] `test_tero_inbox_all_keeps_unread`; `test_phone_text_off_rejects_message`
- [ ] протокол и гайд с кодом 7, тест протокола обновлён
- [ ] реализовать

**Вне этой фазы:** пробуждение по сообщению, `tero wake`, `wake.py` (фаза 2); `marks` и проверка
`owner_alive` (фаза 4).

## wake: одна функция пробуждения, разбуженный ограничен

**Где и как:** спека п. 3. Вынос первым пунктом: пробуждение из `src/loop/autopilot.py` (`maybe_wake` и
соседнее) → `src/loop/wake.py`, повторный экспорт старых имён. `src/commands/inbox_cmds.py`
(`_trusted_session_pid`), `src/commands/guide_cmds.py` (`HOSTS_GUIDE`, `--woken`), `src/relay/poller.py`.

- **Одна функция `wake(run_dir, reason, key)` под одним замком процесса**; её зовут главный цикл в
  `waiting_agent`, поток опроса (сообщение, `kind: wake`, `inbox/wake.json`) и автопилот. Проверка
  `on_duty` и лимитов, запуск и запись аренды — под замком. Отказы: `on_duty` → «агент дежурит, аренда до
  HH:MM»; чаще раза в 5 мин на прогон; ручных > 10 или автоматических (вопрос, сообщение) > 20 в сутки на
  машину (`~/.tero/wake.log` под `flock`, два счётчика); `wake_command` пустая и `host` не `claude` →
  подсказка про `host`. Результат — запись `{kind: wake, reason, result, at}` в `state.decisions`.
- **Порядок записей:** (1) в `woken` запись `{reason, question_id|msg_uuid, at, pid: 0}`; (2) запуск через
  трамплин; (3) аренда `{pid, pid_started, host, session: ""}` под `lease.lock` с подписью; (4) дописать
  `pid`, `pid_started` в ту же запись. «Один раз на вопрос / на uuid» — по записям с любым pid, включая 0.
- **Трамплин** ровно как в спеке: `["sh", "-c", 'exec "$@" </dev/null >>"$0" 2>&1 & echo $!', wake_log, *cmd]`,
  `start_new_session=True`, очищенное окружение (список переменных из фазы 1 спеки +
  `_BLOCKED_ENV_VARS` из `claude_native.py`) + `TERO_WOKEN=1`, `cwd=repo`; pid внука из `readline` с
  таймаутом 5 с, `sh` дождаться сразу. Гасить разбуженного только SIGTERM.
- **`woken`** в `state.json` — список (сейчас словарь по `question_id`; старый словарь читается);
  разбуженные pid также в `~/.tero/woken.json` (под `flock`, TTL сутки).
- **Разбуженный ограничен в любом прогоне:** `_trusted_session_pid` сверяет предков с `woken` прогона и с
  глобальным `~/.tero/woken.json`; при `TERO_WOKEN=1` или совпадении — `answer` только `retry`/`skip`/`stop`;
  `resume`, `go`, `relay`, `pair`, `watch`, `wake` отказывают.
- **Откуда:** `tero wake [--run] [-w]` кладёт подписанный `inbox/wake.json`, поток владельца подбирает
  (`reason=manual`); `kind: wake` с телефона — тот же путь (заглушка фазы 1 заменяется). Сообщение при
  `not on_duty` → `wake(reason=message, msg_uuid)` один раз на uuid; `wake_command` пустая и дефолта нет →
  сообщение ждёт, страница пишет «агента нет, сообщение ждёт».
- **Дефолтная команда** при `host: claude` и пустой `wake_command` — ровно argv из спеки п. 3 (`claude -p
  "<промпт>" --tools "Bash" --setting-sources "" --strict-mcp-config --permission-mode dontAsk --max-turns 30
  --allowedTools ... --disallowedTools ...`), промпт до флагов-списков. Подстановки только `{repo}`,
  `{run_dir}`, `{question_id}`, `{task}`, `{reason}`; текст сообщения в команду не подставляется никогда.
  `wake_command`, `host` берутся только из глобального конфига (`config_env.py`, сделано в фазе 1 спеки —
  проверить, что пробуждение читает оттуда).
- `tero guide --woken` — короткий гайд только с разрешёнными командами; `HOSTS_GUIDE` больше не советует
  `--permission-mode acceptEdits`.
- `pult.js`: кнопка `wake` по таблице п. 7 спеки (не показывается при `view.agent.on_duty`), результат из
  журнала решений.

- [ ] вынос в `src/loop/wake.py`, `autopilot.py` не вырос; тесты `test_autopilot.py` из списка спеки перенесены
- [ ] `test_trampoline_grandchild`: pid внука ≠ pid `sh`, `sh` дождались (не зомби), внук не потомок владельца, его вывод в `wake.log`, `killpg` группы владельца его не убивает
- [ ] `test_wake_two_concurrent_one_agent`: два одновременных вызова → один запуск, второй — отказ «агент дежурит»
- [ ] `test_wake_once_per_uuid_including_pid0`: запуск упал (pid 0) → повтор на тот же uuid не запускает
- [ ] `test_wake_limits_two_counters`: 11-й ручной отклонён, автоматический при этом проходит; чаще раза в 5 мин — отказ
- [ ] `test_woken_restricted_any_run`: разбуженный (по `woken.json`) — `answer retry` принят, `answer model` отклонён и в своём, и в другом прогоне; `resume` / `wake` отклонены
- [ ] `test_wake_command_from_repo_ignored`: `wake_command: "sh -c 'touch …'"` в `<repo>/.tero/config.yaml` и в `state.json` → не исполняется
- [ ] `test_default_wake_argv`: argv по спеке, текста сообщения в argv нет, окружение очищено, `TERO_WOKEN=1`
- [ ] `test_tero_wake_cli_signed_file`: `tero wake` → подписанный `wake.json` → поток зовёт `wake(manual)`; неподписанный — игнор + `hud.log`
- [ ] `test_message_wakes_when_not_on_duty` и `test_message_waits_without_command`
- [ ] `tero guide --woken`, правка `HOSTS_GUIDE`
- [ ] реализовать

**Вне этой фазы:** сторож, `resume --detach`, `marks` (фазы 3–4).

## resume в фоне и resume в модели

**Где и как:** спека п. 2, абзацы «Команда одна» — «`/runs?all=1`». `src/loop/detach.py` (`detach_go`
обобщается на `go` и `resume`), `src/loop/resume/entry.py`, `src/loop/resume/recover.py`, индекс
`~/.tero/runs.json`, `relay/src/worker.js` (`/runs`).

- `tero resume --detach`: тот же код, что `go --detach` (новая сессия, `caffeinate`, ждать `state.json`,
  напечатать протокол); готовность — любой статус, записанный **новым** pid. Код 9 владельца (занят лок)
  пробрасывается. Протокол агента: код 5 → `tero resume --detach`.
- `--answer-from-inbox`: читает только подписанный `<run>/inbox/resume-answer.json` и удаляет его; путь во
  флаге не передаётся; ставит `answer_from_phone = True` и пишет в `state.json` вместе с `answer`;
  `ResumeStep.enter` его не сбрасывает. Текст идёт игроку только в `answer_block`, судье — никогда.
- **Индекс прогонов** `~/.tero/runs.json`: `run_id → {repo, started_at}`, пишет владелец при старте под
  `flock`, записи старше 30 дней удаляются.
- **`resume` в модели:** `{mode: ok | needs_text | refused, why}`. Для `stopped` считает владелец при
  переходе в `stopped` из `resume_refusal` (`user` с открытым вопросом → `needs_text`; при глобальном
  `phone_text_answers: false` → `refused`). Функция расчёта для оборвавшихся (владелец мёртв, статус не
  терминальный) из `state.json` и `crash_resumes` — общая, её позовёт сторож в фазе 4; другой живой
  прогон в том же репо → `refused` «в репо идёт другой прогон». `resume` во `view` и белых списках.
- `/runs?all=1` отдаёт терминальные прогоны за 7 дней с `status`, `view.resume`, `updated_at`; без `all`
  — как раньше. `/runs` и cron фильтруют по колонке `status`, не парсят payload. `pult.js`: список «остановлены».

- [ ] `test_resume_detach_ready_by_new_pid`: старый `state.json` со старым pid не считается готовностью
- [ ] `test_resume_detach_passes_code9`
- [ ] `test_answer_from_inbox_phone_not_judge`: текст из подписанного `resume-answer.json` попал в промпт игрока в `answer_block`, в промпте судьи (по ledger) его нет, файл удалён; неподписанный — игнор
- [ ] `test_resume_mode`: `stopped` по `user` с вопросом → `needs_text`; при `phone_text_answers: false` → `refused`; по `secrets` → `refused` с причиной; другой прогон в репо → `refused`
- [ ] `test_runs_index_written_and_pruned`
- [ ] `routes.test.js`: `/runs?all=1` с терминальными, без `all` — прежний ответ
- [ ] `test_detach_prints_protocol_and_returns` обновлён (5 → `resume --detach`)
- [ ] реализовать

**Вне этой фазы:** `tero watch`, `/commands`, `marks`, кнопка `resume` на странице (фаза 4).

## Сторож tero watch и команды с телефона

**Где и как:** спека п. 2 и таблица п. 7. Новый `src/commands/watch_cmd.py` (+ модули по месту),
`relay/src/worker.js`, `relay/migrate-*.sql`, `pult.js`, `phoneLight`.

- `tero watch`: один процесс на машину под `flock` `~/.tero/watch.lock`; `--install` / `--uninstall` /
  `--status`; launchd на маке (`~/Library/LaunchAgents`, `AbandonProcessGroup`), `systemd --user` на Linux
  (`KillMode=process`, проверка `loginctl enable-linger` — печатает команду). `--install` снимает `PATH` и
  `HOME`, проверяет чтение репо из индекса и печатает, что разрешить. `watch` в `PRELOAD_COMMANDS`.
- **Опрос = пульс:** раз в 30 с `POST /commands/poll` с ключом записи, `machine_id`, отметками;
  ретранслятор обновляет `machines.last_seen`. Отметки по прогонам из индекса (незавершённые и
  остановленные за 7 дней): `{run_id, owner_alive, host_alive, checked_at, resume}`; полный список раз в
  5 мин, при смене `owner_alive` — сразу. Живость владельца — по pid и `pid_started` из `state.json`
  (`registry.pid_is_alive`), **не через `acquire()` на `run.lock`**; `host_alive` — по подписанной аренде;
  `resume` — функция фазы 3.
- Отметки в отдельных колонках таблицы состояний (`owner_alive`, `host_alive`, `owner_checked_at`,
  `resume_w`), не в payload: `POST /state` владельца их не заполняет, cron-CAS их не задевает. `GET /state`
  и `/runs?all=1` отдают их подобъектом `marks` с `machine_last_seen`.
- **Команды:** только `resume <run_id>` (регулярка имени папки прогона) с необязательным текстом ≤ 2000;
  только с токена PWA; TTL 10 мин; не больше одной ожидающей на прогон. `POST /commands/take` →
  исполнение в отдельном потоке → `POST /commands/result` с кодом и текстом; между `take` и `result` > 10 мин
  → `lost`. Сторож исполняет `tero resume --detach -w <repo> --run <repo>/.tero/runs/<run_id>` (полные пути,
  argv списком), при `needs_text` сначала кладёт подписанный `resume-answer.json` и добавляет
  `--answer-from-inbox`; код 9 → до 3 повторов с паузой 10 с. Текст с телефона в argv не попадает никогда.
- `/answer` для `message`/`wake` отказывает при `marks.owner_alive === false` и для `stopped`: «прогон не
  идёт, сначала resume».
- Страница: подмена 2 в `phoneLight` (`owner_alive === false` при незавершённом → красная «оборвался»,
  «проверено в HH:MM»); кнопка `resume` по таблице п. 7: по `marks.resume_w`, если `owner_checked_at` позже
  `updated_at`, иначе `view.resume`; серая при `machine_last_seen` старше 2 мин («машина не на связи»);
  скрыта без единой отметки по машине с подсказкой `tero watch --install`; при `needs_text` — с полем.
- `tero go --detach` без установленного сторожа печатает одну строку «сторожа нет: `tero watch --install`…».

- [ ] `test_watch_single_instance_flock` (второй `watch` выходит)
- [ ] `test_watch_marks_owner_alive_without_run_lock`: живость по pid/`pid_started`, `run.lock` не берётся (идущий `go` его не теряет); смена `owner_alive` → отметка в том же цикле
- [ ] `test_watch_take_crash_lost` на фейковом ретрансляторе: `take` → падение → после TTL `lost`
- [ ] `test_watch_resume_argv_no_phone_text`: argv список с полными путями, текста нет; `needs_text` → подписанный `resume-answer.json`; код 9 → 3 повтора
- [ ] `test_watch_install_files` в `tmp_path`-`HOME`: plist / unit с `PATH`, `HOME`, `AbandonProcessGroup` / `KillMode=process`; `--uninstall` убирает
- [ ] `routes.test.js` / `schema.test.js`: `/commands/poll|take|result`, TTL, одна ожидающая на прогон, `last_seen`, колонки отметок не заполняются из `POST /state`, `marks` в `GET /state`; `/answer` message при `owner_alive=false` → отказ
- [ ] `pult.test.js` / `dom.test.js`: `phoneLight` подмена 2; кнопка `resume` серая / скрыта / с полем по таблице
- [ ] реализовать

**Вне этой фазы:** транскрипт хозяина (фаза 5).

## Хозяин в логе

**Где и как:** спека п. 5. Новый модуль хвоста транскрипта (`src/loop/host_tail.py` или по месту),
`src/loop/lease.py` (`session`), `src/hud/model.py` / `model_view.py` (слияние в `log`), `src/hud/view.html`
(цвет роли), `relay/sync-hud.sh` → `relay/public/index.html`, `pult.js` (фильтр «только хозяин»).

- **Сессия:** `lease.session` из `CLAUDE_CODE_SESSION_ID`, запасной путь `~/.claude/sessions/<pid>.json`
  (`sessionId`, `cwd`). Путь транскрипта: `<CLAUDE_CONFIG_DIR или ~/.claude>/projects/<cwd с дефисами>/<id>.jsonl`,
  иначе глоб `projects/*/<id>.jsonl`. Угадывания по свежести нет. Нет id → роли «хозяин» нет,
  `tero status`: «сессия хозяина: не известна». Пробуждение пишет `session: ""`.
- **Что берётся:** только строки `assistant`, только блоки `text` (первая непустая строка, ≤ 300 символов;
  `thinking` исключается) и `tool_use` по белому списку: `Read`/`Edit`/`Write`/`Glob` → путь относительно
  репо, вне репо только имя файла, секретные (`.env`, ключи) только именем; `Bash` → первое слово команды,
  не `VAR=…`, basename, по регулярке `[A-Za-z0-9._+-]{1,32}`, иначе `bash`; остальные инструменты — только
  имя. Ни результатов, ни `user`, ни субагентов. Всё через `scrub_secrets`.
- **Куда:** отдельный `host.jsonl` в папке прогона, пишет только владелец (поток раз в 2 с, пока хозяин
  `alive`), строки `{ts, role: "host", type: text|tool, text}`; первое чтение — с конца файла. Модель
  сливает `host.jsonl` со `stream.jsonl` только в `log` по времени; `_doing`, `_age`, `_current_turn` его не видят.
- Роль «хозяин» — свой цвет (тёплый песочный) в `view.html` и `ROLE_NAMES`; лампочку и плашку не трогает.
  После правки `view.html` — `sync-hud.sh`, копия равна источнику плюс блок.
- Пушер: изменения только `host.jsonl` — не чаще раза в 15 с, как лог.
- `pult.js`: переключатель «только хозяин» над логом рядом с полем сообщения.

- [ ] `test_host_tail_whitelist` на фейковом `.jsonl`: текст обрезан до 300, `thinking` нет; `Bash` с `DATABASE_URL=… cmd` → `cmd`, heredoc → `cat`, `/usr/local/bin/run` → `run`; `WebFetch` с URL → только `WebFetch`; `Read` вне репо → имя файла; `sk-…` в тексте замаскирован; `user`-строки и субагенты не берутся
- [ ] `test_host_session_fallback`: без `CLAUDE_CODE_SESSION_ID` — из `sessions/<pid>.json`; без обоих — роли нет, строка в `status`
- [ ] `test_doing_age_ignore_host`: `_doing` и `_age` по `stream.jsonl`, свежий `host.jsonl` их не меняет; `log` слит по времени
- [ ] `test_host_tail_from_end`: старые строки транскрипта при старте не попадают
- [ ] `dom.test.js` / тест копии `view.html`: роль `host` с цветом; фильтр «только хозяин» в `pult.test.js`
- [ ] реализовать

---

## Решения в этом плане, которые принял я (Claude)

- П. 3 и п. 2 спеки разбиты на две фазы каждый (сообщение раньше `wake`; `resume --detach` раньше сторожа):
  каждая сдаётся сама и меньше риск зациклиться судье на огромной фазе. В фазе 1 `kind: wake` — заглушка с
  записью в журнал решений, настоящее пробуждение — фаза 2.
- Спайки не сделаны → оба пути на фейках, как разрешает спека; `--restricted` в дефолтную команду не добавляется.
