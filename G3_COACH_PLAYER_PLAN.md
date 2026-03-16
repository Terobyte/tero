# G3 Coach-Player — Полный план реализации

> Дата: 2026-03-14
> Статус: действующий
> Язык реализации: **Python 3.11+**
> Философия: система которая делает работу И учится делать её лучше

---

## 0. Суть системы за 30 секунд

Есть задача. Её выполняют **два независимых агента одновременно**.
Каждый работает в своей изоляции, не видит работу другого.
После — автоматически считаем баги в обоих результатах.
**Судья** смотрит на оба результата и выбирает лучший.
Победивший результат идёт в продакшн.

При этом **каждый прогон записывается**. Через 20 прогонов система начинает
понимать: "с codex на рефакторинге всегда меньше багов". Через 50 прогонов —
предлагает правильную конфигурацию автоматически.

Через 100 прогонов — применяет свой же пайплайн к своему коду.

---

## 1. Почему это работает лучше чем один агент

Один агент работает по одной траектории. Если она неоптимальная — результат плохой.
Два агента работают по разным траекториям одновременно. Вероятность что хотя бы
одна из них хорошая — выше.

Судья устраняет субъективность: он видит оба результата, оба теста, оба diff-а
и выбирает по критериям, а не по ощущению.

Метрика багов устраняет иллюзию успеха: не "кажется готово", а "0 тестов упало,
0 типовых ошибок, линтер чист".

---

## 2. Три слоя системы

```text
┌────────────────────────────────────────────────────────┐
│  СЛОЙ 3: Learning Layer                                │
│  knowledge base, bug metrics, recommendations         │
├────────────────────────────────────────────────────────┤
│  СЛОЙ 2: Orchestration (G3)                            │
│  state machine, duel runner, judge, promote           │
├────────────────────────────────────────────────────────┤
│  СЛОЙ 1: Provider Layer                                │
│  ccg, ccg2, codex, claude — независимые launcher-ы    │
└────────────────────────────────────────────────────────┘
```

**Важно**: эти слои не смешиваются.

- `ccg` и `ccg2` — просто команды. Они не знают про G3.
- G3 — оркестратор. Он вызывает команды как subprocess.
- Learning layer — наблюдатель. Он читает результаты и накапливает знания.

---

## 3. Целевая архитектура

```text
                  один файл с ТЗ
                        │
                        ▼
           ┌────────────────────────┐
           │     Orchestrator G3    │
           │  (state machine loop)  │
           └───────────┬────────────┘
                       │
           ┌───────────┼────────────┐
           │                       │
           ▼                       ▼
 ┌─────────────────┐     ┌─────────────────┐
 │   Agent A       │     │   Agent B       │
 │   ccg           │     │   ccg2          │
 │   isolированный │     │   изолированный │
 │   workspace     │     │   workspace     │
 └────────┬────────┘     └────────┬────────┘
          │                       │
          └───────────┬───────────┘
                      │
                      ▼
          ┌─────────────────────┐
          │  Bug Detection      │
          │  Pipeline           │
          │  (оба агента)       │
          └──────────┬──────────┘
                     │
                     ▼
          ┌─────────────────────┐
          │  Judge Stage        │
          │  single / panel     │
          └──────────┬──────────┘
                     │
                     ▼
          ┌─────────────────────┐
          │  Selection          │
          │  best / synthesize  │
          └──────────┬──────────┘
                     │
                     ▼
          ┌─────────────────────┐
          │  Promote Winner     │
          └──────────┬──────────┘
                     │
                     ▼
          ┌─────────────────────┐
          │  Learning Record    │
          │  runs.jsonl update  │
          │  insights rebuild   │
          └─────────────────────┘
```

---

## 4. Главные решения архитектуры

| Решение | Обоснование |
|---|---|
| 2 агента параллельно, а не 3 подряд | Меньше времени, больше разнообразия |
| Баги как основная метрика | Объективно, автоматически, не зависит от мнения |
| Learning встроен с первого прогона | Нельзя добавить learning потом без потери данных |
| Провайдеры как отдельные команды | Независимость, можно добавлять без изменений G3 |
| State machine с write-ahead | Resume после любого сбоя |
| Knowledge base в JSONL | Простой формат, грепается, не теряется |

---

## 5. Структура проекта

```text
g3-coach-player/
├── g3.py                         # CLI entrypoint
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── orchestrator.py           # главный цикл
│   ├── duel.py                   # параллельный запуск агентов
│   ├── state.py                  # state machine + persistence
│   ├── config.py                 # config resolution chain
│   ├── worktree.py               # workspace isolation
│   ├── selection.py              # выбор победителя
│   ├── tests_runner.py           # запуск тестов проекта
│   ├── bug_detector.py           # Bug Detection Pipeline
│   ├── judge.py                  # judge runner
│   ├── learning/
│   │   ├── __init__.py
│   │   ├── recorder.py           # запись run records
│   │   ├── analyzer.py           # анализ runs.jsonl → insights
│   │   ├── recommender.py        # рекомендации перед запуском
│   │   ├── calibrator.py         # калибровка весов
│   │   └── classifier.py         # классификация задач
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── claude_glm.py
│   │   ├── claude_code.py
│   │   └── codex.py
│   ├── prompts/
│   │   ├── agent.py
│   │   ├── judge.py
│   │   └── synthesis.py
│   ├── parsers/
│   │   ├── output.py
│   │   └── verdict.py
│   └── terminal/
│       ├── console.py
│       ├── live_display.py
│       └── commands.py
├── tests/
│   ├── test_provider_registry.py
│   ├── test_claude_glm_provider.py
│   ├── test_worktree_manager.py
│   ├── test_duel_runner.py
│   ├── test_bug_detector.py
│   ├── test_judge_selection.py
│   ├── test_state_manager.py
│   ├── test_verdict_parser.py
│   ├── test_learning_recorder.py
│   ├── test_learning_analyzer.py
│   ├── test_learning_recommender.py
│   ├── test_cli_go_command.py
│   └── e2e/
│       ├── test_dual_agent_happy_path.py
│       ├── test_dual_agent_retry_path.py
│       ├── test_learning_accumulation.py
│       └── test_self_improvement.py
├── templates/
│   ├── agent_prompt.md
│   ├── judge_prompt.md
│   └── synthesis_prompt.md
└── .g3/
    ├── config.yaml
    ├── knowledge/
    │   ├── runs.jsonl
    │   ├── insights.yaml
    │   ├── overrides.yaml
    │   └── weight_history.jsonl
    └── sessions/
        └── <session_id>/
            ├── session.json
            ├── session.log
            ├── agent_a/
            ├── agent_b/
            ├── judge/
            ├── synthesis/
            └── promote/
```

---

## 6. Phase 0 — Launcher Layer: `ccg` и `ccg2`

### Зачем это отдельная фаза

Без рабочих launcher-ов всё остальное — мёртвый код.
Нельзя тестировать оркестратор если нет команд для запуска агентов.
Phase 0 — это фундамент. Пока он не готов — следующие фазы не начинаются.

### Цель Phase 0

Два самостоятельных launcher-а:

- `ccg` — account A (BlackboxAI)
- `ccg2` — account B (BlackboxAI)

Каждый должен:

- запускаться вручную из терминала
- запускаться из subprocess без `shell=True`
- иметь **отдельный** `CLAUDE_HOME` и auth state
- работать одновременно с другим без конфликтов

### Шаг 0.1 — Создать launcher `ccg`

**Файл:** `~/.local/bin/ccg`

```bash
#!/bin/bash
set -euo pipefail

# Account A — BlackboxAI
export ANTHROPIC_BASE_URL="https://api.blackbox.ai"
export ANTHROPIC_AUTH_TOKEN="${BLACKBOX_ACCOUNT_A_TOKEN:?Set BLACKBOX_ACCOUNT_A_TOKEN in env}"
export ANTHROPIC_MODEL="blackboxai/z-ai/glm-5"
export ANTHROPIC_SMALL_FAST_MODEL="kimi-k2.5"
export CLAUDE_HOME="${HOME}/.claude-glm-a"

mkdir -p "${CLAUDE_HOME}"
exec claude "$@"
```

```bash
chmod +x ~/.local/bin/ccg
```

**Проверка:**
```bash
ccg -p "say hello"
# Должен ответить. Если нет — проверь BLACKBOX_ACCOUNT_A_TOKEN в env.
```

### Шаг 0.2 — Создать launcher `ccg2`

**Файл:** `~/.local/bin/ccg2`

```bash
#!/bin/bash
set -euo pipefail

# Account B — отдельный токен, отдельный CLAUDE_HOME
export ANTHROPIC_BASE_URL="https://api.blackbox.ai"
export ANTHROPIC_AUTH_TOKEN="${BLACKBOX_ACCOUNT_B_TOKEN:?Set BLACKBOX_ACCOUNT_B_TOKEN in env}"
export ANTHROPIC_MODEL="blackboxai/z-ai/glm-5"
export ANTHROPIC_SMALL_FAST_MODEL="kimi-k2.5"
export CLAUDE_HOME="${HOME}/.claude-glm-b"

mkdir -p "${CLAUDE_HOME}"
exec claude "$@"
```

```bash
chmod +x ~/.local/bin/ccg2
```

### Шаг 0.3 — Добавить токены в env

```bash
# ~/.zshrc или ~/.bashrc
export BLACKBOX_ACCOUNT_A_TOKEN="sk-..."
export BLACKBOX_ACCOUNT_B_TOKEN="sk-..."
```

**Никогда не хардкодить токены в launcher-файлах.**

### Шаг 0.4 — Проверить изоляцию

Открыть два терминала:

```bash
# Терминал 1
ccg -p "what is your account label"

# Терминал 2
ccg2 -p "what is your account label"
```

Убедиться что:
1. Оба отвечают независимо
2. У них разные session state (смотреть `~/.claude-glm-a` vs `~/.claude-glm-b`)
3. Запуск одного не влияет на другой

### Acceptance Criteria Phase 0

| Критерий | Проверка |
|---|---|
| `ccg -p "ping"` отвечает | `ccg -p "say pong"` |
| `ccg2 -p "ping"` отвечает | `ccg2 -p "say pong"` |
| Оба работают одновременно | запустить в двух терминалах |
| Разные `CLAUDE_HOME` | `echo $CLAUDE_HOME` в каждом launcher |
| Subprocess работает | `python -c "import subprocess; r=subprocess.run(['ccg','-p','ping'],capture_output=True); print(r.stdout)"` |

---

## 7. Phase 1 — Provider Abstraction + Config

### Цель

