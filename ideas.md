# G3 Ideas & Feature Roadmap

## 0. Multi-Model Configuration (G1/G2 Selection)

**Идея:** Выбор модели для каждой роли - Player и Judge.

**Доступные модели (blackboxai keys):**
- **G1** - `blackboxai/z-ai/glm-5` (или другой первичный ключ)
- **G2** - `blackboxai/z-ai/glm-5` (вторичный ключ)

**Конфигурация:**
```yaml
# .g3/config.yaml
models:
  player: g1  # или g2
  judge: g2   # или g1

# Или через CLI
g3 run --player=g1 --judge=g2
g3 run --player=g2 --judge=g1  # наоборот
```

**Пары для экспериментов:**
```
┌────────────────────────────────────────────────┐
│  Configuration         │ Player │ Judge │ Notes │
├────────────────────────────────────────────────┤
│  Balanced              │   G1   │   G2  │ Стандарт     │
│  Strong Player         │   G2   │   G1  │ Сложные задачи│
│  Strong Judge          │   G1   │   G2  │ Строгий review│
│  Self-Review (эксперимент) │ G1/G2 │ G1/G2 │ Та же модель │
└────────────────────────────────────────────────┘
```

**Зачем это нужно:**
1. **Cost optimization** - дешёвый Player, умный Judge
2. **Speed vs Quality** - быстрый Player, тщательный Judge
3. **A/B Testing** - сравнение моделей на тех же задачах
4. **Fallback** - если одна модель недоступна, switch на другую

**Реализация:**
```python
class ModelConfig:
    MODELS = {
        'g1': 'blackboxai/z-ai/glm-4',
        'g2': 'blackboxai/z-ai/glm-5',
    }

    def __init__(self, player: str = 'g1', judge: str = 'g2'):
        self.player_model = self.MODELS[player]
        self.judge_model = self.MODELS[judge]

    def get_client(self, role: str) -> LLMClient:
        model = self.player_model if role == 'player' else self.judge_model
        return LLMClient(model=model)
```

**UI выбора при старте:**
```
🎮 G3 Session Setup

Select Player model:
  [1] G1 (glm-4) - faster, cheaper
  [2] G2 (glm-5) - smarter, slower  [default]

Select Judge model:
  [1] G1 (glm-4) - basic review
  [2] G2 (glm-5) - deep analysis  [default]

→ Player: G2, Judge: G2
```

### 🔧 Runtime Model Switching (NEW!)

**Проблема:** Нужно менять модель Player/Coach "на лету" без перезапуска сессии.

**Решение:** Команда `/switch` или горячая клавиша для мгновенной смены модели.

**UI во время работы:**
```
┌─────────────────────────────────────────────┐
│  ⚡ Model Switch (Ctrl+M)                    │
├─────────────────────────────────────────────┤
│                                             │
│  Current config:                            │
│    Player: G2 (glm-5)                       │
│    Judge:  G1 (glm-4)                       │
│                                             │
│  What to switch?                            │
│  [1] Player model                           │
│  [2] Judge model                            │
│  [3] Both (swap)                            │
│  [4] Cancel                                 │
│                                             │
│  → Selection: [1]                           │
│                                             │
│  Select new Player model:                   │
│  [1] G1 (glm-4) - faster, cheaper           │
│  [2] G2 (glm-5) - smarter, slower           │
│                                             │
│  ✅ Player switched: G2 → G1                │
│  Next turn will use G1 for Player           │
└─────────────────────────────────────────────┘
```

**Способы вызова:**
```bash
# CLI команда
g3 switch --player=g1        # сменить только Player
g3 switch --judge=g2         # сменить только Judge
g3 switch --swap             # поменять местами

# Интерактивный режим
g3 switch                    # покажет меню

# Внутри сессии (hotkey или команда)
/switch                      # алиас для g3 switch
Ctrl+M                       # горячая клавиша
```

