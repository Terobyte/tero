# LDB Integration Plan — `tero ldb`

> **Что делаем:** интегрируем LDB (Large Language Model Debugger, ACL'24, [FloridSleeves/LLMDebugger](https://github.com/FloridSleeves/LLMDebugger)) — block-level runtime verification — как **отдельную команду** `tero ldb`, рядом с существующим статическим `tero debug`. Отдельные конфиги, отдельное меню, общий provider-слой.
> **Источник:** оригинальный код прочитан в `/tmp/ldb-source/programming/{tracing,generators,executors}/`. Алгоритм описан в спеке ниже (раздел "Алгоритм LDB").
> **Workflow:** TDD по фазам. Каждый чекбокс — короткий шаг. Коммит после каждой фазы.

---

## Контекст и мотивация

Существующий `tero debug` — **статический анализатор**: Player читает код по тексту через `INTENSITY_PROMPTS` и выдаёт список багов на основе паттернов. Сильные стороны: работает на любом коде, не нужен исполняемый ввод. Слабые стороны: пропускает баги, которые видны только в runtime (off-by-one в неочевидных формулах, swallowed exceptions с реальными значениями, race в state-mutation).

LDB закрывает именно эту дыру: разбивает функцию на basic blocks через CFG, запускает код с **синтезированными входами**, печатает значения переменных на границах каждого блока, и LLM по этим значениям точно указывает на baggy block. На HumanEval с GPT-4o это даёт **98.2%** (paper). У нас будет ниже из-за продакшн-адаптации (см. ниже), но всё равно дополняет статический Player.

---

## Алгоритм LDB (как в paper и их коде)

```
┌─ Вход: file.py + entry_point (имя функции) + (опц.) failing_test
│
├─ 1. Block decomposition (tracing/tracer.py:divide)
│      staticfg.CFGBuilder → basic blocks с line-ranges
│
├─ 2. Trace execution (tracing/tracer.py:get_trace)
│      python -m trace -t → line-level execution log
│
├─ 3. Value profiling (tracing/tracer.py:instrument_simple_block)
│      Вставка `print(f'Value_After:{lineno}|var=val|...')` после границ блоков
│      Повторный запуск → значения переменных на границах
│
├─ 4. LLM Player (generators/py_generate.py:check_block_correctness)
│      Промт: "вот блоки с трассой, для каждого скажи correct/incorrect + explanation"
│      Парсит JSON: {"block": "BLOCK-N", "correct": bool, "explanation": str}
│      Первый incorrect → buggy block
│
└─ 5. (только Mode 3) LLM Fixer
       Промт: "вот wrong block + explanation, почини"
       → новая версия функции
```

**Наша адаптация для продакшн-кода без готовых тестов:**

- [x] **Синтез входов через отдельного LLM-агента (Input-Synthesizer)**: получает сигнатуру, типы, docstring + соседний контекст вызовов и выдаёт 2-3 валидных входа в формате `entry(...)`. Это **полноценный 4-й агент** с собственным выбором провайдера/модели в меню — наравне с Player/Tester/Fixer.
- [x] **Failing test опционален**: если user передал `--test "assert foo(1)==2"`, используем как в оригинале (skipping Input-Synthesizer). Иначе вызываем Input-Synthesizer.
- [x] **Block-level (не line/function)**: фиксируем `level="block"` — это компромисс по precision/cost из paper.
- [x] **bugs.md в корне working_dir**: LDB пишет находки в `{working_dir}/bugs.md` (тот же файл, что и `tero debug`, чтобы был один источник правды).
- [x] **Whole-project mode**: опциональный `--all` обходит каждую публичную функцию в `working_dir/` (по AST), запускает LDB-пайплайн на каждой. Дефолт — `--file --entry` (прозрачно: user видит что анализируется).
- [x] **Mode 3 auto-commit**: после успешного Fixer'а runner делает `git add -A && git commit -m "ldb fix: <summary>"` (зеркалит поведение `tero debug`).

---

## Architectural Decisions

| # | Вопрос | Решение | Обоснование |
|---|--------|---------|-------------|
| 1 | Где живёт LDB? | Новый пакет `src/ldb/` (не трогаем `src/debugger*`) | Изоляция; `tero debug` остаётся как fast static, `tero ldb` — heavy runtime |
| 2 | Block CFG | Вендорим `programming/tracing/staticfg/` из LDB → `src/ldb/staticfg/` (MIT) | Их код 4 файла, протестирован на HumanEval; писать свой CFG-builder = риск |
| 3 | Tracing | `python -m trace -t` через subprocess (как в LDB) | `sys.settrace` в одном процессе ломается на async/threads в продакшн-коде |
| 4 | Mode 2 vs 3 | CLI flag `--mode {2,3}` + меню radio | Mode 2 = readonly (Input+Player+Tester), Mode 3 = destructive (+Fixer+auto-commit) |
| 5 | Архитектурный fix prompt | 2-фазный внутри одного вызова: «Phase A — Architectural review (root-cause + design alternatives), Phase B — Minimal patch» | User: «сначала архитектурные фиксы потом обычные» — единый prompt без двух раундтрипов до LLM |
| 6 | bugs.md location | `{working_dir}/bugs.md` (см. feedback memory) | Всегда корень, без вложенности |
| 7 | Gemini CLI provider | Новый `src/providers/gemini.py` (клон `opencode.py`), default `gemini-2.5-pro` | `gemini -p <prompt> -o stream-json` — тот же subprocess+JSONL паттерн |
| 8 | Агентов 4, не 3 | Input-Synthesizer (LLM) + Player + Tester + Fixer, каждый с независимым provider/model | User просит: «каждая фаза отдельный вызов и возможность выбрать оператора» |
| 9 | Scope target | `--file --entry` обязательны; опциональный `--all` для whole-project | Прозрачность: дефолт показывает user'у что именно анализируется |
| 10 | Auto-commit Mode 3 | После Fixer: `git add -- <files>` (НЕ `-A`) + commit | Использует селективный staging-паттерн из `src/debugger.py:524 _git_commit(..., files=...)`. Чистая `tero debug` тоже умеет селективно — план зеркалит этот безопасный путь, а НЕ её fallback на `-A` (issues #11, #12) |

---

## File Structure

**Создаём:**
- [x] `src/providers/gemini.py` — Gemini CLI provider (subprocess + stream-json)
- [x] `src/ldb/__init__.py` — публичные экспорты
- [x] `src/ldb/blocks.py` — обёртка над staticfg (`decompose_function(prog, entry) -> List[Block]`)
- [x] `src/ldb/tracer.py` — runtime tracer (`trace_function(prog, test, entry) -> List[BlockTrace]`)
- [~] `src/ldb/inputs.py` — **LLM-driven** синтез входов (`synthesize_inputs_llm(provider, source, entry) -> List[str]`)
- [~] `src/ldb/prompts.py` — `INPUT_PROMPT_LDB`, `PLAYER_PROMPT_LDB`, `TESTER_PROMPT_LDB`, `FIXER_PROMPT_LDB_ARCH`
- [~] `src/ldb/runner.py` — главный класс `LdbRunner` (Input → Player → Tester → Fixer)
- [~] `src/ldb/scope.py` — `iter_targets(working_dir)` для `--all` режима (обход AST публичных функций)
- [~] `src/ldb/staticfg/` — вендор-копия из `/tmp/ldb-source/programming/tracing/staticfg/`
- [~] `tests/test_ldb_blocks.py`, `tests/test_ldb_tracer.py`, `tests/test_ldb_inputs.py`, `tests/test_ldb_scope.py`, `tests/test_ldb_runner.py`
- [~] `tests/test_gemini_provider.py`

**Модифицируем:**
- [~] `src/providers/__init__.py` — регистрация `gemini`
- [~] `src/providers/registry.py` — фабрика `gemini`
- [~] `src/cli_entry.py` — `PROVIDER_CHOICES` += `"gemini"`, новый subparser `ldb`, `run_ldb()`
- [~] `src/menu.py` — новое меню `run_ldb_menu()`, добавить `gemini` в `PROVIDER_PRESETS`, `GEMINI_MODEL_PRESETS`
- [~] `src/config.py` — поля `ldb_*` (mirror `debug_*`) + env mapping
- [~] `src/constants.py` — `DEFAULT_LDB_LIMIT_VALUE`, `DEFAULT_LDB_TIMEOUT_S`
- [~] `pyproject.toml` — deps: `astroid`, `astunparse` (staticfg уже вендорим)

---

## Phase 1: Gemini CLI Provider

- [~] **1.1 Написать failing-тест для GeminiProvider.check_ready()**

Создать `tests/test_gemini_provider.py`:

```python
from src.providers.gemini import GeminiConfig, GeminiProvider

def test_check_ready_when_command_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    p = GeminiProvider(GeminiConfig(command="gemini"))
    ok, reason = p.check_ready()
    assert ok is False
    assert "gemini" in reason.lower()

def test_check_ready_when_command_present(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/gemini")
    p = GeminiProvider(GeminiConfig(command="gemini"))
    ok, reason = p.check_ready()
    assert ok is True
    assert reason == ""

def test_display_name():
    p = GeminiProvider(GeminiConfig(default_model="gemini-2.5-pro"))
    assert "Gemini" in p.display_name
    assert "gemini-2.5-pro" in p.display_name
```

Запуск: `pytest tests/test_gemini_provider.py -v` → ожидается ImportError (модуль не существует).

- [~] **1.2 Создать `src/providers/gemini.py`**

Скопировать `src/providers/opencode.py` как стартовую точку. Изменить:
- [~] Класс `GeminiConfig`: `command="gemini"`, `default_model="gemini-2.5-pro"`, `display_name="Gemini"`.
- [~] В `_build_command()`: команду собрать как `[command, "-p", prompt, "-o", "stream-json", "--yolo"]`. Подтверждено работает: `echo | gemini -p "hi" -o stream-json --yolo` выдаёт JSONL. Передавать модель через `-m`. **Не передавать stdin** (как opencode) — Gemini берёт prompt только из `-p`. Если prompt > N символов — писать в tmp file и `cat tmp | gemini -p "see stdin" -o stream-json --yolo`.
- [~] **Реальный формат событий** (из подтверждённого smoke-теста):
  - `{"type":"init","timestamp":...,"session_id":...,"model":"..."}` — игнорируем
  - `{"type":"message","role":"assistant","content":"...","delta":true}` — оборачиваем в `AdaptedMessage(role="assistant", content=[TextBlock(text=content)])`
  - `{"type":"result","status":"success","stats":{...}}` — финальный, фиксируем токены
- [~] Реализовать `_adapt_gemini_event(event)` — НЕ копировать `_adapt_opencode_event` дословно: ключи разные.
- [~] `check_ready()`: `shutil.which(self.config.command) is not None`.
- [~] `display_name`: `f"Gemini ({self.config.default_model})"`.

- [~] **1.3 Запустить тест из 1.1** → должен пройти. Затем добавить интеграционный тест с реальным `gemini --version` через `subprocess`:

```python
def test_gemini_cli_available():
    import subprocess
    res = subprocess.run(["gemini", "--version"], capture_output=True, timeout=5)
    assert res.returncode == 0
```

(Skipped if not installed.)

- [~] **1.4 Зарегистрировать в фабрике**

В `src/providers/__init__.py:create_provider()` добавить ветку:

```python
if provider_type == "gemini":
    from .gemini import GeminiProvider, GeminiConfig
    gemini_cfg = GeminiConfig(
        command=provider_config.get("command", "gemini"),
        default_model=provider_config.get("default_model", "gemini-2.5-pro"),
        default_timeout=provider_config.get("default_timeout", DEFAULT_PROVIDER_TIMEOUT_S),
    )
    return GeminiProvider(gemini_cfg)
```

И аналогично в `src/providers/registry.py:_create_provider()`.

- [~] **1.5 Прокинуть `gemini` в CLI choices**

`src/cli_entry.py:17`:

```python
PROVIDER_CHOICES = ["zai", "claude", "codex", "opencode", "kilo", "gemini"]
```

- [~] **1.6 Прокинуть в меню**

`src/menu.py`:

```python
GEMINI_MODEL_PRESETS = {
    "Gemini 2.5 Pro": "gemini-2.5-pro",
    "Gemini 2.5 Flash": "gemini-2.5-flash",
    "Default (no -m)": "",
}

PROVIDER_PRESETS = {
    ...existing...,
    "Gemini (CLI)": "gemini",
}
```

В `_model_presets_for_provider()`: ветка `if provider == "gemini": return GEMINI_MODEL_PRESETS`.
В `_fixed_model_for_provider()`: gemini не fixed, остаётся `""`.

- [~] **1.7 Smoke-тест ручной**

```bash
G3_PLAYER_PROVIDER=gemini tero go --no-menu --plan requirements.md
```

Проверить, что Player запустился через `gemini -p ... -o stream-json` и stream дошёл до stdout.

- [~] **1.8 Коммит**

```bash
git add src/providers/gemini.py src/providers/__init__.py src/providers/registry.py src/cli_entry.py src/menu.py tests/test_gemini_provider.py
git commit -m "add gemini cli provider"
```

---

## Phase 2: LDB Core — block decomposition + tracer

- [~] **2.1 Вендор staticfg**

```bash
cp -r /tmp/ldb-source/programming/tracing/staticfg src/ldb/staticfg
```

Обновить `src/ldb/staticfg/__init__.py` чтобы импорт работал как `from src.ldb.staticfg import CFGBuilder`. Проверить импорт:

```bash
python -c "from src.ldb.staticfg import CFGBuilder; print(CFGBuilder)"
```

- [x] **2.2 Failing test для blocks.decompose_function()**

`tests/test_ldb_blocks.py`:

```python
from src.ldb.blocks import decompose_function

def test_decompose_simple_function():
    src = "def add(a, b):\n    if a > 0:\n        return a + b\n    return b\n"
    blocks = decompose_function(src, entry="add")
    assert blocks is not None
    assert len(blocks) >= 2  # if-branch, return
    # каждый блок — (block_id: int, lines: list[str], start: int, end: int)
    assert all(isinstance(b.block_id, int) for b in blocks)
    assert all(b.start <= b.end for b in blocks)

def test_decompose_returns_none_on_syntax_error():
    blocks = decompose_function("def broken(\n", entry="broken")
    assert blocks is None
```

- [x] **2.3 Имплементировать `src/ldb/blocks.py`**

```python
"""Block-level decomposition via staticfg CFG."""

from dataclasses import dataclass
from typing import List, Optional

from src.ldb.staticfg import CFGBuilder


@dataclass(frozen=True)
class Block:
    block_id: int
    lines: list[str]   # исходные строки блока
    start: int         # 0-indexed line in source
    end: int

def decompose_function(source: str, entry: str) -> Optional[List[Block]]:
    """Split a function into basic blocks via control-flow graph.

    Returns None if source has syntax errors or entry not found.
    Adapted from /tmp/ldb-source/programming/tracing/tracer.py:divide().
    """
    try:
        cfg = CFGBuilder().build_from_src("block", source)
    except Exception:
        return None
    src_lines = source.split("\n")
    blocks = []
    for raw in cfg:
        # raw.at(), raw.end() — 1-indexed inclusive ranges from staticfg
        start = raw.at() - 1
        end = raw.end() - 1
        blocks.append(Block(
            block_id=raw.id,
            lines=src_lines[start:end + 1],
            start=start,
            end=end,
        ))
    return blocks
```

- [x] **2.4 Запустить тест 2.2** → должен пройти. Если staticfg ругается на `from .staticfg import` — поправить относительные импорты внутри вендоренного пакета.

- [x] **2.5 Failing test для tracer**

> **Note (issue #13)**: `trace_function()` принимает source как **строку** и САМ записывает её во временный файл (поведение унаследовано из LDB `tracer.py:get_trace`). Тест передаёт строку, никаких файлов руками создавать не надо. Это контракт API — задокументирован в docstring.

`tests/test_ldb_tracer.py`:

```python
from src.ldb.tracer import trace_function

def test_trace_function_buggy_returns_blocks_with_values(tmp_path, monkeypatch):
    """trace_function writes source to a tmp file internally; test verifies the contract."""
    monkeypatch.chdir(tmp_path)  # tracer creates its tmp files in CWD
    src = (
        "def add(a, b):\n"
        "    s = a - b  # bug: should be +\n"
        "    return s\n"
    )
    test = "assert add(2, 3) == 5"
    result = trace_function(source=src, test=test, entry="add", timeout=5)
    assert result.kind == "ok"
    assert len(result.blocks) >= 1
    flat = "\n".join(line for b in result.blocks for line in b.rendered)
    assert "a=2" in flat or "a = 2" in flat
    assert "b=3" in flat or "b = 3" in flat

def test_trace_function_timeout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = "def loop(x):\n    while True:\n        x += 1\n"
    result = trace_function(source=src, test="loop(0)", entry="loop", timeout=2)
    assert result.kind == "timeout"

def test_trace_function_syntax_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = trace_function(source="def broken(:\n", test="broken()", entry="broken", timeout=5)
    assert result.kind == "fail"
```

- [x] **2.6 Имплементировать `src/ldb/tracer.py`**

Адаптировать `programming/tracing/tracer.py` функции `divide`, `instrument_simple_block`, `get_trace`, `collect_runtime_value_simple`, `parse_runtime_value_simple_block` в один публичный entrypoint `trace_function()`. Возврат:

```python
@dataclass
class BlockTrace:
    block_id: int
    rendered: list[str]  # ['# a=2 b=3', '    s = a - b', '# a=2 b=3 s=-1']

@dataclass
class TraceResult:
    kind: str  # "ok" | "timeout" | "fail" | "parse_fail"
    blocks: list[BlockTrace]
    error: str  # пустая на ok
```

Ключевые точки портирования:
- [x] Subprocess запуск Python через `python -m trace -t` для line-trace и через инструментированный код для value-profile (как в LDB).
- [x] `_str=str` хедер обязателен (LDB использует `_str(__var_list[x])`, потому что `str` могут переопределить).
- [x] Логи трейса в `{working_dir}/.ldb-trace/` (не в `../tracing_log/` как у LDB).
- [x] Cleanup временных файлов через `try/finally`.

- [x] **2.7 Запустить tracer-тесты** → проходят.

- [ ] **2.8 Коммит**

```bash
git add src/ldb/__init__.py src/ldb/staticfg src/ldb/blocks.py src/ldb/tracer.py tests/test_ldb_blocks.py tests/test_ldb_tracer.py
git commit -m "ldb core: block decomposition + runtime tracer"
```

---

## Phase 3: Input-Synthesizer (LLM-агент, не код)

> User: «каждая фаза отдельный вызов и возможность выбрать оператора». Поэтому Input-Synthesizer — это **полноценный LLM-агент**, не код-эвристика. У него свой провайдер/модель в меню и CLI.

- [ ] **3.1 Failing test для inputs.synthesize_inputs_llm()**

`tests/test_ldb_inputs.py`:

```python
import pytest
from unittest.mock import AsyncMock
from src.ldb.inputs import synthesize_inputs_llm, parse_inputs_response

def test_parse_inputs_response_extracts_calls():
    raw = '''Here are the inputs:
1. `add(2, 3)` — basic positives
2. `add(-1, 1)` — boundary
3. `add(0, 0)` — zero
'''
    inputs = parse_inputs_response(raw, entry="add")
    assert "add(2, 3)" in inputs[0]
    assert "add(-1, 1)" in inputs[1]
    assert len(inputs) == 3

def test_parse_inputs_skips_garbage():
    raw = "I don't know, maybe try add(1, 2)?"
    inputs = parse_inputs_response(raw, entry="add")
    assert any("add(1, 2)" in i for i in inputs)

@pytest.mark.asyncio
async def test_synthesize_inputs_llm_calls_provider():
    fake_provider = AsyncMock()
    async def fake_run(*a, **kw):
        from src.providers import AdaptedMessage, TextBlock
        yield AdaptedMessage(role="assistant", content=[TextBlock(text="1. `add(1, 2)`\n2. `add(0, 0)`")])
    fake_provider.run = fake_run
    inputs = await synthesize_inputs_llm(
        provider=fake_provider, source="def add(a, b): return a+b",
        entry="add", model="", n=2,
    )
    assert len(inputs) == 2
    assert "add(1, 2)" in inputs[0]
```

- [ ] **3.2 Создать `src/ldb/prompts.py` с `INPUT_PROMPT_LDB`** (другие промты добавит Phase 4.1 — НЕ перезаписывать файл там)

```python
INPUT_PROMPT_LDB = '''You are a test-input designer for runtime debugging.

You will receive a function's source code. Your job: produce 2-3 SHORT, valid call expressions
that exercise the function's main paths — happy path, boundary, edge case.

Rules:
- Each input must be a valid Python expression `<entry>(...)` that runs without crashing the
  interpreter at parse time. We need to TRACE execution, not test correctness.
- Prefer values from docstring examples / doctests if present.
- Use type annotations to pick reasonable values (int → 0, 1, -1; str → "abc"; list → [1,2,3]).
- Avoid expensive inputs (huge lists, recursive structures) — they slow tracing.
- Output as a numbered list, one input per line, code in backticks. No prose explanations.

Example output:
1. `parse_csv("a,b,c")`
2. `parse_csv("")`
3. `parse_csv("a,,b")`
'''
```

- [ ] **3.3 Имплементировать `src/ldb/inputs.py`**

```python
"""LLM-driven input synthesis for LDB tracing."""

import re
from typing import List
from src.providers.base import AgentProvider
from src.ldb.prompts import INPUT_PROMPT_LDB


def parse_inputs_response(raw: str, entry: str) -> List[str]:
    """Extract `entry(...)` calls from LLM response (numbered list, backticks)."""
    matches = re.findall(rf"`({re.escape(entry)}\([^`]*\))`", raw)
    if matches:
        return matches
    # Fallback: any line containing entry(...)
    return re.findall(rf"({re.escape(entry)}\([^)\n]*\))", raw)


async def synthesize_inputs_llm(
    provider: AgentProvider,
    source: str,
    entry: str,
    model: str = "",
    n: int = 2,
    working_dir: str = ".",
) -> List[str]:
    """Ask the LLM to produce N call expressions exercising `entry`."""
    user_prompt = (
        f"Function source:\n```python\n{source}\n```\n\n"
        f"Entry point: `{entry}`\n\n"
        f"Produce {n} input call expressions."
    )
    collected = []
    async for msg in provider.run(
        prompt=user_prompt,
        system_prompt=INPUT_PROMPT_LDB,
        working_dir=working_dir,
        max_turns=1,
        model=model,
    ):
        for block in getattr(msg, "content", []) or []:
            if hasattr(block, "text"):
                collected.append(block.text)
    inputs = parse_inputs_response("\n".join(collected), entry)
    return inputs[:n] if inputs else [f"{entry}()"]
```

- [ ] **3.4 Запустить inputs-тесты** → проходят.

- [ ] **3.5 Коммит**

```bash
git add src/ldb/inputs.py src/ldb/prompts.py tests/test_ldb_inputs.py
git commit -m "ldb: llm-driven input synthesizer agent"
```

---

## Phase 4: Prompts (Player, Tester, Fixer-arch)

- [ ] **4.1 Дополнить `src/ldb/prompts.py`** (файл уже создан в Phase 3.2 с `INPUT_PROMPT_LDB` — ИСПОЛЬЗОВАТЬ append, не overwrite)

Базируется на `programming/generators/py_generate.py:check_block_correctness` (там msg.content собирается с примерами JSON), но адаптирован под наш формат и под отсутствие failing-test.

```python
PLAYER_PROMPT_LDB = '''You are a senior Python engineer doing block-level runtime debugging.

You will receive:
1. A function's source code.
2. The execution trace for that function on synthesized inputs, split into blocks.
3. Each block shows: lines of code + variable values BEFORE and AFTER the block runs.

Your job: for EACH block, decide if its runtime behavior is correct given the function's docstring/intent.

## Output format

Output one JSON object per line, no prose:

{"block": "BLOCK-0", "correct": true, "explanation": "Initializes accumulator to 0."}
{"block": "BLOCK-1", "correct": false, "explanation": "Subtracts instead of adds — the docstring says 'sum of two numbers'. Line `s = a - b` should be `s = a + b`."}

Rules:
- One JSON object per line, no markdown fences.
- "correct" is bool (true/false), no strings.
- Mark a block "correct: false" only if you can name a SPECIFIC line and the SPECIFIC wrong value vs expected.
- Skip "looks fine" — only report definitive bugs.
- If multiple blocks have the same root cause, mark only the FIRST one as the bug.
'''

TESTER_PROMPT_LDB = '''You are a test engineer. For each LDB-confirmed bug, write ONE pytest test that:

1. Imports the actual function from its source path (no mocking the function under test).
2. Calls the function with the SAME inputs LDB used to expose the bug.
3. Asserts the CORRECT behavior (so the test FAILS on the buggy code, PASSES once fixed).

Output a JSON list:

[
    {"bug_id": 1, "test_file": "tests/test_ldb_bug_<n>.py", "status": "confirmed"},
    {"bug_id": 2, "test_file": null, "status": "false_positive"}
]

After writing each test, run it with `pytest <path> -x -q` to confirm it FAILS.
If a test unexpectedly PASSES (the bug isn't really there), mark status as "false_positive".
'''

FIXER_PROMPT_LDB_ARCH = '''You are fixing confirmed bugs found by LDB. You will work in TWO phases for EACH bug:

## Phase A — Architectural Review (always first)

Before touching code, ask:
1. **Root cause class**: is this a *local* bug (single wrong operator/value) or *architectural* (wrong abstraction, missing invariant, broken contract between functions)?
2. **Design alternatives**: if architectural, are there 1-2 cleaner refactors that would make this class of bug impossible? List them with trade-offs.
3. **Decision**: pick architectural fix OR local patch, with one-line justification. Default to LOCAL unless the architectural cost is small AND it eliminates a class of bugs.

Output Phase A as comments at the top of your fix:

```
# LDB Phase A:
# Root cause: <local|architectural>
# Decision: <local-patch|refactor-X>
# Why: <1 line>
```

## Phase B — Implementation

Implement the chosen fix. Rules:
- If LOCAL: minimal diff, change only the buggy lines.
- If ARCHITECTURAL: full refactor, but keep the public signature stable (callers must keep working).
- Run the test from Tester phase: `pytest <path> -x -q` — must PASS after your fix.
- Then run the full suite: `pytest tests/ -x -q --tb=short`. If anything breaks, ADJUST YOUR FIX, do NOT modify other tests.
- After all bugs fixed and suite green: `git add -A && git commit -m "ldb fix: <summary>"`.

## Output

For each bug, output the Phase A comment block + the new code, in the exact format the project expects.
'''
```

- [ ] **4.2 Тест на парсинг ответа Player'а**

`tests/test_ldb_runner.py` (заглушка пока). Тест проверяет, что **только первый** `correct: false` возвращается (правило промта Player: «If multiple blocks have the same root cause, mark only the FIRST one as the bug»):

```python
from src.ldb.runner import parse_player_response

def test_parse_player_response_returns_only_first_incorrect():
    """Per PLAYER_PROMPT_LDB rule: report only the FIRST wrong block."""
    raw = '''
{"block": "BLOCK-0", "correct": true, "explanation": "init ok"}
{"block": "BLOCK-1", "correct": false, "explanation": "wrong op"}
{"block": "BLOCK-2", "correct": false, "explanation": "downstream side-effect of BLOCK-1"}
'''
    fake_trace_blocks = [
        type("BT", (), {"rendered": [f"line {i}"], "block_id": i})()
        for i in range(3)
    ]
    bug = parse_player_response(raw, fake_trace_blocks)
    assert bug is not None
    assert bug.block_id == 1
    assert "wrong op" in bug.explanation
    # Make sure we DIDN'T return BLOCK-2 — it's downstream

def test_parse_player_response_all_correct():
    raw = '{"block": "BLOCK-0", "correct": true, "explanation": "ok"}'
    assert parse_player_response(raw, []) is None
```

- [ ] **4.3 Имплементировать `parse_player_response()` в runner.py**

Адаптация `programming/generators/py_generate.py:parse_explanation`. Возвращает первый блок с `correct=false` или `None`.

- [ ] **4.4 Коммит**

```bash
git add src/ldb/prompts.py src/ldb/runner.py tests/test_ldb_runner.py
git commit -m "ldb: prompts (player/tester/arch-fixer) + response parser"
```

---

## Phase 5: Config + CLI + Menu

- [ ] **5.1 Поля в `src/config.py:Config`**

После `debug_*` полей добавить (заметь — **4 агента**, у Input-Synthesizer тоже свой провайдер/модель):

```python
# LDB (Block-level runtime debugger)
ldb_input_provider: str = "claude"      # NEW: input synthesizer
ldb_player_provider: str = "claude"
ldb_tester_provider: str = "claude"
ldb_fixer_provider: str = "codex"
ldb_input_model: str = ""               # NEW
ldb_player_model: str = ""
ldb_tester_model: str = ""
ldb_fixer_model: str = ""
ldb_mode: int = 2                       # 2 = input+find+test, 3 = +fix +commit
ldb_target_file: str = ""               # path to file (required if not --all)
ldb_target_entry: str = ""              # function name (required if not --all)
ldb_test_input: str = ""                # optional explicit assert; empty = LLM synthesize
ldb_scope_all: bool = False             # --all: walk all public functions in working_dir
ldb_max_iterations: int = 3
ldb_timeout_s: int = 30
```

И env mapping в `_ENV_MAP`:

```python
"G3_LDB_INPUT_PROVIDER": ("ldb_input_provider", str),
"G3_LDB_PLAYER_PROVIDER": ("ldb_player_provider", str),
"G3_LDB_TESTER_PROVIDER": ("ldb_tester_provider", str),
"G3_LDB_FIXER_PROVIDER": ("ldb_fixer_provider", str),
"G3_LDB_INPUT_MODEL": ("ldb_input_model", str),
"G3_LDB_PLAYER_MODEL": ("ldb_player_model", str),
"G3_LDB_TESTER_MODEL": ("ldb_tester_model", str),
"G3_LDB_FIXER_MODEL": ("ldb_fixer_model", str),
"G3_LDB_MODE": ("ldb_mode", int),
"G3_LDB_MAX_ITERATIONS": ("ldb_max_iterations", int),
"G3_LDB_TIMEOUT_S": ("ldb_timeout_s", int),
```

**ВАЖНО:** добавить `ldb_input_provider`, `ldb_player_provider`, `ldb_tester_provider`, `ldb_fixer_provider` в whitelist нормализации `_normalize_provider_name()` внутри `resolve_config()` (строки 446-461 `src/config.py`), иначе env `G3_LDB_PLAYER_PROVIDER=claude` не пройдёт нормализацию:

```python
for key in (
    ...existing...,
    "debug_player_provider", "debug_tester_provider", "debug_fixer_provider",
    "ldb_input_provider", "ldb_player_provider", "ldb_tester_provider", "ldb_fixer_provider",
):
```

- [ ] **5.2 Subparser `ldb` в `src/cli_entry.py`**

После `debug_parser` добавить:

```python
ldb_parser = subparsers.add_parser(
    "ldb", help="Block-level runtime debugger (LDB, ACL'24)"
)
ldb_parser.add_argument("--working-dir", "-w", type=str, default=".")
ldb_parser.add_argument("--file", type=str, required=False, help="path to .py with target function (required unless --all)")
ldb_parser.add_argument("--entry", type=str, required=False, help="target function name (required unless --all)")
ldb_parser.add_argument("--all", action="store_true", default=False, dest="ldb_scope_all", help="walk every public function in working_dir")
ldb_parser.add_argument("--test", type=str, default=None, help="explicit assert; if omitted, Input-Synthesizer LLM generates")
ldb_parser.add_argument("--mode", type=int, choices=[2, 3], default=None, dest="ldb_mode")
ldb_parser.add_argument("--input-provider", choices=PROVIDER_CHOICES, default=None, dest="ldb_input_provider")
ldb_parser.add_argument("--player-provider", choices=PROVIDER_CHOICES, default=None, dest="ldb_player_provider")
ldb_parser.add_argument("--tester-provider", choices=PROVIDER_CHOICES, default=None, dest="ldb_tester_provider")
ldb_parser.add_argument("--fixer-provider", choices=PROVIDER_CHOICES, default=None, dest="ldb_fixer_provider")
ldb_parser.add_argument("--input-model", type=str, default=None, dest="ldb_input_model")
ldb_parser.add_argument("--player-model", type=str, default=None, dest="ldb_player_model")
ldb_parser.add_argument("--tester-model", type=str, default=None, dest="ldb_tester_model")
ldb_parser.add_argument("--fixer-model", type=str, default=None, dest="ldb_fixer_model")
ldb_parser.add_argument("--max-iterations", type=int, default=None, dest="ldb_max_iterations")
ldb_parser.add_argument("--no-menu", action="store_true", default=False)
```

И функция `run_ldb(args)`:

```python
def run_ldb(args) -> None:
    from src.ldb.runner import LdbRunner
    from src.menu import run_ldb_menu

    import dataclasses
    # Bug fix (issue #2): include input_provider/input_model in CLI override mapping
    config = resolve_config({
        "working_dir": args.working_dir,
        **{k: getattr(args, k, None) for k in [
            "ldb_mode",
            "ldb_input_provider", "ldb_player_provider", "ldb_tester_provider", "ldb_fixer_provider",
            "ldb_input_model", "ldb_player_model", "ldb_tester_model", "ldb_fixer_model",
            "ldb_max_iterations", "ldb_scope_all",
        ]},
    })
    # Antipattern fix (issue #14): use dataclasses.replace, not Config(**dict)
    config = dataclasses.replace(
        config,
        ldb_target_file=args.file or config.ldb_target_file,
        ldb_target_entry=args.entry or config.ldb_target_entry,
        ldb_test_input=args.test or config.ldb_test_input,
    )
    # Validation (issue #9): require either explicit target OR --all
    if not config.ldb_scope_all and not (config.ldb_target_file and config.ldb_target_entry):
        if args.no_menu:
            print("Error: --file + --entry required, or use --all", file=sys.stderr)
            sys.exit(2)
        # else: menu will collect them
    if not args.no_menu:
        config = run_ldb_menu(config)
    if config is None:
        sys.exit(0)
    # Re-validate after menu
    if not config.ldb_scope_all and not (config.ldb_target_file and config.ldb_target_entry):
        print("Error: target not configured", file=sys.stderr)
        sys.exit(2)

    runner = LdbRunner(config)
    result = runner.run_sync()
    sys.exit(0 if result.success else 1)
```

В `main()` добавить ветку:

```python
elif args.command == "ldb":
    run_ldb(args)
```

- [ ] **5.3 Меню `run_ldb_menu()` в `src/menu.py`**

**Добавить `import dataclasses`** в верх `src/menu.py` (если ещё нет).

Зеркало `run_debugger_menu()` с дополнительным пунктом «Mode» (2 vs 3) и полями для file/entry. Использует `_questionary_select_provider_model()` (он уже есть).

> **Style note:** Существующий `src/menu.py` использует `Config(**{**config.__dict__, ...})` (16+ мест). LDB-меню использует `dataclasses.replace()` — это более безопасный паттерн (выживет при добавлении `__post_init__`). НЕ переписывать существующий код — просто использовать новый стиль для новых функций.

```python
LDB_MODE_PRESETS = {
    "Mode 2 — Find bugs + Write tests (no fix)": 2,
    "Mode 3 — Find + Test + Fix (architectural-first)": 3,
}

def run_ldb_menu(config: "Config") -> "Config":
    """Interactive LDB settings menu (issue #5: also requires _fallback_ldb_menu below)."""
    if not QUESTIONARY_AVAILABLE:
        return _fallback_ldb_menu(config)

    while True:
        scope_label = "ALL public functions" if config.ldb_scope_all else f"{config.ldb_target_file}::{config.ldb_target_entry}"
        choices = [
            questionary.Choice("▶   Run LDB", value="start"),
            questionary.Separator("─── Target ─────────────────────"),
            questionary.Choice(f"    Scope:  {scope_label}", value="scope"),
            questionary.Choice(f"    Test:   {config.ldb_test_input or '(LLM-synthesized)'}", value="test"),
            questionary.Separator("─── Pipeline ───────────────────"),
            questionary.Choice(f"    Mode:   {config.ldb_mode}", value="mode"),
            questionary.Separator("─── Agents (each its own provider/model) ─────"),
            questionary.Choice(f"    Input:  {_provider_model_label(config.ldb_input_provider, config.ldb_input_model)}", value="input"),
            questionary.Choice(f"    Player: {_provider_model_label(config.ldb_player_provider, config.ldb_player_model)}", value="player"),
            questionary.Choice(f"    Tester: {_provider_model_label(config.ldb_tester_provider, config.ldb_tester_model)}", value="tester"),
            questionary.Choice(f"    Fixer:  {_provider_model_label(config.ldb_fixer_provider, config.ldb_fixer_model)}", value="fixer"),
            questionary.Separator("───────────────────────────────"),
            questionary.Choice("←   Back", value="back"),
        ]
        ans = questionary.select("🧪 LDB — settings", choices=choices).ask()
        if ans in (None, "back"):
            return None
        if ans == "start":
            return config
        if ans == "scope":
            scope_choice = questionary.select(
                "Scope:",
                choices=["Single function (--file --entry)", "All public functions (--all)"],
            ).ask()
            if scope_choice and scope_choice.startswith("All"):
                config = dataclasses.replace(config, ldb_scope_all=True)
            elif scope_choice:
                f = questionary.text("Path to .py file:", default=config.ldb_target_file).ask() or ""
                e = questionary.text("Function name:", default=config.ldb_target_entry).ask() or ""
                config = dataclasses.replace(config,
                                   ldb_scope_all=False,
                                   ldb_target_file=f, ldb_target_entry=e)
        elif ans == "test":
            v = questionary.text("Explicit assert (empty = LLM synthesize):", default=config.ldb_test_input).ask()
            config = dataclasses.replace(config, ldb_test_input=v or "")
        elif ans == "mode":
            v = questionary.select("Pipeline mode:", choices=list(LDB_MODE_PRESETS.keys())).ask()
            if v: config = dataclasses.replace(config, ldb_mode=LDB_MODE_PRESETS[v])
        elif ans in ("input", "player", "tester", "fixer"):
            field = f"ldb_{ans}_provider"
            mfield = f"ldb_{ans}_model"
            config = _questionary_select_provider_model(config, field, mfield, ans.capitalize())


def _fallback_ldb_menu(config: "Config") -> "Config":
    """Plain-text menu when questionary is unavailable (issue #5).

    Mirrors `_fallback_debugger_menu` pattern in src/menu.py.
    """
    print("\n🧪 LDB — settings (questionary not installed, plain text mode)")
    while True:
        scope = "ALL" if config.ldb_scope_all else f"{config.ldb_target_file}::{config.ldb_target_entry}"
        print(f"  [1] Scope:    {scope}")
        print(f"  [2] Test:     {config.ldb_test_input or '(LLM-synthesized)'}")
        print(f"  [3] Mode:     {config.ldb_mode}")
        print(f"  [4] Input  agent: {config.ldb_input_provider}/{config.ldb_input_model or 'default'}")
        print(f"  [5] Player agent: {config.ldb_player_provider}/{config.ldb_player_model or 'default'}")
        print(f"  [6] Tester agent: {config.ldb_tester_provider}/{config.ldb_tester_model or 'default'}")
        print(f"  [7] Fixer  agent: {config.ldb_fixer_provider}/{config.ldb_fixer_model or 'default'}")
        print(f"  [Enter] Run    [q] Quit")
        ans = input("  › ").strip().lower()
        if ans == "":
            return config
        if ans == "q":
            return None
        if ans == "1":
            mode = input("  Scope (s=single / a=all): ").strip().lower()
            if mode == "a":
                config = dataclasses.replace(config, ldb_scope_all=True)
            elif mode == "s":
                f = input("  File path: ").strip()
                e = input("  Entry function: ").strip()
                config = dataclasses.replace(
                    config, ldb_scope_all=False,
                    ldb_target_file=f, ldb_target_entry=e,
                )
        elif ans == "2":
            v = input("  Explicit assert (empty = synthesize): ").strip()
            config = dataclasses.replace(config, ldb_test_input=v)
        elif ans == "3":
            v = input("  Mode (2 or 3): ").strip()
            if v in ("2", "3"):
                config = dataclasses.replace(config, ldb_mode=int(v))
        elif ans in ("4", "5", "6", "7"):
            role = {"4": "input", "5": "player", "6": "tester", "7": "fixer"}[ans]
            config = _fallback_select_provider_model(
                config, f"ldb_{role}_provider", f"ldb_{role}_model", role.capitalize()
            )
```

- [ ] **5.4 Smoke-тест меню**

```bash
tero ldb --working-dir .
```

Проверить все пункты меню работают, переключение Mode 2/3 видно.

- [ ] **5.5 Коммит**

```bash
git add src/config.py src/cli_entry.py src/menu.py src/constants.py
git commit -m "ldb: cli, menu, config wiring"
```

---

## Phase 5b: Whole-project scope (`--all`)

- [ ] **5b.1 Failing test для `iter_targets()`**

`tests/test_ldb_scope.py`:

```python
from src.ldb.scope import iter_targets

def test_iter_targets_finds_public_functions(tmp_path):
    (tmp_path / "a.py").write_text("def foo(x): return x\ndef _hidden(): return 1\n")
    (tmp_path / "b.py").write_text("def bar(): pass\n")
    (tmp_path / "_skip.py").write_text("def skipped(): pass\n")
    targets = list(iter_targets(str(tmp_path)))
    names = sorted((Path(t.file).name, t.entry) for t in targets)
    assert (("a.py", "foo")) in names
    assert (("b.py", "bar")) in names
    assert not any("_hidden" in t.entry or "skipped" in t.entry for t in targets)
```

- [ ] **5b.2 Имплементировать `src/ldb/scope.py`**

```python
"""Walk all public functions in working_dir for `--all` mode."""

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache", "tests"}
# "tests" is intentional: LDB targets source code, not test files.
# Tests written by the Tester agent (test_ldb_*.py) live in tests/ and should not be re-analyzed.


@dataclass(frozen=True)
class LdbTarget:
    file: str
    entry: str


def iter_targets(working_dir: str) -> Iterator[LdbTarget]:
    root = Path(working_dir)
    for py in root.rglob("*.py"):
        # Bug fix (issue #4): operator precedence — split skip-dir from skip-name,
        # and only skip filenames (not all parts) that start with "_".
        # Without this, src/ldb/__init__.py would be skipped because "ldb" path
        # contains "_" components in nested projects, and ANY file in a path
        # with a "_"-prefixed component dropped out.
        parts = py.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in parts):
            continue
        if py.name.startswith("_"):  # only the FILE name, e.g. _private.py
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                yield LdbTarget(file=str(py), entry=node.name)
```

- [ ] **5b.3 Запустить тест 5b.1** → проходит.

- [ ] **5b.4 Коммит**

```bash
git add src/ldb/scope.py tests/test_ldb_scope.py
git commit -m "ldb: --all scope walker for whole-project mode"
```

---

## Phase 6: LdbRunner — orchestration

- [ ] **6.1 Failing test для LdbRunner.run() (Mode 2)**

`tests/test_ldb_runner.py` (расширить):

```python
import pytest
from unittest.mock import patch, AsyncMock
from src.config import Config
from src.ldb.runner import LdbRunner

@pytest.mark.asyncio
async def test_runner_mode_2_writes_bugs_md(tmp_path):
    src = tmp_path / "buggy.py"
    src.write_text("def add(a, b):\n    return a - b\n")
    config = Config(
        working_dir=str(tmp_path),
        ldb_target_file=str(src),
        ldb_target_entry="add",
        ldb_mode=2,
        ldb_player_provider="claude",
    )
    # Замокать провайдеров — не дёргать реальный LLM
    with patch("src.ldb.runner.create_provider") as cp:
        fake = AsyncMock()
        fake.run.return_value = AsyncMock(__aiter__=...)  # заглушка
        cp.return_value = fake
        runner = LdbRunner(config)
        result = await runner.run()
    assert (tmp_path / "bugs.md").exists()
    # В Mode 2 не должен быть git commit
```

- [ ] **6.2 Имплементировать `LdbRunner` в `src/ldb/runner.py`**

```python
"""LdbRunner — orchestrates Input → Player → Tester → (Fixer if mode=3)."""

import asyncio
import dataclasses
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.config import Config
# Bug fix (issue #1): correct import name
from src.ldb.blocks import decompose_function
from src.ldb.tracer import trace_function
from src.ldb.inputs import synthesize_inputs_llm
from src.ldb.prompts import (
    PLAYER_PROMPT_LDB, TESTER_PROMPT_LDB, FIXER_PROMPT_LDB_ARCH,
)
from src.providers import create_provider


@dataclass
class LdbBug:
    block_id: int
    block_lines: list[str]
    explanation: str

@dataclass
class LdbResult:
    success: bool
    bugs_found: int
    tests_written: int
    bugs_fixed: int  # 0 in mode 2

class LdbRunner:
    def __init__(self, config: Config):
        self.config = config
        self.working_dir = config.working_dir
        # 4 separate providers — each phase its own LLM call w/ user-chosen operator.
        # NOTE: create_provider() expects config keys matching the provider's Config class.
        # "default_model" works for opencode/gemini; codex expects "model"; zai expects "claude_home".
        # An empty default_model is safe — it just means "use the provider's built-in default".
        self._input_agent = create_provider(config.ldb_input_provider, {"default_model": config.ldb_input_model})
        self._player = create_provider(config.ldb_player_provider, {"default_model": config.ldb_player_model})
        self._tester = create_provider(config.ldb_tester_provider, {"default_model": config.ldb_tester_model})
        if config.ldb_mode >= 3:
            self._fixer = create_provider(config.ldb_fixer_provider, {"default_model": config.ldb_fixer_model})

    def run_sync(self) -> LdbResult:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Already inside an event loop (e.g. pytest-asyncio) — use nest_asyncio
            # or create a new thread. Simplest: run in a separate thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.run()).result()
        return asyncio.run(self.run())

    async def run(self) -> LdbResult:
        # Bug fix (issue #3): unify target type — always LdbTarget objects
        from src.ldb.scope import iter_targets, LdbTarget
        if self.config.ldb_scope_all:
            targets = list(iter_targets(self.working_dir))
        else:
            targets = [LdbTarget(
                file=self.config.ldb_target_file,
                entry=self.config.ldb_target_entry,
            )]

        agg = LdbResult(True, 0, 0, 0)
        touched_files: list[str] = []
        for tgt in targets:
            sub, files_changed = await self._run_target(tgt.file, tgt.entry)
            agg = LdbResult(
                success=agg.success and sub.success,
                bugs_found=agg.bugs_found + sub.bugs_found,
                tests_written=agg.tests_written + sub.tests_written,
                bugs_fixed=agg.bugs_fixed + sub.bugs_fixed,
            )
            touched_files.extend(files_changed)

        # Auto-commit in Mode 3, but only stage explicit files (issue #12 — safety)
        if self.config.ldb_mode == 3 and agg.bugs_fixed > 0:
            self._git_commit(agg.bugs_fixed, touched_files)
        return agg

    async def _run_target(self, file: str, entry: str) -> tuple[LdbResult, list[str]]:
        """Returns (result, files_changed). Files list used for selective git add."""
        source = Path(file).read_text()
        files_changed: list[str] = []

        # 1. Decompose
        blocks = decompose_function(source, entry)
        if blocks is None:
            return LdbResult(False, 0, 0, 0), []

        # 2. Inputs — explicit user assert OR LLM-synthesized
        if self.config.ldb_test_input:
            tests = [self.config.ldb_test_input]
        else:
            tests = await synthesize_inputs_llm(
                provider=self._input_agent,
                source=source, entry=entry,
                model=self.config.ldb_input_model,
                n=2, working_dir=self.working_dir,
            )

        # 3. Trace each input, ask Player to mark wrong block
        bugs: list[LdbBug] = []
        for t in tests:
            trace = trace_function(source=source, test=t, entry=entry, timeout=self.config.ldb_timeout_s)
            if trace.kind != "ok":
                continue
            bug = await self._run_player(source, trace.blocks)
            if bug:
                bugs.append(bug)

        # 4. Append to bugs.md (always working_dir root — feedback memory)
        if bugs:
            self._append_bugs_md(file, entry, bugs)

        if not bugs:
            return LdbResult(True, 0, 0, 0), []

        # 5. Tester writes pytest tests (Mode 2 + 3)
        test_paths = await self._run_tester(source, file, entry, bugs)
        files_changed.extend(test_paths)

        if self.config.ldb_mode == 2:
            return LdbResult(True, len(bugs), len(test_paths), 0), files_changed

        # 6. Fixer (Mode 3 only) — modifies the source file
        fixed = await self._run_fixer(source, file, entry, bugs)
        if fixed > 0:
            files_changed.append(file)
        return LdbResult(True, len(bugs), len(test_paths), fixed), files_changed

    # ── helpers fleshed out (issue #10): no more ... stubs ──

    async def _collect_text(self, provider, *, prompt: str, system_prompt: str, model: str = "") -> str:
        """Collect text content from provider stream.

        NOTE: Only TextBlock is collected — ToolUseBlock is intentionally skipped.
        This is correct for Input/Player/Tester (read-only analysis), and acceptable
        for Fixer because subprocess providers (codex) apply side-effects via their
        own tool loop; we only need the final text summary here.
        """
        chunks: list[str] = []
        async for msg in provider.run(
            prompt=prompt, system_prompt=system_prompt,
            working_dir=self.working_dir, max_turns=1, model=model,
        ):
            for block in getattr(msg, "content", []) or []:
                if hasattr(block, "text"):
                    chunks.append(block.text)
        return "\n".join(chunks)

    async def _run_player(self, source: str, trace_blocks) -> LdbBug | None:
        rendered = "\n".join(
            f"[BLOCK-{b.block_id}]\n" + "\n".join(b.rendered)
            for b in trace_blocks[:10]  # cap like LDB paper does
        )
        user_prompt = (
            f"Source:\n```python\n{source}\n```\n\n"
            f"Block traces with values BEFORE/AFTER each block:\n{rendered}\n\n"
            "For each block, output one JSON object per line: "
            '{"block":"BLOCK-N","correct":bool,"explanation":str}.'
        )
        raw = await self._collect_text(
            self._player, prompt=user_prompt,
            system_prompt=PLAYER_PROMPT_LDB, model=self.config.ldb_player_model,
        )
        return parse_player_response(raw, trace_blocks)

    async def _run_tester(self, source: str, file: str, entry: str, bugs: list[LdbBug]) -> list[str]:
        bug_payload = "\n".join(
            f"BUG-{i}: {b.explanation}\nBlock:\n" + "\n".join(b.block_lines)
            for i, b in enumerate(bugs, 1)
        )
        user_prompt = (
            f"Source file: {file}\nEntry: {entry}\n"
            f"Source:\n```python\n{source}\n```\n\nConfirmed bugs:\n{bug_payload}"
        )
        raw = await self._collect_text(
            self._tester, prompt=user_prompt,
            system_prompt=TESTER_PROMPT_LDB, model=self.config.ldb_tester_model,
        )
        # Parse JSON list — fallback to []
        try:
            parsed = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
        except Exception:
            return []
        return [item["test_file"] for item in parsed
                if item.get("status") == "confirmed" and item.get("test_file")]

    async def _run_fixer(self, source: str, file: str, entry: str, bugs: list[LdbBug]) -> int:
        bug_payload = "\n".join(
            f"BUG-{i} in {file}::{entry}: {b.explanation}\nBlock:\n" + "\n".join(b.block_lines)
            for i, b in enumerate(bugs, 1)
        )
        user_prompt = (
            f"Source file: {file}\n"
            f"Source:\n```python\n{source}\n```\n\nBugs to fix:\n{bug_payload}"
        )
        # Fixer is expected to modify files via its own tools (subprocess provider),
        # so we don't capture its text — we trust the side-effects + return count.
        await self._collect_text(
            self._fixer, prompt=user_prompt,
            system_prompt=FIXER_PROMPT_LDB_ARCH, model=self.config.ldb_fixer_model,
        )
        # Verify the fix: run full pytest once, return count of bugs if green.
        return len(bugs) if self._verify_fix_passes() else 0

    def _verify_fix_passes(self) -> bool:
        # Run pytest on the test files; treat exit 0 as success.
        # Called ONCE after all bugs are fixed, not per-bug.
        try:
            res = subprocess.run(
                ["pytest", "tests/", "-x", "-q", "--tb=no"],
                cwd=self.working_dir, capture_output=True, timeout=60,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _git_commit(self, count: int, files: list[str]):
        # Issue #12 fix: stage ONLY explicit files (mirror src/debugger.py:524 _git_commit pattern).
        # Avoids accidentally committing .env, build artifacts, etc.
        if not files:
            return
        unique_files = sorted(set(files))
        try:
            subprocess.run(
                ["git", "add", "--"] + unique_files,
                cwd=self.working_dir, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m",
                 f"ldb fix: {count} bug(s) via block-level runtime debugger"],
                cwd=self.working_dir, check=True, capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            import warnings
            warnings.warn(
                f"LDB auto-commit failed (exit {e.returncode}): {e.stderr.decode()[:200]}",
                stacklevel=2,
            )


def parse_player_response(raw: str, trace_blocks) -> "LdbBug | None":
    """Returns the FIRST block with correct=false (per PLAYER_PROMPT_LDB rule).
    Subsequent incorrects are downstream of the first and are skipped.
    """
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        correct = item.get("correct")
        if isinstance(correct, str):
            correct = correct.lower() == "true"
        if correct is False:
            block_label = item.get("block", "")
            m = re.search(r"BLOCK-(\d+)", block_label)
            if not m:
                continue
            block_id = int(m.group(1))
            block_lines = (
                trace_blocks[block_id].rendered
                if 0 <= block_id < len(trace_blocks) else []
            )
            return LdbBug(
                block_id=block_id,
                block_lines=block_lines,
                explanation=item.get("explanation", ""),
            )
    return None

```

(`_append_bugs_md` остаётся методом класса `LdbRunner` — добавляется в тело класса выше:)

```python
    def _append_bugs_md(self, file: str, entry: str, bugs):
        # ВСЕГДА в корне working_dir — закреплено в feedback memory.
        # Format note: `tero debug` writes `## <file> — Bug <n>` headings.
        # LDB writes `## <file>::<entry> — Bug <n> (BLOCK-<id>)` — compatible,
        # both use `## ` level + code block + explanation.
        path = Path(self.working_dir) / "bugs.md"
        existing = path.read_text() if path.exists() else "# Bugs\n\n"
        body = [existing.rstrip(), "\n"]
        for i, b in enumerate(bugs, 1):
            body.append(f"## {file}::{entry} — Bug {i} (BLOCK-{b.block_id})\n")
            body.append("```python\n" + "\n".join(b.block_lines) + "\n```")
            body.append(f"**Explanation:** {b.explanation}\n")
        path.write_text("\n".join(body))
```

- [ ] **6.3 Запустить тесты** → проходят (с моками).

- [ ] **6.4 End-to-end smoke**

Создать `/tmp/buggy_demo.py`:

```python
def add(a: int, b: int) -> int:
    """Sum two numbers."""
    return a - b  # bug
```

Запустить:

```bash
tero ldb --no-menu --file /tmp/buggy_demo.py --entry add --mode 2 --player-provider claude
```

Ожидание: появляется `bugs.md` в корне cwd с описанием wrong block, в `tests/` появляется `test_ldb_bug_1.py` с failing-тестом, **код НЕ изменён** (Mode 2).

Затем: `--mode 3` → код пофикшен, тест проходит, есть git-коммит «ldb fix: …».

- [ ] **6.5 Коммит**

```bash
git add src/ldb/runner.py tests/test_ldb_runner.py
git commit -m "ldb: runner orchestrating player/tester/fixer pipeline"
```

---

## Phase 7: Финальная проверка

- [ ] **7.1 Запустить полный тест-сьют**

```bash
pytest tests/ -x -q --tb=short
```

Все проходят. Если что-то сломалось — фиксим.

- [ ] **7.2 Smoke оба режима**

Mode 2: `tero ldb --no-menu --file <file> --entry <fn> --mode 2` → bugs.md + test, без правок кода.
Mode 3: `tero ldb --no-menu --file <file> --entry <fn> --mode 3` → bugs.md + test + код пофикшен + git коммит.

- [ ] **7.3 Меню**

`tero ldb` (без аргументов) → интерактивное меню. Все пункты редактируются. «Run LDB» → запускает runner.

- [ ] **7.4 Gemini как player**

```bash
tero ldb --no-menu --file <file> --entry <fn> --mode 2 --player-provider gemini --player-model gemini-2.5-pro
```

Должен пройти, использовав Gemini CLI.

- [ ] **7.5 Финальный коммит и сводка в README**

```bash
git add README.md  # если обновили
git commit -m "ldb: smoke verified, document tero ldb usage"
```

---

## Self-review checklist (выполнить перед стартом)

- [ ] **Спека LDB соответствует их коду** — алгоритм в "Алгоритм LDB" совпадает с `tracer.py:get_code_traces_block` + `py_generate.py:check_block_correctness`.
- [ ] **Все file paths абсолютные** — да, везде указаны `src/...` и `tests/...`.
- [ ] **Каждая фаза заканчивается коммитом** — да.
- [ ] **Mode 2 vs Mode 3 ветка явно прописана** — да, в `LdbRunner.run()` по `config.ldb_mode`.
- [ ] **bugs.md в корне working_dir** — закреплено в `_append_bugs_md` (`Path(self.working_dir) / "bugs.md"`).
- [ ] **Gemini integration не блокирует LDB фазу** — Phase 1 независима, можно делать параллельно с Phase 2-4.

---

## Подтверждённые решения (2026-05-02)

- [ ] **Input synthesis = LLM-агент** (Input-Synthesizer), у каждой фазы свой провайдер/модель → 4 агента в меню/CLI/конфиге.
- [ ] **Gemini default**: `gemini-2.5-pro`. Команда: `gemini -p <prompt> -o stream-json --yolo` (подтверждено smoke-тестом).
- [ ] **Mode 3 auto-commit**: селективный staging — `git add -- <files>` (только изменённые исходники + новые тесты), НЕ `-A`. Сообщение: `"ldb fix: N bug(s) via block-level runtime debugger"`.
- [ ] **Scope**: `--file --entry` обязательны (прозрачность), плюс опциональный `--all` для whole-project обхода всех публичных функций (Phase 5b).

## Phase Dependencies (issue #15)

Phase 1 (Gemini provider) **независима** от Phase 2-7. Если Gemini CLI ещё не установлен / не нужен — можно пропустить и сделать LDB на existing провайдерах (zai, claude, codex, opencode, kilo). Phase 1 — отдельная feature, **не блокирует** LDB.

Рекомендуемый порядок: **2 → 3 → 4 → 5/5b → 6 → 7 (LDB)** и **1 → 7-smoke (Gemini)** параллельно. Можно стартовать с любой ветки.

## Acknowledgement of review fixes (2026-05-02)

Этот план прошёл code-review с 15 пунктами. Применены фиксы:
- [ ] **#1** import `synthesize_inputs_llm` (Phase 6.2)
- [ ] **#2** `ldb_input_provider/model` теперь в `run_ldb()` (Phase 5.2)
- [ ] **#3** unified `LdbTarget` для обоих режимов (Phase 6.2)
- [ ] **#4** `_SKIP_DIRS` precedence + filename-only `_*` skip (Phase 5b.2)
- [ ] **#5** добавлен `_fallback_ldb_menu()` (Phase 5.3)
- [ ] **#6** реальный формат событий Gemini документирован (Phase 1.2)
- [ ] **#7** Phase 4.1 теперь дополняет, а не создаёт `prompts.py`
- [ ] **#8** тест Player'а явно проверяет «only first incorrect»
- [ ] **#9** валидация `--file/--entry` vs `--all` в `run_ldb()`
- [ ] **#10** `_run_player/_run_tester/_run_fixer` раскрыты (Phase 6.2)
- [ ] **#11** «зеркалит debug» уточнено: используется селективный путь, не `-A` fallback
- [ ] **#12** `git add -- <files>` вместо `-A` (Phase 6.2)
- [ ] **#13** контракт `trace_function(source: str)` явно задокументирован + `monkeypatch.chdir(tmp_path)` в тесте (Phase 2.5)
- [ ] **#14** везде `dataclasses.replace(config, ...)` вместо `Config(**{**config.__dict__, ...})`
- [ ] **#15** Phase 1 помечена как параллельная, не блокирующая

## Second-review fixes (2026-05-02)

План прошёл второй аудит с 17 пунктами (16 применены, #14 --yolo оставлен как есть):
- [ ] **#R1** Невалидный Python синтаксис `dataclasses.replace(config, "key": val})` → `dataclasses.replace(config, key=val)` (5 мест)
- [ ] **#R2** `from src.providers import AgentProvider` → `from src.providers.base import AgentProvider` (Protocol не экспортируется из `__init__`)
- [ ] **#R3** Добавлен NOTE о config keys для `create_provider()` — `default_model` не universal key
- [ ] **#R4** `_run_fixer` вызывает `_verify_fix_passes()` один раз (было N раз с одинаковым результатом)
- [ ] **#R5** `trace_function()` вызывается через keyword args для ясности
- [ ] **#R6** `ldb_*_provider` добавлены в `_normalize_provider_name()` whitelist в `resolve_config()`
- [ ] **#R7** Добавлен `import dataclasses` + style note о двух паттернах Config update
- [ ] **#R8** `_questionary_select_provider_model` работает с любыми field names (информационная заметка)
- [ ] **#R9** Тест `parse_player_response` теперь передаёт `trace_blocks` (было 0 аргументов, функция ждёт 2)
- [ ] **#R10** `_append_bugs_md` получает guard `if bugs:` + заметка о совместимости формата с `tero debug`
- [ ] **#R11** `_SKIP_DIRS` → `tests/` skip задокументирован как intent
- [ ] **#R12** `_collect_text` получает docstring о намеренном пропуске ToolUseBlock
- [ ] **#R13** `import dataclasses` добавлен в Phase 5.3
- [ ] **#R14** ~~`--yolo` configurable~~ — оставлен как есть (user decision)
- [ ] **#R15** `run_sync()` обрабатывает already-running event loop через ThreadPoolExecutor
- [ ] **#R16** Guard от пустого `_append_bugs_md` — не пишем пустой заголовок в bugs.md
- [ ] **#R17** `_git_commit` теперь `warnings.warn()` вместо silent `pass`

Готов стартовать.
