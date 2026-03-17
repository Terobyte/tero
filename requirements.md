# Tero — Requirements v2

## Обзор

Расширение tero (G3 coach-player loop) четырьмя независимыми фичами:

- [x] **CCG Multi-Account** — два аккаунта Blackbox (ccg / ccg2) с разными токенами, параллельная работа
- [x] **Codex Provider** — новый провайдер через ai-cli-proxy-api (OpenAI-compatible)
- [x] **TDD Mode** — toggle: Test Writer пишет тесты перед имплементацией
- [x] **Code Review** — toggle: финальный review через Codex после Coach approval

Фичи независимы друг от друга. TDD и Code Review — тогглы в меню, можно включать по отдельности или оба сразу.

---

## Часть 0: CCG Multi-Account (ccg / ccg2)

### 0.1 Цель

Поддержка двух независимых Blackbox аккаунтов, чтобы Player и Coach могли работать на разных ключах параллельно, без блокировки друг друга rate limits.

### 0.2 Текущее состояние

- [ ] Launcher scripts `launcher/ccg` и `launcher/ccg2` уже используют разные токены и CLAUDE_HOME
- [ ] `BLACKBOX_ACCOUNT_A_TOKEN` → ccg (Account A, `~/.claude-glm-a`)
- [ ] `BLACKBOX_ACCOUNT_B_TOKEN` → ccg2 (Account B, `~/.claude-glm-b`)
- [ ] Legacy helper `CcgEnv.from_env()` должен указывать на Account A home (`~/.claude-glm-a`)
- [ ] Основной runtime путь для `ccg`/`ccg2` должен идти через `CcgEnv.for_account()` / `create_provider()`

### 0.3 Решение: два CcgEnv

**config.py — использовать `for_account()` как основной путь, а `from_env*()` оставить как legacy helpers:**

```python
@dataclass
class CcgEnv:
    base_url: str
    auth_token: str
    model: str
    small_model: str
    claude_home: str

    @classmethod
    def from_env(cls, claude_home: str = "~/.claude-glm-a") -> "CcgEnv":
        """Legacy helper for Account A."""
        ...

    @classmethod
    def from_env_b(cls, claude_home: str = "~/.claude-glm-b") -> "CcgEnv":
        """Legacy helper for Account B. Must not fall back to Account A token."""
        ...

    @classmethod
    def for_account(cls, account_name: str, provider_config: dict | None = None) -> "CcgEnv":
        """Primary constructor used by the provider factory and registry."""
        ...
```

Важно:
- `from_env_b()` намеренно читает только `BLACKBOX_ACCOUNT_B_TOKEN`, без fallback на Account A.
- Основной runtime путь для `ccg`/`ccg2` проходит через `for_account()`, а не через `_build()`.

### 0.4 Провайдер ccg2

**providers/__init__.py — обновить create_provider():**

```python
if provider_name in ("ccg", "ccg2"):
    if ccg_env is None:
        raise ValueError("CcgEnv required for CCG provider")
    return CcgProvider(ccg_env)
```

`ccg2` не отдельная реализация провайдера, а тот же `CcgProvider` с другим `CcgEnv`.
Выбор Account A / Account B должен происходить **снаружи фабрики**: в `CoachPlayerSession`,
launcher scripts или тестах. Фабрика не должна сама решать, какой ключ читать из env.

### 0.5 CoachPlayerSession — два env

**coach_player.py:**

```python
class CoachPlayerSession:
    def __init__(self, config, requirements, plan_file_path=""):
        # ...
        self.ccg_env = CcgEnv.from_env(config.claude_home)      # Account A
        self.ccg_env_b = CcgEnv.from_env_b()                     # Account B

        def _get_ccg_env(provider_name: str):
            if provider_name == "ccg":
                return self.ccg_env
            if provider_name == "ccg2":
                return self.ccg_env_b
            return None

        self.player_provider = create_provider(
            config.player_provider,
            _get_ccg_env(config.player_provider),
            provider_configs.get(config.player_provider),
        )
        self.coach_provider = create_provider(
            config.coach_provider,
            _get_ccg_env(config.coach_provider),
            provider_configs.get(config.coach_provider),
        )
```

Если Player=ccg, Coach=ccg2 → каждый использует свой токен, свой CLAUDE_HOME → параллельная работа без конфликтов.
Если нужно, env для `ccg2` можно передавать явно извне; `ccg2` здесь выступает как отдельный provider id
для выбора в CLI/menu, но implementation class остаётся той же.

### 0.6 Меню

```python
PROVIDER_PRESETS = {
    "CCG  (Blackbox A)": "ccg",
    "CCG2 (Blackbox B)": "ccg2",
    "Claude Pro (native)": "claude",
    "Codex (GPT via proxy)": "codex",
}
```

### 0.7 CLI

```bash
tero go --player-provider=ccg --coach-provider=ccg2
# Player на Account A, Coach на Account B — параллельно
```

