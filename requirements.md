# Codex Provider — Полная замена: CLI-Proxy → Нативный Codex CLI

> **Статус**: План реализации  
> **Дата**: 2026-03-20  
> **Codex CLI**: `codex-cli 0.115.0` (`/Users/terobyte/.nvm/versions/node/v24.12.0/bin/codex`)  
> **Текущая модель**: `gpt-5.4` с `reasoning_effort = xhigh`

---

## Проблема

Текущий Codex-провайдер (`src/providers/codex.py`) работает через цепочку:

```
tero → codex.py → HTTP POST → ai-cli-proxy-api (Go) → Claude Code CLI → OpenAI API
```

Это приводит к:
- [x] **Мусорному выводу** — конвертация OpenAI SSE → Claude SDK-формат теряет контекст
- [x] **Лишней зависимости** — Go-сервер `ai-cli-proxy-api` нужно запускать и поддерживать
- [x] **Нет доступа к тулзам** — proxy не передаёт результаты `command_execution`, файловых операций
- [x] **Нет нативного code review** — `codex review` не используется вообще
- [x] **Сложный auto-start** — 150+ строк кода на управление PID/socket/health proxy

## Решение

Заменить HTTP-proxy подход на **прямой запуск `codex exec --json`** как subprocess (по аналогии с `claude_native.py`).

Codex CLI v0.115.0 поддерживает:
- [x] `codex exec --json` — JSONL стриминг с полным описанием тулзов
- [x] `codex exec review --json` — нативный code review с JSONL выводом
- [x] `codex review --json --uncommitted` — ревью незакоммиченных изменений
- [x] Sandbox (`read-only`, `workspace-write`, `danger-full-access`)
- [x] MCP серверы (figma уже подключен)
- [x] Feature flags (`shell_tool`, `multi_agent`, `fast_mode` и др.)
- [x] `--ephemeral` — без персистентных сессий (для batch)

---

## Формат JSONL (Codex CLI `--json`)

### Структура событий

```jsonl
{"type":"thread.started","thread_id":"..."}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"..."}}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"...","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"...","aggregated_output":"...","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"..."}}
{"type":"turn.completed","usage":{"input_tokens":33650,"cached_input_tokens":28544,"output_tokens":543}}
{"type":"error","message":"..."}
{"type":"turn.failed","error":{"message":"..."}}
```

### Типы item

| `item.type`          | Описание                              | Маппинг в tero                        |
|----------------------|---------------------------------------|---------------------------------------|
| `agent_message`      | Текстовый ответ агента                | `AdaptedMessage(role="assistant")`    |
| `command_execution`  | Выполнение shell-команды (tool use)   | `ToolUseBlock` + `ToolResultBlock`    |
| `file_edit`          | Редактирование файла (если появится)  | `ToolUseBlock` + `ToolResultBlock`    |
| `error`              | Ошибка модели или рантайма            | Лог + продолжение                     |

### Типы верхнеуровневых событий

| `type`              | Описание                                   |
|---------------------|---------------------------------------------|
| `thread.started`    | Начало сессии (содержит `thread_id`)       |
| `turn.started`      | Начало хода                                |
| `turn.completed`    | Ход завершён (содержит `usage` с токенами) |
| `turn.failed`       | Ход упал с ошибкой                         |
| `item.started`      | Начало элемента (tool в процессе)          |
| `item.completed`    | Элемент завершён                           |
| `error`             | Глобальная ошибка                          |

---

## План реализации

### Шаг 1. Новый `CodexConfig` (dataclass)

Заменить текущий конфиг (proxy-ориентированный) на CLI-ориентированный.

**Файл**: `src/providers/codex.py`

```python
@dataclass
class CodexConfig:
    """Configuration for native Codex CLI provider."""
    command: str = "codex"                        # путь к бинарнику
    default_model: str = ""                       # пустая строка = дефолт из ~/.codex/config.toml
    default_timeout: int = 900                    # таймаут subprocess (секунды)
    sandbox_mode: str = "workspace-write"         # read-only | workspace-write | danger-full-access
    approval_policy: str = "never"                # для exec: never | on-request | untrusted
    ephemeral: bool = True                        # --ephemeral (без сохранения сессий)
    full_auto: bool = False                       # --full-auto (sandbox + on-request)
    bypass_approvals: bool = True                 # --dangerously-bypass-approvals-and-sandbox
    config_overrides: dict[str, str] | None = None  # -c key=value пары
    extra_args: list[str] | None = None           # доп. аргументы CLI
```

