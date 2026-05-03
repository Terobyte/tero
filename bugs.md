# Deep Code Audit — Bug Report

Аудит всего исходного кода tero (30+ .py файлов, ~6000 строк).

## Сводка

| Категория | Кол-во |
|-----------|--------|
| Подтверждённые баги (RED — тест падает) | 16 |
| False positive (GREEN — тест проходит) | 3 |
| Edge cases (GREEN — код работает) | 4 |
| **Итого** | **23** |

## Принцип тестирования

- **RED тест**: утверждает правильное поведение → **падает**, доказывая баг
- **GREEN тест**: утверждает что код работает → **проходит**, доказывая false positive

---

## Подтверждённые баги — RED тесты (16 + 2 дубли/доп)

### BUG-01: `ProviderConfig` — конфликт имён двух разных классов
- **Файл**: `src/config.py:165` vs `src/providers/registry.py:13`
- **Серьёзность**: критическая
- **Суть**: `config.ProviderConfig` имеет `(type, config)`, а `registry.ProviderConfig` — `(name, type, config)`. Оба используются в `orchestrator.py`. Передача экземпляра одного типа вместо другого — `AttributeError` на `.name`.
- **Тест**: `TestProviderConfigNameCollision` (3 теста)
- **Файл теста**: `tests/test_audit_bugs_critical.py`

### BUG-02: `_schedule_counts` молча перекрывает намеренные нули
- **Файл**: `src/batch_executor.py:442-445`
- **Серьёзность**: серьёзная
- **Суть**: `sum(values) <= 0` подменяет пользовательские нули на дефолты без предупреждения.
- **Тест**: `TestScheduleCountsRespectsZeros`
- **Файл теста**: `tests/test_audit_bugs_critical.py`

### BUG-04: `ProcessGuard.kill_new_processes` — нет pgrep fallback
- **Файл**: `src/process_guard.py:55-76`
- **Серьёзность**: серьёзная
- **Суть**: `snapshot_pids()` имеет pgrep fallback, а `kill_new_processes()` — нет (молча return). Без psutil процессы не убиваются.
- **Тест**: `TestProcessGuardPgrepFallback`
- **Файл теста**: `tests/test_audit_bugs_serious.py`

### BUG-05: `RunRecorder.record()` — string decision даёт неверный status
- **Файл**: `src/learning/recorder.py:67`
- **Серьёзность**: средняя
- **Суть**: `getattr("winner_a", "action", "")` возвращает `""`, статус становится `"completed"` вместо `"approved"`.
- **Тест**: `TestRecorderStringDecisionStatus`
- **Файл теста**: `tests/test_audit_bugs_serious.py`

### BUG-06: `_run_phase_zero` использует `id()` — хрупко
- **Файл**: `src/coach_player.py:470-476`
- **Серьёзность**: серьёзная
- **Суть**: Словарь `{id(item): idx}` ломается при любом `replace()` или копировании.
- **Тест**: `TestPhaseZeroIdMapping`
- **Файл теста**: `tests/test_audit_bugs_serious.py`

### BUG-07: `parse_enriched_plan` молча отбрасывает невалидные индексы
- **Файл**: `src/plan_tracker.py:470`
- **Серьёзность**: средняя
- **Суть**: Фаза получает меньше шагов чем указано — молча, без предупреждения.
- **Тест**: `TestEnrichedPlanInvalidIndices`
- **Файл теста**: `tests/test_audit_bugs_serious.py`

### BUG-14: `_match_header` — ложное срабатывание на prose
- **Файл**: `src/batch_executor.py:171-190`
- **Серьёзность**: серьёзная
- **Суть**: `cleaned.startswith(bare)` + `rest[0]==" "` — прозу парсит как report header.
- **Тест**: `TestMatchHeaderProseNotMatched` (2 теста)
- **Файл теста**: `tests/test_audit_bugs_serious.py`

### BUG-16: State machine — `_RESUMABLE_TO_AGENTS_RUNNING` конфликтует с `_VALID_TRANSITIONS`
- **Файл**: `src/state.py:27-38` vs `src/state.py:42-99`
- **Серьёзность**: серьёзная
- **Суть**: `ROUND_FAILED` есть в `_RESUMABLE` но нет в `_VALID_TRANSITIONS`. Special-case обходит инвариант.
- **Тест**: `TestStateMachineContradiction`
- **Файл теста**: `tests/test_audit_bugs_serious.py`

### BUG-17: `BugDetector._check_tests` — возвращает 1 при любой ошибке pytest
- **Файл**: `src/bug_detector.py:237-248`
- **Серьёзность**: средняя
- **Суть**: Internal error → fallback `return 1` — будто есть 1 failed test.
- **Тест**: `TestBugDetectorInternalErrorReturnsZero` (2 теста)
- **Файл теста**: `tests/test_audit_bugs_serious.py`