`--player-provider` и `--coach-provider` choices: `["ccg", "ccg2", "claude", "codex"]`

### 0.8 Display Names

```python
class CcgProvider:
    @property
    def display_name(self) -> str:
        # Используем account_label, а не string matching по пути
        model = ...  # existing logic
        account = self.env.account_label
        return f"CCG ({model}/{account})"
```

### 0.9 Use Cases

| Player | Coach | Зачем |
|--------|-------|-------|
| ccg | ccg2 | Параллельная работа, no rate limit conflicts |
| ccg2 | ccg | Если Account A занят другим проектом |
| ccg | ccg | Один аккаунт (как сейчас) |
| ccg | claude | CCG для Player, Claude Pro для Coach |

### 0.10 Тесты

- [ ] `CcgEnv.from_env()` читает `BLACKBOX_ACCOUNT_A_TOKEN`
- [ ] `CcgEnv.from_env_b()` читает `BLACKBOX_ACCOUNT_B_TOKEN`
- [ ] `create_provider("ccg2")` создаёт CcgProvider с env_b
- [ ] Разные `claude_home` у ccg и ccg2

---

## Часть 1: Codex Provider

### 1.1 Цель

Добавить третий провайдер `codex` для Player и Coach, работающий через локальный ai-cli-proxy-api (Go-прокси с OAuth для OpenAI Codex).

### 1.2 Архитектура

```
tero (Python)
  ↓
providers/codex.py
  ↓ HTTP (OpenAI-compatible API)
ai-cli-proxy-api (Go) — localhost:8765
  ↓ OAuth
OpenAI Codex (GPT-5.4, GPT-5.4 HIGH, GPT-5.4 ULTRA-HIGH)
```

### 1.3 Провайдер: `CodexProvider`

**Файл:** `g3/src/providers/codex.py`

**Интерфейс:** реализует `AgentProvider` protocol (base.py):
- [ ] `async run(prompt, system_prompt, working_dir, max_turns, model)` → AsyncIterator
- [ ] `check_ready()` → tuple[bool, str]
- [ ] `display_name` → str

**Конфигурация:**

```python
@dataclass
class CodexConfig:
    api_url: str = "http://localhost:8765"
    api_key: str = ""
    model: str = "gpt-5.4-medium"
```

**Реализация run():**
- [ ] HTTP POST к `/v1/chat/completions` с `stream: true`
- [ ] SSE parsing (data: ... линии)
- [ ] Формирование messages: `[{"role": "system", ...}, {"role": "user", ...}]`
- [ ] Yield адаптированных сообщений через message_adapter
- [ ] Использовать `httpx.AsyncClient` для async streaming

**Реализация check_ready():**
- [ ] GET `/v1/models` на прокси
- [ ] Проверить что есть хотя бы одна модель с `"gpt"` **или** `"codex"` в ID
- [ ] Если прокси не отвечает → `(False, "Proxy not reachable at {base_url}. Run: cd ai-cli-proxy-api && go run ./cmd/server")`
- [ ] Если нет моделей → `(False, "No Codex models. Run: ai-cli-proxy-api login codex")`

### 1.4 Адаптация сообщений

ai-cli-proxy-api возвращает OpenAI-формат SSE:

```json
{"choices": [{"delta": {"content": "текст"}, "finish_reason": null}]}
```

Нужен адаптер OpenAI SSE → AdaptedMessage (уже есть TextBlock, ToolUseBlock, ToolResultBlock в message_adapter.py).

**Примечание по реализации:**

В текущем коде адаптация OpenAI SSE делается приватным методом `CodexProvider._adapt_chunk()`, а не общей функцией `adapt_openai_sse_chunk()` в `message_adapter.py`.

### 1.5 Интеграция в фабрику провайдеров

**Файл:** `g3/src/providers/__init__.py`

Добавить в `create_provider()`:

```python
if provider_name == "codex":
    from .codex import CodexProvider, CodexConfig
    codex_cfg = CodexConfig(
        api_url=provider_config.get("api_url", provider_config.get("base_url", "http://localhost:8765")),
        api_key=provider_config.get("api_key", ""),
        model=provider_config.get("model", provider_config.get("default_model", "gpt-5.4-medium")),
    )
    return CodexProvider(codex_cfg)
```

### 1.6 Config & CLI

**config.py — расширить choices:**
- [ ] `player_provider` и `coach_provider`: добавить `"codex"` к допустимым значениям
- [ ] Env vars: `G3_PLAYER_PROVIDER=codex`, `G3_COACH_PROVIDER=codex`

**g3.py — CLI args:**
- [ ] `--player-provider` choices: `["ccg", "ccg2", "claude", "codex"]`
- [ ] `--coach-provider` choices: `["ccg", "ccg2", "claude", "codex"]`

### 1.7 Меню

**menu.py — добавить Codex в PROVIDER_PRESETS:**

