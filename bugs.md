# Bug Report — Active

22 failing tests as of 2026-05-02. Run:
```
python3 -m pytest tests/test_audit_bugs_critical.py tests/test_audit_bugs_serious.py tests/test_audit_bugs_medium.py tests/test_bugs_md_negative_registry.py tests/test_bugs_md_sw_negative.py -v
```

---

## Audit 1 — batch_executor, cli_entry

### BUG-02/27: `_schedule_counts` молча перекрывает нули
- **Файл**: `src/batch_executor.py:442-445`
- **Фикс**: `if sum(values) == 0: return defaults` + respect individual zeros
- **Тесты**: `TestScheduleCountsRespectsZeros`, `TestScheduleCountsOverridesZeros`, negative-registry BUG-02/27

### BUG-20: `--limit 0` в debug игнорируется
- **Файл**: `src/cli_entry.py:390`
- **Фикс**: `is not None` check вместо truthy
- **Тест**: `TestDebugLimitZeroPropagated`

---

## Audit 2 — plan_tracker, role_router, turn_runner, providers

### PLAN-B1: `display_label_for("judge")` крашит при пустом judge provider
- **Файл**: `src/role_router.py:77,84`
- **Фикс**: guard `provider_for("judge")` аналогично `provider_name_for`

### PLAN-B5: `_schedule_counts(0,0,0)` → all-zero без fallback (связан BUG-02)
- **Файл**: `src/batch_executor.py:436-443`
- **Фикс**: `if sum(values) == 0: return defaults`

### PLAN-B6: `PlanItem.__new__` кэш без `skipped` → мутация frozen dataclass
- **Файл**: `src/plan_tracker.py:30-38`
- **Фикс**: добавить `skipped` в cache key

### PLAN-B7: Skip-ветка не обновляет `tracker.items` (ломается после фикса B6)
- **Файл**: `src/batch_executor.py:604-608`
- **Фикс**: обновлять `tracker.items` явно в skip-ветке

### GEN-B9: Continuation перезаписывает provider на каждой итерации
- **Файл**: `src/turn_runner.py:221`
- **Фикс**: resolve provider один раз перед loop

### GEN-B11: `claude_native.py` молча игнорирует `returncode=None`
- **Файл**: `src/providers/claude_native.py:68`
- **Фикс**: `if event.returncode is not None and event.returncode != 0:`

### GEN-B16: `menu.py` — bare `if` вместо `elif` + missing `continue`
- **Файл**: `src/menu.py:530,778`
- **Фикс**: `elif answer == "s":`, `continue` после `run_debugger_menu()`

---

## Audit 3 — providers, plan_tracker, duel, recorder

### SW-02: `ProviderError` import внутри метода (registry.py)
- **Файл**: `src/providers/registry.py:136`
- **Фикс**: перенести import наверх файла

### SW-06: Crash при `stderr=None` в claude_native
- **Файл**: `src/providers/claude_native.py:69-73`
- **Фикс**: `(event.stderr or b'').decode()`

### SW-07: FD leak в codex `_build_env` при mkstemp
- **Файл**: `src/providers/codex.py:213-216`
- **Фикс**: try/finally вокруг `os.write()`, close fd

### SW-11: `subprocess.run` без timeout в `check_ready`
- **Файл**: `src/providers/claude_native.py:84-89`
- **Фикс**: `timeout=10`

### SW-13: Continuation передаёт `provider=None` в `run_turn`
- **Файл**: `src/turn_runner.py:217-248`
- **Фикс**: preserve resolved provider, не перезаписывать при ошибке

### SW-47: `min(i, len-1)` возвращает wrong step при invalid index
- **Файл**: `src/plan_tracker.py:492`
- **Фикс**: `items[i] for i in step_indices if 0 <= i < len(items)`

### SW-54: FD leak при JSON parse error в recorder
- **Файл**: `src/learning/recorder.py:171-213`
- **Фикс**: try/finally с явным close, catch `json.JSONDecodeError`

### SW-61: Worktrees никогда не удаляются — disk leak
- **Файл**: `src/duel.py:105-159`
- **Фикс**: cleanup в `finally` блоке

---

## Closed (~78 fixed)

BUG-01, BUG-03, BUG-04, BUG-05, BUG-06, BUG-07, BUG-14, BUG-15, BUG-16, BUG-17, BUG-18, BUG-21, BUG-22, BUG-25, PLAN-B2, PLAN-B3, PLAN-B4, GEN-B8, GEN-B10, GEN-B12, GEN-B13, GEN-B14, GEN-B15, GEN-B17, SW-01, SW-03..SW-05, SW-08..SW-10, SW-12, SW-14..SW-19, SW-20..SW-46, SW-48..SW-60
