# G3 Execution Optimization Ideas

## Проблема: 136 шагов = 136 вызовов API

**Симптом:** Каждое действие агента — отдельный API-вызов к Sonnet. При сложной задаче получаем 136+ вызовов.

**Корневая причина:**
1. Нет батчинга действий в фазы
2. Нет прогресс-таблицы в UI
3. Каждый tool call = отдельный turn

---

## Решение 1: Batched Execution (Фазы)

### Концепция

Вместо пошагового выполнения, группируем действия в **фазы**:

```
┌─────────────────────────────────────────────────────────────┐
│  Сейчас: 136 шагов × 1 API call = 136 calls               │
│                                                             │
│  После: 4 фазы × 1 API call = 4 calls (batched substeps)   │
└─────────────────────────────────────────────────────────────┘
```

### Архитектура фаз

```yaml
phases:
  - name: "Explore & Plan"
    actions:
      - glob: "**/*.py"           # batched: найти все Python файлы
      - grep: "class.*Provider"   # batched: найти все провайдеры
      - read: "config.py"        # batched: прочитать конфиг
    output: "plan.md"
    max_turns: 1                  # ВСЁ за один API call

  - name: "Implement"
    actions:
      - write: "providers/ccg.py"
      - write: "providers/claude_native.py"
      - write: "providers/__init__.py"
    max_turns: 1                  # batched writes

  - name: "Test & Validate"
    actions:
      - run: "pytest tests/"
      - run: "mypy src/"
    max_turns: 1

  - name: "Finalize"
    actions:
      - commit: "feat: multi-provider"
      - pr: "Multi-provider architecture"
    max_turns: 1
```

### Реализация: PhaseExecutor

```python
from dataclasses import dataclass
from typing import list


@dataclass
class Phase:
    name: str
    actions: list[dict]      # Список действий для batch-выполнения
    output: str = ""         # Ожидаемый артефакт
    max_turns: int = 1       # Все действия за N turns


@dataclass
class PhaseResult:
    phase: str
    status: str              # "success" | "partial" | "failed"
    actions_done: int
    actions_total: int
    output_path: str = ""
    error: str = ""


class PhaseExecutor:
    """Execute multiple actions in a single API call."""

    def __init__(self, provider, working_dir: str):
        self.provider = provider
        self.working_dir = working_dir

    async def execute_phase(self, phase: Phase) -> PhaseResult:
        """Execute all phase actions in ONE API call."""
        # Собираем все действия в один большой prompt
        prompt = self._build_batch_prompt(phase)

        # ОДИН вызов API с большим max_turns
        async for msg in self.provider.run(
            prompt=prompt,
            system_prompt=self._phase_system_prompt(phase),
            working_dir=self.working_dir,
            max_turns=phase.max_turns,
        ):
            yield msg

        # Парсим результат и проверяем, что все действия выполнены

    def _build_batch_prompt(self, phase: Phase) -> str:
        """Build a single prompt containing ALL actions."""
        actions_text = []
        for i, action in enumerate(phase.actions, 1):
            action_type = list(action.keys())[0]
            action_value = action[action_type]
            actions_text.append(f"{i}. [{action_type}] {action_value}")

        return f"""
Execute ALL of the following actions in this phase: {phase.name}

Actions to complete:
{chr(10).join(actions_text)}

Expected output: {phase.output or 'N/A'}

Complete all actions before responding.
"""

    def _phase_system_prompt(self, phase: Phase) -> str:
        return f"""You are in phase: {phase.name}

Execute ALL actions in sequence. Do not stop until all are complete.
Report progress after each action.

Phase context: {phase.output or 'General execution'}
"""
```

### Пример: 136 шагов → 4 фазы

**До (текущее):**
```
Action 1: glob "**/*.py"           → call 1
Action 2: read "config.py"        → call 2
Action 3: read "ccg.py"           → call 3
...
Action 136: commit "done"         → call 136

Total: 136 API calls
```

