# Refactoring Plan — `greenhouse_apply.py`, `indeed_apply.py` & `indeed_screening.py`

> **Дата создания:** 2026-03-09
> **Статус:** В планировании
> **Цель:** Разбить два монолитных applier-файла на узкоспециализированные модули

---

## Текущее состояние (актуально)

| Файл | Строк | Байт | Основные проблемы |
|------|-------|------|-------------------|
| `greenhouse_apply.py` | **1768** | 96KB | 1 класс + 24 метода в одном файле |
| `indeed_apply.py` | **1133** | 56KB | Captcha + form filling + selectors вперемешку |
| `indeed_screening.py` | **933** | 40KB | Question discovery (5 стратегий) + answer routing + fill mechanics в одном классе |

> **Примечание:** С момента написания старого `REFACTORING_PLAN.md` (07.03) Greenhouse вырос
> ещё на **+349 строк** (1419 → 1768). Этот план конкретизирует Фазы 2, 3 и добавляет Фазу C.

---

## Фаза A — Разбивка `greenhouse_apply.py` (1768 строк)

### Анализ зоны ответственности

Файл сейчас содержит **7 несвязанных концептов**:

| Концепт | Методы | Строк |
|---------|--------|-------|
| URL parsing | `_parse_greenhouse_url`, `_page_url` | ~20 |
| Main workflow | `apply_http` | ~149 |
| Pre-submit validation | `_find_empty_required_fields` | ~160 |
| Email security code | `_handle_security_code` | ~106 |
| File upload | `_upload_file`, `_upload_resume`, `_upload_cover_letter` | ~60 |
| Custom questions (AI) | `_fill_custom_questions`, `_fill_radios_universal` | ~200 |
| Combobox engine | `_fill_combobox`, `_fill_combobox_el`, `_fill_after_other`, `_fill_select_native`, `_click_by_bbox` | **~750** |
| HITL / label utils | `_hitl_for_field`, `_get_label` | ~45 |

> `_fill_combobox_el` — **743 строки** только для React combobox логики. Это самый большой метод.

---

### Целевая структура

```
src/applier/greenhouse/
├── __init__.py           # re-export: from .applier import GreenhouseApplier
├── applier.py            # GreenhouseApplier + apply_http workflow         (~250 строк)
├── utils.py              # get_label — общая утилита (Greenhouse + validator)  (~30 строк)
├── validator.py          # _find_empty_required_fields + pre-submit checks  (~180 строк)
├── security.py           # _handle_security_code + GmailCodeReader           (~120 строк)
├── form_filler.py        # _fill_custom_questions + _fill_radios_universal   (~230 строк)
├── combobox_engine.py    # ReactComboboxDriver + fill_combobox + helpers     (~500 строк)
├── combobox_js.py        # JS-шаблоны для page.evaluate (GET_OPTS_JS и т.д.)  (~200 строк)
└── file_uploader.py      # _upload_resume + _upload_cover_letter + _upload_file (~80 строк)
```

**Цель:** Максимальный файл ≤ 500 строк (сейчас 1768), большинство ≤ 250 строк.

---

### Контекстный объект подачи: `ApplyContext`

> ⚠️ **Проблема «паровоза аргументов»:** простой перенос методов в функции (`fill_custom_questions(page, bank, ai, hitl, ctx)`) не решает проблему — «паровоз» просто переезжает из метода в модульные функции.
>
> **Решение: `ApplyContext` dataclass** — передаётся одним объектом во всё дерево вызовов.

```python
# greenhouse/context.py (~30 строк)
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from playwright.async_api import Page
    from src.platforms.base_scraper import Job
    from src.applier.answer_bank import AnswerBank

@dataclass
class ApplyContext:
    page: Page                      # Playwright Page (typed!)
    job: Job                        # Job dataclass
    answer_bank: AnswerBank         # AnswerBank instance
    screenshots_dir: Path
    bot_mode: bool = False
    profile: Optional[dict] = None  # profile.yaml dict
    job_id: str = ""                # correlation ID для логов (hash URL)
```

> ⚠️ **TYPE_CHECKING guard:** Типы `Page`, `Job`, `AnswerBank` импортируются только для type checkers.
> В runtime зависимостей на Playwright нет — `context.py` можно импортировать без браузера.

Все функции в `greenhouse/` принимают `ctx: ApplyContext` вместо 4–5 отдельных аргументов:
```python
# До (паровоз):
async def handle_security_code(page, job, answer_bank, screenshots_dir) -> bool: ...

# После (контекст):
async def handle_security_code(ctx: ApplyContext) -> bool: ...
```

> ⚠️ **Lifecycle объекта `page`:** если `apply_http` пересоздаёт или переходит на новую страницу в процессе (навигация между шагами), `page` может инвалидироваться. Хранить `page` в `ApplyContext` — правильно: все функции обращаются к `ctx.page`, а не к локальной копии, полученной в момент вызова. Никаких `p = ctx.page` в начале функции с дальнейшим использованием `p` — только `ctx.page` напрямую.

---

### Детальный план по файлам

#### `greenhouse/__init__.py` (NEW)
```python
from .applier import GreenhouseApplier

__all__ = ["GreenhouseApplier"]
```

---

#### `greenhouse/applier.py` (NEW — ~250 строк)

**Переносим из `greenhouse_apply.py`:**
- Все `import`-ы (переписать на относительные)
- Константы: `RESUME_PATH`, `COVER_LETTER_PATH`, `SCREENSHOTS_DIR`, `SELECTORS`
- `_parse_greenhouse_url()` (standalone функция)
- Класс `GreenhouseApplier`:
  - `__init__`
  - `_page_url`
  - `apply_http` (основной workflow, ~149 строк)

**Зависимости (импортировать из соседних модулей):**
```python
from .validator import find_empty_required_fields
from .security import handle_security_code
from .form_filler import fill_custom_questions
from .file_uploader import upload_resume, upload_cover_letter
```

> ⚠️ **Важно:** `apply_http` разбивается **без изменения логики**. Просто вызовы `self._метод()`
> заменяются на `await метод(page, ...)` из импортированных модулей.

---

#### `greenhouse/utils.py` (NEW — ~30 строк)

**Переносим:** `_get_label` (строки 1751–1767) — **общая утилита** для label-резолюции.

> ⚠️ **Почему не в `combobox_engine.py`:** `_get_label` нужна и валидатору, и form_filler, и combobox.
> Если оставить в `combobox_engine.py` — `validator.py` получит циклический coupling.
> Отдельный `utils.py` убирает callbacks и делает зависимости явными.

```python
async def get_label(page, element) -> str:
    """Получить текст label для элемента формы (по for/aria/ancestor)."""
    ...
```

---

#### `greenhouse/validator.py` (NEW — ~180 строк)

**Переносим:** `_find_empty_required_fields` (строки 253–412)

Сигнатура после рефакторинга (standalone функция):
```python
from .utils import get_label

async def find_empty_required_fields(page) -> list[str]:
    """Найти все видимые обязательные поля которые пустые."""
    ...
```

> Больше никакого `get_label_fn` callback — `validator.py` импортирует `get_label` из `utils.py` напрямую.

---