Оркестратор не должен знать детали запуска каждого провайдера.
Он просто вызывает `provider.run(prompt, workspace)`.

Провайдеры конфигурируются в YAML, а не хардкодятся.
Один адаптер `ClaudeGlmProvider` обслуживает и `ccg` и `ccg2`.

### Шаг 1.1 — `src/config.py`

```python
"""Загрузка и merge конфигурации: defaults → user → project → env → CLI."""

import os
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class ProviderConfig:
    name: str
    type: str
    command: str
    claude_home: str | None = None
    account_label: str | None = None
    default_timeout: int = 600


@dataclass
class ResolvedConfig:
    max_rounds: int = 3
    autonomous: bool = False
    run_tests: bool = True
    judge_mode: str = "single"
    selection: str = "best"
    agent_a: str = "ccg"
    agent_b: str = "ccg2"
    judge: str = "ccg"
    judge_2: str | None = None
    worktree_mode: str = "auto"
    verbose: bool = False
    dry_run: bool = False
    working_dir: str = "."
    plan_file: str = ""
    run_bug_detection: bool = True
    ask_feedback: bool = True


def load_yaml(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def load_providers(raw: dict) -> dict[str, ProviderConfig]:
    result = {}
    for name, cfg in raw.get("providers", {}).items():
        result[name] = ProviderConfig(
            name=name,
            type=cfg["type"],
            command=cfg["command"],
            claude_home=cfg.get("claude_home"),
            account_label=cfg.get("account_label"),
            default_timeout=cfg.get("default_timeout", 600),
        )
    return result


def resolve_config(cli_args: dict) -> tuple[ResolvedConfig, dict[str, ProviderConfig]]:
    """Merge всех слоёв. Возвращает конфиг и провайдеры."""
    user_raw = load_yaml(Path.home() / ".config" / "g3" / "config.yaml")
    project_raw = load_yaml(Path(".g3") / "config.yaml")

    # Провайдеры: user < project (project переопределяет)
    providers = {**load_providers(user_raw), **load_providers(project_raw)}

    # Defaults: user < project < env < CLI
    defaults = {
        "max_rounds": 3, "autonomous": False, "run_tests": True,
        "judge_mode": "single", "selection": "best",
        "agent_a": "ccg", "agent_b": "ccg2", "judge": "ccg",
        "run_bug_detection": True, "ask_feedback": True,
    }

    for layer in [user_raw.get("defaults", {}), project_raw.get("defaults", {})]:
        defaults.update({k: v for k, v in layer.items() if v is not None})

    env_map = {
        "G3_MAX_ROUNDS": ("max_rounds", int),
        "G3_DEFAULT_JUDGE": ("judge", str),
        "G3_AUTONOMOUS": ("autonomous", lambda x: x.lower() == "true"),
    }
    for env_key, (cfg_key, conv) in env_map.items():
        if val := os.environ.get(env_key):
            defaults[cfg_key] = conv(val)

    defaults.update({k: v for k, v in cli_args.items() if v is not None})
    valid_fields = ResolvedConfig.__dataclass_fields__
    cfg = ResolvedConfig(**{k: v for k, v in defaults.items() if k in valid_fields})
    return cfg, providers
```

### Шаг 1.2 — `src/providers/base.py`

```python
"""Контракт для всех провайдеров."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    files_changed: list[str] = field(default_factory=list)
    summary: str = ""
    risks: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def health_check(self) -> bool:
        """Быстрая проверка без реального API-вызова."""
        ...

    @abstractmethod
    def run(
        self,
        prompt: str,
        working_dir: str,
        autonomous: bool = False,
        timeout_s: int = 600,
        env_overrides: dict[str, str] | None = None,
    ) -> AgentResult: ...

    def estimate_cost(self, prompt_tokens: int) -> float:
        return 0.0

    def supports_streaming(self) -> bool:
        return False
```

### Шаг 1.3 — `src/providers/claude_glm.py`

```python
"""Провайдер для ccg / ccg2 (BlackboxAI через claude launcher)."""

import os
import shutil
import subprocess
import time

from src.config import ProviderConfig
from src.providers.base import BaseProvider, AgentResult


class ClaudeGlmProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self.config = config

    def name(self) -> str:
        return self.config.name

    def health_check(self) -> bool:
        # 1. Бинарник доступен?
        if not shutil.which(self.config.command):
            return False
        # 2. CLAUDE_HOME существует или создаётся
        if self.config.claude_home:
            home = os.path.expanduser(self.config.claude_home)
            os.makedirs(home, exist_ok=True)
        return True

    def run(
        self,
        prompt: str,
        working_dir: str,
        autonomous: bool = False,
        timeout_s: int = 600,
        env_overrides: dict[str, str] | None = None,
    ) -> AgentResult:
        cmd = [self.config.command, "-p"]
        if autonomous:
            cmd.append("--dangerously-skip-permissions")
        cmd.append(prompt)

        env = os.environ.copy()
        if self.config.claude_home:
            env["CLAUDE_HOME"] = os.path.expanduser(self.config.claude_home)
        if env_overrides:
            env.update(env_overrides)

        started = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=working_dir, env=env,
                capture_output=True, text=True, timeout=timeout_s,
            )
            return AgentResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_s=round(time.time() - started, 2),
                files_changed=self._parse_section(proc.stdout, "FILES_CHANGED"),
                summary=self._parse_section_text(proc.stdout, "SUMMARY"),
                risks=self._parse_section_text(proc.stdout, "RISKS"),
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                success=False, exit_code=-1, stdout="",
                stderr=f"Timeout after {timeout_s}s",
                duration_s=float(timeout_s),
            )

    def estimate_cost(self, prompt_tokens: int) -> float:
        return prompt_tokens * 0.00001

    def _parse_section(self, out: str, marker: str) -> list[str]:
        m = f"--- {marker} ---"
        if m not in out:
            return []
        return [l.strip() for l in out.split(m)[1].split("---")[0].splitlines() if l.strip()]

    def _parse_section_text(self, out: str, marker: str) -> str:
        m = f"--- {marker} ---"
        if m not in out:
            return out[-2000:] if len(out) > 2000 else out
        return out.split(m)[1].split("---")[0].strip()
```

### Шаг 1.4 — `src/providers/registry.py`

```python
"""Registry: имя провайдера → инстанс."""

from src.config import ProviderConfig
from src.providers.base import BaseProvider
from src.providers.claude_glm import ClaudeGlmProvider


PROVIDER_TYPES: dict[str, type[BaseProvider]] = {
    "claude_glm": ClaudeGlmProvider,
    "claude_code": ClaudeGlmProvider,  # тот же интерфейс
}


class ProviderRegistry:
    def __init__(self, configs: dict[str, ProviderConfig]):
        self._configs = configs
        self._cache: dict[str, BaseProvider] = {}

    def get(self, name: str) -> BaseProvider:
        if name not in self._cache:
            cfg = self._configs.get(name)
            if not cfg:
                raise KeyError(f"Provider '{name}' not found. Available: {list(self._configs)}")
            cls = PROVIDER_TYPES.get(cfg.type)
            if not cls:
                raise ValueError(f"Unknown provider type '{cfg.type}'")
            self._cache[name] = cls(cfg)
        return self._cache[name]

    def health_check_all(self) -> dict[str, bool]:
        return {name: self.get(name).health_check() for name in self._configs}
```

### Шаг 1.5 — `.g3/config.yaml`

```yaml
defaults:
  max_rounds: 3
  autonomous: false
  run_tests: true
  run_bug_detection: true
  ask_feedback: true

providers:
  ccg:
    type: claude_glm
    command: ccg
    claude_home: "~/.claude-glm-a"
    account_label: "blackbox-a"

  ccg2:
    type: claude_glm
    command: ccg2
    claude_home: "~/.claude-glm-b"
    account_label: "blackbox-b"

  codex:
    type: codex
    command: codex
    default_timeout: 900

  claude:
    type: claude_code
    command: claude
    default_timeout: 600

# Presets для быстрого запуска
presets:
  fast:
    agent_a: ccg
    agent_b: ccg2
    judge: ccg
    max_rounds: 1
    run_tests: false
  thorough:
    agent_a: ccg
    agent_b: codex
    judge: ccg2
    max_rounds: 5
    selection: synthesize
  panel:
    agent_a: ccg
    agent_b: ccg2
    judge: ccg
    judge_2: codex
    judge_mode: panel
```

### Acceptance Criteria Phase 1

```bash
python -c "
from src.config import resolve_config
cfg, providers = resolve_config({})
print(cfg)
print(providers)
"
# Должен напечатать ResolvedConfig и словарь провайдеров

python -m pytest tests/test_provider_registry.py -v
# Все тесты зелёные
```

---

## 8. Phase 2 — Worktree Isolation

### Зачем это критично

Если два агента работают в одной директории — они будут перезаписывать файлы друг друга.
Это не просто race condition — это гарантированная катастрофа.

**Правило**: каждый агент работает в своём изолированном workspace. Без исключений.

### Приоритет методов изоляции

1. **Git worktree** — предпочтительно. Быстро, нет дублирования файлов, git знает о ветке.
2. **Filesystem copy** — fallback. Медленнее, больше диска, но работает без git.
3. **In-place (отключено)** — запрещено для dual-agent mode.

### Шаг 2.1 — `src/worktree.py`

