# OpenCode Provider (MIMO + Kimi) + TDD Fix + Code Review Fix

**Дата:** 2026-03-24
**Статус:** Phase 1 complete ✅

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

### Phase 1 — OpenCode провайдер + MIMO + Kimi (ПРИОРИТЕТ)

OpenCode JSON формат (`opencode run --format json`):
```
{"type":"step_start", "part": {"type":"step-start"}}
{"type":"text",       "part": {"type":"text", "text":"..."}}
{"type":"tool_use",   "part": {"type":"tool", "tool":"bash", "callID":"...", "state":{"status":"completed","input":{"command":"...","description":"..."},"output":"...","metadata":{"exit":0}}}}
{"type":"step_finish","part": {"type":"step-finish","reason":"stop|tool-calls","tokens":{"total":N,"input":N,"output":N}}}
{"type":"error",      "error":{"name":"...", "data":{"message":"..."}}}
```

CLI: `opencode run --format json --dir <working_dir> -m <model> -`
System prompt: нет `CODEX_INSTRUCTIONS`. Вставляем в начало: `<SYSTEM INSTRUCTIONS>\n{system_prompt}\n</SYSTEM INSTRUCTIONS>\n\n{user_prompt}`

Подтверждённые модели (`opencode models`):
- `opencode/mimo-v2-pro-free` — MIMO v2 Pro (бесплатный, 128k ctx)
- `opencode/mimo-v2-omni-free` — MIMO v2 Omni (бесплатный, быстрее)
- `opencode/minimax-m2.5-free` — MiniMax M2.5 (бесплатный, **1M ctx!**)
- `opencode/nemotron-3-super-free` — Nemotron 3 Super (бесплатный)
- `openrouter/moonshotai/kimi-k2:free` — Kimi K2 (бесплатный, 128k ctx)
- `openrouter/moonshotai/kimi-k2.5` — Kimi K2.5 (платный, 128k ctx)

- [x] 1. Создать `src/providers/opencode.py` с `OpenCodeConfig` и `OpenCodeProvider`:
  - `OpenCodeConfig`: `command="opencode"`, `default_model="opencode/mimo-v2-pro-free"`, `default_timeout=900`
  - `OpenCodeProvider.run(prompt, system_prompt, working_dir, max_turns, model)` — запускает subprocess, читает JSONL, конвертирует в `AdaptedMessage`
  - `_adapt_opencode_event(event)` — конвертер: `text` → assistant message, `tool_use` → `ToolUseBlock + ToolResultBlock`, `step_finish` → обновить токены, `error` → error message
  - Токены из `step_finish.part.tokens.input` / `.output` → `self._last_input_tokens` / `self._last_output_tokens`
  - `check_ready()` — проверяет `shutil.which("opencode")`
  - `display_name` property — показывает имя модели
- [x] 2. Зарегистрировать `OpenCodeProvider` в `src/providers/__init__.py` и `src/providers/registry.py` (ветка `"opencode"` в `create_provider`)
- [x] 3. Добавить `OPENCODE_MODEL_PRESETS` в `src/menu.py`:
  ```python
  OPENCODE_MODEL_PRESETS = {
      "MIMO Pro  (free)":         "opencode/mimo-v2-pro-free",
      "MIMO Omni (free)":         "opencode/mimo-v2-omni-free",
      "MiniMax M2.5 (free)":      "opencode/minimax-m2.5-free",
      "Kimi K2   (free)":         "openrouter/moonshotai/kimi-k2:free",
      "Kimi K2.5":                "openrouter/moonshotai/kimi-k2.5",
      "Nemotron 3 Super (free)":  "opencode/nemotron-3-super-free",
      "Ввести вручную...":        "__custom__",
  }
  ```