#### `greenhouse/security.py` (NEW — ~120 строк)

**Переносим:** `_handle_security_code` (строки 414–519)

```python
from src.applier.gmail_reader import GmailCodeReader
from src.stealth.timing_engine import gaussian_delay
from .context import ApplyContext

async def handle_security_code(ctx: ApplyContext) -> bool:
    """Обработать email verification (security code) от Greenhouse.
    
    Использует ctx.page, ctx.job, ctx.answer_bank, ctx.screenshots_dir.
    """
    ...
```

> ✅ **Исправлено противоречие:** строка 88 и этот блок теперь оба используют `ctx: ApplyContext`.

---

#### `greenhouse/form_filler.py` (NEW — ~230 строк)

**Переносим:**
- `_fill_custom_questions` (строки 614–783) — **AS-IS, без изменения бизнес-логики**
- `_fill_radios_universal` (строки 785–861)

> 🚨 **ВАЖНО: `_fill_custom_questions` переносится БЕЗ замены на `AnswerRouter`.**
>
> **Причина:** текущая реализация использует **batch AI** (`asyncio.gather`) — сканирует ВСЕ поля, потом
> отправляет их пачкой в Gemini. `AnswerRouter` работает **per-question** (один вызов AI на вопрос).
> Замена batch → per-question — это **изменение бизнес-логики**, которое:
> 1. Увеличит latency (N вызовов вместо 1)
> 2. Увеличит стоимость API
> 3. Нарушит принцип «без изменения поведения»
>
> **Будущее (Phase 2, отдельный PR):** унификация через `BatchAnswerRouter` с методом
> `answer_batch(questions: list[Question]) -> list[str]`. Но это — отдельная задача.

```python
from .combobox_engine import fill_combobox_el, fill_select_native
from .utils import get_label
from .context import ApplyContext

async def fill_custom_questions(ctx: ApplyContext) -> None:
    """Greenhouse custom questions — batch AI пайплайн.
    
    Переносим AS-IS из greenhouse_apply.py. Использует:
    - ctx.answer_bank для AnswerBank lookup
    - ctx.page для DOM-операций
    - self.ai / self._hitl из GreenhouseApplier (передаются дополнительно)
    """
    ...

async def fill_radios_universal(ctx: ApplyContext) -> None:
    """Радио-кнопки на Greenhouse — batch AI."""
    ...
```

> ✅ **Принцип:** на этом шаге `form_filler.py` — это точная копия логики из `greenhouse_apply.py`,
> только вместо `self.page` → `ctx.page`, `self.bank` → `ctx.answer_bank`.

---

#### `greenhouse/file_uploader.py` (NEW — ~80 строк)

**Переносим:**
- `_upload_file` (строки 555–572)
- `_upload_resume` (строки 574–589)
- `_upload_cover_letter` (строки 591–612)

```python
async def upload_file(page, selector: str, file_path: Path) -> None: ...
async def upload_resume(page, file_path: Path) -> None: ...
async def upload_cover_letter(page, file_path: Path) -> None: ...
```

---

#### `greenhouse/combobox_js.py` (NEW — ~200 строк)

**JS-шаблоны для `page.evaluate()`** — вынесены из `_fill_combobox_el` для читаемости:

```python
# Все JS-строки как Python-константы
GET_OPTS_JS = """..."""
FIND_OTHER_OPTION_JS = """..."""
FIND_SCHOOL_OTHER_JS = """..."""
CLICK_BY_TEXT_JS = """..."""
OPEN_CONTROL_JS = """..."""
```

> ✅ **Бонус:** JS-шаблоны можно тестировать отдельно через `page.evaluate()` в Playwright-тестах.
> Изменения в JS логике не требуют чтения 500 строк Python кода.

---

#### `greenhouse/combobox_engine.py` (NEW — ~500 строк, было ~780)

**Переносим:**
- `_fill_combobox` — wrapper (строки 536–553)
- `_fill_combobox_el` — основной движок ~743 строки (978–1721)
- `_fill_after_other` (863–929)
- `_fill_select_native` (931–947)
- `_click_by_bbox` (949–976)
- `_fill` — простой helper (521–534)

> ⚠️ **`_get_label` сюда НЕ идёт** — перенесена в `utils.py` (см. выше) для устранения
> циклического coupling с `validator.py`.

> 🚨 **Честный статус этого шага: РЕИНЖИНИРИНГ, не просто рефакторинг.**
>
> Переписывание 743 строк вложенных замыканий в класс `ReactComboboxDriver` меняет области видимости переменных и способ мутации состояния. Принцип «без изменения поведения» здесь условен — риск регрессии близок к 100% без покрытия.
>
> **Обязательное предусловие (Шаг 10a):** перед началом переписывания написать Playwright e2e-тесты на **существующий** код `_fill_combobox_el`. Тесты должны проверять реальный браузер с мок-страницей, содержащей React combobox. Только при зелёных e2e-тестах переходить к рефакторингу.
>
> ```bash
> # tests/e2e/test_combobox_baseline.py — тесты на СТАРЫЙ код
> # Запустить до и после рефакторинга — результат должен совпадать
> pytest tests/e2e/test_combobox_baseline.py -v
> ```

**Целевая архитектура после реинжиниринга:**

```python
from .combobox_js import GET_OPTS_JS, FIND_OTHER_OPTION_JS, CLICK_BY_TEXT_JS
from .utils import get_label

class ReactComboboxDriver:
    """Изолированный драйвер для одного React combobox-элемента."""

    def __init__(self, page, element, label_hint: str = "") -> None:
        self.page = page
        self.element = element
        self.label_hint = label_hint

    async def fill(self, value: str) -> bool:
        """Точка входа — пробует стратегии по очереди, возвращает True при успехе."""
        return (
            await self._try_direct_input(value)
            or await self._try_dropdown_search(value)
            or await self._try_other_fallback(value)
            or await self._try_scroll_and_pick(value)
            or await self._try_js_fallback(value)
        )

    async def _wait_for_listbox(self, timeout: int = 3000) -> bool: ...
    async def _try_direct_input(self, value: str) -> bool:
        """Стратегия 0+1: focus+ArrowDown → JS-клик control → ищем опцию."""
        ...
    async def _try_dropdown_search(self, value: str) -> bool:
        """Стратегия 2: ввод текста → фильтрация."""
        ...
    async def _try_other_fallback(self, value: str) -> bool:
        """Стратегия 1.5: ввести 'other' → кликнуть Other → _fill_after_other."""
        ...
    async def _try_scroll_and_pick(self, value: str) -> bool:
        """Стратегия 3: открыть без ввода → скроллить listbox → найти опцию."""
        ...
    async def _try_js_fallback(self, value: str) -> bool:
        """Стратегия 4: JS dispatch change event как последний resort."""
        ...
    async def _pick_option(self, opts: list) -> bool:
        """Кликнуть первую подходящую опцию (exact → word-boundary)."""
        ...

# Публичные хелперы (тонкие обёртки над драйвером):
async def fill(page, selector: str, value: str) -> None: ...
async def fill_combobox(page, selector: str, value: str) -> None: ...
async def fill_combobox_el(page, element, value: str, label_hint: str = "") -> None:
    return await ReactComboboxDriver(page, element, label_hint).fill(value)
async def fill_after_other(page, combobox_el, value: str, hint: str = "") -> None: ...
async def fill_select_native(element, value: str) -> None: ...
async def click_by_bbox(page, text: str, selectors: str) -> None: ...
```