```python
"""Изоляция workspace для каждого агента."""

import os
import shutil
import subprocess
from pathlib import Path


class WorktreeManager:
    def __init__(self, session_dir: str, source_dir: str, mode: str = "auto"):
        self.session_dir = session_dir
        self.source_dir = source_dir
        self.mode = mode
        self._used: set[str] = set()

    def create(self, agent_name: str) -> str:
        """Создать изолированный workspace. Возвращает абсолютный путь."""
        if agent_name in self._used:
            raise ValueError(f"Workspace for '{agent_name}' already created in this session")
        self._used.add(agent_name)

        ws = os.path.join(self.session_dir, agent_name)
        if os.path.exists(ws):
            shutil.rmtree(ws)

        if self.mode == "git" or (self.mode == "auto" and self._is_git()):
            try:
                return self._create_git_worktree(agent_name, ws)
            except subprocess.CalledProcessError:
                return self._create_copy(ws)
        return self._create_copy(ws)

    def get_diff(self, agent_name: str) -> str:
        ws = os.path.join(self.session_dir, agent_name)
        if self._is_git() and os.path.isdir(os.path.join(ws, ".git")):
            r = subprocess.run(["git", "diff", "HEAD"], cwd=ws, capture_output=True, text=True)
            return r.stdout
        r = subprocess.run(["diff", "-ruN", "--exclude=.git",
                            self.source_dir, ws], capture_output=True, text=True)
        return r.stdout

    def cleanup(self, agent_name: str):
        ws = os.path.join(self.session_dir, agent_name)
        if self._is_git():
            subprocess.run(["git", "worktree", "remove", ws, "--force"],
                           cwd=self.source_dir, capture_output=True)
            branch = f"g3/{os.path.basename(self.session_dir)}/{agent_name}"
            subprocess.run(["git", "branch", "-D", branch],
                           cwd=self.source_dir, capture_output=True)
        elif os.path.exists(ws):
            shutil.rmtree(ws, ignore_errors=True)

    def cleanup_all(self):
        for name in list(self._used) + ["synthesis"]:
            self.cleanup(name)

    def _is_git(self) -> bool:
        return os.path.isdir(os.path.join(self.source_dir, ".git"))

    def _create_git_worktree(self, agent_name: str, ws: str) -> str:
        session_id = os.path.basename(self.session_dir)
        branch = f"g3/{session_id}/{agent_name}"
        subprocess.run(["git", "branch", branch, "HEAD"],
                       cwd=self.source_dir, check=True, capture_output=True)
        subprocess.run(["git", "worktree", "add", ws, branch],
                       cwd=self.source_dir, check=True, capture_output=True)
        return ws

    def _create_copy(self, ws: str) -> str:
        shutil.copytree(
            self.source_dir, ws,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "node_modules", ".venv", "*.pyc", ".g3"
            ),
        )
        return ws
```

### Acceptance Criteria Phase 2

```bash
python -m pytest tests/test_worktree_manager.py -v
```

Проверить что:
- Два workspace создаются в разных путях
- Попытка создать второй workspace с тем же именем → ошибка
- `cleanup_all` удаляет оба workspace
- Fallback на copy-mode если не git repo

---

## 9. Phase 3 — Bug Detection Pipeline

### Зачем это самая важная фаза

Без Bug Detection у нас нет объективной метрики.
Judge мог бы просто гадать. Learning layer не знал бы что хорошо, а что плохо.

**Bug Detection — это объективный судья поверх человеческого судьи.**

Запускается после каждого агента, до judge-stage.
Результат попадает и в judge-prompt, и в run record.

### Что считается багом

| Источник | Баги |
|---|---|
| Компиляция / импорт | crash = +10 (критично) |
| Тесты (pytest/jest/etc) | каждый failed = +1 |
| Type checker (mypy/tsc) | каждый error = +1 |
| Linter (ruff/eslint) | каждый error = +1, warning = 0 |
| Review agent (опционально) | каждая найденная проблема = +1 |

**Warnings не считаются**. Только errors. Это важно — иначе любой проект
с legacy-кодом будет выглядеть плохо из-за старых предупреждений.

### Bug Score шкала

| Score | Статус | Реакция системы |
|---|---|---|
| 0 | Идеально | Сильный положительный сигнал для learning |
| 1-2 | Приемлемо | Записываем как "good" |
| 3-5 | Значительные проблемы | Записываем как "mediocre" |
| 6-9 | Серьёзные дефекты | Записываем как "poor" |
| 10+ / compile fail | Катастрофа | Считается failed, judge может авто-отклонить |

### Шаг 3.1 — `src/bug_detector.py`

```python
"""Bug Detection Pipeline — объективная метрика качества работы агента."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BugReport:
    compile_bugs: int = 0
    test_bugs: int = 0
    type_bugs: int = 0
    lint_bugs: int = 0
    review_bugs: int = 0
    total: int = 0
    details: list[str] = field(default_factory=list)
    status: str = "ok"       # ok, mediocre, poor, failed
    ran_stages: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.total = (
            self.compile_bugs + self.test_bugs +
            self.type_bugs + self.lint_bugs + self.review_bugs
        )
        if self.compile_bugs >= 10:
            self.status = "failed"
        elif self.total >= 10:
            self.status = "failed"
        elif self.total >= 6:
            self.status = "poor"
        elif self.total >= 3:
            self.status = "mediocre"
        else:
            self.status = "ok"


class BugDetector:
    def __init__(
        self,
        run_tests: bool = True,
        run_types: bool = True,
        run_lint: bool = True,
        run_compile: bool = True,
    ):
        self.run_tests = run_tests
        self.run_types = run_types
        self.run_lint = run_lint
        self.run_compile = run_compile

    def run(self, workspace: str) -> BugReport:
        """Запустить все стадии обнаружения багов в workspace."""
        compile_bugs, compile_details = 0, []
        test_bugs, test_details = 0, []
        type_bugs, type_details = 0, []
        lint_bugs, lint_details = 0, []
        ran = []

        if self.run_compile:
            compile_bugs, compile_details = self._check_compile(workspace)
            ran.append("compile")
            # Если компиляция упала — остальное не запускаем
            if compile_bugs >= 10:
                return BugReport(
                    compile_bugs=compile_bugs,
                    details=compile_details,
                    ran_stages=ran,
                )

        if self.run_tests:
            test_bugs, test_details = self._check_tests(workspace)
            ran.append("tests")

        if self.run_types:
            type_bugs, type_details = self._check_types(workspace)
            ran.append("types")

        if self.run_lint:
            lint_bugs, lint_details = self._check_lint(workspace)
            ran.append("lint")

        return BugReport(
            compile_bugs=compile_bugs,
            test_bugs=test_bugs,
            type_bugs=type_bugs,
            lint_bugs=lint_bugs,
            details=compile_details + test_details + type_details + lint_details,
            ran_stages=ran,
        )

    def _check_compile(self, workspace: str) -> tuple[int, list[str]]:
        """Попытаться импортировать / скомпилировать проект."""
        ws = Path(workspace)

        # Python: попытка компиляции всех .py файлов
        if list(ws.rglob("*.py")):
            r = subprocess.run(
                ["python", "-m", "compileall", "-q", "."],
                cwd=workspace, capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                return 10, [f"Compile error: {r.stderr[:500]}"]

        # Node: попытка синтаксической проверки если есть package.json
        if (ws / "package.json").exists() and list(ws.rglob("*.ts")):
            r = subprocess.run(
                ["npx", "tsc", "--noEmit"],
                cwd=workspace, capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                errors = r.stdout.count("error TS")
                return min(errors, 10), [f"TypeScript error count: {errors}"]

        return 0, []

    def _check_tests(self, workspace: str) -> tuple[int, list[str]]:
        ws = Path(workspace)
        details = []

        # Python pytest
        if list(ws.rglob("test_*.py")) or list(ws.rglob("*_test.py")):
            r = subprocess.run(
                ["pytest", "-q", "--tb=no", "--no-header"],
                cwd=workspace, capture_output=True, text=True, timeout=120,
            )
            failures = self._count_pytest_failures(r.stdout)
            if failures > 0:
                details.append(f"pytest: {failures} failed")
            return failures, details

        # JavaScript jest
        if (ws / "package.json").exists():
            r = subprocess.run(
                ["npx", "jest", "--passWithNoTests", "--silent"],
                cwd=workspace, capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                failures = r.stdout.count("FAIL ")
                details.append(f"jest: {failures} failed")
                return failures, details

        return 0, []

    def _check_types(self, workspace: str) -> tuple[int, list[str]]:
        ws = Path(workspace)
        details = []

        if list(ws.rglob("*.py")) and self._has_cmd("mypy"):
            r = subprocess.run(
                ["mypy", ".", "--ignore-missing-imports", "--no-error-summary"],
                cwd=workspace, capture_output=True, text=True, timeout=60,
            )
            errors = r.stdout.count(": error:")
            if errors > 0:
                details.append(f"mypy: {errors} errors")
            return errors, details

        return 0, []

    def _check_lint(self, workspace: str) -> tuple[int, list[str]]:
        ws = Path(workspace)
        details = []

        if list(ws.rglob("*.py")) and self._has_cmd("ruff"):
            r = subprocess.run(
                ["ruff", "check", ".", "--quiet"],
                cwd=workspace, capture_output=True, text=True, timeout=30,
            )
            errors = len([l for l in r.stdout.splitlines() if l.strip() and not l.startswith("Found")])
            if errors > 0:
                details.append(f"ruff: {errors} errors")
            return errors, details

        return 0, []

    def _count_pytest_failures(self, output: str) -> int:
        for part in output.split():
            if "failed" in part:
                try:
                    return int(output.split("failed")[0].strip().split()[-1])
                except (ValueError, IndexError):
                    pass
        return 0

    def _has_cmd(self, cmd: str) -> bool:
        import shutil
        return shutil.which(cmd) is not None
```

### Acceptance Criteria Phase 3

```bash
python -m pytest tests/test_bug_detector.py -v
```

Проверить что:
- Компиляция Python файлов с синтаксической ошибкой → compile_bugs = 10
- Упавший тест → test_bugs = 1
- Чистый проект → total = 0, status = "ok"
- Если compile упал → type и lint не запускаются
- Корректный `status` для каждого диапазона

---

## 10. Phase 4 — Duel Runner

### Зачем async

Два агента работают одновременно. Если запускать их последовательно —
теряем половину смысла. Агент B должен работать пока работает агент A.

Используем `asyncio.to_thread()` — это запускает блокирующую функцию
(subprocess) в отдельном потоке, не блокируя event loop.

### Один раунд — полная механика

```text
1. Создать workspace A и workspace B
2. Запустить Agent A (в потоке)    ┐
3. Запустить Agent B (в потоке)    ├── параллельно
4. Дождаться обоих               ─┘
5. Запустить Bug Detection на A    ┐
6. Запустить Bug Detection на B    ├── параллельно
7. Дождаться обоих               ─┘
8. Извлечь diff A и diff B
9. Judge: сравнить A и B
10. Вернуть RoundResult
```

### Шаг 4.1 — `src/duel.py`