**Что удаляется**:
- [x] `api_url`, `api_key` — больше нет HTTP proxy
- [x] `auto_start`, `proxy_repo_path`, `proxy_config_path` — нет Go-сервера
- [x] `proxy_log_path`, `proxy_pid_path`, `startup_timeout_s`, `auth_dir` — нет управления процессом proxy

### Шаг 2. Новый `CodexProvider.run()` — subprocess + JSONL парсинг

**Файл**: `src/providers/codex.py`

```python
class CodexProvider:
    """Codex provider via native CLI (codex exec --json)."""

    def __init__(self, config: CodexConfig | None = None):
        self.config = config or CodexConfig()
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
    ) -> AsyncIterator:
        """Run Codex agent via CLI and yield adapted messages.

        Launches `codex exec --json` as subprocess, reads JSONL from stdout,
        and yields AdaptedMessage objects compatible with tero's streaming UI.
        """
        cmd = self._build_command(model, working_dir)
        env = self._build_env(system_prompt)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            env=env,
        )

        # Отправляем промпт через stdin (аргумент "-" означает "читай из stdin")
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        async for line in proc.stdout:
            decoded = line.decode("utf-8").strip()
            if not decoded:
                continue
            try:
                event = json.loads(decoded)
            except json.JSONDecodeError:
                continue

            adapted = self._adapt_codex_event(event)
            if adapted is not None:
                yield adapted

        await proc.wait()

        if proc.returncode and proc.returncode != 0:
            stderr_data = await proc.stderr.read()
            stderr_text = stderr_data.decode("utf-8", errors="replace")
            # Не бросаем ошибку если были results — codex может вернуть ненулевой код
            # при tool failures, но работа была выполнена
            if stderr_text.strip():
                yield AdaptedMessage(
                    role="assistant",
                    content=[TextBlock(text=f"[codex stderr] {stderr_text.strip()}")],
                    type="text",
                )

    def _build_command(self, model: str = "", working_dir: str = "") -> list[str]:
        """Build codex exec CLI command."""
        resolved_model = model or self.config.default_model
        
        cmd = [
            self.config.command,
            "exec",
            "--json",      # JSONL вывод в stdout
        ]

        if resolved_model:
            cmd.extend(["-m", resolved_model])

        if working_dir:
            cmd.extend(["-C", working_dir])

        # Sandbox mode
        if self.config.bypass_approvals:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.config.full_auto:
            cmd.append("--full-auto")
        else:
            cmd.extend(["-s", self.config.sandbox_mode])

        if self.config.ephemeral:
            cmd.append("--ephemeral")

        # Config overrides (-c key=value)
        if self.config.config_overrides:
            for key, value in self.config.config_overrides.items():
                cmd.extend(["-c", f'{key}="{value}"'])

        # Extra args
        if self.config.extra_args:
            cmd.extend(self.config.extra_args)

        # Промпт из stdin
        cmd.append("-")

        return cmd

    def _build_env(self, system_prompt: str = "") -> dict:
        """Build environment for subprocess.
        
        Codex CLI использует CODEX_SYSTEM_PROMPT env var для system prompt,
        что позволяет передать инструкции без аргумента CLI.
        """
        env = os.environ.copy()
        if system_prompt:
            # Codex использует instructions из CODEX_INSTRUCTIONS env var
            # или --config instructions=... ; проверим оба варианта
            env["CODEX_INSTRUCTIONS"] = system_prompt
        return env
```

### Шаг 3. Адаптер JSONL → AdaptedMessage

**Файл**: `src/providers/codex.py` (метод класса)