```python
PROVIDER_PRESETS = {
    "CCG (Blackbox/GLM-5)": "ccg",
    "CCG2 (Blackbox B)": "ccg2",
    "Claude Pro (native)": "claude",
    "Codex (GPT via proxy)": "codex",
}

CODEX_MODEL_PRESETS = {
    "GPT-5.4 Medium     (gpt-5.4-medium)": "gpt-5.4-medium",
    "GPT-5.4 High       (gpt-5.4-high)": "gpt-5.4-high",
    "GPT-5.4 Ultra High (gpt-5.4-ultra-high)": "gpt-5.4-ultra-high",
}
```

При выборе Codex — показывать CODEX_MODEL_PRESETS для выбора модели.

### 1.8 .g3/config.yaml

```yaml
providers:
  codex:
    type: codex
    api_url: "http://localhost:8765"
    api_key: ""
    model: "gpt-5.4-medium"
```

### 1.9 Зависимости

- [ ] `httpx` — для async HTTP (уже может быть, иначе добавить в requirements.txt)
- [ ] ai-cli-proxy-api должен быть запущен и авторизован

### 1.10 Тесты

- [ ] `tests/test_codex_provider.py`:
  - [ ] Unit: CodexConfig defaults
  - [ ] Unit: `CodexProvider._adapt_chunk()` для text, role-only delta, finish_reason
  - [ ] Unit: check_ready() с mock httpx
  - [ ] Integration: create_provider("codex") возвращает CodexProvider

---

## Часть 2: TDD Mode (Toggle)

### 2.1 Цель

Опциональный режим: перед имплементацией каждого шага, отдельный агент (Test Writer) генерирует тесты. Player затем имплементирует код так, чтобы тесты прошли.

### 2.2 Pipeline с TDD

**Стандартный цикл (без TDD):**
```
Player implements → Coach reviews → [approved | feedback → retry]
```

**С TDD toggle:**
```
Test Writer generates tests → Player implements (tests must pass) → Coach reviews → [approved | feedback → retry]
```

### 2.3 Кто пишет тесты

- [ ] Test Writer использует **coach_provider/coach_model** (тот же провайдер что и Coach)
- [ ] Отдельный system prompt: `TEST_WRITER_SYSTEM_PROMPT`
- [ ] Отдельный prompt builder: `build_test_writer_prompt()`

### 2.4 Test Writer System Prompt

```
TEST_WRITER_SYSTEM_PROMPT = """You are a Test Architect. Your job is to write comprehensive tests BEFORE implementation.

RULES:
- Read the requirement carefully
- Look at the existing codebase to understand the testing patterns, framework, and structure
- Write tests that will FAIL right now (the feature is not implemented yet)
- Tests must cover: happy path, edge cases, error handling
- Use the project's existing test framework and conventions
- Place tests in the correct test directory following project conventions
- Tests should be specific and verifiable — no vague assertions
- Do NOT implement the feature — only write tests

OUTPUT:
- Create test file(s) with all tests
- Print summary of what tests cover"""
```

### 2.5 Test Writer Prompt Builder

```python
def build_test_writer_prompt(
    current_step: str,
    step_num: int,
    total_steps: int,
    completed_steps: list[str],
) -> str:
```

Содержимое:
- [ ] Текущий шаг (что нужно реализовать)
- [ ] Контекст уже сделанных шагов
- [ ] Инструкция: напиши тесты которые проверят что этот шаг реализован правильно

### 2.6 Модификация Player Prompt (при TDD)

Когда TDD включен, player prompt дополняется:

```
## Tests Already Written
Tests have been created for this step. Your implementation MUST pass all tests.
Run the tests after implementation to verify.
```

### 2.6.1 Enforced test run

TDD режим должен быть **обязательным**, а не только prompt hint.

После каждого Player attempt, но **до** Coach turn, система запускает тесты:

```python
if self.config.tdd_mode:
    test_result = await self._run_tests_for_step(step.text)
    streaming_ui.print_tdd_status(test_result.passed, test_result.summary)

    if not test_result.passed:
        feedback = Feedback(
            "1. The tests written for this step are still failing.\n"
            f"2. Test output summary:\n{test_result.summary}\n"
            "3. Fix the implementation until the tests pass."
        )
        continue  # skip coach, retry player
```

Требования к этому шагу:
- [ ] Если тесты падают, Coach **не запускается**
- [ ] Если тесты проходят, только тогда начинается Coach review
- [ ] Разрешить override через config: `test_command` (если пусто, использовать autodetect)
- [ ] Выполнять команду без `shell=True`
- [ ] Добавить `test_timeout_s` в config/env/CLI
- [ ] Автодетект команд держать простым и детерминированным: `pytest.ini`/pytest в `pyproject.toml` → `pytest -q`, `package.json` → `npm test`, `Cargo.toml` → `cargo test`, `Makefile` → `make test`

### 2.7 Config

