# TDD Fix + Code Review Fix + OpenCode Provider (MIMO/MiniMax)

**Дата:** 2026-03-24
**Статус:** Готов к реализации

---

## Root Causes (диагностика)

### Bug 1 — TDD: `test_writer_provider` / `test_writer_model` не используются

`src/coach_player.py:164,176` — `_provider_name_for_role("test_writer")` возвращает `config.coach_provider`.
`src/coach_player.py:518-525` — `model_override=self.config.coach_model` вместо `test_writer_model`.
Config-поля `test_writer_provider`/`test_writer_model` отображаются в меню и сохраняются, но ни разу не читаются в runtime. **Test writer всегда работает на coach-провайдере.**

### Bug 2 — TDD: bare `pytest` не в PATH

`src/coach_player.py:1124` — fallback возвращает `["pytest", "-q"]`.
Если pytest установлен как `python3 -m pytest` (стандартно для venv), subprocess бросает `FileNotFoundError` → `_run_tests` возвращает `(False, "Test command failed: ...")` → TDD-режим вечно блокирует плеера с "fix tests".

### Bug 3 — Code Review: `parse_review_output` никогда не видит `CODE_REVIEW_PASSED` от нативного Codex review

`src/coach_player.py:844` — при `role=="reviewer" and isinstance(provider, CodexProvider)` вызывается `provider.run_review()` → `codex exec review --json`.
Нативный Codex review пишет в своём формате ("No issues found", "LGTM", "The code looks correct" и т.д.), даже если в prompt сказано "respond with CODE_REVIEW_PASSED".
`src/feedback.py:207` — ищет строго `CODE_REVIEW_PASSED\b` → **всегда возвращает `ReviewIssues`**.
Следствие: плеер тратит попытки на фикс несуществующих проблем, шаг апрувится только через `max_review_iterations`.

---

## Шаги реализации

### Phase 1 — Фиксы TDD и Code Review

- [ ] 1. Исправить `_provider_name_for_role` и `_provider_for_role` в `src/coach_player.py`: добавить отдельную ветку для `"test_writer"`, которая читает `config.test_writer_provider` и создаёт провайдер через `_get_or_create_provider`
- [ ] 2. Исправить `_run_turn` для роли `test_writer`: передавать `model_override=self.config.test_writer_model` вместо `self.config.coach_model`
- [ ] 3. Исправить `_detect_test_command` в `src/coach_player.py`: заменить fallback `["pytest", "-q"]` на `["python3", "-m", "pytest", "-q"]`; то же для ветки с `pyproject.toml`
- [ ] 4. Расширить `parse_review_output` в `src/feedback.py`: кроме `CODE_REVIEW_PASSED`, распознавать фразы нативного Codex review — "no issues", "no critical issues", "looks good", "LGTM", "no bugs", "code is correct" (case-insensitive, отдельный regex `_CODE_REVIEW_OK_RE`)
- [ ] 5. Написать тесты: `tests/test_tdd_provider_routing.py` — проверить что test_writer использует `test_writer_provider`, а не coach; `tests/test_review_parse.py` — проверить новые паттерны `parse_review_output`

### Phase 2 — OpenCode провайдер (базовый)

OpenCode JSON формат (из `opencode run --format json`):
```
{"type":"step_start", "part": {"type":"step-start"}}
{"type":"text",       "part": {"type":"text", "text":"..."}}
{"type":"tool_use",   "part": {"type":"tool", "tool":"bash", "state":{"status":"completed","input":{"command":"...","description":"..."},"output":"...","metadata":{"exit":0}}}}
{"type":"step_finish","part": {"type":"step-finish","reason":"stop|tool-calls","tokens":{"total":N,"input":N,"output":N}}}
{"type":"error",      "error":{"name":"...", "data":{"message":"..."}}}
```

System prompt: нет env var-аналога `CODEX_INSTRUCTIONS`. Вставляем в начало сообщения:
`<SYSTEM INSTRUCTIONS>\n{system_prompt}\n</SYSTEM INSTRUCTIONS>\n\n{user_prompt}`

CLI команда: `opencode run --format json --dir <working_dir> -m <model> -`
Рабочая директория: `--dir` флаг (не `-C` как у codex).

- [ ] 6. Создать `src/providers/opencode.py` с `OpenCodeConfig` и `OpenCodeProvider`:
  - `OpenCodeConfig`: `command="opencode"`, `default_model="opencode/mimo-v2-pro-free"`, `default_timeout=900`
  - `OpenCodeProvider.run(prompt, system_prompt, working_dir, max_turns, model)` — запускает `opencode run --format json --dir <dir> -m <model> -`, читает JSONL, конвертирует в `AdaptedMessage`
  - `_adapt_opencode_event(event)` — конвертер событий: `text` → `AdaptedMessage(role="assistant")`, `tool_use` → `ToolUseBlock + ToolResultBlock`, `error` → AdaptedMessage с ошибкой
  - Токены из `step_finish.part.tokens.input` / `.output` → `self._last_input_tokens` / `self._last_output_tokens`
  - `check_ready()` — проверяет `shutil.which("opencode")`
  - `display_name` property
- [ ] 7. Зарегистрировать `OpenCodeProvider` в `src/providers/__init__.py` и `src/providers/registry.py` (ветка `"opencode"` в `create_provider`)
- [ ] 8. Написать тест `tests/test_opencode_provider.py`: проверить `_adapt_opencode_event` для каждого типа события, `check_ready` с mock