**Поведение при переключении:**
```
┌────────────────────────────────────────────────┐
│  Timing          │ Behavior                     │
├────────────────────────────────────────────────┤
│  Mid-turn        │ Finish current turn, then    │
│                  │ apply new model next turn    │
│                  │                              │
│  Between turns   │ Immediate switch             │
│                  │                              │
│  Mid-task        │ Continue task with new model │
│                  │ (seamless handoff)           │
└────────────────────────────────────────────────┘
```

**Хранение состояния:**
```yaml
# .g3/runtime-state.yaml
current_session:
  player_model: g1
  judge_model: g2
  last_switch: "2024-01-15T14:30:00Z"
  switch_history:
    - from: g2
      to: g1
      role: player
      timestamp: "2024-01-15T14:30:00Z"
```

**Реализация:**
```python
class ModelSwitcher:
    def __init__(self, config: ModelConfig):
        self.config = config
        self.pending_switch = None

    def switch_model(self, role: str, new_model: str):
        """Schedule model switch for next turn"""
        self.pending_switch = {
            'role': role,
            'from': self.config.get_model(role),
            'to': new_model
        }
        # Apply after current turn completes
        return self.pending_switch

    def apply_pending(self):
        """Apply pending switch if exists"""
        if self.pending_switch:
            self.config.set_model(
                self.pending_switch['role'],
                self.pending_switch['to']
            )
            self.log_switch(self.pending_switch)
            self.pending_switch = None

    def show_menu(self):
        """Interactive TUI menu"""
        # Rich/console UI with arrow keys navigation
        pass
```

**Use Cases:**
1. **"Игрок тупит"** → switch Player на G2 (умнее)
2. **"Судья слишком строгий"** → switch Judge на G1 (мягче)
3. **"Контекст заканчивается"** → swap (меняем роли)
4. **"Нужна скорость"** → оба на G1
5. **"Нужен quality"** → оба на G2

**Интеграция с CLI:**
```python
# В cli.py добавить subcommand
@app.command()
def switch(
    player: Optional[str] = typer.Option(None, "--player", "-p"),
    judge: Optional[str] = typer.Option(None, "--judge", "-j"),
    swap: bool = typer.Option(False, "--swap", "-s"),
    interactive: bool = typer.Option(True, "--interactive", "-i")
):
    """Switch models during runtime"""
    switcher = ModelSwitcher(load_config())

    if swap:
        switcher.swap_models()
    elif player:
        switcher.switch_model('player', player)
    elif judge:
        switcher.switch_model('judge', judge)
    elif interactive:
        switcher.show_menu()
```

---

## 1. Test-Driven Development Mode (TDD Mode)

**Идея:** Перед началом реализации вызывать Opus для создания тестов.

**Workflow:**
```
1. Получаем задачу → Вызываем Opus (test-writer)
2. Opus создаёт comprehensive test suite
3. Реализуем код → Тесты уже готовы
4. Запускаем тесты → Итерируем до passing
```

**Преимущества:**
- Тесты пишутся "сверху вниз" - от требований к реализации
- Opus как отдельная роль "test architect"
- Гарантия coverage ещё до написания кода

**Интеграция с Coach/Player:**
- Coach может играть роль test-writer
- Player имплементирует под тесты
- Judge проверяет: тесты pass + код quality

---

## 2. Multi-Agent Code Review Pipeline

**Идея:** В конце работы вызывать Codex для code review.

**Pipeline:**
```
Implementation → Codex (review) → Bug Finder → Coach Feedback
```

**Роли агентов:**
1. **Codex Reviewer** - code quality, patterns, best practices
2. **Bug Finder** - security vulnerabilities, edge cases, potential bugs
3. **Coach** - обучающий feedback, советы по улучшению

**Интеграция:**
- Автоматический вызов после завершения Player работы
- Результаты сохраняются в `.g3/reviews/`
- Coach анализирует и даёт фидбек для обучения

---

## 3. Offline-First Autonomy

**Проблема:** Агент может "затупить" или остановиться при потере сети.

**Решение: Fully Autonomous Agent с Offline Resilience**