```python
@dataclass
class Config:
    # ... existing fields ...
    tdd_mode: bool = False  # TDD toggle
    test_command: str = ""  # empty = auto-detect project test command
    test_timeout_s: int = 60
```

**CLI:** `--tdd` flag
**Env:** `G3_TDD_MODE=true`
**Env:** `G3_TEST_COMMAND="pytest -q"`
**Env:** `G3_TEST_TIMEOUT_S=60`
**Config yaml:** `defaults.tdd_mode: true`, `defaults.test_command: "pytest -q"`

### 2.8 Меню

Добавить в меню тогглы:

```python
questionary.Separator("─── режимы ──────────────────────────────"),
questionary.Choice(f"    TDD Mode:       {'вкл' if config.tdd_mode else 'выкл'}", value="tdd_mode"),
questionary.Choice(f"    Code Review:    {'вкл' if config.code_review else 'выкл'}", value="code_review"),
```

Переключение — простой toggle (как verbose/autonomous).

### 2.9 Интеграция в CoachPlayerSession.run()

В цикле по шагам, **перед первой player attempt**:

```python
if self.config.tdd_mode:
    # --- Test Writer turn ---
    streaming_ui.print_test_writer_header(step_num, total_steps)

    test_prompt = build_test_writer_prompt(
        current_step=step.text,
        step_num=step_num,
        total_steps=total_steps,
        completed_steps=completed_steps,
    )

    await self._run_turn(
        role="test_writer",
        prompt=test_prompt,
        system_prompt=TEST_WRITER_SYSTEM_PROMPT,
        max_turns=15,
        timeout_s=self.config.coach_timeout_s,
        model_override=self.config.coach_model,
    )
```

Test Writer запускается **один раз на шаг** (не на каждую попытку). Тесты пишутся перед первой попыткой, дальше Player итерирует пока тесты не пройдут.
После **каждой** player attempt тесты прогоняются автоматически; только успешный test run пропускает шаг к Coach.

### 2.10 Streaming UI

Новые функции в streaming.py:

```python
def print_test_writer_header(step_num, total_steps):
    """Print header for test writer phase."""
    # 🧪 [Step 1/5] Test Writer generating tests...

def print_tdd_status(tests_passed: bool, test_output: str):
    """Print TDD test run results."""
```

### 2.11 Тесты

- [ ] `tests/test_tdd_mode.py`:
  - [ ] Config: tdd_mode=True парсится из CLI, env, yaml
  - [ ] Config: `test_command` парсится из CLI, env, yaml
  - [ ] Prompt builder: build_test_writer_prompt() содержит нужные поля
  - [ ] Flow: при tdd_mode=True вызывается test_writer перед player
  - [ ] Flow: если тесты упали, Coach не вызывается

---

## Часть 3: Code Review Toggle

### 3.1 Цель

Опциональный финальный review через отдельного агента (Codex/GPT) после того как Coach уже одобрил шаг. Ищет баги, security issues, best practices нарушения.

### 3.2 Pipeline с Code Review

**Стандартный цикл:**
```
Player → Coach → APPROVED → next step
```

**С Code Review toggle:**
```
Player → Coach → APPROVED → Code Reviewer → [ok | issues → feedback → retry]
```

### 3.3 Кто делает review

- [ ] По умолчанию: `codex` провайдер (если настроен)
- [ ] Fallback: тот же `coach_provider`
- [ ] Настраивается отдельно: `review_provider` / `review_model`

Для runtime-routing нужен отдельный provider slot:
- [ ] `player` → `self.player_provider`
- [ ] `coach` → `self.coach_provider`
- [ ] `test_writer` → `self.coach_provider` (или alias `self.test_writer_provider`)
- [ ] `reviewer` → `self.review_provider`

### 3.4 Config

```python
@dataclass
class Config:
    # ... existing fields ...
    code_review: bool = False       # Code Review toggle
    review_provider: str = ""       # empty = use codex if available, else coach_provider
    review_model: str = ""          # empty = provider default
```

**CLI:** `--code-review` flag, `--review-provider`, `--review-model`
**Env:** `G3_CODE_REVIEW=true`, `G3_REVIEW_PROVIDER=codex`

### 3.5 Code Reviewer System Prompt

```
CODE_REVIEWER_SYSTEM_PROMPT = """You are a Code Reviewer specializing in bug finding and security analysis.

You are reviewing code that has ALREADY been approved by a coach. Your job is to find issues
the coach missed.

FOCUS AREAS:
- Security vulnerabilities (injection, XSS, auth bypass, secrets in code)
- Logic bugs (off-by-one, race conditions, null handling)
- Performance issues (N+1 queries, memory leaks, blocking calls)
- Error handling gaps (unhandled exceptions, silent failures)
- Best practices violations specific to the language/framework

DO NOT review:
- Code style or formatting
- Naming conventions
- Minor refactoring suggestions

PROCESS:
- Read the changed/new files for the current step
- Analyze for the focus areas above
- If critical issues found → numbered list of issues
- If no critical issues → CODE_REVIEW_PASSED

Your verdict MUST end with either CODE_REVIEW_PASSED or a numbered list of critical issues."""
```