```python
"""Параллельный запуск двух агентов + Bug Detection + Judge."""

import asyncio
from dataclasses import dataclass

from src.providers.registry import ProviderRegistry
from src.worktree import WorktreeManager
from src.bug_detector import BugDetector, BugReport
from src.judge import JudgeRunner, JudgeDecision
from src.providers.base import AgentResult


@dataclass
class RoundResult:
    result_a: AgentResult
    result_b: AgentResult
    bugs_a: BugReport
    bugs_b: BugReport
    diff_a: str
    diff_b: str
    decision: JudgeDecision
    workspace_a: str
    workspace_b: str


class DuelRunner:
    def __init__(
        self,
        registry: ProviderRegistry,
        worktree: WorktreeManager,
        bug_detector: BugDetector,
        judge: JudgeRunner,
    ):
        self.registry = registry
        self.worktree = worktree
        self.bug_detector = bug_detector
        self.judge = judge

    async def run_round(
        self,
        task: str,
        agent_a_name: str,
        agent_b_name: str,
        autonomous: bool = False,
    ) -> RoundResult:
        # 1. Создаём изолированные workspace
        ws_a = self.worktree.create("agent_a")
        ws_b = self.worktree.create("agent_b")

        agent_a = self.registry.get(agent_a_name)
        agent_b = self.registry.get(agent_b_name)

        # 2. Параллельный запуск агентов
        result_a, result_b = await asyncio.gather(
            asyncio.to_thread(agent_a.run, task, ws_a, autonomous),
            asyncio.to_thread(agent_b.run, task, ws_b, autonomous),
        )

        # 3. Параллельный Bug Detection
        bugs_a, bugs_b = await asyncio.gather(
            asyncio.to_thread(self.bug_detector.run, ws_a),
            asyncio.to_thread(self.bug_detector.run, ws_b),
        )

        # 4. Diff extraction
        diff_a = self.worktree.get_diff("agent_a")
        diff_b = self.worktree.get_diff("agent_b")

        # 5. Judge
        decision = self.judge.compare(
            task=task,
            result_a=result_a, result_b=result_b,
            bugs_a=bugs_a, bugs_b=bugs_b,
            diff_a=diff_a, diff_b=diff_b,
        )

        return RoundResult(
            result_a=result_a, result_b=result_b,
            bugs_a=bugs_a, bugs_b=bugs_b,
            diff_a=diff_a, diff_b=diff_b,
            decision=decision,
            workspace_a=ws_a, workspace_b=ws_b,
        )
```

### Acceptance Criteria Phase 4

```bash
python -m pytest tests/test_duel_runner.py -v
```

Проверить что:
- Оба агента запускаются параллельно (mock providers с time.sleep)
- Bug Detection запускается для обоих workspace
- Если Agent A упал — RoundResult всё равно создаётся, decision = auto-win B
- judge.compare вызывается с корректными аргументами

---

## 11. Phase 5 — Judge Stage

### Роль судьи

Judge не просто выбирает победителя. Он:
1. Видит полный контекст: оба результата, оба bug score, оба diff
2. Оценивает по 6 критериям (1-10 каждый)
3. Принимает одно из 4 решений: `winner_a`, `winner_b`, `synthesize`, `retry`
4. Объясняет причину на 2-3 предложения

### Автоматические shortcuts (до вызова LLM)

| Ситуация | Решение | Вызов LLM? |
|---|---|---|
| A упал (exit≠0), B ок | `winner_b`, confidence=high | Нет |
| B упал, A ок | `winner_a`, confidence=high | Нет |
| Оба упали | `retry` | Нет |
| A bug_score≥10, B<3 | `winner_b`, confidence=high | Нет |
| Оба bug_score≥10 | `retry` | Нет |
| A и B идентичны (diff одинаковый) | `winner_a`, confidence=low | Нет |

Только если ни один shortcut не сработал — вызываем LLM judge.

### Шаг 5.1 — `src/judge.py`

```python
"""Judge: сравнивает результаты двух агентов и выносит verdict."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.providers.base import BaseProvider, AgentResult
from src.bug_detector import BugReport


@dataclass
class JudgeDecision:
    action: str           # winner_a, winner_b, synthesize, retry
    confidence: str       # high, medium, low
    reason: str = ""
    feedback: list[str] = field(default_factory=list)
    scores_a: dict = field(default_factory=dict)
    scores_b: dict = field(default_factory=dict)
    shortcut: bool = False  # True если решение принято без LLM


class JudgeRunner:
    def __init__(
        self,
        provider: BaseProvider,
        template_path: str = "templates/judge_prompt.md",
    ):
        self.provider = provider
        template = Path(template_path)
        self.template = template.read_text() if template.exists() else ""

    def compare(
        self,
        task: str,
        result_a: AgentResult,
        result_b: AgentResult,
        bugs_a: BugReport,
        bugs_b: BugReport,
        diff_a: str,
        diff_b: str,
    ) -> JudgeDecision:

        # Shortcuts — решаем без LLM
        if shortcut := self._check_shortcuts(result_a, result_b, bugs_a, bugs_b):
            return shortcut

        # Полный judge через LLM
        prompt = self._build_prompt(task, result_a, result_b, bugs_a, bugs_b, diff_a, diff_b)
        judge_output = self.provider.run(prompt, ".", timeout_s=300)
        return self._parse_verdict(judge_output.stdout)

    def _check_shortcuts(
        self,
        ra: AgentResult, rb: AgentResult,
        ba: BugReport, bb: BugReport,
    ) -> JudgeDecision | None:
        if not ra.success and rb.success:
            return JudgeDecision("winner_b", "high", "Agent A failed, Agent B succeeded", shortcut=True)
        if ra.success and not rb.success:
            return JudgeDecision("winner_a", "high", "Agent B failed, Agent A succeeded", shortcut=True)
        if not ra.success and not rb.success:
            return JudgeDecision("retry", "high", "Both agents failed", shortcut=True)
        if ba.total >= 10 and bb.total < 3:
            return JudgeDecision("winner_b", "high",
                f"Agent A catastrophic bugs ({ba.total}), Agent B clean ({bb.total})", shortcut=True)
        if bb.total >= 10 and ba.total < 3:
            return JudgeDecision("winner_a", "high",
                f"Agent B catastrophic bugs ({bb.total}), Agent A clean ({ba.total})", shortcut=True)
        if ba.total >= 10 and bb.total >= 10:
            return JudgeDecision("retry", "high",
                f"Both agents have catastrophic bugs (A={ba.total}, B={bb.total})", shortcut=True)
        return None

    def _build_prompt(self, task, ra, rb, ba, bb, da, db) -> str:
        return f"""You are a code review judge. Two agents independently implemented the same task.
Compare their work and make a decision.

## Original Task
{task}

## Agent A
Summary: {ra.summary}
Files changed: {ra.files_changed}
Bug score: {ba.total} (compile={ba.compile_bugs}, tests={ba.test_bugs}, types={ba.type_bugs}, lint={ba.lint_bugs})
Bug status: {ba.status}
Diff:
```diff
{da[:3000]}
```
Risks: {ra.risks}

## Agent B
Summary: {rb.summary}
Files changed: {rb.files_changed}
Bug score: {bb.total} (compile={bb.compile_bugs}, tests={bb.test_bugs}, types={bb.type_bugs}, lint={bb.lint_bugs})
Bug status: {bb.status}
Diff:
```diff
{db[:3000]}
```
Risks: {rb.risks}

## Scoring Criteria (1-10 each)
1. Correctness — matches requirements
2. Test Coverage — tests pass, new tests added
3. Completeness — all requirements addressed
4. Code Quality — readable, idiomatic
5. Risk Level — regression risk (10=very risky, 1=safe)
6. Diff Quality — minimal and focused

## Decision Rules
- One failed, other succeeded → winner is the successful one
- Both failed → retry
- Both passed, one clearly better → winner_a or winner_b
- Both have complementary strengths → synthesize

## Output Format — ONLY valid JSON, no other text:
{{"scores":{{"agent_a":{{"correctness":0,"test_coverage":0,"completeness":0,"code_quality":0,"risk_level":0,"diff_quality":0,"total":0}},"agent_b":{{"correctness":0,"test_coverage":0,"completeness":0,"code_quality":0,"risk_level":0,"diff_quality":0,"total":0}}}},"action":"winner_a|winner_b|synthesize|retry","confidence":"high|medium|low","reason":"2-3 sentences","feedback":[]}}"""

    def _parse_verdict(self, raw: str) -> JudgeDecision:
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            data = json.loads(raw[start:end])
            scores = data.get("scores", {})
            return JudgeDecision(
                action=data.get("action", "retry"),
                confidence=data.get("confidence", "low"),
                reason=data.get("reason", ""),
                feedback=data.get("feedback", []),
                scores_a=scores.get("agent_a", {}),
                scores_b=scores.get("agent_b", {}),
            )
        except (ValueError, json.JSONDecodeError):
            return JudgeDecision(
                "retry", "low",
                f"Could not parse judge output: {raw[:300]}",
            )
```

### Judge prompt template — `templates/judge_prompt.md`

Описан в `src/judge.py` выше. Шаблон встроен в код для простоты.
Для кастомизации — положить `templates/judge_prompt.md` и он будет использован вместо дефолтного.

### Acceptance Criteria Phase 5

```bash
python -m pytest tests/test_judge_selection.py -v
```

Проверить:
- Shortcut: A упал → winner_b без LLM вызова
- Shortcut: Оба упали → retry без LLM вызова
- LLM вызов: нормальный JSON → корректный JudgeDecision
- LLM вызов: мусорный output → retry с fallback
- LLM вызов: JSON внутри текста → корректно парсится

---

## 12. Phase 6 — Learning System

### Принцип

С первого прогона система собирает данные. Данные накапливаются.
Система учится — сначала на человеческом feedback, потом на корреляции метрик.

Это не отдельная фаза которую "добавим потом". Это инфраструктура которая
должна работать с прогона №1, иначе данные будут потеряны.

### Архитектура Learning Layer

```text
.g3/knowledge/
├── runs.jsonl          ← append-only, одна строка = один прогон
├── insights.yaml       ← авто-генерируется после каждого прогона
├── overrides.yaml      ← human-set правила (не перезаписываются авто)
└── weight_history.jsonl ← история калибровки весов
```

### Структура Run Record

```json
{
  "run_id": "run_042",
  "session_id": "sess_20260314_153000",
  "timestamp": "2026-03-14T15:30:00Z",
  "task": {
    "file": "./requirements.md",
    "type": "feature",
    "complexity": "medium",
    "word_count": 340
  },
  "config": {
    "agent_a": "ccg",
    "agent_b": "ccg2",
    "judge": "codex",
    "selection": "best",
    "timeout_s": 600,
    "autonomous": true
  },
  "results": {
    "agent_a": {
      "success": true,
      "bug_score": 2,
      "bugs_by_stage": {"compile": 0, "tests": 1, "types": 1, "lint": 0},
      "duration_s": 180,
      "files_changed": 4
    },
    "agent_b": {
      "success": true,
      "bug_score": 0,
      "bugs_by_stage": {"compile": 0, "tests": 0, "types": 0, "lint": 0},
      "duration_s": 240,
      "files_changed": 3
    }
  },
  "judge_verdict": {
    "winner": "agent_b",
    "action": "winner_b",
    "confidence": "high",
    "shortcut": false
  },
  "outcome": {
    "rounds_used": 1,
    "total_duration_s": 520,
    "final_winner": "agent_b",
    "promoted": true
  },
  "human_feedback": {
    "rating": "approve",
    "notes": "",
    "timestamp": "2026-03-14T15:40:00Z"
  },
  "quality_score": 0.87
}
```