> ✅ JS-шаблоны вынесены в `combobox_js.py` → `combobox_engine.py` уменьшен с ~780 до ~500 строк.

> ✅ Каждая стратегия — отдельный тестируемый метод. `fill()` — цепочка, а не 700-строчный God Method.
> `_try_direct_input(mock_page, mock_el)` можно тестировать изолированно, без запуска остальных стратегий.

---

### Файлы которые нужно обновить после рефакторинга Greenhouse

> ⚠️ **Полная карта потребителей** — обновить все до удаления `greenhouse_apply.py`.

| Файл | Тип импорта | Что меняется |
|------|-------------|-------------|
| `src/applier/__init__.py` | **НЕ ТРОГАТЬ** | ⚠️ Сейчас НЕ экспортирует `GreenhouseApplier`. Добавлять в `__getattr__` (PEP 562), НЕ в top-level import — иначе сломает lazy loading |
| `src/applier/queue_manager.py` (в `_apply_greenhouse`) | lazy (внутри функции) | `from src.applier.greenhouse import GreenhouseApplier` |
| `src/applier/easy_apply.py` (в `_apply_external`) | lazy | `from src.applier.greenhouse import GreenhouseApplier` |
| `src/applier/indeed_router.py` (в `_route_to_ats`) | lazy | `from src.applier.greenhouse import GreenhouseApplier` |
| `src/platforms/greenhouse_scraper.py` (в `apply`) | lazy | `from src.applier.greenhouse import GreenhouseApplier` |
| `src/gui/apply_service.py` (в `apply_greenhouse`) | lazy | `from src.applier.greenhouse import GreenhouseApplier` |
| `src/bot/ats_test_menu.py` (в `_run_test`) | lazy | `from src.applier.greenhouse import GreenhouseApplier` |
| **`src/bot/its_check_bot.py:32`** | **⚡ TOP-LEVEL** | **Обновить первым — сломается при старте бота** |
| **Удалить** `src/applier/greenhouse_apply.py` | — | После обновления всех 8 потребителей и проверки тестов |

> ⚠️ **`src/applier/__init__.py`:** сейчас НЕ содержит `GreenhouseApplier` / `IndeedApplier` в `__all__`.
> Все потребители делают lazy import напрямую (`from src.applier.greenhouse_apply import ...`).
> Если добавить `from .greenhouse import GreenhouseApplier` в top-level `__init__.py` —
> весь пакет `greenhouse/` (с Playwright) загрузится при `import src.applier`, что сломает скрапер-бота.
> **Решение:** добавить в `__getattr__` (PEP 562) по аналогии с `SelectorEngine`.

---

## Фаза B — Разбивка `indeed_apply.py` (1133 строки)

### Анализ зоны ответственности

| Концепт | Методы | Строк |
|---------|--------|-------|
| Константы/конфиг | `IA` dict, env vars | ~70 |
| Auth/Login | `_ensure_logged_in` | ~15 |
| Main workflow | `apply`, `apply_on_page` | ~260 |
| Frame detection | `_find_apply_frame`, `_click_apply` | ~60 |
| Captcha (Full stack) | `_solve_visible_captcha`, `_solve_indeed_captcha_2captcha`, `_install_captcha_interceptor`, `_extract_sitekey` | **~350** |
| Form filling | `_fill_fields`, `_fill_field`, `_handle_radios` | ~115 |
| Navigation | `_click_continue_or_submit`, `_has_submit_button` | ~90 |
| Loop utilities | `_semantic_fingerprint`, `_is_success`, `_dump_frame_html` | ~55 |
| File upload | `_upload_resume` | ~70 |
| **LLM fallback** | **`UniversalAIFiller`** (из `universal_ai_filler.py`) | **~10 (использование)** |
| **Pattern store** | **`FormPatternStore`** (из `form_pattern_store.py`) | **~5 (использование)** |

---

### Целевая структура

```
src/applier/indeed/
├── __init__.py           # re-export: from .applier import IndeedApplier
├── applier.py            # IndeedApplier + apply + apply_on_page           (~300 строк)
├── captcha.py            # Весь captcha stack (sitekey, 2captcha, interceptor) (~380 строк)
├── config.py             # IA dict — только CSS-константы, никакого кода    (~70 строк)
├── locators.py           # _click_apply + _find_apply_frame (Playwright-функции) (~50 строк)
├── form_filler.py        # _fill_fields + _fill_field + _handle_radios        (~130 строк)
└── navigation.py         # _click_continue_or_submit + _has_submit_button + _is_success + utils (~120 строк)
```

> ⚠️ **Разделение config.py / locators.py (п.7 критики):** `IA` dict — это конфигурация (данные), `click_apply` — это Playwright-манипуляция (код). Они не должны жить в одном файле.
> - `config.py` содержит только константы — можно импортировать без загрузки браузера, тестировать тривиально.
> - `locators.py` содержит только функции поиска и взаимодействия.

**Цель:** Максимальный файл ≤ 380 строк (сейчас 1133).

---

### Детальный план по файлам

#### `indeed/__init__.py` (NEW)
```python
from .applier import IndeedApplier

__all__ = ["IndeedApplier"]
```

---

#### `indeed/config.py` (NEW — ~70 строк) ← бывший selectors.py, ТОЛЬКО данные

**Переносим:**
- `IA` dict (строки 55–123) — все CSS-константы

```python
# Конфигурация: только данные, никакого кода, никакого Playwright
IA: dict = {
    "apply_button": "...",
    "frame_selector": "...",
    # ...
}
```

> **Принцип:** `config.py` — это YAML/JSON в Python-синтаксисе. Можно импортировать и тестировать без браузера.

---

#### `indeed/locators.py` (NEW — ~50 строк) ← бывший selectors.py, ТОЛЬКО функции

**Переносим:**
- `_click_apply` (764–789) — кнопка Apply
- `_find_apply_frame` (791–821) — поиск iframe

```python
from .config import IA

async def click_apply(page, selector_engine=None) -> bool: ...
async def find_apply_frame(page, selector_engine=None): ...
```

> 🔒 **`ia_legacy.py` — FROZEN, не трогать.** В `src/applier/ia_legacy.py` уже существует идентичный `IA` dict («Legacy rollback safety net для USE_SELECTOR_ENGINE=false»).
> **НЕ импортировать `IA` из `indeed/config.py` в `ia_legacy.py`** — это сломает назначение fallback.
> Дублирование здесь — это благо: `ia_legacy.py` — подушка безопасности с хардкоженными константами.
> Если `selector_engine` изменит selector — `ia_legacy` не сломается. Именно это и нужно.

---

#### `indeed/captcha.py` (NEW — ~380 строк)

**Переносим (самый изолированный блок):**
- `_extract_sitekey` (420–465)
- `_solve_visible_captcha` (498–578)
- `_solve_indeed_captcha_2captcha` (590–668)
- `_install_captcha_interceptor` (670–759)