### 3.6 Code Review Prompt Builder

```python
def build_code_review_prompt(
    current_step: str,
    step_num: int,
    total_steps: int,
) -> str:
```

Содержимое:
- [ ] Какой шаг был реализован
- [ ] Инструкция: проверь реализацию на баги и security issues
- [ ] Акцент на `git diff` чтобы смотреть именно изменения

### 3.7 Интеграция в CoachPlayerSession.run()

**После** Coach одобрил шаг (verdict == Approved), **перед** mark_step_done:

```python
if isinstance(verdict, Approved) and self.config.code_review:
    # --- Code Review turn ---
    streaming_ui.print_code_review_header(step_num, total_steps)

    review_prompt = build_code_review_prompt(
        current_step=step.text,
        step_num=step_num,
        total_steps=total_steps,
    )

    review_result = await self._run_turn(
        role="reviewer",
        prompt=review_prompt,
        system_prompt=CODE_REVIEWER_SYSTEM_PROMPT,
        max_turns=8,
        timeout_s=self.config.coach_timeout_s,
        model_override=self.config.review_model,
    )

    review_verdict = parse_review_output(review_result.messages)

    if isinstance(review_verdict, ReviewPassed):
        # Proceed to mark step done
        streaming_ui.print_review_passed(step_num)
    else:
        # Send review feedback back to player
        feedback = Feedback(review_verdict.text)
        streaming_ui.print_review_issues(review_verdict.text)
        step_approved = False  # force another player iteration
        continue
```

### 3.8 Review Verdict Parsing

**feedback.py — добавить:**

```python
@dataclass
class ReviewPassed:
    """Code review passed with no critical issues."""
    pass

@dataclass
class ReviewIssues:
    """Code review found issues."""
    text: str

def parse_review_output(messages: list) -> ReviewPassed | ReviewIssues:
    """Parse code reviewer output for verdict."""
    # Look for CODE_REVIEW_PASSED in final text
    # Otherwise extract issues list
```

### 3.9 Review Provider Resolution

В `CoachPlayerSession.__init__()`:

```python
if self.config.code_review:
    review_provider_name = self.config.review_provider
    if not review_provider_name:
        # Auto-detect: use codex if available, else coach.
        # Guard check_ready() so review auto-detect cannot crash session init.
        try:
            codex_prov = create_provider("codex", None, provider_configs.get("codex"))
            ok, _ = codex_prov.check_ready()
        except Exception:
            ok = False
        review_provider_name = "codex" if ok else self.config.coach_provider

    self.review_provider = create_provider(review_provider_name, None, provider_configs.get(review_provider_name))
```

`review_provider` должен реально использоваться в `_run_turn()`. Недостаточно просто создать `self.review_provider`;
нужно обновить роутинг роли на провайдер.

Например:

```python
provider = self._provider_for_role(role)
```

### 3.10 Меню

В menu.py — добавить тогглы (вместе с TDD):

```python
questionary.Separator("─── режимы ──────────────────────────────"),
questionary.Choice(f"    TDD Mode:       {tdd_display}", value="tdd_mode"),
questionary.Choice(f"    Code Review:    {review_display}", value="code_review"),
```

При включении Code Review — опционально спросить review provider:

```python
if setting == "code_review":
    config.code_review = not config.code_review
    if config.code_review and not config.review_provider:
        # Ask for review provider
        choice = questionary.select(
            "Провайдер для Code Review:",
            choices=["Codex (auto-detect)", "Coach (same as coach)", "Выбрать..."],
        ).ask()
```

### 3.11 Streaming UI

```python
def print_code_review_header(step_num, total_steps):
    """🔍 [Step 1/5] Code Review (Codex/GPT-5.4)..."""

def print_review_passed(step_num):
    """✅ Code Review passed — no critical issues"""

def print_review_issues(issues_text):
    """⚠ Code Review found issues: ..."""
```

### 3.12 Сохранение результатов review

Результаты review сохраняются в `.g3/reviews/`:

```
.g3/reviews/
  <run-id>/step-1-review.md
  <run-id>/step-2-review.md
```

Формат:
```markdown
# Code Review — Step 1
- [ ] Provider: codex/gpt-5.4
- [ ] Verdict: PASSED | ISSUES_FOUND
- [ ] Issues: (если есть)
```

### 3.13 Тесты

- [ ] `tests/test_code_review.py`:
  - [ ] Config: code_review=True парсится из CLI, env, yaml
  - [ ] parse_review_output(): CODE_REVIEW_PASSED → ReviewPassed
  - [ ] parse_review_output(): numbered list → ReviewIssues
  - [ ] Flow: review вызывается только после Coach approval

---

## Часть 4: Полный Pipeline (все тогглы включены)

### 4.1 Полная последовательность

Когда и TDD Mode, и Code Review включены одновременно:

```
Для каждого шага:
  - [ ] [TDD]    Test Writer генерирует тесты (один раз)
  - [ ] [IMPL]   Player имплементирует (тесты должны пройти)  ─┐
  - [ ] [COACH]  Coach проверяет                                │ retry loop
  - [ ] [REVIEW] Code Reviewer проверяет (после Coach OK)       │
  └── feedback → retry from step 2 ──────────────────────────┘
  - [ ] [DONE]   Шаг помечен как выполненный
```

### 4.2 Retry Logic

- [ ] **Coach отклонил** → feedback идёт Player, retry с шага 2
- [ ] **Code Review нашёл issues** → feedback идёт Player, retry с шага 2
- [ ] **Тесты не переписываются** при retry (написаны один раз в шаге 1)
- [ ] max_turns применяется к общему числу Player attempts

### 4.3 Status Display

```
⚙  tero — настройка  (↑↓ выбор, Enter)
  ▶   Запустить
  ─── провайдеры ──────────────────────────
      Player:         ccg (GLM-5)
      Coach:          claude (SONNET)
  ─── режимы ──────────────────────────────
      TDD Mode:       выкл
      Code Review:    выкл
  ─── настройки ───────────────────────────
      Рабочая папка:  ~/project
      ...
```

### 4.4 Runtime Header

При запуске сессии отображать активные режимы:

```
--- tero coach-player ---
  Файл плана: requirements.md
  Шагов: 5  |  Макс. попыток на шаг: 10
  Player: CCG (GLM-5)  |  Coach: Claude Pro (sonnet)
  Режимы: TDD ✓  Code Review ✓ (Codex/GPT-5.4)
```

---

## Часть 5: Файловая структура изменений

### 5.1 Новые файлы

```
g3/src/providers/codex.py           — Codex провайдер
g3/tests/test_codex_provider.py     — тесты Codex провайдера
g3/tests/test_tdd_mode.py           — тесты TDD mode
g3/tests/test_code_review.py        — тесты Code Review
g3/tests/test_ccg_multiaccount.py   — тесты CCG multi-account
```

### 5.2 Изменяемые файлы

```
g3/src/config.py                    — CcgEnv.from_env_b(), for_account(), tdd_mode, code_review, review_provider, review_model
                                   — test_command, test_timeout_s
g3/src/providers/__init__.py        — ccg2 + codex в create_provider()
g3/src/providers/codex.py           — адаптация OpenAI SSE внутри `_adapt_chunk()`
g3/src/coach_player.py              — ccg_env_b, TDD и Code Review фазы в loop
g3/src/prompts.py                   — добавить TEST_WRITER и CODE_REVIEWER prompts
g3/src/menu.py                      — добавить ccg2, codex, тогглы в меню
g3/src/streaming.py                 — добавить UI функции для новых фаз
g3/src/feedback.py                  — добавить ReviewPassed, ReviewIssues, parse_review_output()
g3/g3.py                            — добавить CLI args: --tdd, --test-command, --code-review, --review-provider, ccg2 choice
g3/requirements.txt                 — добавить httpx (если нет)
```

---

## Часть 6: Порядок реализации

### Phase 0: CCG Multi-Account
- [ ] 0.1 Добавить `CcgEnv.from_env_b()` и `for_account()` в config.py
- [ ] 0.2 Обновить `create_provider("ccg2")` — принимать выбранный env, без автоподмены внутри фабрики
- [ ] 0.3 Обновить CoachPlayerSession — передавать правильный env для ccg/ccg2
- [ ] 0.4 Добавить `ccg2` в CLI choices (g3.py)
- [ ] 0.5 Добавить CCG2 в меню (PROVIDER_PRESETS)
- [ ] 0.6 Display name: CCG-A / CCG-B
- [ ] 0.7 Тесты multi-account

### Phase 1: Codex Provider
- [ ] 1.1 Создать `codex.py` с CodexConfig и CodexProvider
- [ ] 1.2 Адаптировать OpenAI SSE внутри `CodexProvider._adapt_chunk()`
- [ ] 1.3 Добавить `codex` в `create_provider()` (__init__.py)
- [ ] 1.4 Добавить `codex` в CLI choices и config
- [ ] 1.5 Добавить Codex в меню (PROVIDER_PRESETS + CODEX_MODEL_PRESETS)
- [ ] 1.6 Тесты codex провайдера

### Phase 2: TDD Mode
- [ ] 2.1 Добавить `tdd_mode` в Config, CLI, env
- [ ] 2.1.1 Добавить `test_command` в Config, CLI, env
- [ ] 2.1.2 Добавить `test_timeout_s` в Config, CLI, env
- [ ] 2.2 Добавить TEST_WRITER_SYSTEM_PROMPT и build_test_writer_prompt() в prompts.py
- [ ] 2.3 Добавить TDD toggle в меню
- [ ] 2.4 Добавить print_test_writer_header() в streaming.py
- [ ] 2.5 Интегрировать test_writer фазу в coach_player.py run()
- [ ] 2.6 Тесты TDD mode