### BUG-20: `--limit 0` в debug игнорируется
- **Файл**: `src/cli_entry.py:390`
- **Серьёзность**: средняя
- **Суть**: `0` falsy → elif не выполняется. `--limit 0` полностью игнорируется.
- **Тест**: `TestDebugLimitZeroPropagated`
- **Файл теста**: `tests/test_audit_bugs_serious.py`

### BUG-15: `_latest_assistant_text` теряет первый фрагмент
- **Файл**: `src/feedback.py:111-138`
- **Серьёзность**: средняя
- **Суть**: Break на non-assistant между двумя assistant сообщениями — первый текст теряется.
- **Тест**: `TestLatestAssistantTextCapturesAll`
- **Файл теста**: `tests/test_audit_bugs_medium.py`

### BUG-18: `provider.claude_home` перезаписывает общий конфиг
- **Файл**: `src/config.py:482-485`
- **Серьёзность**: средняя
- **Суть**: Один `claude_home` для всех провайдеров. provider.claude_home затирает ZAI путь.
- **Тест**: `TestConfigClaudeHomeOverride`
- **Файл теста**: `tests/test_audit_bugs_medium.py`

### BUG-21: `_MODEL_CONTEXT_WINDOWS` — "codex" substring match
- **Файл**: `src/config.py:254`
- **Серьёзность**: низкая
- **Суть**: `"codex" in model` — любая модель с "codex" в имени получает 1M окно.
- **Тест**: `TestModelContextWindowsCodexSubstring` (2 теста)
- **Файл теста**: `tests/test_audit_bugs_medium.py`

### BUG-22: `short_model_name` — "glm" catch-all → "GLM-5"
- **Файл**: `src/config.py:348`
- **Серьёзность**: низкая
- **Суть**: Любая модель с "glm" отображается как "GLM-5".
- **Тест**: `TestShortModelNameGlmCatchall` (2 теста)
- **Файл теста**: `tests/test_audit_bugs_medium.py`

### BUG-25: `_is_recoverable_error` — "eof" substring слишком широкий
- **Файл**: `src/providers/chain.py:46`
- **Серьёзность**: низкая
- **Суть**: `"eof"` совпадает с любым сообщением содержащим эти три буквы.
- **Тест**: `TestIsRecoverableErrorEofSubstring` (2 теста)
- **Файл теста**: `tests/test_audit_bugs_medium.py`

### BUG-27: `_schedule_counts` перекрывает нули (дубль BUG-02)
- **Файл**: `src/batch_executor.py:442-445`
- **Тест**: `TestScheduleCountsOverridesZeros`
- **Файл теста**: `tests/test_audit_bugs_medium.py`

---

## False positives — GREEN тесты (3)

### FP-11: `_fallback_menu` — нет return после 'd'
- **Вердикт**: НЕ БАГ. `_launch_debugger` вызывает `sys.exit()`.
- **Тест**: `TestFallbackMenuNoBug`

### FP-19: Picker timer leak при ESC
- **Вердикт**: НЕ БАГ. `_handle_locked` отменяет и обнуляет `_cancel_timer` (строки 336-338).
- **Тест**: `TestPickerTimerNoLeak`

### FP-26: `KeyboardListener` finally block — UnboundLocalError
- **Вердикт**: НЕ БАГ. `old_settings` инициализирован в `None` до try.
- **Тест**: `TestKeyboardListenerNoUnboundLocal`

---

## Edge cases — GREEN тесты (4)

### BUG-03: `PhaseFailedError` — __str__ с phase=None
- **Вердикт**: НЕ БАГ. Код корректно обрабатывает `phase=None`.
- **Тест**: `TestPhaseFailedErrorNonePhase` (3 теста)
- **Файл теста**: `tests/test_audit_bugs_critical.py`

### BUG-08: `_read_export_from_zshrc` — обычные кавычки работают
- **Тест**: `TestReadExportFromZshrcNormalCases` (2 теста)

### BUG-13: `_build_compact_summary` — content=None
- **Тест**: `TestBuildCompactSummaryNoBug` (2 теста)

---

## Файлы тестов

| Файл | RED | GREEN | Всего |
|------|-----|-------|-------|
| `tests/test_audit_bugs_critical.py` | 4 | 3 | 7 |
| `tests/test_audit_bugs_serious.py` | 11 | 0 | 11 |
| `tests/test_audit_bugs_medium.py` | 11 | 7 | 18 |
| **Итого** | **26** | **10** | **36** |

Запуск: `python3 -m pytest tests/test_audit_bugs_critical.py tests/test_audit_bugs_serious.py tests/test_audit_bugs_medium.py -v`