**После (batched):**
```
Phase 1: "Explore" (actions 1-30)   → call 1 (max_turns=5)
Phase 2: "Plan" (actions 31-50)    → call 2 (max_turns=3)
Phase 3: "Implement" (actions 51-120) → call 3 (max_turns=10)
Phase 4: "Validate" (actions 121-136) → call 4 (max_turns=5)

Total: 4 API calls
```

---

## Решение 2: Progress Table UI

### Проблема
Пользователь не видит прогресс. 136 шагов проходят "вслепую".

### Решение: Rich Progress Table

```python
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn


class ProgressTracker:
    """Show progress in terminal with Rich."""

    def __init__(self, phases: list[Phase]):
        self.phases = phases
        self.console = Console()
        self.current_phase = 0
        self.current_action = 0

    def show_overview(self):
        """Show phase overview table."""
        table = Table(title="G3 Execution Plan")
        table.add_column("Phase", style="cyan")
        table.add_column("Actions", justify="right")
        table.add_column("Status", style="green")

        for i, phase in enumerate(self.phases):
            status = "⏳ Pending"
            if i < self.current_phase:
                status = "✅ Done"
            elif i == self.current_phase:
                status = "🔄 In Progress"

            table.add_row(
                phase.name,
                str(len(phase.actions)),
                status,
            )

        self.console.print(table)

    def show_progress(self):
        """Show live progress with spinner."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
        ) as progress:
            for phase in self.phases:
                task = progress.add_task(
                    f"[cyan]{phase.name}[/cyan]",
                    total=len(phase.actions),
                )
                for action in phase.actions:
                    # Execute action...
                    progress.advance(task)
                    progress.update(
                        task,
                        description=f"[cyan]{phase.name}[/cyan] → {action}",
                    )

    def show_result(self, result: PhaseResult):
        """Show phase result."""
        if result.status == "success":
            self.console.print(f"[green]✅ {result.phase} complete[/green]")
        elif result.status == "partial":
            self.console.print(
                f"[yellow]⚠️ {result.phase} partial: "
                f"{result.actions_done}/{result.actions_total}[/yellow]"
            )
        else:
            self.console.print(f"[red]❌ {result.phase} failed: {result.error}[/red]")
```

### UI Example

```
╔═══════════════════════════════════════════════════════════════╗
║                    G3 Execution Plan                          ║
╠═══════════════════════════════════════════════════════════════╣
║  Phase              │  Actions  │  Status                     ║
╠═══════════════════════════════════════════════════════════════╣
║  Explore & Plan     │     30    │  ✅ Done                    ║
║  Implement          │     70    │  🔄 In Progress (23/70)     ║
║  Test & Validate    │     25    │  ⏳ Pending                 ║
║  Finalize           │     11    │  ⏳ Pending                 ║
╚═══════════════════════════════════════════════════════════════╝

🔄 Implement → write: providers/claude_native.py
   └─ Creating Claude Native provider...
```

---

## Решение 3: Action Batching в Prompt

### Концепция
Вместо того чтобы вызывать API для каждого действия, собираем **batch** действий в один prompt:

**Пример batch-prompt:**

```python
BATCH_PROMPT = """
Execute the following actions in sequence:

1. [GLOB] Find all Python files matching "providers/*.py"
2. [GREP] Search for "class.*Provider" in all files
3. [READ] Read the content of "config.py"
4. [READ] Read the content of "coach_player.py"
5. [ANALYZE] Based on readings, create a plan for multi-provider

After completing all actions, provide:
- Summary of findings
- The plan in markdown format

Do NOT stop after each action. Complete ALL actions before responding.
"""
```

### BatchActionBuilder