```python
    def _adapt_codex_event(self, event: dict) -> AdaptedMessage | None:
        """Convert Codex JSONL event to AdaptedMessage.

        Handles all Codex event types:
        - [x] thread.started → ignored (metadata only)
        - [x] turn.started → empty assistant marker
        - [x] item.completed (agent_message) → TextBlock
        - [x] item.started (command_execution) → ToolUseBlock (in_progress)
        - [x] item.completed (command_execution) → ToolResultBlock
        - [x] turn.completed → result with usage stats
        - [x] error / turn.failed → error text
        """
        etype = event.get("type", "")

        # --- Thread / Turn lifecycle ---
        if etype == "thread.started":
            return None  # metadata only, игнорируем

        if etype == "turn.started":
            return AdaptedMessage(
                role="assistant",
                content=[],
                type="assistant",
            )

        # --- Item events ---
        if etype in ("item.completed", "item.started"):
            item = event.get("item", {})
            return self._adapt_item(item, etype)

        # --- Turn completion (contains usage) ---
        if etype == "turn.completed":
            usage = event.get("usage", {})
            self._last_input_tokens = usage.get("input_tokens", 0)
            self._last_output_tokens = usage.get("output_tokens", 0)
            return AdaptedMessage(
                role="assistant",
                content=[],
                stop_reason="end_turn",
                type="result",
            )

        # --- Errors ---
        if etype == "error":
            msg = event.get("message", "Unknown error")
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"⚠ Codex error: {msg}")],
                type="text",
            )

        if etype == "turn.failed":
            error = event.get("error", {})
            msg = error.get("message", "Turn failed") if isinstance(error, dict) else str(error)
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"⚠ Codex turn failed: {msg}")],
                type="text",
            )

        return None

    def _adapt_item(self, item: dict, event_type: str) -> AdaptedMessage | None:
        """Convert a single Codex item to AdaptedMessage."""
        item_type = item.get("type", "")

        # --- Agent message (text response) ---
        if item_type == "agent_message":
            text = item.get("text", "")
            if not text:
                return None
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=text)],
                type="text",
            )

        # --- Command execution (tool use) ---
        if item_type == "command_execution":
            command = item.get("command", "")
            status = item.get("status", "")
            exit_code = item.get("exit_code")
            output = item.get("aggregated_output", "")

            if event_type == "item.started":
                # Tool use начат
                return AdaptedMessage(
                    role="assistant",
                    content=[ToolUseBlock(
                        name="shell",
                        input={"command": command},
                        id=item.get("id", ""),
                    )],
                    stop_reason="tool_use",
                    type="tool_use",
                )

            if event_type == "item.completed":
                # Tool result завершён
                is_error = exit_code is not None and exit_code != 0
                result_text = output or ""
                if exit_code is not None:
                    result_text = f"[exit code: {exit_code}]\n{result_text}"
                
                return AdaptedMessage(
                    role="tool",
                    content=[ToolResultBlock(
                        tool_use_id=item.get("id", ""),
                        content=result_text,
                        is_error=is_error,
                    )],
                    type="tool_result",
                )

        # --- File edit (если Codex начнёт выдавать такие элементы) ---
        if item_type in ("file_edit", "file_write", "file_read"):
            path = item.get("path", item.get("file", ""))
            content = item.get("content", item.get("text", ""))
            
            if event_type == "item.started":
                return AdaptedMessage(
                    role="assistant",
                    content=[ToolUseBlock(
                        name=item_type,
                        input={"path": path},
                        id=item.get("id", ""),
                    )],
                    stop_reason="tool_use",
                    type="tool_use",
                )
            
            if event_type == "item.completed":
                return AdaptedMessage(
                    role="tool",
                    content=[ToolResultBlock(
                        tool_use_id=item.get("id", ""),
                        content=content or f"[{item_type}: {path}]",
                        is_error=False,
                    )],
                    type="tool_result",
                )

        # --- Error item ---
        if item_type == "error":
            msg = item.get("message", "Unknown item error")
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"⚠ {msg}")],
                type="text",
            )

        return None
```

### Шаг 4. `check_ready()` и `display_name`

```python
    def check_ready(self) -> tuple[bool, str]:
        """Check if Codex CLI is installed and authenticated."""
        if not shutil.which(self.config.command):
            return False, f"'{self.config.command}' not found in PATH. Install: npm i -g @openai/codex"

        # Проверяем что codex может запуститься (не проверяем auth — exec сделает это сам)
        try:
            result = subprocess.run(
                [self.config.command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False, f"codex --version failed: {result.stderr}"
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, f"Codex CLI check failed: {e}"

        return True, ""

    @property
    def display_name(self) -> str:
        """Human-readable name for UI."""
        model = self.config.default_model or "default"
        return f"Codex ({model})"
```

### Шаг 5. Нативный Code Review через `codex exec review`

**Новый метод** в `CodexProvider`:

```python
    async def run_review(
        self,
        working_dir: str,
        review_prompt: str = "",
        model: str = "",
        uncommitted: bool = True,
    ) -> AsyncIterator:
        """Run codex exec review --json for native code review.
        
        Использует нативный код-ревью Codex CLI вместо ручного промпта.
        Это позволяет Codex самому анализировать git diff и давать структурированную обратную связь.
        
        Маппинг на tero фичи:
        - [x] code_review=True в config → используем codex exec review
        - [x] review_provider=codex → используем этот метод вместо обычного run()
        """
        resolved_model = model or self.config.default_model
        
        cmd = [
            self.config.command,
            "exec", "review",
            "--json",
        ]

        if resolved_model:
            cmd.extend(["-m", resolved_model])

        if uncommitted:
            cmd.append("--uncommitted")

        if self.config.ephemeral:
            cmd.append("--ephemeral")

        if self.config.bypass_approvals:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.config.full_auto:
            cmd.append("--full-auto")

        cmd.append("--skip-git-repo-check")

        # Кастомный промпт для ревью (если передан)
        if review_prompt:
            cmd.append("-")  # читаем из stdin

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if review_prompt else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )

        if review_prompt:
            proc.stdin.write(review_prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

        async for line in proc.stdout:
            decoded = line.decode("utf-8").strip()
            if not decoded:
                continue
            try:
                event = json.loads(decoded)
            except json.JSONDecodeError:
                continue

            adapted = self._adapt_codex_event(event)
            if adapted is not None:
                yield adapted

        await proc.wait()
```

### Шаг 6. Интеграция с `coach_player.py` — Code Review через нативный Codex

**Файл**: `src/coach_player.py` — метод code review phase

Текущий код ревью (строки ~674-720) вызывает `self._run_turn(role="reviewer", ...)`.
Когда `review_provider == "codex"`, нужно использовать `run_review()` вместо `run()`:

```python
# В _run_turn или в отдельном методе _run_review_turn:
if role == "reviewer" and isinstance(provider, CodexProvider):
    # Используем нативный codex review вместо обычного run
    run_method = provider.run_review(
        working_dir=self.config.working_dir,
        review_prompt=prompt,
        model=model,
        uncommitted=True,
    )
else:
    run_method = provider.run(**run_kwargs)

async for msg in run_method:
    ...
```

### Шаг 7. Обновление `__init__.py` — фабрика `create_provider`

**Файл**: `src/providers/__init__.py`

```python
    if provider_type == "codex":
        codex_cfg = CodexConfig(
            command=provider_config.get("command", "codex"),
            default_model=provider_config.get("default_model", provider_config.get("model", "")),
            default_timeout=provider_config.get("default_timeout", 900),
            sandbox_mode=provider_config.get("sandbox_mode", "workspace-write"),
            approval_policy=provider_config.get("approval_policy", "never"),
            ephemeral=provider_config.get("ephemeral", True),
            full_auto=provider_config.get("full_auto", False),
            bypass_approvals=provider_config.get("bypass_approvals", True),
            config_overrides=provider_config.get("config_overrides"),
            extra_args=provider_config.get("extra_args"),
        )
        return CodexProvider(codex_cfg)
```

### Шаг 8. Обновление `registry.py`

Аналогично шагу 7, обновить `_create_provider()` в `ProviderRegistry`.

### Шаг 9. Обновление `menu.py` и `runtime_controls.py`

**Модели Codex** — больше не нужны `gpt-5.4-medium/high/ultra-high` алиасы,
потому что Codex CLI сам резолвит модели через `~/.codex/config.toml`.

Обновить `CODEX_MODEL_PRESETS`:

```python
CODEX_MODEL_PRESETS = {
    "GPT-5.4 (default)":         "",                    # дефолт из config.toml
    "GPT-5.4 xhigh reasoning":  "gpt-5.4",             # с xhigh из config  
    "o3":                        "o3",
    "o4-mini":                   "o4-mini",
    "Ввести вручную...":         "__custom__",
}
```

Обновить `MODEL_PRESETS` в `runtime_controls.py`:

```python
MODEL_PRESETS = [
    ("GLM-1",       "ccg",    "blackboxai/z-ai/glm-5"),
    ("GLM-2",       "ccg2",   "blackboxai/z-ai/glm-5"),
    ("Sonnet",      "claude", "claude-sonnet-4-6"),
    ("Opus",        "claude", "claude-opus-4-6"),
    ("GPT-5.4",     "codex",  ""),             # дефолт из ~/.codex/config.toml
    ("o3",          "codex",  "o3"),
    ("o4-mini",     "codex",  "o4-mini"),
]
```

### Шаг 10. Обновление `config.py`

Обновить `_MODEL_CONTEXT_WINDOWS`:

```python
_MODEL_CONTEXT_WINDOWS = [
    ...
    ("gpt-5.4",           128_000),
    ("gpt-5",             128_000),
    ("o3",                128_000),
    ("o4-mini",           128_000),
    ("codex",             128_000),   # fallback
    ...
]
```