### Phase 3 — Модели MIMO и MiniMax в меню и runtime_controls

Подтверждённые free модели из `opencode models`:
- `opencode/mimo-v2-pro-free` — MIMO v2 Pro (бесплатный)
- `opencode/mimo-v2-omni-free` — MIMO v2 Omni (бесплатный, быстрее)
- `opencode/minimax-m2.5-free` — MiniMax M2.5 (бесплатный)
- `opencode/nemotron-3-super-free` — Nemotron 3 Super (бесплатный)

- [ ] 9. Добавить `OPENCODE_MODEL_PRESETS` в `src/menu.py`:
  ```python
  OPENCODE_MODEL_PRESETS = {
      "MIMO Pro  (free)":         "opencode/mimo-v2-pro-free",
      "MIMO Omni (free)":         "opencode/mimo-v2-omni-free",
      "MiniMax M2.5 (free)":      "opencode/minimax-m2.5-free",
      "Nemotron 3 Super (free)":  "opencode/nemotron-3-super-free",
      "Ввести вручную...":        "__custom__",
  }
  ```
- [ ] 10. Добавить `"OpenCode (MIMO/free)": "opencode"` в `PROVIDER_PRESETS` в `src/menu.py`
- [ ] 11. Добавить обработку провайдера `"opencode"` в `_edit_setting_questionary` и `_fallback_menu` — выбор модели из `OPENCODE_MODEL_PRESETS` (аналогично codex)
- [ ] 12. Добавить в `MODEL_PRESETS` в `src/runtime_controls.py`:
  ```python
  ("MIMO-Pro",    "opencode", "opencode/mimo-v2-pro-free"),
  ("MIMO-Omni",   "opencode", "opencode/mimo-v2-omni-free"),
  ("MiniMax-2.5", "opencode", "opencode/minimax-m2.5-free"),
  ```
- [ ] 13. Добавить в `_MODEL_CONTEXT_WINDOWS` в `src/config.py`:
  ```python
  ("mimo",        131_072),  # MIMO v2 (128k context)
  ("minimax-m2",  1_000_000),  # MiniMax M2.5 (1M context!)
  ("nemotron",    131_072),
  ```

### Phase 4 — Config и defaults

- [ ] 14. Добавить `opencode` провайдер в `.g3/config.yaml`:
  ```yaml
  opencode:
    type: opencode_native
    command: opencode
    default_model: opencode/mimo-v2-pro-free
    default_timeout: 900
  ```
- [ ] 15. Добавить preset в `.g3/config.yaml`:
  ```yaml
  mimo_free:
    player_provider: opencode
    player_model: opencode/mimo-v2-pro-free
    coach_provider: ccg
    max_rounds: 3
  ```
- [ ] 16. Обновить `_save_global_default` в `src/menu.py` — убедиться что `opencode`-специфичные поля сохраняются корректно

### Phase 5 — Финальная проверка

- [ ] 17. Прогнать полный тест-сьют: `python3 -m pytest tests/ -q` — убедиться что все 190+ тестов проходят
- [ ] 18. Ручная проверка TDD mode: включить в меню → запустить g3 на тестовом проекте → убедиться что test_writer фаза выполняется на правильном провайдере
- [ ] 19. Ручная проверка Code Review: включить → запустить → убедиться что при отсутствии проблем `parse_review_output` возвращает `ReviewPassed`
- [ ] 20. Ручная проверка OpenCode: `opencode` как player_provider с MIMO free → запустить один шаг → проверить что инструменты работают и токены считаются

---

## Справка: OpenCode event adapter (набросок)

```python
def _adapt_opencode_event(self, event: dict):
    t = event.get("type")
    part = event.get("part", {})

    if t == "text":
        text = part.get("text", "")
        if text:
            return AdaptedMessage(role="assistant", content=[TextBlock(text=text)])

    elif t == "tool_use":
        state = part.get("state", {})
        tool = part.get("tool", "")
        call_id = part.get("callID", "")
        inp = state.get("input", {})
        cmd = inp.get("command") or inp.get("description") or str(inp)
        output = state.get("output", "")
        meta = state.get("metadata", {})
        exit_code = meta.get("exit", 0)

        tool_use = ToolUseBlock(id=call_id, name=tool, input={"command": cmd})
        tool_result = ToolResultBlock(
            tool_use_id=call_id,
            content=output[:MAX_TOOL_OUTPUT],
            is_error=(exit_code != 0),
        )
        return AdaptedMessage(role="tool", content=[tool_use, tool_result])

    elif t == "step_finish":
        tokens = part.get("tokens", {})
        self._last_input_tokens = tokens.get("input", 0)
        self._last_output_tokens = tokens.get("output", 0)

    elif t == "error":
        err = event.get("error", {})
        msg = err.get("data", {}).get("message") or err.get("name", "unknown error")
        return AdaptedMessage(role="assistant", content=[TextBlock(text=f"[OpenCode error: {msg}]")])

    return None
```

---

## Справка: расширенный `_CODE_REVIEW_OK_RE` для parse_review_output

```python
_CODE_REVIEW_OK_RE = re.compile(
    r"CODE_REVIEW_PASSED"
    r"|no\s+critical\s+issues?"
    r"|no\s+issues?\s+found"
    r"|looks?\s+good"
    r"|\bLGTM\b"
    r"|no\s+bugs?\s+found"
    r"|code\s+is\s+correct"
    r"|everything\s+looks?\s+(good|correct|fine)",
    re.IGNORECASE,
)
```