```python
class BatchActionBuilder:
    """Build batch prompts from action lists."""

    def __init__(self):
        self.actions = []

    def glob(self, pattern: str) -> "BatchActionBuilder":
        self.actions.append({"glob": pattern})
        return self

    def grep(self, pattern: str, path: str = "") -> "BatchActionBuilder":
        self.actions.append({"grep": pattern, "path": path})
        return self

    def read(self, file_path: str) -> "BatchActionBuilder":
        self.actions.append({"read": file_path})
        return self

    def write(self, file_path: str, content: str = "") -> "BatchActionBuilder":
        self.actions.append({"write": file_path, "content": content})
        return self

    def build_prompt(self) -> str:
        """Generate batch execution prompt."""
        lines = ["Execute the following actions in sequence:\n"]
        for i, action in enumerate(self.actions, 1):
            action_type = list(action.keys())[0]
            action_value = action[action_type]
            lines.append(f"{i}. [{action_type.upper()}] {action_value}")

        lines.append("\nComplete ALL actions before responding.")
        return "\n".join(lines)


# Usage:
batch = (
    BatchActionBuilder()
    .glob("providers/*.py")
    .grep("class.*Provider")
    .read("config.py")
    .read("coach_player.py")
)

prompt = batch.build_prompt()
# Single API call with all 4 actions
```

---

## Решение 4: Checkpoint-Based Execution

### Концепция
Сохранять состояние после каждой фазы, чтобы:
1. Не терять прогресс при ошибках
2. Продолжать с последней успешной фазы
3. Показывать пользователю точный статус

```python
@dataclass
class Checkpoint:
    phase: str
    action_index: int
    timestamp: datetime
    result: str  # "success" | "failed" | "partial"


class CheckpointManager:
    """Save/load execution state."""

    def __init__(self, checkpoint_dir: str = ".g3/checkpoints"):
        self.checkpoint_dir = checkpoint_dir

    def save(self, checkpoint: Checkpoint):
        path = f"{self.checkpoint_dir}/{checkpoint.phase}.json"
        with open(path, "w") as f:
            json.dump(asdict(checkpoint), f)

    def load(self, phase: str) -> Checkpoint | None:
        path = f"{self.checkpoint_dir}/{phase}.json"
        if os.path.exists(path):
            with open(path) as f:
                return Checkpoint(**json.load(f))
        return None

    def get_last_completed(self) -> str | None:
        """Get the last successfully completed phase."""
        checkpoints = sorted(
            glob(f"{self.checkpoint_dir}/*.json"),
            key=lambda p: os.path.getmtime(p),
            reverse=True,
        )
        for cp_path in checkpoints:
            with open(cp_path) as f:
                cp = json.load(f)
                if cp["result"] == "success":
                    return cp["phase"]
        return None
```

### Resume Flow

```python
async def run_with_resume(phases: list[Phase]):
    checkpoint_mgr = CheckpointManager()
    last_completed = checkpoint_mgr.get_last_completed()

    if last_completed:
        console.print(f"[yellow]Resuming from: {last_completed}[/yellow]")
        start_index = next(
            i for i, p in enumerate(phases) if p.name == last_completed
        ) + 1
    else:
        start_index = 0

    for phase in phases[start_index:]:
        result = await executor.execute_phase(phase)
        checkpoint_mgr.save(Checkpoint(
            phase=phase.name,
            action_index=len(phase.actions),
            timestamp=datetime.now(),
            result=result.status,
        ))
```

---

## Итоговая архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                    G3 Batched Executor                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Parse Plan → Phases (4-6 phases)                           │
│                    ↓                                             │
│  2. BatchActionBuilder → Single prompt per phase               │
│                    ↓                                             │
│  3. PhaseExecutor → ONE API call per phase                     │
│                    ↓                                             │
│  4. ProgressTracker → Rich UI table                            │
│                    ↓                                             │
│  5. CheckpointManager → Resume on failure                      │
│                                                                 │
│  Result: 136 steps → 4-6 API calls (95% reduction!)            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Метрики успеха

| Метрика | До | После |
|---------|-----|-------|
| API calls для 136 шагов | 136 | 4-6 |
| Время выполнения | 136 × latency | 6 × latency |
| Потеря прогресса при ошибке | 100% | 0% (checkpoint) |
| Видимость прогресса | 0% | 100% (table) |
| Возможность resume | Нет | Да |

---

## Next Steps

1. [ ] Реализовать `Phase` и `PhaseExecutor`
2. [ ] Реализовать `BatchActionBuilder`
3. [ ] Реализовать `ProgressTracker` с Rich
4. [ ] Реализовать `CheckpointManager`
5. [ ] Интегрировать в `coach_player.py`
6. [ ] Добавить CLI флаг `--batch-mode`