```python
from src.stealth.audio_captcha_solver import AudioCaptchaSolver  # ← не забыть!

async def extract_sitekey(page, target=None) -> Optional[str]: ...
async def solve_visible_captcha(page, target, selector_engine=None) -> bool: ...
async def solve_indeed_captcha_2captcha(sitekey: str, page_url: str) -> Optional[str]: ...
async def install_captcha_interceptor(page, token: str) -> None: ...
```

> ✅ **Низкий риск** — captcha методы полностью независимы от остальной логики.
> Зависимости: `httpx`, `asyncio`, `loguru`, `StealthBrowser` (через `selector_engine`),
> `AudioCaptchaSolver` (из `src.stealth.audio_captcha_solver`).

---

#### `indeed/form_filler.py` (NEW — ~130 строк)

**Переносим:**
- `_upload_resume` (823–893)
- `_fill_fields` (895–956)
- `_fill_field` (958–978)
- `_handle_radios` (980–1009)

```python
async def upload_resume(target, resume_path: Path) -> None: ...
async def fill_fields(target, bank, screening, page=None, context=None) -> None: ...
async def fill_field(target, selector: str, value: str, page=None) -> None: ...
async def handle_radios(target, screening) -> None: ...
```

---

#### `indeed/navigation.py` (NEW — ~120 строк)

**Переносим:**
- `_has_submit_button` (1011–1023)
- `_click_continue_or_submit` (1025–1098)
- `_is_success` (1100–1119)
- `_semantic_fingerprint` (467–496)
- `_dump_frame_html` (1121–1132)

```python
async def has_submit_button(target) -> bool: ...
async def click_continue_or_submit(target, page, selector_engine=None) -> str: ...
async def is_success(page, selector_engine=None) -> bool: ...
async def semantic_fingerprint(target) -> str: ...
async def dump_frame_html(target, label: str) -> None: ...
```

---

#### `indeed/applier.py` (NEW — ~300 строк)

**Оставляем здесь:**
- Env vars: `RESUME_PATH`, `INDEED_COOKIES`, `TWOCAPTCHA_KEY`, etc.
- Класс `IndeedApplier`:
  - `__init__` (с injection SelectorEngine/HealthMonitor)
  - `_ensure_logged_in`
  - `apply` (standalone flow)
  - `apply_on_page` (main loop + LLM fallback)

**Импортирует из соседних модулей:**
```python
from .config import IA
from .locators import click_apply, find_apply_frame
from .captcha import solve_visible_captcha, install_captcha_interceptor
from .form_filler import upload_resume, fill_fields
from .navigation import click_continue_or_submit, is_success, semantic_fingerprint

# Внешние зависимости — НЕ забыть перенести из indeed_apply.py:
from src.applier.universal_ai_filler import UniversalAIFiller    # LLM fallback
from src.applier.form_pattern_store import FormPatternStore      # паттерн-матчинг
from src.applier.universal_screening import UniversalScreeningHandler
```

> ⚠️ **`UniversalAIFiller` и `FormPatternStore`** используются в `apply_on_page` как LLM fallback
> (строка 278 оригинала). Без них `IndeedApplier` потеряет способность обрабатывать
> нестандартные формы.

---

### Файлы которые нужно обновить после рефакторинга Indeed

| Файл | Тип импорта | Что меняется |
|------|-------------|-------------|
| `src/applier/__init__.py` | **НЕ ТРОГАТЬ** | Добавить в `__getattr__` (PEP 562) — НЕ в top-level |
| **`src/applier/indeed_router.py:26`** | **⚡ TOP-LEVEL** | **`IndeedScreeningHandler` — обновить первым, сломается при импорте!** |
| `src/applier/indeed_router.py:116,135` (в `_route_to_ats`) | lazy | `IndeedApplier` и `GreenhouseApplier` |
| `src/applier/ia_legacy.py` | **Не трогать** | Остаётся FROZEN (см. выше) |
| **Удалить** `src/applier/indeed_apply.py` | — | После проверки тестов |

> ⚠️ **`indeed_router.py:26`** содержит **top-level import** `from src.applier.indeed_screening import IndeedScreeningHandler`.
> Это НЕ lazy — при удалении `indeed_screening.py` модуль сломается **при импорте**.
> Обновить наравне с `its_check_bot.py`.

---

## Фаза C — Разбивка `indeed_screening.py` (933 строки)

> 🚨 **Переименование в рамках этой фазы:** `IndeedScreeningHandler` — ложное название. Класс уже сейчас используется BambooHR, Workday, Lever, Ashby. «Потом» не наступает — фиксируем правильное имя сразу.
> - Пакет: `src/applier/universal_screening/`
> - Класс: `UniversalScreeningHandler`
> - Обратная совместимость: `IndeedScreeningHandler = UniversalScreeningHandler` алиас в `__init__.py` — убрать после обновления всех 6 потребителей.

### Анализ зоны ответственности

Файл сейчас содержит **4 несвязанных концепта** в одном классе `UniversalScreeningHandler` (ныне `IndeedScreeningHandler`):

| Концепт | Методы | Строк |
|---------|--------|-------|
| Модели и HITL helpers | `Question` dataclass, `_is_hitl_needed`, `_is_hitl_question` | ~20 |
| Поиск вопросов на странице | `find_all_questions`, `_get_label_for_element` | **~340** |
| Механика клика radio/select/checkbox | `_click_radio_option`, `_fill_radio`, `_fill_select`, `_fill_checkbox` | **~235** |
| Routing ответов (3-уровневый pipeline) | `answer_question`, `handle_all_questions`, `_load_skills_context`, `_answer_open_ended` | ~340 |

> **Примечание:** `find_all_questions` содержит 5 разных стратегий обнаружения радио-вопросов (fieldset, ARIA, name-group, select, text/checkbox/textarea). Это главная «толстая» часть файла — 340 строк DOM-работы.

---

### Целевая структура

```
src/applier/universal_screening/
├── __init__.py           # UniversalScreeningHandler + алиас IndeedScreeningHandler
├── models.py             # Question dataclass + is_hitl_needed + is_hitl_question    (~30 строк)
├── question_finder.py    # find_all_questions + _get_label_for_element               (~360 строк)
├── radio_filler.py       # click_radio_option + fill_radio + fill_select + fill_checkbox (~230 строк)
├── answer_router.py      # класс AnswerRouter (делегат зависимостей)                 (~150 строк)
└── handler.py            # UniversalScreeningHandler (тонкий фасад)                  (~80 строк)
```

**Цель:** Максимальный файл ≤ 360 строк (сейчас 933), большинство ≤ 150 строк.

---

### Детальный план по файлам

#### `universal_screening/__init__.py` (NEW)
```python
from .handler import UniversalScreeningHandler
from .models import Question

# Backward compat alias — удалить после обновления всех 6 потребителей
IndeedScreeningHandler = UniversalScreeningHandler

__all__ = ["UniversalScreeningHandler", "IndeedScreeningHandler", "Question"]
```

---

#### `universal_screening/models.py` (NEW — ~30 строк)

