# bugs.md

## Hunt 2026-11-12 — пульт на телефоне, UI/UX (3 волны × 3 Codex Luna)

### Волна 1 — H1 состояния · H2 гонки и жизненный цикл · H3 края данных

### B01 — черновик ответа переезжает в другой прогон с тем же question.id — MAJOR — fixed
- class: state · location: relay/public/pult.js:29-60 (captureDraft/restoreDraft), loadOnce pickRun · found-by: W1-H1
- trigger: пульт открыт в корне (без ?run_id), печатаю в вопрос 7-1 прогона A; раз в 60 с /runs обновляется, pickRun выбирает B (тоже ждёт ответа, вопрос 7-1) → другой questionKey, верх перерисован, restoreDraft сверяет только qid и кладёт текст в форму B
- expected vs actual: черновик привязан к run_id+machine_id+qid / текст и кнопки уходят в чужой прогон

### B02 — упавший (failed) прогон висит в «прогоны», а не в «закончены» — MINOR — fixed
- class: state · location: relay/public/pult.js:348 (isStoppedRun), relay/src/worker.js:207,212 · found-by: W1-H1, W1-H3
- trigger: прогон со status=failed → список «прогоны» навсегда (worker тоже исключает только done/stopped, без 7-дневного среза)
- expected vs actual: failed — терминальный, как в TERMINAL / показывается живым вечно

### B03 — после отказа ответа кнопки нового вопроса остаются серыми до следующего опроса — MINOR — fixed
- class: race · location: relay/public/pult.js answer() (btns.forEach по старому DOM) · found-by: W1-H2
- trigger: POST /answer висит, опрос перерисовал вопрос (applyLocks выключил новые кнопки, sending=true); POST падает → включаются старые, уже удалённые кнопки
- expected vs actual: сразу applyLocks() по живому DOM / новые кнопки мертвы до 4 с (H2 писал «навсегда» — неверно, следующий load их включает)

### B04 — двойной тап «отправить» хозяину шлёт сообщение дважды — MINOR — fixed
- class: duplication · location: relay/public/pult.js sendMessage · found-by: W1-H2
- trigger: два тапа по #msg-btn до ответа релея → два POST kind=message, worker вставляет оба
- expected vs actual: одно сообщение, кнопка занята на время запроса / два одинаковых

### Волна 2 — H1 молчащие ошибки · H2 контракт Python→JS · H3 iOS/PWA и тексты

### B05 — отсчёт ответа застывает на «через 5 с» — MINOR — fixed
- class: ui · location: relay/public/pult.js:235-240 (renderPending), drawPending · found-by: W2-H3
- trigger: тап любого действия, ждать 5 с
- expected vs actual: 5…4…3…2…1 / drawPending ищет #pending-left, а renderPending его не рисует — цифра не меняется до самой отправки
- evidence: renderPending отдаёт `<p id="pending">«…» через ${left} с <button …>` без span; `const newLeft = fresh.querySelector('#pending-left'); if (left && newLeft) …`

### B06 — оранжевый долг красит соседнюю фазу (долг первой фазы не виден вовсе) — MAJOR — fixed
- class: contract · location: src/hud/model.py:215 (`(i + 1) in debt_nos`) · found-by: W2-H2
- trigger: debt[].phase пишется 0-based (src/loop/debt.py:202 ctx.phase.index; Phase.index=len(phases) в plan.py:78); живые state.json: meta engine-worlds phase_index 22, debt [15,20,22]; tero 07-tail phase_index 1, debt [1]
- expected vs actual: оранжевая точка ровно у фазы долга / у предыдущей; долг фазы 0 не красится; runSummary «долг N» и плитка считают по неверным секторам
- note: фикстура tests/hud/test_model.py:35,63 написана в 1-based и закрепляет ошибку

### Pattern P1 — fetch бросает при обрыве сети, обработчик не ловит → кнопка «молчит» — MINOR
### B07 — pair / sendResume / sendWake / sendMessage без try вокруг fetch — MINOR — fixed
- class: silent-failure · location: relay/public/pult.js pair (~787), sendResume (~881), sendWake (~898), sendMessage (~912) · found-by: W2-H1
- trigger: авиарежим, тап «привязать» / «resume» / «разбудить» / «отправить»
- expected vs actual: текст «нет сети — не ушло» / unhandled rejection; привязка висит на «проверяю код…», остальные молчат

### B08 — любой отказ /push/vapid показывается как «на сервере не задан ключ пушей» — MINOR — fixed
- class: wrong-info · location: relay/public/pult.js enablePush (~968) · found-by: W2-H1
- trigger: /push/vapid отвечает 401/5xx или сети нет
- expected vs actual: «релей ответил N» / «не задан ключ» — ведёт чинить не то

### Волна 3 — H1 PWA/iOS жизненный цикл · H2 индексы и единицы в hud-моделях (пусто) · H3 серверный контракт worker (пусто)

### B09 — после повторной привязки «переподписать пуши» отвечает 409 «subscription owned by another device» — MINOR — fixed
- class: lifecycle · location: relay/public/pult.js pair (~787), relay/src/worker.js:564-572 · found-by: W3-H1
- trigger: телефон с включёнными пушами привязать новым кодом (старое устройство не отозвано, exchangePairCode даёт новый device_id), нажать «Пуши включены ✓ (переподписать)»
- expected vs actual: подписка переходит к новой привязке / 409 и английская ошибка; пуши при этом продолжают идти через старое устройство — пропадут, когда его отзовут
- parking (W3-H3): /answers?question_id без run_id/machine_id читает все прогоны — штатный клиент шлёт оба id, не баг пульта