### Phase 3: Code Review
- [ ] 3.1 Добавить `code_review`, `review_provider`, `review_model` в Config, CLI, env
- [ ] 3.2 Добавить CODE_REVIEWER_SYSTEM_PROMPT и build_code_review_prompt() в prompts.py
- [ ] 3.3 Добавить ReviewPassed, ReviewIssues, parse_review_output() в feedback.py
- [ ] 3.4 Добавить Code Review toggle в меню
- [ ] 3.5 Добавить print_code_review_header/passed/issues в streaming.py
- [ ] 3.6 Интегрировать review фазу в coach_player.py run()
- [ ] 3.7 Добавить сохранение review результатов в .g3/reviews/
- [ ] 3.8 Тесты Code Review

### Phase 4: Integration
- [ ] 4.1 Полный pipeline: TDD + Code Review вместе
- [ ] 4.2 Runtime header с отображением активных режимов
- [ ] 4.3 End-to-end тест всех режимов

---

## Часть 7: Coach Silent Failure Fix

### 7.1 Проблема

GLM-5 (через CCG provider) регулярно завершает сессию без финального текстового сообщения —
последний SDK message является tool result, а не текстом. Реже такое случается с Sonnet.

Проблемное поведение, которое нужно исключить:
- [ ] `parse_coach_output` не должен превращать отсутствие вердикта в обычный player-feedback
- [ ] Player не должен получать фиктивный фидбек, когда молчит именно coach
- [ ] Step-level retry должен повторять coach, а не заставлять Player бессмысленно переписывать код

### 7.2 Два отдельных бага

**Баг A: `_is_assistant_message` в feedback.py**

```python
# Сейчас:
msg_type = type(msg).__name__  # → "AdaptedMessage", не "AssistantMessage"
if msg_type == "AssistantMessage":  # ВСЕГДА False
    return True
# Fallback проверяет только наличие .content — срабатывает для ВСЕХ AdaptedMessage
# включая role="tool" (tool results)
if hasattr(msg, "content") and not hasattr(msg, "tool_use_id"):
    return True
```

Если последнее сообщение в очереди — `AdaptedMessage(role="tool", ...)`, оно ошибочно
принимается за ассистента. `_extract_text_from_message` не находит `.text` в ToolResultBlock
и возвращает пустую строку → срабатывает "no output" fallback.

**Фикс:**
```python
def _is_assistant_message(msg) -> bool:
    # SDK native type
    if type(msg).__name__ == "AssistantMessage":
        return True
    # AdaptedMessage — проверять role явно
    if hasattr(msg, "role"):
        return msg.role == "assistant"
    return False
```

**Баг Б: "нет вердикта" неотличимо от "нет ответа"**

Текущий `parse_coach_output` возвращает одинаковый `Feedback(...)` в двух разных ситуациях:
- [ ] Coach не выдал вообще ничего (0 assistant messages)
- [ ] Coach написал текст, но без `IMPLEMENTATION_APPROVED` и без нумерованного списка

Ситуация 2 — это реальный фидбек (пусть и плохо структурированный) и его нужно передавать Player.
Ситуация 1 — это сбой coach, player здесь не при чём.

### 7.3 Новый тип вердикта: `NoVerdict`

**feedback.py — добавить:**

```python
@dataclass
class NoVerdict:
    """Coach завершил работу без вердикта (нет текста / не ответил).

    Это НЕ фидбек для Player. Это сигнал что coach нужно повторить.
    """
    pass

Verdict = Approved | Feedback | NoVerdict
```

**Обновить `parse_coach_output`:**

```python
def parse_coach_output(messages: list) -> Verdict:
    text = _latest_assistant_text(messages)

    if not text:
        return NoVerdict()

    if "IMPLEMENTATION_APPROVED" in text:
        return Approved()

    return Feedback(text)
```

### 7.4 Coach retry логика в coach_player.py

Когда verdict == `NoVerdict`, проблема на стороне coach, а не player.
Решение: повторить **только coach** turn, не трогая player.

```python
# В CoachPlayerSession.run(), после coach turn:
COACH_RETRY_MAX = 2  # или из config

for coach_attempt in range(1, COACH_RETRY_MAX + 1):
    coach_result = await self._run_turn(role="coach", ...)
    verdict = parse_coach_output(coach_result.messages)

    if not isinstance(verdict, NoVerdict):
        break  # получили реальный вердикт

    if coach_attempt < COACH_RETRY_MAX:
        streaming_ui.print_coach_no_verdict_retry(coach_attempt, COACH_RETRY_MAX)
    else:
        # Исчерпали повторы — coach не смог выдать вердикт
        streaming_ui.print_coach_silent_skip()
        verdict = Approved()  # пропускаем шаг (см. 7.5)
```