**Переносим:**
- `_is_hitl_needed` (строки 25–29) — standalone функция
- `_is_hitl_question` (строки 32–40) — standalone функция
- `Question` dataclass (строки 43–50)

```python
from dataclasses import dataclass
from typing import Any, List

@dataclass
class Question:
    text: str
    field_type: str  # radio, select, text, number, checkbox, textarea
    options: List[str]
    element: Any
    required: bool = True

def is_hitl_needed(field_type: str, question_text: str) -> bool: ...
def is_hitl_question(text: str) -> bool: ...
```

---

#### `universal_screening/question_finder.py` (NEW — ~360 строк)

**Переносим:**
- `find_all_questions` (строки 87–426) — весь DOM-поиск (5 стратегий)
- `_get_label_for_element` (строки 428–477) — helper для label-резолюции

Стратегии обнаружения вопросов (сохраняем порядок):
1. fieldset > legend (стандарт + 4 источника для legend)
2. `[role='radiogroup']` / `[role='group']` (ARIA-группы)
3. `input[type=radio]` сгруппированные по `name` (Indeed consent)
4. `<select>` элементы
5. `input[type=text/number]`, `input[type=checkbox]`, `<textarea>`

```python
async def find_all_questions(page) -> list[Question]: ...
async def get_label_for_element(element, page) -> Optional[str]: ...
```

> ⚠️ **Важно:** `find_all_questions` возвращает `List[Question]` — `page` передаётся как аргумент, не `self.page`.
>
> **Контракт мока `page` (для тестов):** каждый модуль использует только часть из ~200 методов Playwright Page. Документируем явно:
> - `question_finder.py` использует: `query_selector_all`, `evaluate`, `inner_text`
> - `radio_filler.py` использует: `evaluate`, `dispatch_event`, `click`, `wait_for_selector`
> - `combobox_engine.py` использует: `query_selector`, `fill`, `bounding_box`, `wait_for_selector`, `evaluate`
>
> `AsyncMock()` молча проглатывает вызовы неизвестных методов — тест будет зелёным при сломанном коде. Используем `spec=Page` или реальный Playwright: `browser.new_page() → page.set_content(html)`.

---

#### `universal_screening/radio_filler.py` (NEW — ~230 строк)

**Переносим:**
- `_click_radio_option` (строки 686–785) — 5-шаговая стратегия клика (aria-radio, label JS click, label Playwright, force-click, JS checked)
- `_fill_radio` (строки 787–909) — matching ответа к radio-опции (4 прохода: exact label, exact value, partial, file-extension fallback)
- `_fill_select` (строки 911–925) — fallback-цепочка label→value→partial
- `_fill_checkbox` (строки 927–933)

```python
async def click_radio_option(radio) -> bool: ...
async def fill_radio(element, answer: str, page=None) -> bool: ...
async def fill_select(element, answer: str) -> None: ...
async def fill_checkbox(element, answer: str) -> None: ...
```

> ✅ **Нет зависимостей** от `AnswerBank`/`AIAnswerer` — все функции работают только с Playwright-элементами.

---

#### `universal_screening/answer_router.py` (NEW — ~120 строк)

**Переносим:**
- `answer_question` (строки 479–579) — 3-уровневый pipeline (AnswerBank → cache → AI + HITL)
- `_load_skills_context` (строки 581–601) — загрузка `data/candidate/skills/*.md`
- `_answer_open_ended` (строки 603–617) — DEPRECATED, удалить, вызовов нет

> ⚠️ **`handle_all_questions` сюда НЕ идёт.** «Router» принимает `Question` DTO и возвращает `str`. Он не знает о Playwright, DOM и страницах. DOM-оркестрация (find → answer → fill loop) — это зона `UniversalScreeningHandler` (см. `handler.py` ниже).

**Паттерн: чистая бизнес-логика без зависимости от браузера.**

Зависимости (`AnswerBank`, `AIAnswerer`, `HumanReviewPrompter`) принимаются один раз в `__init__` — и не прокидываются по всему дереву вызовов.

```python
class AnswerRouter:
    def __init__(
        self,
        answer_bank: AnswerBank,
        ai_answerer: AIAnswerer,
        hitl: HumanReviewPrompter,
    ) -> None: ...

    async def answer_question(
        self, question: Question, context: Optional[dict] = None
    ) -> Optional[str]:
        """AnswerBank → cache → AI + HITL pipeline. Возвращает str, никакого DOM."""
        ...

    async def _load_skills_context(self, question_text: str) -> str: ...
```

> ✅ **Тестируемость:** `await AnswerRouter(mock_bank, mock_ai, mock_hitl).answer_question(question)` — чистый юнит-тест, `page` вообще не нужен.

---

#### `universal_screening/handler.py` (NEW — ~120 строк)

**Оставляем здесь** `UniversalScreeningHandler` — он владеет **DOM-оркестрацией** (find → answer → fill loop), которую `AnswerRouter` намеренно не содержит.

- Создаёт `AnswerRouter` из своих зависимостей один раз в `__init__`
- `_load_profile()` — загрузка `profile.yaml` (перенести из `IndeedScreeningHandler.__init__`)
- `handle_all_questions` живёт **здесь** — это единственное место с доступом к `page` + `router` + `radio_filler` одновременно

```python
from pathlib import Path
import yaml
from .question_finder import find_all_questions
from .answer_router import AnswerRouter
from .radio_filler import fill_radio, fill_select, fill_checkbox

class UniversalScreeningHandler:
    def __init__(self, profile=None, bot_mode=False):
        if profile is None:
            profile = self._load_profile()  # ← восстановлена!
        self.profile = profile
        self.answer_bank = AnswerBank(profile)
        self.ai_answerer = AIAnswerer(profile)
        self._hitl = HumanReviewPrompter(bot_mode=bot_mode)
        self._router = AnswerRouter(self.answer_bank, self.ai_answerer, self._hitl)

    def _load_profile(self) -> dict:
        """Load profile from data/candidate/profile.yaml."""
        path = Path("data/candidate/profile.yaml")
        if path.exists():
            return yaml.safe_load(path.read_text()) or {}
        return {}

    async def find_all_questions(self, page) -> list[Question]:
        return await find_all_questions(page)

    async def answer_question(self, question, context=None) -> Optional[str]:
        return await self._router.answer_question(question, context)

    async def handle_all_questions(self, page, context=None) -> int:
        """DOM-оркестрация: find → answer → fill. Роутер DOM не видит."""
        questions = await find_all_questions(page)
        filled = 0
        for q in questions:
            answer = await self._router.answer_question(q, context)
            if answer is None:
                continue
            if q.field_type == "radio":
                await fill_radio(q.element, answer, page)
            elif q.field_type == "select":
                await fill_select(q.element, answer)
            elif q.field_type == "checkbox":
                await fill_checkbox(q.element, answer)
            else:
                await q.element.fill(answer)
            filled += 1
        return filled
```

> ✅ `AnswerRouter` тестируется без браузера. `handler.py` тестируется с мок-страницей. Разделение чёткое.
> ✅ **`_load_profile` восстановлена** — без неё `AnswerBank(None)` получит пустой профиль,
> потеряв данные из `profile.yaml` (имя, телефон, адрес и т.д.).

---

