# как спать и как будить: хозяева и лимиты (T0)

Замеры на 2026-10-22. «Замерено» = запущено вживую; «из кода» = видно в `src/providers/*`;
«неизвестно» = не замерено, замерить на маке до T11b (где CLI установлены).

Замеры шли в облачном контейнере: там есть только `claude` 2.1.308. `codex`, `opencode`, `agy`,
`muse`, `gemini`, `cursor-agent`, `wrangler` не установлены, `opencode.ai` из контейнера не открывается.

## Хозяева (кто держит агента-«мозг» tero)

### Claude Code

Замерено. Фоновая команда (`Bash` с `run_in_background`) будит агента, когда завершается: это основной
путь — `tero go --detach`, потом `tero wait` фоном. Один вызов на переднем плане длится до 10 мин
(по умолчанию 2 мин), фоновый — до 2 ч, потом его убивают и агента будят. Значит, `tero wait --timeout`
держится ниже 7200 с (берём 6600), а код `6` значит «просто запусти `wait` снова». Снаружи сессию можно
разбудить так: `claude -p --resume <session-id> "<текст>"`; `claude --bg --resume <id>` продолжает её фоном
(`--fork-session` создаёт новый id). Будильник нужен только как запасной путь.

`wake_command` по умолчанию:

```
claude -p --permission-mode acceptEdits "Ты мозг tero. Выполни: tero guide; tero status --json --run {run_dir}; ответь на вопрос {question_id}"
```

### opencode

Не замерено. Из resilience-spec: в `opencode run` есть `-s/--session`, `--fork`, `--attach <server>`,
флага compact нет. Будит ли фоновая команда агента — неизвестно; максимальная длина вызова — неизвестно;
нативный compact через `--attach`/server API (вопрос T8c) — неизвестно. Пока не замерено, основной путь —
`wake_command`, а не фоновый `wait`.

`wake_command` по умолчанию:

```
opencode run --dangerously-skip-permissions "Ты мозг tero. Выполни: tero guide; tero status --json --run {run_dir}; ответь на вопрос {question_id}"
```

### Muse

Из кода: в tero Muse — это тот же `claude -p`, только на Anthropic-совместимом API Meta (`api.meta.ai`,
ключ `MODEL_API_KEY`). Если Muse — хозяин внутри Claude Code, сон и пробуждение такие же, как у Claude Code
(предположение, проверить на маке). Длина вызова — как у Claude Code.

`wake_command` по умолчанию: команда Claude Code выше, запущенная с окружением Muse
(`ANTHROPIC_BASE_URL=https://api.meta.ai`, ключ из `~/.tero/secrets.env`, модель `muse-spark-1.3`).

## Что каждый CLI сообщает о лимитах

| провайдер | токены за вызов | лимит / время сброса | стоимость | контекстное окно |
|---|---|---|---|---|
| `claude` | да (`result.usage`) | **да**, замерено | да (`total_cost_usd`) | да (`modelUsage.*.contextWindow`) |
| `muse` | да (тот же поток `claude`) | неизвестно (бэкенд не Anthropic) | неизвестно | неизвестно |
| `codex` | да (`turn.completed.usage`, из кода) | неизвестно в `exec --json`; в `~/.codex/sessions/**/*.jsonl` по слухам есть `token_count` с `rate_limits` (5 ч и неделя, время сброса) | неизвестно | неизвестно |
| `opencode` | да (`step_finish.part.tokens`, из кода) | неизвестно | неизвестно | неизвестно |
| `agy` | неизвестно | неизвестно | неизвестно | неизвестно |

Событие `claude -p --output-format stream-json --verbose` (замерено):

```json
{"type": "rate_limit_event", "rate_limit_info": {
  "status": "allowed_warning", "resetsAt": 1792904400, "rateLimitType": "seven_day",
  "utilization": 0.9, "surpassedThreshold": 0.75, "isUsingOverage": false,
  "unifiedWindows": {"five_hour": {"utilization": 0.23, "resetsAt": 1792714200},
                     "seven_day": {"utilization": 0.9, "resetsAt": 1792904400}}}}
```

Из этого `wait` для `claude` получает точное время сброса (`resetsAt`, unix-секунды), и `limits` в
`state.json` можно заполнить процентом окна. Сейчас `ClaudeNativeProvider._capture_usage` берёт только
`input_tokens`/`output_tokens`; `rate_limit_event` и `total_cost_usd` выбрасываются (это работа T15).

Для остальных `wait` работает без данных: паузы 5 → 15 → 30 мин (спека, часть B).

## `agy models` (2026-10-22, из мастер-плана)

`gemini-3.8-flash-*`, `claude-sonnet-4-6`, `claude-opus-4-6-thinking`, `gpt-oss-120b-medium`.
Gemini 3.1 Pro в списке нет. Судья по умолчанию — `gemini-3.8-flash-high` (T9a сверяет со списком на старте).

## Замерить на маке (до T11b)

- [ ] opencode: будит ли фоновая команда агента, максимум одного вызова, `opencode run -s <id>` снаружи, compact через `--attach`.
- [ ] Muse как хозяин: то же, что у Claude Code? Есть ли `rate_limit_event` от `api.meta.ai`.
- [ ] codex: есть ли `rate_limits` в stdout `codex exec --json` или только в файлах сессий.
- [ ] agy: что в `--output-format json` о токенах и лимитах; как выглядит ошибка протухшего токена.