### Формула Quality Score

```
quality_score =
  weights.bug_score  × normalize(10 - min(bug_winner, 10), 0, 10)
+ weights.test_pass  × (tests_passed / tests_total if tests_total > 0 else 1.0)
+ weights.duration   × normalize(max_time - actual_time, 0, max_time)
+ weights.retry      × normalize(max_rounds - rounds_used, 0, max_rounds)
+ weights.human      × human_factor
```

Где:
- `normalize(v, min, max)` → 0.0..1.0
- `human_factor`: approve=1.0, partial=0.5, reject=0.0, skip=auto_score
- `max_time` = configured timeout
- `max_rounds` = max_rounds from config

### Дефолтные веса (обоснование)

| Вес | Значение | Обоснование |
|---|---|---|
| `bug_score: 0.50` | Главный | Меньше багов = лучше работа |
| `test_pass: 0.20` | Важный | Тесты — доказательство корректности |
| `duration: 0.10` | Вторичный | Скорость важна, но не главное |
| `retry: 0.10` | Вторичный | Меньше retry = эффективнее |
| `human: 0.10` | Коррекция | Человек может исправить автоматику |

### Шаг 6.1 — `src/learning/recorder.py`

```python
"""Запись run records в runs.jsonl."""

import json
import os
from datetime import datetime
from pathlib import Path

from src.providers.base import AgentResult
from src.bug_detector import BugReport
from src.judge import JudgeDecision


class RunRecorder:
    def __init__(self, knowledge_dir: str = ".g3/knowledge"):
        self.knowledge_dir = Path(knowledge_dir)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.runs_file = self.knowledge_dir / "runs.jsonl"
        self._run_counter = self._count_existing_runs()

    def record(
        self,
        session_id: str,
        task_file: str,
        task_type: str,
        task_complexity: str,
        config: dict,
        result_a: AgentResult,
        result_b: AgentResult,
        bugs_a: BugReport,
        bugs_b: BugReport,
        decision: JudgeDecision,
        rounds_used: int,
        total_duration_s: float,
        weights: dict,
    ) -> str:
        self._run_counter += 1
        run_id = f"run_{self._run_counter:04d}"

        winner = "agent_b" if decision.action == "winner_b" else "agent_a"
        winner_bugs = bugs_b if decision.action == "winner_b" else bugs_a

        quality = self._compute_quality(
            winner_bugs=winner_bugs,
            rounds_used=rounds_used,
            max_rounds=config.get("max_rounds", 3),
            duration_s=total_duration_s,
            timeout_s=config.get("timeout_s", 600),
            weights=weights,
        )

        record = {
            "run_id": run_id,
            "session_id": session_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "task": {
                "file": task_file,
                "type": task_type,
                "complexity": task_complexity,
                "word_count": self._word_count(task_file),
            },
            "config": config,
            "results": {
                "agent_a": {
                    "success": result_a.success,
                    "bug_score": bugs_a.total,
                    "bugs_by_stage": {
                        "compile": bugs_a.compile_bugs,
                        "tests": bugs_a.test_bugs,
                        "types": bugs_a.type_bugs,
                        "lint": bugs_a.lint_bugs,
                    },
                    "duration_s": result_a.duration_s,
                    "files_changed": len(result_a.files_changed),
                },
                "agent_b": {
                    "success": result_b.success,
                    "bug_score": bugs_b.total,
                    "bugs_by_stage": {
                        "compile": bugs_b.compile_bugs,
                        "tests": bugs_b.test_bugs,
                        "types": bugs_b.type_bugs,
                        "lint": bugs_b.lint_bugs,
                    },
                    "duration_s": result_b.duration_s,
                    "files_changed": len(result_b.files_changed),
                },
            },
            "judge_verdict": {
                "winner": winner,
                "action": decision.action,
                "confidence": decision.confidence,
                "shortcut": decision.shortcut,
            },
            "outcome": {
                "rounds_used": rounds_used,
                "total_duration_s": total_duration_s,
                "final_winner": winner,
                "promoted": True,
            },
            "human_feedback": {
                "rating": None,
                "notes": "",
                "timestamp": None,
            },
            "quality_score": quality,
        }

        with open(self.runs_file, "a") as f:
            f.write(json.dumps(record) + "\n")

        return run_id

    def update_feedback(self, run_id: str, rating: str, notes: str = ""):
        """Обновить human feedback для прогона."""
        lines = []
        updated = False

        if self.runs_file.exists():
            for line in self.runs_file.read_text().splitlines():
                record = json.loads(line)
                if record["run_id"] == run_id:
                    record["human_feedback"] = {
                        "rating": rating,
                        "notes": notes,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                    updated = True
                lines.append(json.dumps(record))

        if updated:
            self.runs_file.write_text("\n".join(lines) + "\n")

    def load_all(self) -> list[dict]:
        if not self.runs_file.exists():
            return []
        return [json.loads(l) for l in self.runs_file.read_text().splitlines() if l.strip()]

    def _compute_quality(
        self, winner_bugs, rounds_used, max_rounds, duration_s, timeout_s, weights
    ) -> float:
        bug_factor = max(0.0, (10 - min(winner_bugs.total, 10)) / 10)
        duration_factor = max(0.0, (timeout_s - min(duration_s, timeout_s)) / timeout_s)
        retry_factor = max(0.0, (max_rounds - rounds_used) / max_rounds)

        return round(
            weights.get("bug_score", 0.50) * bug_factor +
            weights.get("duration", 0.10) * duration_factor +
            weights.get("retry", 0.10) * retry_factor,
            3,
        )

    def _word_count(self, file_path: str) -> int:
        try:
            return len(Path(file_path).read_text().split())
        except Exception:
            return 0

    def _count_existing_runs(self) -> int:
        if not self.runs_file.exists():
            return 0
        return sum(1 for l in self.runs_file.read_text().splitlines() if l.strip())
```

### Шаг 6.2 — `src/learning/classifier.py`

```python
"""Классификация задачи по типу и сложности."""

from dataclasses import dataclass
from pathlib import Path


TASK_KEYWORDS: dict[str, list[str]] = {
    "bugfix":   ["fix", "bug", "error", "crash", "broken", "issue", "patch", "resolve"],
    "refactor": ["refactor", "clean", "reorganize", "simplify", "restructure", "extract"],
    "test":     ["test", "coverage", "spec", "assertion", "mock", "fixture"],
    "docs":     ["document", "readme", "docstring", "comment", "docs", "wiki"],
    "feature":  ["add", "implement", "create", "new", "build", "develop", "integrate"],
}

COMPLEXITY_THRESHOLDS = {"low": 50, "medium": 200}


@dataclass
class TaskClassification:
    type: str          # feature, bugfix, refactor, test, docs
    complexity: str    # low, medium, high
    word_count: int
    confidence: float  # 0.0-1.0, насколько уверены в классификации


def classify_task(plan_file: str) -> TaskClassification:
    try:
        text = Path(plan_file).read_text().lower()
    except Exception:
        return TaskClassification("feature", "medium", 0, 0.0)

    words = text.split()
    word_count = len(words)

    scores: dict[str, int] = {t: 0 for t in TASK_KEYWORDS}
    for word in words:
        for task_type, keywords in TASK_KEYWORDS.items():
            if word in keywords:
                scores[task_type] += 1

    best_type = max(scores, key=scores.get)  # type: ignore
    total_matches = sum(scores.values())
    confidence = scores[best_type] / max(total_matches, 1)

    if word_count < COMPLEXITY_THRESHOLDS["low"]:
        complexity = "low"
    elif word_count < COMPLEXITY_THRESHOLDS["medium"]:
        complexity = "medium"
    else:
        complexity = "high"

    return TaskClassification(
        type=best_type,
        complexity=complexity,
        word_count=word_count,
        confidence=round(confidence, 2),
    )
```

### Шаг 6.3 — `src/learning/analyzer.py`