### Файлы которые нужно обновить после рефакторинга Screening

Шаг 1 — создать пакет, импорт через алиас (все потребители продолжают работать).
Шаг 2 — обновить каждый потребитель на `UniversalScreeningHandler`.
Шаг 3 — удалить алиас из `__init__.py`.

| Файл | Текущий импорт | После обновления |
|------|---------------|-----------------|
| `src/applier/indeed_apply.py:35` | `IndeedScreeningHandler` | `UniversalScreeningHandler` |
| `src/applier/indeed_router.py:26` | `IndeedScreeningHandler` | `UniversalScreeningHandler` |
| `src/applier/bamboohr_apply.py:24` | `IndeedScreeningHandler` | `UniversalScreeningHandler` |
| `src/applier/workday_apply.py:23` | `IndeedScreeningHandler` | `UniversalScreeningHandler` |
| `src/applier/lever_apply.py:30` | `IndeedScreeningHandler` | `UniversalScreeningHandler` |
| `src/applier/ashby_apply.py:23` | `IndeedScreeningHandler` | `UniversalScreeningHandler` |
| **Удалить** `src/applier/indeed_screening.py` | — | После обновления всех 6 потребителей |

---

## Порядок выполнения

```
Шаг 0:  grep-аудит перед стартом
   │     grep -rn "from.*greenhouse_apply import" . --include="*.py"
   │     grep -rn "from.*indeed_apply import" . --include="*.py"
   │     grep -rn "from.*indeed_screening import" . --include="*.py"
   │     grep -rn "_answer_open_ended" . --include="*.py"   # проверка DEPRECATED
   │     grep -rn "AudioCaptchaSolver" . --include="*.py"   # зависимость captcha
   │     grep -rn "UniversalAIFiller" . --include="*.py"    # LLM fallback зависимость
   │     git tag pre-refactor  ← ОБЯЗАТЕЛЬНО (rollback safety net)
   │
Шаг 1: Создать src/applier/config.py (общие RESUME_PATH, COVER_LETTER_PATH, SCREENSHOTS_DIR)
   │
Шаг 2: Фаза C — universal_screening/models.py + question_finder.py
   │            + собрать HTML-фикстуры + написать Playwright-тесты (НЕ AsyncMock)
   │
Шаг 3: Фаза C — universal_screening/radio_filler.py     (изолированная механика клика)
   │
Шаг 4: Фаза C — universal_screening/answer_router.py    (класс AnswerRouter — только str, no page)
   │            ⚠️ _answer_open_ended — НЕ переносить, удалить (DEPRECATED, вызовов нет)
   │
Шаг 5: Фаза C — universal_screening/handler.py + __init__.py (с алиасом IndeedScreeningHandler)
   │            handler.py владеет DOM-loop (find → answer → fill)
   │            ⚠️ handler.py содержит _load_profile() — восстановить из IndeedScreeningHandler!
   │
Шаг 6: Обновить 5 из 6 потребителей на UniversalScreeningHandler
   │     ⚠️ indeed_apply.py НЕ обновляем — он удаляется в Шаге 9 (избегаем двойной работы)
   │     ⚠️ indeed_router.py:26 — TOP-LEVEL import! Обновить ОБЯЗАТЕЛЬНО
   │
Шаг 7: Фаза B — indeed/captcha.py   (самый изолированный, низкий риск)
   │     ⚠️ Не забыть AudioCaptchaSolver — зависимость _solve_visible_captcha
   │
Шаг 8: Фаза B — indeed/config.py + indeed/locators.py + indeed/navigation.py
   │
Шаг 9: Фаза B — indeed/form_filler.py + indeed/applier.py + __init__.py
   │     ⚠️ indeed/applier.py сразу импортирует UniversalScreeningHandler (пропускаем промежуточный шаг)
   │     ⚠️ Не забыть перенести UniversalAIFiller + FormPatternStore в indeed/applier.py
   │     Удалить src/applier/indeed_apply.py
   │
Шаг 10: Обновить импорты (queue_manager, indeed_router)
   │
Шаг 10.5: Smoke-test перед удалением alias
   │      grep -rn "IndeedScreeningHandler" . --include="*.py" | grep -v "# alias"
   │      ← должно вернуть 0 результатов, иначе НЕ удалять алиас
   │
Шаг 11: ⚡ БЛОКИРУЮЩИЙ — написать e2e-тесты на СТАРЫЙ _fill_combobox_el
   │      tests/e2e/test_combobox_baseline.py — реальный браузер, мок-страница с React combobox
   │      pytest tests/e2e/test_combobox_baseline.py -v  ← должны быть зелёными
   │
Шаг 12: Фаза A — greenhouse/combobox_js.py (JS-шаблоны) + greenhouse/combobox_engine.py
   │     РЕИНЖИНИРИНГ — только при зелёных e2e
   │     ReactComboboxDriver: fill() → _try_direct_input / _try_dropdown_search / _try_js_fallback
   │     pytest tests/e2e/test_combobox_baseline.py -v  ← проверить после
   │
Шаг 13: Фаза A — greenhouse/utils.py + greenhouse/validator.py + greenhouse/security.py
   │
Шаг 14: Фаза A — greenhouse/form_filler.py (переносит batch AI AS-IS, без замены на AnswerRouter)
   │            + greenhouse/file_uploader.py + greenhouse/context.py (ApplyContext)
   │
Шаг 15: Фаза A — greenhouse/applier.py + __init__.py
   │
Шаг 16: Обновить импорты (its_check_bot ПЕРВЫМ, затем queue_manager, easy_apply, indeed_router,
   │      greenhouse_scraper, apply_service, ats_test_menu)
   │      ⚠️ НЕ добавлять top-level import в src/applier/__init__.py — использовать __getattr__ (PEP 562)
   │      Удалить src/applier/greenhouse_apply.py
   │
Шаг 17: pytest + ручная проверка по одной вакансии на каждой платформе
```

> **Почему Screening перед Indeed?** `indeed_screening.py` — хорошо изолирован, даёт уверенность в паттерне.
>
> **Почему Indeed перед Greenhouse?** Combobox (Шаг 11–12) требует e2e-тестов как предусловия — это самый рискованный шаг. Делаем его последним с максимальной подготовкой.
>
> **Шаг 6 vs 9:** `indeed_apply.py` — намеренно пропускаем обновление, файл будет пересоздан как `indeed/applier.py` в Шаге 9. Двойная работа исключена.
>
> **Шаг 10.5 (NEW):** smoke-test перед удалением alias `IndeedScreeningHandler` — проверяем что все потребители обновлены. Без этого один пропущенный файл → `ImportError` в runtime.
>
> **О порядке C → B:** Фаза B (`indeed/captcha.py`) затрагивает 2 файла и безопаснее по количеству потребителей. Фаза C затрагивает 6 потребителей. Обоснование C первой: `indeed_screening.py` не имеет зависимостей от browser-контекста (только AnswerBank/AI/HITL) — проще всего отработать паттерн «класс → несколько модулей» на нём. Если порядок неприемлем — Фазу B (captcha-only) можно безопасно поднять первой без последствий.

---

## Принципы рефакторинга