**Архитектура:**
```
┌─────────────────────────────────────────┐
│           State Machine                  │
│  ┌─────┐  ┌─────┐  ┌──────┐  ┌─────┐    │
│  │Init │→│Work │→│Retry │→│Resume│    │
│  └─────┘  └─────┘  └──────┘  └─────┘    │
│      ↑        │         ↑        │      │
│      └────────┴─────────┴────────┘      │
└─────────────────────────────────────────┘
```

**Ключевые принципы:**

1. **Local State Persistence**
   - Весь progress сохраняется локально
   - State checkpoint после каждого шага
   - Возможность resume с любой точки

2. **Offline Queue**
   - Действия буферизуются в локальной очереди
   - При восстановлении сети - replay
   - Идемпотентность операций

3. **Stuck Detection**
   - Heartbeat monitoring
   - Timeout на операции
   - Auto-recovery mechanism

4. **Network Resilience**
   - Graceful degradation при потере сети
   - Локальные операции продолжаются
   - Sync при восстановлении

**Реализация:**
```python
class AutonomousAgent:
    def __init__(self):
        self.state = PersistentState()
        self.offline_queue = OfflineQueue()
        self.heartbeat = HeartbeatMonitor()

    def run(self):
        while not self.state.is_complete():
            try:
                self.step()
                self.state.checkpoint()
            except NetworkError:
                self.offline_mode()
            except StuckError:
                self.recover()

    def offline_mode(self):
        # Continue with local operations
        # Queue network-dependent actions
        pass

    def recover(self):
        # Load last checkpoint
        # Retry with different strategy
        pass
```

**Требования к реализации:**
- [ ] Local SQLite/JSON state storage
- [ ] Action queue с retry logic
- [ ] Network status monitoring
- [ ] Stuck detection (no progress timeout)
- [ ] Auto-resume mechanism
- [ ] Conflict resolution для offline changes

---

## 4. Stuck Prevention & Recovery

**Проблемы:**
- Агент "зациклился" на одной задаче
- Нет прогресса долгое время
- Неразрешимая ошибка блокирует работу

**Решения:**

### 4.1 Progress Tracking
```yaml
progress_checkpoints:
  - interval: 60s  # check every minute
  - metric: files_changed OR tests_run OR messages_sent
  - threshold: 0 changes in 5 minutes → STUCK
```

### 4.2 Escalation Ladder
```
Level 1: Try alternative approach (self)
Level 2: Request user clarification (interactive)
Level 3: Skip and move to next task (autonomous)
Level 4: Save state and pause (safe mode)
```

### 4.3 Recovery Strategies
- **Strategy A:** Rollback to last checkpoint, try different path
- **Strategy B:** Decompose problem into smaller subtasks
- **Strategy C:** Ask Coach for guidance
- **Strategy D:** Mark as blocked, continue with other tasks

---

## 5. Integrated Coaching System

**Coach как ментор на протяжении всего процесса:**

```
┌─────────────────────────────────────────────┐
│                  Coach Layer                 │
├─────────────────────────────────────────────┤
│                                             │
│  Pre-work:     Test generation (Opus)       │
│  During:       Hints, warnings, suggestions │
│  Post-work:    Review (Codex) + Bug Finding │
│  Learning:     Pattern extraction, teaching │
│                                             │
└─────────────────────────────────────────────┘
```

**Coach Capabilities:**
- Предсказывает potential issues
- Даёт hints без direct answer
- Анализирует mistakes для обучения
- Создаёт "teaching moments"

---

## Priority Matrix

| Feature | Impact | Effort | Priority |
|---------|--------|--------|----------|
| TDD Mode | High | Medium | P1 |
| Code Review Pipeline | High | Medium | P1 |
| Offline Autonomy | Critical | High | P0 |
| Stuck Prevention | High | Medium | P1 |
| Integrated Coaching | Medium | Low | P2 |

---

## Next Steps

1. [ ] Design offline-first state machine
2. [ ] Prototype TDD workflow with Opus
3. [ ] Integrate Codex review pipeline
4. [ ] Implement stuck detection
5. [ ] Add coach teaching capabilities