```python
"""Анализ runs.jsonl → insights.yaml."""

import yaml
from collections import defaultdict
from pathlib import Path


DEFAULT_WEIGHTS = {
    "bug_score": 0.50,
    "test_pass": 0.20,
    "duration": 0.10,
    "retry": 0.10,
    "human": 0.10,
}


class InsightsAnalyzer:
    def __init__(self, knowledge_dir: str = ".g3/knowledge"):
        self.knowledge_dir = Path(knowledge_dir)

    def rebuild(self, runs: list[dict]) -> dict:
        """Перестроить insights из всех run records."""
        if not runs:
            return {"total_runs": 0, "message": "No runs yet"}

        insights = {
            "total_runs": len(runs),
            "agent_pairs": self._analyze_pairs(runs),
            "judges": self._analyze_judges(runs),
            "task_types": self._analyze_task_types(runs),
            "timeout_insights": self._analyze_timeouts(runs),
            "calibrated_weights": self._calibrate_weights(runs),
        }

        out_file = self.knowledge_dir / "insights.yaml"
        with open(out_file, "w") as f:
            yaml.dump(insights, f, default_flow_style=False, allow_unicode=True)

        return insights

    def _analyze_pairs(self, runs: list[dict]) -> dict:
        pairs: dict = defaultdict(lambda: {"runs": 0, "total_bugs": 0, "approves": 0, "task_types": defaultdict(int)})

        for r in runs:
            pair = f"{r['config']['agent_a']}+{r['config']['agent_b']}"
            winner = r["judge_verdict"]["winner"]
            winner_bugs = r["results"][winner]["bug_score"]

            pairs[pair]["runs"] += 1
            pairs[pair]["total_bugs"] += winner_bugs

            rating = r.get("human_feedback", {}).get("rating")
            if rating == "approve":
                pairs[pair]["approves"] += 1

            task_type = r.get("task", {}).get("type", "unknown")
            pairs[pair]["task_types"][task_type] += 1

        result = {}
        for pair, stats in pairs.items():
            n = stats["runs"]
            result[pair] = {
                "runs": n,
                "avg_bug_score": round(stats["total_bugs"] / n, 2),
                "approve_rate": round(stats["approves"] / n, 2) if n > 0 else None,
                "best_for": [t for t, c in stats["task_types"].items() if c == max(stats["task_types"].values())],
            }
        return result

    def _analyze_judges(self, runs: list[dict]) -> dict:
        judges: dict = defaultdict(lambda: {"runs": 0, "shortcuts": 0})

        for r in runs:
            j = r["config"]["judge"]
            judges[j]["runs"] += 1
            if r["judge_verdict"].get("shortcut"):
                judges[j]["shortcuts"] += 1

        return {j: dict(s) for j, s in judges.items()}

    def _analyze_task_types(self, runs: list[dict]) -> dict:
        types: dict = defaultdict(lambda: {"runs": 0, "pairs": defaultdict(int), "total_bugs": 0})

        for r in runs:
            t = r.get("task", {}).get("type", "unknown")
            pair = f"{r['config']['agent_a']}+{r['config']['agent_b']}"
            winner = r["judge_verdict"]["winner"]
            winner_bugs = r["results"][winner]["bug_score"]

            types[t]["runs"] += 1
            types[t]["pairs"][pair] += 1
            types[t]["total_bugs"] += winner_bugs

        result = {}
        for t, stats in types.items():
            n = stats["runs"]
            best_pair = max(stats["pairs"], key=stats["pairs"].get)  # type: ignore
            result[t] = {
                "runs": n,
                "best_pair": best_pair,
                "avg_bug_score": round(stats["total_bugs"] / n, 2),
            }
        return result

    def _analyze_timeouts(self, runs: list[dict]) -> list[str]:
        insights = []
        low_timeout = [r for r in runs if r["config"].get("timeout_s", 600) < 300]
        high_complexity = [r for r in low_timeout
                           if r.get("task", {}).get("complexity") == "high"]
        if len(high_complexity) >= 3:
            failures = sum(1 for r in high_complexity if not r["results"]["agent_a"]["success"])
            rate = round(failures / len(high_complexity) * 100)
            insights.append(f"timeout<300 on high complexity: {rate}% failure rate ({len(high_complexity)} runs)")
        return insights

    def _calibrate_weights(self, runs: list[dict]) -> dict:
        with_feedback = [r for r in runs if r.get("human_feedback", {}).get("rating") not in (None, "skip")]

        if len(with_feedback) < 20:
            return {**DEFAULT_WEIGHTS, "calibration_confidence": "low", "note": f"Need 20+ runs, have {len(with_feedback)}"}

        # Простая корреляция bug_score → approve
        approves = [1.0 if r["human_feedback"]["rating"] == "approve" else 0.0 for r in with_feedback]
        avg_approve = sum(approves) / len(approves)

        # Чем меньше bagов у победителя — тем выше approve?
        bug_scores = []
        for r in with_feedback:
            winner = r["judge_verdict"]["winner"]
            bug_scores.append(r["results"][winner]["bug_score"])

        # Нормализация: низкий bug score = хорошо
        correlation = self._simple_correlation(
            [max(0.0, (10 - min(b, 10)) / 10) for b in bug_scores],
            approves,
        )

        # Обновляем вес bug_score пропорционально корреляции
        adjusted_bug_weight = max(0.30, min(0.60, 0.50 + (correlation - 0.5) * 0.2))

        weights = dict(DEFAULT_WEIGHTS)
        weights["bug_score"] = round(adjusted_bug_weight, 3)
        weights["calibration_confidence"] = "medium" if len(with_feedback) < 50 else "high"
        return weights

    def _simple_correlation(self, xs: list[float], ys: list[float]) -> float:
        n = len(xs)
        if n < 2:
            return 0.5
        mx, my = sum(xs) / n, sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
        return round(num / den, 3) if den > 0 else 0.0
```

### Шаг 6.4 — `src/learning/recommender.py`

```python
"""Рекомендации конфигурации перед запуском."""

import yaml
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Recommendation:
    agent_a: str | None = None
    agent_b: str | None = None
    judge: str | None = None
    max_rounds: int | None = None
    confidence: str = "none"    # none, low, medium, high
    supporting_data: list[str] = None  # type: ignore
    warnings: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.supporting_data is None:
            self.supporting_data = []
        if self.warnings is None:
            self.warnings = []


class ConfigRecommender:
    def __init__(self, knowledge_dir: str = ".g3/knowledge"):
        self.knowledge_dir = Path(knowledge_dir)

    def recommend(self, task_type: str, task_complexity: str) -> Recommendation:
        insights_file = self.knowledge_dir / "insights.yaml"
        if not insights_file.exists():
            return Recommendation(confidence="none", supporting_data=["No data yet"])

        with open(insights_file) as f:
            insights = yaml.safe_load(f) or {}

        total_runs = insights.get("total_runs", 0)
        if total_runs < 5:
            return Recommendation(
                confidence="none",
                supporting_data=[f"Only {total_runs} runs so far. Need 5+ for recommendations."],
            )

        rec = Recommendation()
        rec.supporting_data = []
        rec.warnings = []

        # Найти лучшую пару для этого типа задачи
        task_types = insights.get("task_types", {})
        if task_type in task_types:
            best_pair = task_types[task_type].get("best_pair")
            avg_bugs = task_types[task_type].get("avg_bug_score", "?")
            if best_pair:
                parts = best_pair.split("+")
                if len(parts) == 2:
                    rec.agent_a, rec.agent_b = parts
                    rec.supporting_data.append(
                        f"{best_pair} → avg {avg_bugs} bugs on {task_type} tasks "
                        f"({task_types[task_type].get('runs', 0)} runs)"
                    )

        # Найти лучшего judge
        judges = insights.get("judges", {})
        if judges:
            best_judge = max(judges.items(), key=lambda x: x[1].get("runs", 0))[0]
            rec.judge = best_judge

        # Предупреждения по таймаутам
        for warning in insights.get("timeout_insights", []):
            if task_complexity == "high" and "high complexity" in warning:
                rec.warnings.append(f"⚠️  {warning}")

        # Уровень уверенности
        type_runs = task_types.get(task_type, {}).get("runs", 0)
        if type_runs >= 20:
            rec.confidence = "high"
        elif type_runs >= 10:
            rec.confidence = "medium"
        elif type_runs >= 5:
            rec.confidence = "low"
        else:
            rec.confidence = "none"

        return rec
```

### Human Feedback CLI Flow

После каждого прогона система спрашивает:

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Session complete. Bug score winner: 0 (clean)
  How was the result?

  [A] Approve   — good result, I'll use it
  [R] Reject    — bad result, needs redo
  [P] Partial   — some parts ok, some not
  [S] Skip      — no opinion
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Ввод сохраняется в run record. Опционально — добавить текстовые заметки.

### Pre-Run Recommendation Display

```text
╔══════════════════════════════════════════════════════════════╗
║  G3 Pre-Run Analysis                                        ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Task: ./req.md  │  Type: refactor  │  Complexity: medium   ║
║                                                              ║
║  📊 Based on 15 similar runs:                                ║
║                                                              ║
║  RECOMMENDED:                                                ║
║    Agent A: ccg                                              ║
║    Agent B: codex   ← best for refactoring                   ║
║    Judge:   ccg2                                             ║
║                                                              ║
║  ⚠️  ccg+ccg2 on refactoring: avg 3.1 bugs (8 runs)         ║
║                                                              ║
║  Confidence: MEDIUM (15 similar runs)                        ║
║                                                              ║
║  [Enter] Accept recommended  [O] Use my config               ║
╚══════════════════════════════════════════════════════════════╝
```

### Acceptance Criteria Phase 6

```bash
python -m pytest tests/test_learning_recorder.py tests/test_learning_analyzer.py tests/test_learning_recommender.py -v
```

Проверить что:
- `RunRecorder.record()` пишет в `runs.jsonl`
- `update_feedback()` обновляет существующую запись без дублирования
- `InsightsAnalyzer.rebuild()` генерирует корректный insights.yaml
- При < 5 прогонах → recommender возвращает confidence="none"
- При >= 20 прогонах с feedback → калибровка весов срабатывает

---

## 13. Phase 7 — State Machine + Resume

### Зачем state machine

Каждый прогон занимает 5-20 минут. Сбой на любом этапе без состояния = начинай заново.
State machine решает это: каждый шаг записывает состояние на диск **до** выполнения действия.
`/resume` читает последнее состояние и продолжает с точки сбоя.

### Состояния

```python
from enum import Enum

class SessionState(Enum):
    CREATED                = "created"
    PREPARING_WORKSPACES   = "preparing_workspaces"
    AGENTS_RUNNING         = "agents_running"
    TESTS_RUNNING          = "tests_running"
    BUG_DETECTION          = "bug_detection"
    JUDGING                = "judging"
    WINNER_SELECTED        = "winner_selected"
    SYNTHESIZING           = "synthesizing"
    SYNTHESIS_COMPLETE     = "synthesis_complete"
    PROMOTING              = "promoting"
    COMPLETED              = "completed"
    RETRY                  = "retry"
    ROUND_FAILED           = "round_failed"
    JUDGE_FAILED           = "judge_failed"
    PROMOTE_FAILED         = "promote_failed"
    STOPPED                = "stopped"
    FAILED                 = "failed"
```

### Допустимые переходы

```python
TRANSITIONS: dict[SessionState, list[SessionState]] = {
    SessionState.CREATED: [
        SessionState.PREPARING_WORKSPACES
    ],
    SessionState.PREPARING_WORKSPACES: [
        SessionState.AGENTS_RUNNING,
        SessionState.FAILED,
    ],
    SessionState.AGENTS_RUNNING: [
        SessionState.BUG_DETECTION,
        SessionState.ROUND_FAILED,
        SessionState.STOPPED,
    ],
    SessionState.BUG_DETECTION: [
        SessionState.JUDGING,
        SessionState.ROUND_FAILED,
    ],
    SessionState.JUDGING: [
        SessionState.WINNER_SELECTED,
        SessionState.SYNTHESIZING,
        SessionState.RETRY,
        SessionState.JUDGE_FAILED,
    ],
    SessionState.WINNER_SELECTED: [SessionState.PROMOTING],
    SessionState.SYNTHESIZING: [
        SessionState.SYNTHESIS_COMPLETE,
        SessionState.ROUND_FAILED,
    ],
    SessionState.SYNTHESIS_COMPLETE: [SessionState.PROMOTING],
    SessionState.PROMOTING: [
        SessionState.COMPLETED,
        SessionState.PROMOTE_FAILED,
    ],
    SessionState.RETRY: [
        SessionState.PREPARING_WORKSPACES,
        SessionState.FAILED,
    ],
    SessionState.ROUND_FAILED: [SessionState.RETRY, SessionState.FAILED],
    SessionState.JUDGE_FAILED: [SessionState.JUDGING, SessionState.FAILED],
    SessionState.PROMOTE_FAILED: [SessionState.PROMOTING, SessionState.FAILED],
}
```