### 7.5 Что делать если coach так и не ответил: Sonnet Fallback

Если после `coach_retry_max` попыток основной coach (GLM-5) всё равно не дал вердикт —
**один раз** вызвать fallback coach (Sonnet). После получения вердикта от Sonnet сессия
продолжается с основным coach как обычно.

```
GLM-5 → NoVerdict → retry → NoVerdict → retry → NoVerdict
  → Sonnet (один раз) → Approved / Feedback
  → следующая player attempt → GLM-5 coach снова
```

Fallback не заменяет основного coach навсегда — только закрывает этот конкретный
«мёртвый» вердикт. GLM-5 остаётся основным coach на следующих итерациях.

**Конфигурация:**
```python
@dataclass
class Config:
    coach_retry_max: int = 2             # повторов GLM-5 при NoVerdict перед escalation
    coach_fallback_provider: str = "claude"  # провайдер для fallback (Sonnet)
    coach_fallback_model: str = ""           # пусто = provider default
```

CLI: `--coach-fallback-provider=claude`, `--coach-fallback-model=...`
Env: `G3_COACH_FALLBACK_PROVIDER=claude`, `G3_COACH_FALLBACK_MODEL=...`

**Fallback provider инициализируется в `CoachPlayerSession.__init__()`:**

```python
self.coach_fallback_provider = create_provider(
    config.coach_fallback_provider,
    self.ccg_env,
    provider_configs.get(config.coach_fallback_provider),
) if config.coach_fallback_provider else None
```

**Логика в run():**

```python
for coach_attempt in range(1, coach_retry_max + 1):
    coach_result = await self._run_turn(role="coach", ...)
    verdict = parse_coach_output(coach_result.messages)

    if not isinstance(verdict, NoVerdict):
        break

    if coach_attempt < coach_retry_max:
        streaming_ui.print_coach_no_verdict_retry(coach_attempt, coach_retry_max)
else:
    # Основной coach молчит — вызвать fallback один раз
    streaming_ui.print_coach_fallback_escalation(self.coach_fallback_provider.display_name)
    fallback_result = await self._run_turn(
        role="coach_fallback",
        prompt=coach_prompt,
        system_prompt=COACH_STRICT_SYSTEM_PROMPT,
        max_turns=8,
        timeout_s=self.config.coach_timeout_s,
        model_override=self.config.coach_fallback_model,
    )
    verdict = parse_coach_output(fallback_result.messages)
    if isinstance(verdict, NoVerdict):
        # Даже Sonnet не ответил — редкий случай, завершить сессию с ошибкой
        raise RuntimeError("Fallback coach also produced no verdict")
```

`"coach_fallback"` роутится на `self.coach_fallback_provider` в `_run_turn`.

### 7.6 Streaming UI

```python
def print_coach_no_verdict_retry(attempt: int, max_attempts: int):
    """⚠ Coach не дал вердикт — повтор {attempt}/{max_attempts}..."""

def print_coach_fallback_escalation(fallback_name: str):
    """⚠ Coach молчит — передаю {fallback_name} для вердикта..."""
```

### 7.7 Тесты

- [ ] `tests/test_feedback.py` — обновить:
  - [ ] `parse_coach_output([])` → `NoVerdict()`
  - [ ] `parse_coach_output([AdaptedMessage(role="tool", ...)])` → `NoVerdict()` (не Feedback!)
  - [ ] `parse_coach_output([AdaptedMessage(role="assistant", content=[TextBlock("some text")])])` → `Feedback("some text")`
  - [ ] `parse_coach_output([AdaptedMessage(role="assistant", content=[TextBlock("IMPLEMENTATION_APPROVED")])])` → `Approved()`
  - [ ] `_is_assistant_message(AdaptedMessage(role="tool", ...))` → `False`
  - [ ] `_is_assistant_message(AdaptedMessage(role="assistant", ...))` → `True`

- [ ] `tests/test_coach_player.py` — добавить:
  - [ ] При `NoVerdict` × `coach_retry_max`: вызывается fallback provider (Sonnet)
  - [ ] Fallback возвращает `Approved` / `Feedback` → сессия продолжается нормально
  - [ ] Если и fallback возвращает `NoVerdict` → RuntimeError
  - [ ] После fallback вердикта следующая итерация использует снова основной coach

### 7.8 Порядок реализации

- [ ] 7.1 Фикс `_is_assistant_message` в feedback.py (role-based check)
- [ ] 7.2 Добавить `NoVerdict` в feedback.py
- [ ] 7.3 Обновить `parse_coach_output` — возвращать `NoVerdict` вместо fallback Feedback
- [ ] 7.4 Добавить `coach_retry_max`, `coach_fallback_provider`, `coach_fallback_model` в Config, CLI, env
- [ ] 7.5 Добавить coach retry loop + fallback escalation в coach_player.py
- [ ] 7.6 Добавить UI функции в streaming.py
- [ ] 7.7 Обновить тесты feedback + coach_player