Обновить `short_model_name()`:

```python
def short_model_name(model: str) -> str:
    m = model.lower()
    if not m or m == "default":
        return "CODEX"     # дефолт Codex модель
    if "o3" == m:
        return "o3"
    if "o4-mini" == m:
        return "o4-mini"
    if "gpt-5.4" in m:
        return "GPT-5.4"
    ...
```

### Шаг 11. Обновление `context_manager.py`

Функция `_compact_codex_context` по-прежнему нужна, но теперь
`provider._last_input_tokens` заполняется из `turn.completed.usage.input_tokens` 
вместо HTTP response headers.

Никаких изменений в логике не требуется — токены заполняются в `_adapt_codex_event`.

### Шаг 12. Удаление мёртвого кода

Удалить из `codex.py`:
- [x] `_MODEL_REASONING_ALIASES` dict
- [x] `_resolve_model_and_effort()` method
- [x] `_adapt_chunk()` method
- [x] `_tool_use_block_from_delta()` method
- [x] `_coerce_tool_input()` method
- [x] `_check_health()` method
- [x] `_check_api_access()` method
- [x] `_autostart_proxy()` method
- [x] `_wait_for_health()` method
- [x] `_proxy_repo_path()` method
- [x] `_proxy_config_path()` method
- [x] `_proxy_command()` method
- [x] `_log_path()` method
- [x] `_pid_path()` method
- [x] `_generated_proxy_config_path()` method
- [x] `_ensure_generated_proxy_config()` method
- [x] `_read_pid()` static method
- [x] `_pid_alive()` static method
- [x] `_is_api_port_open()` method
- [x] `_host_port()` method

Удалить импорты:
- [x] `httpx`
- [x] `yaml` (если больше нигде не используется в codex.py)
- [x] `socket`

Можно удалить `ai-cli-proxy-api/` git submodule если он больше не нужен.

---

## Маппинг нативных тулзов Codex на фичи tero

### Code Review (Phase 3 в coach_player)

| tero фича               | Текущее поведение                        | Новое поведение (Codex CLI)                          |
|--------------------------|------------------------------------------|------------------------------------------------------|
| `code_review=True`       | Запускает обычный `run()` с промптом     | `codex exec review --json --uncommitted` с промптом  |
| Review iterations        | Player fix → re-review loop              | Тот же loop, но review через `run_review()`          |
| Review bugs logging      | `_log_review_result()` в `.g3/bugs/`     | Без изменений — работает на уровне `AdaptedMessage`  |

### TDD Mode (test writer)

| tero фича               | Текущее поведение                        | Новое поведение                                      |
|--------------------------|------------------------------------------|------------------------------------------------------|
| `tdd_mode=True`          | Coach пишет тесты через `run()`          | Codex `exec --json` с test-writing промптом          |
| Test execution           | `_run_tests()` в coach_player            | Без изменений — Codex CLI сам может запускать тесты  |

### Bug Detection  

| tero фича               | Текущее поведение                        | Новое поведение                                      |
|--------------------------|------------------------------------------|------------------------------------------------------|
| `BugDetector`            | Stub (возвращает пустой `BugReport`)     | Codex нативно выполняет shell — может запускать lint  |
| Sandbox mode             | Нет sandboxing                           | `--sandbox workspace-write` — безопасное выполнение  |

### Codex Native Features для tero

| Codex Feature          | Статус   | Применение в tero                                     |
|------------------------|----------|-------------------------------------------------------|
| `shell_tool`           | stable   | Нативное выполнение команд (тесты, lint, build)      |
| `multi_agent`          | stable   | Потенциально: параллельные coach/player Codex агенты  |
| `fast_mode`            | stable   | Быстрые ответы без reasoning для простых задач        |
| `undo`                 | stable   | Откат изменений при провале ревью                     |
| `shell_snapshot`       | stable   | Снимок shell состояния между вызовами                 |
| `image_generation`     | dev      | Будущее: генерация UI скриншотов                      |
| `js_repl`              | exp      | Выполнение JS для frontend задач                      |
| `web_search_request`   | dep      | Исследование через web (deprecated, но доступно)     |
| `memories`             | dev      | Память контекста между сессиями                       |

---

## Файлы для изменения