Переход в недопустимое состояние → `InvalidTransitionError`.

### `src/state.py`

```python
"""State machine с write-ahead persistence."""

import json
import os
from datetime import datetime
from pathlib import Path
from enum import Enum


class SessionState(Enum):
    CREATED             = "created"
    PREPARING_WORKSPACES = "preparing_workspaces"
    AGENTS_RUNNING      = "agents_running"
    BUG_DETECTION       = "bug_detection"
    JUDGING             = "judging"
    WINNER_SELECTED     = "winner_selected"
    SYNTHESIZING        = "synthesizing"
    SYNTHESIS_COMPLETE  = "synthesis_complete"
    PROMOTING           = "promoting"
    COMPLETED           = "completed"
    RETRY               = "retry"
    ROUND_FAILED        = "round_failed"
    JUDGE_FAILED        = "judge_failed"
    PROMOTE_FAILED      = "promote_failed"
    STOPPED             = "stopped"
    FAILED              = "failed"


TRANSITIONS: dict[SessionState, list[SessionState]] = {
    SessionState.CREATED: [SessionState.PREPARING_WORKSPACES],
    SessionState.PREPARING_WORKSPACES: [SessionState.AGENTS_RUNNING, SessionState.FAILED],
    SessionState.AGENTS_RUNNING: [SessionState.BUG_DETECTION, SessionState.ROUND_FAILED, SessionState.STOPPED],
    SessionState.BUG_DETECTION: [SessionState.JUDGING, SessionState.ROUND_FAILED],
    SessionState.JUDGING: [SessionState.WINNER_SELECTED, SessionState.SYNTHESIZING, SessionState.RETRY, SessionState.JUDGE_FAILED],
    SessionState.WINNER_SELECTED: [SessionState.PROMOTING],
    SessionState.SYNTHESIZING: [SessionState.SYNTHESIS_COMPLETE, SessionState.ROUND_FAILED],
    SessionState.SYNTHESIS_COMPLETE: [SessionState.PROMOTING],
    SessionState.PROMOTING: [SessionState.COMPLETED, SessionState.PROMOTE_FAILED],
    SessionState.RETRY: [SessionState.PREPARING_WORKSPACES, SessionState.FAILED],
    SessionState.ROUND_FAILED: [SessionState.RETRY, SessionState.FAILED],
    SessionState.JUDGE_FAILED: [SessionState.JUDGING, SessionState.FAILED],
    SessionState.PROMOTE_FAILED: [SessionState.PROMOTING, SessionState.FAILED],
}

TERMINAL_STATES = {SessionState.COMPLETED, SessionState.STOPPED, SessionState.FAILED}


class InvalidTransitionError(Exception):
    pass


class SessionManager:
    def __init__(self, session_dir: str):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.session_dir / "session.json"
        self.log_file = self.session_dir / "session.log"
        self._state: dict = {}

    def create(self, session_id: str, config: dict) -> dict:
        self._state = {
            "session_id": session_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "state": SessionState.CREATED.value,
            "current_round": 0,
            "config": config,
            "rounds": [],
            "final_winner": None,
            "run_id": None,
        }
        self._save()
        self._log(f"SESSION CREATED {session_id}")
        return self._state

    def transition(self, new_state: SessionState, payload: dict | None = None) -> dict:
        current = SessionState(self._state["state"])
        allowed = TRANSITIONS.get(current, [])

        if new_state not in allowed:
            raise InvalidTransitionError(
                f"Cannot go from {current.value} to {new_state.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )

        self._state["state"] = new_state.value
        if payload:
            self._state.update(payload)

        self._save()
        self._log(f"STATE → {new_state.value}" + (f" {payload}" if payload else ""))
        return self._state

    def load(self) -> dict:
        if self.state_file.exists():
            self._state = json.loads(self.state_file.read_text())
        return self._state

    @classmethod
    def find_resumable(cls, sessions_root: str) -> list[dict]:
        """Найти все незавершённые сессии."""
        root = Path(sessions_root)
        resumable = []
        for session_dir in sorted(root.iterdir(), reverse=True):
            state_file = session_dir / "session.json"
            if state_file.exists():
                data = json.loads(state_file.read_text())
                if data.get("state") not in [s.value for s in TERMINAL_STATES]:
                    resumable.append(data)
        return resumable

    def _save(self):
        self.state_file.write_text(json.dumps(self._state, indent=2))

    def _log(self, message: str):
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_file, "a") as f:
            f.write(f"[{ts}] {message}\n")
```

---

## 14. Phase 8 — CLI + Terminal UI

### Команды

| Команда | Назначение |
|---|---|
| `g3 go --plan FILE` | Запустить новую duel-сессию |
| `g3 resume` | Продолжить последнюю незавершённую сессию |
| `g3 status` | Показать текущее состояние |
| `g3 stop` | Остановить текущую сессию |
| `g3 history` | История сессий |
| `g3 inspect SESSION_ID` | Детали конкретной сессии |
| `g3 insights` | Показать knowledge base |
| `g3 feedback SESSION_ID` | Добавить feedback к прошлой сессии |
| `g3 recommend --plan FILE` | Показать рекомендацию без запуска |

### Флаги `go`

| Флаг | Тип | Default |
|---|---|---|
| `--plan` | PATH | required |
| `--working-dir` | PATH | `.` |
| `--max-rounds` | INT | 3 |
| `--agent-a` | CHOICE | `ccg` |
| `--agent-b` | CHOICE | `ccg2` |
| `--judge` | CHOICE | `ccg` |
| `--selection` | CHOICE | `best` |
| `--autonomous` | FLAG | off |
| `--tests` | FLAG | off |
| `--bug-detection` | FLAG | on |
| `--no-feedback` | FLAG | off |
| `--preset` | NAME | — |
| `--verbose` | FLAG | off |
| `--dry-run` | FLAG | off |

### `g3.py` — entrypoint

```python
#!/usr/bin/env python3
"""G3 Coach-Player: dual-agent orchestration CLI."""

import argparse
import sys
from src.config import resolve_config
from src.orchestrator import Orchestrator


def cmd_go(args):
    cfg, providers = resolve_config(vars(args))
    if cfg.dry_run:
        print(f"DRY RUN")
        print(f"  agents  : {cfg.agent_a} vs {cfg.agent_b}")
        print(f"  judge   : {cfg.judge}")
        print(f"  plan    : {cfg.plan_file}")
        print(f"  rounds  : {cfg.max_rounds}")
        print(f"  bugs    : {'on' if cfg.run_bug_detection else 'off'}")
        return
    orch = Orchestrator(cfg, providers)
    orch.run()


def cmd_resume(args):
    from src.state import SessionManager
    resumable = SessionManager.find_resumable(".g3/sessions")
    if not resumable:
        print("No resumable sessions found.")
        return
    session_data = resumable[0]
    print(f"Resuming session: {session_data['session_id']}")
    cfg, providers = resolve_config(session_data["config"])
    orch = Orchestrator(cfg, providers, session_id=session_data["session_id"])
    orch.resume()


def cmd_insights(args):
    from src.learning.recorder import RunRecorder
    from src.learning.analyzer import InsightsAnalyzer
    recorder = RunRecorder()
    runs = recorder.load_all()
    analyzer = InsightsAnalyzer()
    insights = analyzer.rebuild(runs)
    print(f"\nTotal runs: {insights.get('total_runs', 0)}")
    pairs = insights.get("agent_pairs", {})
    if pairs:
        print("\nAgent Pairs:")
        for pair, stats in pairs.items():
            print(f"  {pair}: {stats.get('runs')} runs, avg bugs={stats.get('avg_bug_score')}, approve={stats.get('approve_rate')}")


def main():
    parser = argparse.ArgumentParser(prog="g3", description="G3 Coach-Player")
    sub = parser.add_subparsers(dest="command")

    go = sub.add_parser("go", aliases=["/go"])
    go.add_argument("--plan", required=True, dest="plan_file")
    go.add_argument("--working-dir", default=".", dest="working_dir")
    go.add_argument("--max-rounds", type=int, default=None, dest="max_rounds")
    go.add_argument("--agent-a", default=None, dest="agent_a")
    go.add_argument("--agent-b", default=None, dest="agent_b")
    go.add_argument("--judge", default=None)
    go.add_argument("--judge-2", default=None, dest="judge_2")
    go.add_argument("--judge-mode", default=None, dest="judge_mode")
    go.add_argument("--selection", default=None)
    go.add_argument("--autonomous", action="store_true", default=None)
    go.add_argument("--tests", action="store_true", dest="run_tests", default=None)
    go.add_argument("--no-bug-detection", action="store_false", dest="run_bug_detection")
    go.add_argument("--no-feedback", action="store_false", dest="ask_feedback")
    go.add_argument("--preset", default=None)
    go.add_argument("--verbose", action="store_true", default=None)
    go.add_argument("--dry-run", action="store_true", dest="dry_run")
    go.set_defaults(func=cmd_go)

    resume = sub.add_parser("resume", aliases=["/resume"])
    resume.add_argument("--session", default=None)
    resume.set_defaults(func=cmd_resume)

    insights = sub.add_parser("insights", aliases=["/insights"])
    insights.set_defaults(func=cmd_insights)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
```

### Terminal Output — как выглядит прогон

**Старт:**
```text
╔══════════════════════════════════════════════════╗
║  G3 Coach-Player v0.1                           ║
╚══════════════════════════════════════════════════╝

  Task:    ./req.md  (refactor, medium complexity)
  Agents:  ccg (A) vs codex (B)
  Judge:   ccg2
  Rounds:  max 3

  📊 Recommendation: ccg+codex (confidence: medium)
     Based on 12 similar refactor runs, avg 0.9 bugs.

  Starting round 1/3...
```

**Во время работы:**
```text
Round 1/3
──────────────────────────────────────────────────
  Agent A (ccg)    ⠋ running...  2m 14s
  Agent B (codex)  ⠙ running...  2m 14s
──────────────────────────────────────────────────
```

**После агентов:**
```text
Round 1/3 — Complete
──────────────────────────────────────────────────
  Agent A (ccg)    ✅ done  3m12s   5 files
  Agent B (codex)  ✅ done  4m45s   3 files

  Bug Detection:
  Agent A: bugs=2 (tests=1, types=1)
  Agent B: bugs=0  ✨ clean
──────────────────────────────────────────────────
```

**Judge verdict:**
```text
⚖️  Judge: Agent B wins (confidence: high)
   "Agent B provides cleaner implementation with
    better error handling and no type errors."
```