1. **Без изменения поведения** — только структурные изменения, никакой новой логики (кроме явно помеченного: combobox → `ReactComboboxDriver`). `_fill_custom_questions` переносится AS-IS с batch AI.
2. **Один шаг — один PR/коммит** — не смешивать несколько шагов; одна feature-ветка, мелкие коммиты
3. **Тест после каждого шага** — `pytest tests/` зелёный перед продолжением
4. **Сохранять публичный API** — `GreenhouseApplier` и `IndeedApplier` остаются публичным интерфейсом
5. **Grep перед удалением** — для каждого удаляемого файла/метода: `grep -rn "ИМЯ" . --include="*.py"` перед удалением

---

## Обработка ошибок при разбивке на модули

При разделении монолита теряется единый `try/except` контекст. Playwright регулярно кидает `StaleElementReferenceError`, `TimeoutError`, `TargetClosedError`.

**Правило:** каждый модуль обрабатывает только свои исключения, recovery-стратегия — на уровне вызывающего.

```
greenhouse/applier.py (apply_http)
 └── try/except на весь workflow → screenshot + log + re-raise
      ├── validator.py → пробрасывает исключения наверх
      ├── form_filler.py → ловит TimeoutError на уровне поля, логирует, продолжает
      └── combobox_engine.py → ловит StaleElement, retry × 2, затем пробрасывает
```

**Retry-стратегия для DOM-операций:**
- Playwright TimeoutError → 1 retry с увеличенным timeout, затем fallback
- StaleElementReference → 1 retry с повторным `query_selector`, затем skip поля
- TargetClosedError → пробрасывается наверх немедленно (страница закрыта)

**Correlation ID:** добавить в `ApplyContext.job_id` (хэш URL вакансии) — прокидывать в каждый log-вызов, чтобы в `bot.log` можно было `grep` по одной заявке.

---

## Соглашение по логированию

```python
# В каждом модуле — именованный логгер:
from loguru import logger
_log = logger.bind(module="greenhouse.combobox_engine")

# В вызовах — всегда job_id из контекста:
_log.debug("fill_combobox_el: trying value={value}", value=value, job_id=ctx.job_id)
```

Формат имени логгера: `{пакет}.{файл}` — позволяет фильтровать в loguru по `filter="greenhouse"`.

---

## Общие константы: `src/applier/config.py` (NEW)

`RESUME_PATH` и другие env vars сейчас будут продублированы в `greenhouse/applier.py` и `indeed/applier.py`. Исправление:

```python
# src/applier/config.py — единый источник истины для env vars
import os
from pathlib import Path

RESUME_PATH = Path(os.getenv("RESUME_PATH", "data/resume.pdf"))
COVER_LETTER_PATH = Path(os.getenv("COVER_LETTER_PATH", "data/cover_letter.pdf"))
SCREENSHOTS_DIR = Path(os.getenv("SCREENSHOTS_DIR", "data/screenshots"))
```

Оба пакета (`greenhouse/`, `indeed/`) импортируют из `src.applier.config`, не определяют своё.

---

## Git-стратегия и откат

**Одна feature-ветка `refactor/applier-split`, мелкие коммиты по одному шагу.**

```bash
# ⚡ ОБЯЗАТЕЛЬНО: создать tag перед стартом для полного rollback
git tag pre-refactor

git checkout -b refactor/applier-split
# Шаг 0: grep-аудит → коммит "chore: applier split pre-audit"
# Шаг 1: config.py → коммит "refactor: extract shared applier config"
# Шаг 2-5: Фаза C → несколько коммитов "refactor(screening): extract question_finder" и т.д.
# ...
```

**Rollback по шагу:** каждый шаг — отдельный коммит. `git revert HEAD` возвращает ровно один шаг.

**Полный rollback:** `git checkout pre-refactor` — возвращает на состояние до рефакторинга.
`git revert` для 17 шагов подряд — это 17 revert-ов. Tag проще и безопаснее.

**Smoke-test после каждого коммита:**
```bash
python -c "from src.applier.greenhouse_apply import GreenhouseApplier; print('OK')"
python -c "from src.applier.indeed_apply import IndeedApplier; print('OK')"
# После Фазы C:
python -c "from src.applier.universal_screening import UniversalScreeningHandler; print('OK')"
```

**Если шаг 12 (combobox) сломал Greenhouse:** `git revert` одного коммита, старый код возвращается. e2e-тесты из Шага 11 помогут найти что именно сломалось.

---

## Circular imports и TYPE_CHECKING

При создании пакетов с перекрёстными зависимостями (например, `greenhouse/form_filler.py` импортирует из `universal_screening/`) риск circular import высок.

**Правило:** для аннотаций типов использовать `TYPE_CHECKING`:
```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.applier.universal_screening.answer_router import AnswerRouter
    from src.applier.greenhouse.context import ApplyContext

# В runtime — только конкретные зависимости, которые реально нужны
```

**Проверка на circular import после каждой фазы:**
```bash
python -c "import src.applier.greenhouse; import src.applier.universal_screening"
```

---

## Верификация

### Юнит-тесты на HTML-дампах (создать в рамках Фазы C)

Главный бонус выноса `find_all_questions` в чистую функцию — тестирование оффлайн без браузера.

> ⚠️ **Ограничение статичного HTML:** современные ATS используют React — HTML меняется после кликов (раскрытые списки, динамические вопросы при выборе радио). Статичный `page.content()` покрывает только нулевое состояние формы.
>
> **Рекомендация:** использовать `browser.new_page() → page.set_content(html)` (реальный Playwright), а HTML-фикстуры делать с минимальным JS для реакции на клики. `AsyncMock()` молча проглатывает неизвестные методы — зелёный тест при сломанном коде.

**Три уровня тестов:**

| Уровень | Инструмент | Покрывает |
|---------|-----------|-----------|
| Юнит (без браузера) | `AsyncMock` + статичный HTML | Парсинг нулевого состояния, field_type detection |
| Интеграция (Playwright) | `page.set_content(html_with_js)` | Клики, динамические вопросы, fill mechanics |
| E2E (живая страница) | Реальная вакансия в тест-режиме | Полный прогон с реальным Indeed/Greenhouse |

**Шаг 1 — Собрать HTML-дампы** (3–4 реальные страницы с формами):
```bash
# Во время ручного прогона записать страницы:
await page.content()  # сохранить в tests/fixtures/indeed_form_*.html
```

Нужны варианты: radio-вопросы, select, text+number, смешанный, consent-чекбокс.

**Шаг 2 — Написать тесты** (`tests/test_universal_screening.py`):
```python
import pytest
# Используем реальный Playwright, не AsyncMock — иначе молчаливые false-positives
from playwright.async_api import async_playwright

@pytest.fixture
async def page_with_html(load_fixture):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        html = load_fixture("indeed_form_radio.html")
        await page.set_content(html)
        yield page
        await browser.close()

@pytest.mark.asyncio
async def test_find_radio_questions(page_with_html):
    questions = await find_all_questions(page_with_html)
    assert len(questions) >= 2
    assert questions[0].field_type == "radio"
```

> ✅ Когда Indeed изменит вёрстку — тест упадёт **сразу**, без ручного прогона по живой вакансии.