- [x] 4. Добавить `"OpenCode (MIMO/Kimi/free)": "opencode"` в `PROVIDER_PRESETS` в `src/menu.py`; добавить обработку провайдера `"opencode"` в `_edit_setting_questionary` и `_fallback_menu` (аналогично ветке codex, но с `OPENCODE_MODEL_PRESETS`)
- [x] 5. Добавить в `MODEL_PRESETS` в `src/runtime_controls.py`:
  ```python
  ("MIMO-Pro",    "opencode", "opencode/mimo-v2-pro-free"),
  ("MIMO-Omni",   "opencode", "opencode/mimo-v2-omni-free"),
  ("MiniMax-2.5", "opencode", "opencode/minimax-m2.5-free"),
  ("Kimi-K2",     "opencode", "openrouter/moonshotai/kimi-k2:free"),
  ```
- [x] 6. Добавить в `_MODEL_CONTEXT_WINDOWS` в `src/config.py`:
  ```python
  ("mimo",             131_072),   # MIMO v2
  ("minimax-m2",     1_000_000),   # MiniMax M2.5 — 1M!
  ("nemotron",         131_072),
  ("kimi-k2",          131_072),   # Kimi K2 / K2.5
  ```
- [x] 7. Добавить `opencode` провайдер в `.g3/config.yaml` и preset `mimo_free`:
  ```yaml
  providers:
    opencode:
      type: opencode_native
      command: opencode
      default_model: opencode/mimo-v2-pro-free
      default_timeout: 900
  presets:
    mimo_free:
      player_provider: opencode
      player_model: opencode/mimo-v2-pro-free
      coach_provider: ccg
      max_rounds: 3
    kimi_free:
      player_provider: opencode
      player_model: openrouter/moonshotai/kimi-k2:free
      coach_provider: ccg
      max_rounds: 3
  ```
- [x] 8. Написать тест `tests/test_opencode_provider.py`: `_adapt_opencode_event` для каждого типа события, `check_ready` mock, токены из `step_finish`

### Phase 2 — Фиксы TDD

- [ ] 9. Исправить `_provider_name_for_role` и `_provider_for_role` в `src/coach_player.py`: добавить отдельную ветку для `"test_writer"` → `config.test_writer_provider` + `_get_or_create_provider(config.test_writer_provider)`
- [ ] 10. Исправить `_run_turn` для роли `test_writer`: передавать `model_override=self.config.test_writer_model` вместо `self.config.coach_model`
- [ ] 11. Исправить `_detect_test_command`: заменить fallback `["pytest", "-q"]` на `["python3", "-m", "pytest", "-q"]`; то же для ветки с `pyproject.toml`
- [ ] 12. Написать тест `tests/test_tdd_provider_routing.py`: проверить что test_writer использует `test_writer_provider`, а не coach

### Phase 3 — Фикс Code Review

- [ ] 13. Расширить `parse_review_output` в `src/feedback.py`: кроме `CODE_REVIEW_PASSED`, распознавать фразы нативного Codex review через `_CODE_REVIEW_OK_RE` (см. справку ниже)
- [ ] 14. Написать тест `tests/test_review_parse.py`: проверить все паттерны нового regex

### Phase 4 — Финальная проверка

- [ ] 15. Прогнать полный тест-сьют: `python3 -m pytest tests/ -q` — убедиться 190+ тестов проходят
- [ ] 16. Ручная проверка OpenCode: player=opencode/mimo-v2-pro-free → запустить один шаг → инструменты работают, токены считаются
- [ ] 17. Ручная проверка Kimi K2 free как player
- [ ] 18. Ручная проверка TDD mode с test_writer_provider=opencode
- [ ] 19. Ручная проверка Code Review: убедиться что при отсутствии проблем возвращает `ReviewPassed`

---

## Справка: OpenCode event adapter

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
        call_id = part.get("callID", "")
        inp = state.get("input", {})
        cmd = inp.get("command") or inp.get("description") or str(inp)
        output = state.get("output", "")
        exit_code = state.get("metadata", {}).get("exit", 0)

        tool_use = ToolUseBlock(id=call_id, name=part.get("tool", "bash"), input={"command": cmd})
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

## Справка: расширенный regex для parse_review_output

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