**Promote + Learning:**
```text
📦 Promoting Agent B...
   Copied: src/auth.py
   Copied: tests/test_auth.py

✅ Done! Session sess_001 complete.
   Winner: Agent B (codex)   Bug score: 0
   Duration: 8m 22s   Rounds used: 1/3

  How was the result? [A]pprove [R]eject [P]artial [S]kip: _
```

**После feedback:**
```text
  ✓ Feedback recorded. Learning updated.
  📈 codex on refactoring: 9/9 approves now.
```

---

## 15. Phase 9 — Panel Mode (dual-judge)

### Когда нужен panel

Single judge достаточен для большинства задач.
Panel нужен когда:
- задача критическая, нужно второе мнение
- конфигурация судьи под сомнением
- нужна высокая уверенность перед promote

### Механика panel

1. `judge_1` анализирует оба результата → verdict
2. `judge_2` анализирует оба результата → verdict (независимо)
3. Если они согласны → принять решение
4. Если не согласны → запустить `resolver`

```text
judge_1 verdict: winner_b (high confidence)
judge_2 verdict: winner_a (medium confidence)
                    │
                    ▼
              Disagreement!
                    │
                    ▼
              Resolver:
              "judge_1 has higher confidence → winner_b"
```

### Resolver Rules

| Ситуация | Решение |
|---|---|
| judge_1 high, judge_2 low | Доверяем judge_1 |
| judge_1 low, judge_2 high | Доверяем judge_2 |
| Оба high, не согласны | synthesize (если scores близки), иначе retry |
| Оба low | retry |
| Один хочет synthesize, другой winner | synthesize |

### Acceptance Criteria Phase 9

```bash
python -m pytest tests/e2e/test_panel_mode.py -v
```

---

## 16. Phase 10 — Self-Improvement Mode

### Идея

G3 применяет свой собственный duel-pipeline к своему коду.
Вместо `working_dir = /path/to/user_project` используем `working_dir = /path/to/g3`.

Это буквально: два агента улучшают G3, bug detection проверяет сам G3,
judge выбирает лучшую версию G3.

### Команда

```bash
g3 self-improve --plan ./improvements/add_streaming_output.md
```

### Что это означает на практике

```text
1. User пишет ТЗ для улучшения G3
   (например: "добавь streaming output для агентов")

2. G3 запускает duel:
   Agent A: ccg,   working_dir = /path/g3, task = "add streaming"
   Agent B: codex, working_dir = /path/g3, task = "add streaming"

3. Bug Detection прогоняет тесты самого G3
   (python -m pytest tests/ в рабочем каталоге G3)

4. Judge выбирает лучшую версию G3

5. Winner promote → G3 обновляет свой код

6. Перезапуск G3 с новым кодом
```

### Защита от self-destruction

Self-improvement mode имеет дополнительные safeguards:

```python
SELF_IMPROVEMENT_SAFEGUARDS = {
    "require_tests_pass": True,        # нельзя promote если тесты упали
    "require_human_approve": True,     # всегда требовать human approve
    "backup_before_promote": True,     # копировать текущий код перед promote
    "min_test_coverage": 0.80,         # не ниже 80% coverage
    "max_files_changed": 20,           # не более 20 файлов за раз
}
```

### Learning об улучшениях G3

Каждый self-improvement прогон записывается в `runs.jsonl` с тегом:
```json
{"task": {"type": "self_improvement", "target": "g3"}}
```

Это позволяет видеть: какие агенты лучше улучшают сам G3.

---

## 17. Error Recovery Matrix

| # | Ситуация | Реакция | Авто? |
|---|---|---|---|
| 1 | Agent A timeout, B ок | A=failed, авто-победа B | Да |
| 2 | Оба timeout | ROUND_FAILED → retry с timeout×1.5 | Да |
| 3 | Agent crash (exit≠0) | Как timeout | Да |
| 4 | Bug detection упал | Продолжаем без bug score, warn | Да |
| 5 | Judge timeout | Retry judge до 2 раз, потом manual | Да (2x) |
| 6 | Judge невалидный JSON | Retry с подсказкой про JSON | Да (1x) |
| 7 | Synthesis ошибка | Fallback на best mode | Да |
| 8 | Git worktree fail | Fallback на copy-mode | Да |
| 9 | Promote merge conflict | PROMOTE_FAILED, ручное разрешение | Нет |
| 10 | Max rounds exceeded | FAILED, показать лучший результат | Да |
| 11 | Disk < 100MB | Отказ старта с диагностикой | Нет |
| 12 | Provider health fail | Ошибка с деталями | Нет |

### Retry Backoff Policy

```python
@dataclass
class RetryPolicy:
    max_agent_retries: int = 1
    max_judge_retries: int = 2
    max_rounds: int = 3
    timeout_multiplier: float = 1.5
    base_agent_timeout: int = 600
    base_judge_timeout: int = 300
```

---

## 18. Security Model

### Токены — только через env

```bash
# ~/.zshrc
export BLACKBOX_ACCOUNT_A_TOKEN="sk-..."
export BLACKBOX_ACCOUNT_B_TOKEN="sk-..."
```

Нельзя: хардкодить в launcher-файлах или config.yaml.

### Изоляция workspace

- Два агента — два разных пути. Никаких shared paths.
- Логи не содержат переменных окружения.
- Session state содержит пути и метрики, не исходный код.
- `runs.jsonl` содержит только конфиг и цифры, не дифы.

### Права файлов

```bash
chmod 700 ~/.local/bin/ccg
chmod 700 ~/.local/bin/ccg2
chmod 700 ~/.claude-glm-a
chmod 700 ~/.claude-glm-b
chmod 700 .g3/
```

---

## 19. Testing Strategy

### Unit Tests (каждый модуль)

| Файл теста | Что проверяет |
|---|---|
| `test_provider_registry.py` | Registry + health checks |
| `test_claude_glm_provider.py` | Command building, CLAUDE_HOME, timeout |
| `test_worktree_manager.py` | Isolation guard, git + copy fallback |
| `test_bug_detector.py` | Все 4 стадии, compile fail stopps rest |
| `test_duel_runner.py` | Параллельность, один упал = авто-победа |
| `test_judge_selection.py` | Shortcuts, LLM parse, fallback |
| `test_state_manager.py` | Transitions, write-ahead, resume |
| `test_verdict_parser.py` | JSON в тексте, невалидный JSON |
| `test_learning_recorder.py` | Append, update feedback |
| `test_learning_analyzer.py` | Insights rebuild, calibration |
| `test_learning_recommender.py` | Мало данных → confidence=none |
| `test_cli_go_command.py` | Флаги, defaults, dry-run |

### Integration / E2E Tests

| Файл теста | Сценарий |
|---|---|
| `e2e/test_dual_agent_happy_path.py` | Оба прошли, judge выбрал, promote |
| `e2e/test_dual_agent_retry_path.py` | Один упал, авто-победа второго |
| `e2e/test_learning_accumulation.py` | 5 прогонов → появляются insights |
| `e2e/test_panel_mode.py` | Два judge не согласны → resolver |
| `e2e/test_self_improvement.py` | G3 улучшает себя, safeguards срабатывают |

### Mock Provider для тестов

```bash
# /tmp/mock_agent.sh
#!/bin/bash
echo "--- SUMMARY ---"
echo "Task completed"
echo "--- FILES_CHANGED ---"
echo "src/feature.py"
echo "--- RISKS ---"
echo "None"
echo "--- END ---"
```

### Quality Gates

```bash
pytest tests/ -q                          # все unit тесты
pytest tests/e2e/ -q                      # e2e тесты
ruff check . --quiet                      # линтер
python -m compileall . -q                 # компиляция
python -m pytest --cov=src --cov-report=term-missing  # coverage
```

Минимальное покрытие: **80%** для `src/providers`, `src/learning`, `src/state`, `src/bug_detector`.

---

## 20. Build Guide — пошаговая инструкция

Выполнять строго по порядку. Каждый шаг зависит от предыдущего.

| Шаг | Что делать | Acceptance |
|---|---|---|
| 0.1 | Создать launcher `ccg` | `ccg -p "ping"` отвечает |
| 0.2 | Создать launcher `ccg2` | `ccg2 -p "ping"` отвечает |
| 0.3 | Проверить изоляцию | Оба работают одновременно |
| 1.1 | Написать `src/config.py` | `resolve_config({})` возвращает defaults |
| 1.2 | Написать `src/providers/base.py` | Компилируется без ошибок |
| 1.3 | Написать `src/providers/claude_glm.py` | Unit тесты зелёные |
| 1.4 | Написать `src/providers/registry.py` | `test_provider_registry.py` зелёный |
| 1.5 | Написать `.g3/config.yaml` | Config loading из файла работает |
| 2.1 | Написать `src/worktree.py` | `test_worktree_manager.py` зелёный |
| 3.1 | Написать `src/bug_detector.py` | `test_bug_detector.py` зелёный |
| 4.1 | Написать `src/duel.py` | `test_duel_runner.py` зелёный |
| 5.1 | Написать `src/judge.py` | `test_judge_selection.py` зелёный |
| 6.1 | Написать `src/learning/recorder.py` | `test_learning_recorder.py` зелёный |
| 6.2 | Написать `src/learning/classifier.py` | Классификация типа задачи работает |
| 6.3 | Написать `src/learning/analyzer.py` | `test_learning_analyzer.py` зелёный |
| 6.4 | Написать `src/learning/recommender.py` | `test_learning_recommender.py` зелёный |
| 7.1 | Написать `src/state.py` | `test_state_manager.py` зелёный |
| 8.1 | Написать `g3.py` + `src/orchestrator.py` | `g3 go --plan req.md --dry-run` работает |
| 8.2 | `pytest tests/ -q` | Все unit тесты зелёные |
| 8.3 | First real duel | `g3 go --plan ./req.md` завершается |
| 9.1 | Panel mode | `test_panel_mode.py` зелёный |
| 10.1 | Self-improvement | `g3 self-improve` работает |

---

## 21. Roadmap

| Фаза | Что | Зависимости |
|---|---|---|
| 0 | Launcher layer (ccg, ccg2) | Ничего |
| 1 | Provider abstraction + config | Phase 0 |
| 2 | Worktree isolation | Phase 1 |
| 3 | Bug Detection Pipeline | Phase 2 |
| 4 | Duel Runner | Phase 2, 3 |
| 5 | Judge Stage | Phase 4 |
| 6 | Learning System | Phase 5 |
| 7 | State Machine + Resume | Phase 4, 5, 6 |
| 8 | CLI + Terminal UI + Orchestrator | Phase 7 |
| 9 | Panel Mode | Phase 8 |
| 10 | Self-Improvement | Phase 8, 6 |