---

### Автоматические тесты
```bash
# После каждого шага:
pytest tests/ -x -v

# Только screening-тесты:
pytest tests/ -k "screening or combobox or question_finder" -v
```

### Ручная проверка (после полного рефакторинга)
1. Запустить GUI (`python gui.py`)
2. Найти 1 вакансию на Greenhouse → нажать Apply → убедиться что форма заполняется и подаётся
3. Найти 1 вакансию на Indeed Easy Apply → нажать Apply → убедиться что проходит шаги формы
4. Проверить логи в `bot.log` — не должно быть `ImportError` и `AttributeError`

---

## Чеклист выполнения

### Предварительный аудит (ДО старта любой фазы)
- [ ] `git tag pre-refactor` — **обязательный** safety net
- [ ] `grep -rn "from.*greenhouse_apply import" . --include="*.py"` — проверить всех потребителей
- [ ] `grep -rn "from.*indeed_apply import" . --include="*.py"`
- [ ] `grep -rn "from.*indeed_screening import" . --include="*.py"`
- [ ] `grep -rn "_answer_open_ended" . --include="*.py"` — подтвердить что вызовов нет → удалить
- [ ] `grep -rn "AudioCaptchaSolver" . --include="*.py"` — зафиксировать зависимость captcha
- [ ] `grep -rn "UniversalAIFiller" . --include="*.py"` — зафиксировать LLM fallback
- [ ] Создать `src/applier/config.py` (общие RESUME_PATH, COVER_LETTER_PATH, SCREENSHOTS_DIR)

### Фаза C — Universal Screening (бывший Indeed Screening)
- [ ] Создать `src/applier/universal_screening/__init__.py` (с алиасом `IndeedScreeningHandler`)
- [ ] Создать `src/applier/universal_screening/models.py` (dataclass `Question` + HITL helpers)
- [ ] Создать `src/applier/universal_screening/question_finder.py` (все 5 стратегий поиска)
- [ ] Создать `src/applier/universal_screening/radio_filler.py` (click_radio_option + fill_radio)
- [ ] Создать `src/applier/universal_screening/answer_router.py` (класс `AnswerRouter` — возвращает `str`, не работает с `page`)
- [ ] ⚠️ **Удалить `_answer_open_ended`** — DEPRECATED, grep подтвердил 0 вызовов
- [ ] Создать `src/applier/universal_screening/handler.py` (`UniversalScreeningHandler` — DOM-loop + `_load_profile()`!)
- [ ] Собрать 3–4 HTML-дампа реальных Indeed-форм → `tests/fixtures/indeed_form_*.html`
- [ ] Написать `tests/test_universal_screening.py` (Playwright `page.set_content()`, не AsyncMock)
- [ ] `pytest tests/` — зелёный
- [ ] Обновить **5 потребителей** на `UniversalScreeningHandler` (`indeed_apply.py` — пропустить, удаляется в Фазе B)
- [ ] ⚠️ **`indeed_router.py:26`** — TOP-LEVEL import, обновить **обязательно** наравне с другими
- [ ] Удалить `src/applier/indeed_screening.py`
- [ ] Smoke-test: `grep -rn "IndeedScreeningHandler" . --include="*.py" | grep -v "# alias"` → 0 результатов
- [ ] Удалить алиас `IndeedScreeningHandler` из `universal_screening/__init__.py` (после Фазы B)

### Фаза B — Indeed
- [ ] Создать `src/applier/indeed/__init__.py`
- [ ] Создать `src/applier/indeed/config.py` (только `IA` dict — никаких функций)
- [ ] Создать `src/applier/indeed/locators.py` (`click_apply` + `find_apply_frame` — только Playwright-функции)
- [ ] `src/applier/ia_legacy.py` — **не трогать** (frozen fallback, намеренное дублирование)
- [ ] Создать `src/applier/indeed/captcha.py` (перенести все 4 captcha метода + `AudioCaptchaSolver`!)
- [ ] Создать `src/applier/indeed/form_filler.py` (upload + fill + radios)
- [ ] Создать `src/applier/indeed/navigation.py` (continue/submit/success/fingerprint)
- [ ] Создать `src/applier/indeed/applier.py` (IndeedApplier + `UniversalScreeningHandler` + `UniversalAIFiller` + `FormPatternStore`)
- [ ] Обновить `src/applier/indeed_router.py:26` (TOP-LEVEL `IndeedScreeningHandler`!) и `:116,135` (lazy `IndeedApplier`/`GreenhouseApplier`)
- [ ] `pytest tests/` — зелёный
- [ ] Удалить `src/applier/indeed_apply.py`

### Фаза A — Greenhouse
- [ ] ⚡ **БЛОКИРУЮЩИЙ:** написать `tests/e2e/test_combobox_baseline.py` на СУЩЕСТВУЮЩИЙ `_fill_combobox_el`
- [ ] `pytest tests/e2e/test_combobox_baseline.py -v` — зелёный (baseline зафиксирован)
- [ ] Создать `src/applier/greenhouse/__init__.py`
- [ ] Создать `src/applier/greenhouse/context.py` (`ApplyContext` с typed полями через `TYPE_CHECKING`)
- [ ] Создать `src/applier/greenhouse/utils.py` (`get_label` — общая утилита для validator/form_filler/combobox)
- [ ] Создать `src/applier/greenhouse/combobox_js.py` (JS-шаблоны `GET_OPTS_JS`, `FIND_OTHER_OPTION_JS` и т.д.)
- [ ] Создать `src/applier/greenhouse/combobox_engine.py` (РЕИНЖИНИРИНГ: `ReactComboboxDriver`, импортирует из `combobox_js.py`)
- [ ] `pytest tests/e2e/test_combobox_baseline.py -v` — зелёный после реинжиниринга
- [ ] Создать `src/applier/greenhouse/validator.py` (использует `get_label` из `utils.py`, НЕ callback)
- [ ] Создать `src/applier/greenhouse/security.py` (использует `ctx: ApplyContext`)
- [ ] Создать `src/applier/greenhouse/form_filler.py` (переносит batch AI AS-IS, **НЕ** заменяет на `AnswerRouter`)
- [ ] Создать `src/applier/greenhouse/file_uploader.py`
- [ ] Создать `src/applier/greenhouse/applier.py`
- [ ] **Первым** обновить `src/bot/its_check_bot.py:32` (top-level импорт — сломает бот при старте)
- [ ] Обновить `src/applier/queue_manager.py` (в `_apply_greenhouse`)
- [ ] Обновить `src/applier/easy_apply.py` (в `_apply_external`)
- [ ] Обновить `src/applier/indeed_router.py` (в `_route_to_ats`)
- [ ] Обновить `src/platforms/greenhouse_scraper.py` (в `apply`)
- [ ] Обновить `src/gui/apply_service.py` (в `apply_greenhouse`)
- [ ] Обновить `src/bot/ats_test_menu.py` (в `_run_test`)
- [ ] ⚠️ `src/applier/__init__.py` — добавить в `__getattr__` (PEP 562), **НЕ** в top-level import
- [ ] `pytest tests/` — зелёный
- [ ] Удалить `src/applier/greenhouse_apply.py`