| Файл                            | Действие                                        | Строк кода |
|---------------------------------|--------------------------------------------------|------------|
| `src/providers/codex.py`        | **Полная перезапись** — CLI вместо HTTP          | ~250       |
| `src/providers/__init__.py`     | Обновить фабрику `create_provider`              | ~15        |
| `src/providers/registry.py`     | Обновить `_create_provider`                     | ~15        |
| `src/menu.py`                   | Обновить `CODEX_MODEL_PRESETS`                  | ~10        |
| `src/runtime_controls.py`       | Обновить `MODEL_PRESETS`                        | ~5         |
| `src/config.py`                 | Обновить `short_model_name`, context windows    | ~10        |
| `src/coach_player.py`           | Review dispatch для CodexProvider               | ~15        |
| `src/context_manager.py`        | Без изменений (уже совместим)                   | 0          |

**Итого**: ~320 строк нового кода, ~490 строк мёртвого кода удаляется.

---

## Проверочный чеклист

- [x] `codex --version` возвращает >= 0.115.0
- [x] `CodexProvider.check_ready()` проходит
- [x] `codex exec --json --ephemeral "say hello"` → JSONL парсится без ошибок
- [x] `command_execution` items корректно маппятся в `ToolUseBlock` + `ToolResultBlock`
- [x] `turn.completed.usage` корректно обновляет `_last_input_tokens`
- [x] Context manager `_compact_codex_context` работает с новыми токенами
- [x] Code review через `codex exec review --json --uncommitted` возвращает результаты
- [x] `streaming_ui.stream_messages()` корректно отображает ToolUse/ToolResult от Codex
- [x] Menu и runtime picker показывают обновлённые модели
- [x] Batch mode с `batch_judge_provider: codex` работает корректно
- [x] Таймаут корректно убивает subprocess
- [x] Stderr ошибки логируются но не крашат сессию

---

## Потенциальные проблемы и решения

### 1. System Prompt injection
**Проблема**: `codex exec` не принимает `--system-prompt` напрямую.  
**Решение**: Использовать `CODEX_INSTRUCTIONS` env var или prepend system prompt к user prompt:
```python
full_prompt = f"<system>\n{system_prompt}\n</system>\n\n{prompt}"
```

### 2. Model не поддерживается
**Проблема**: `codex exec -m o3-mini` → ошибка "not supported with ChatGPT account".  
**Решение**: Обработать `error` и `turn.failed` events, передать наверх как текстовую ошибку.
Адаптер уже это делает.

### 3. Длинный вывод command_execution
**Проблема**: `aggregated_output` может быть очень длинным.  
**Решение**: Truncate в `_adapt_item`:
```python
MAX_TOOL_OUTPUT = 8000
if len(result_text) > MAX_TOOL_OUTPUT:
    result_text = result_text[:MAX_TOOL_OUTPUT] + "\n... (truncated)"
```

### 4. Subprocess зависает
**Проблема**: Codex CLI может зависнуть бесконечно.  
**Решение**: `asyncio.wait_for(timeout=timeout_s)` уже оборачивает `_collect()` в `_run_turn`.
Дополнительно, при TimeoutError нужно `proc.kill()`:
```python
# В run() method — добавить try/finally:
try:
    async for line in proc.stdout:
        ...
finally:
    if proc.returncode is None:
        proc.kill()
        await proc.wait()
```

### 5. `--dangerously-bypass-approvals-and-sandbox` по умолчанию
**Проблема**: Это опасно в продакшне.  
**Решение**: В config по умолчанию `bypass_approvals=True` потому что tero уже контролирует
рабочую директорию и промпты. Для безопасных окружений можно переключить на 
`sandbox_mode="workspace-write"` + `bypass_approvals=False`.

---

## Конфигурация для `.g3/config.yaml`

```yaml
providers:
  codex:
    type: codex
    command: codex              # или полный путь
    default_model: ""           # пустая строка = из ~/.codex/config.toml
    sandbox_mode: workspace-write
    bypass_approvals: true
    ephemeral: true
    default_timeout: 900
```

---

## Порядок выполнения

- [x] **Шаг 1-4**: Перезаписать `codex.py` (Config + Provider + адаптер + check_ready)
- [x] **Шаг 5**: Добавить `run_review()` метод
- [x] **Шаг 6**: Обновить `coach_player.py` для review dispatch
- [x] **Шаг 7-8**: Обновить фабрики в `__init__.py` и `registry.py`
- [x] **Шаг 9**: Обновить UI (menu + runtime_controls)
- [x] **Шаг 10**: Обновить config.py
- [x] **Шаг 11**: Подтвердить что context_manager работает
- [x] **Шаг 12**: Удалить мёртвый код и proxy submodule
- [x] **Тестирование**: Пройти весь чеклист
