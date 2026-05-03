# Tero Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove TDD, TestWriter, Pre-Plan, Plan Polisher, and batch_mode toggle — leaving only the batch Coach-Player pipeline as the single execution mode.

**Architecture:** Delete dead config fields top-down (Config → RoleRouter → CoachPlayer), then remove constants/prompts/streaming, then slim the menu/CLI. Non-batch `run()` method stays as infrastructure but is no longer called from CLI. Tests updated last.

**Tech Stack:** Python 3.12, pytest, questionary (TUI menu)

**Spec:** `docs/superpowers/specs/2026-05-01-tero-simplification-design.md`

---

## File Map

| File | Action |
|------|--------|
| `src/config.py` | Remove 10 fields, 8 env vars, 2 normalization entries |
| `src/role_router.py` | Remove test_writer/preplanner from map + 3 special-cases |
| `src/coach_player.py` | Remove _run_phase_zero(), TDD branches, _run_tests(), dead imports |
| `src/constants.py` | Remove 3 constants |
| `src/prompts.py` | Remove 2 prompts + 2 builder functions |
| `src/streaming.py` | Remove 4 UI helper functions |
| `src/menu.py` | Remove 14 menu items, slim to 7, auto-save on Start |
| `src/cli_entry.py` | Remove 6 CLI args, hardcode BatchExecutor in run_go() |
| `tests/test_tdd_provider_routing.py` | Delete entirely |
| `tests/test_preplan_integration.py` | Delete entirely |
| `tests/test_role_router.py` | Remove test_writer/preplanner test cases |
| `tests/test_config_defaults.py` | Remove batch_mode/tdd_mode/preplan_mode assertions |
| `tests/test_coach_player.py` | Remove TDD/preplan test cases |
| `tests/test_cli.py` | Remove --batch/--tdd/--preplan argument tests |
| `tests/test_menu_bugs.py` | Remove TDD/preplan/batch_mode menu tests |

---

## Task 1: Config — Remove Dead Fields

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Remove fields from `Config` dataclass**

Find and delete these exact lines in `src/config.py`:

```python
# Remove these lines (TDD Mode block ~line 110-113):
    tdd_mode: bool = False
    test_command: str = ""  # empty = auto-detect
    test_timeout_s: int = DEFAULT_TEST_TIMEOUT_S

# Remove these lines (~line 138-143):
    test_writer_provider: str = "zai"
    test_writer_model: str = ""
    # Pre-plan provider
    preplan_mode: bool = False
    preplan_provider: str = "zai"
    preplan_model: str = ""  # empty = provider default
    preplan_timeout_s: int = DEFAULT_PREPLAN_TIMEOUT_S

# Remove this line (~line 91):
    batch_mode: bool = False  # --batch / G3_BATCH_MODE
```

- [ ] **Step 2: Update the constants import at top of config.py**

Find the `from src.constants import (...)` block and remove `DEFAULT_TEST_TIMEOUT_S` and `DEFAULT_PREPLAN_TIMEOUT_S` from it.

- [ ] **Step 3: Remove dead entries from `_ENV_MAP`**

Remove these key-value pairs from the `_ENV_MAP` dict:
```python
    # Remove:
    "G3_TDD_MODE": ("tdd_mode", lambda x: x.lower() in ("true", "1", "yes")),
    "G3_TEST_COMMAND": ("test_command", str),
    "G3_TEST_TIMEOUT_S": ("test_timeout_s", int),
    "G3_BATCH_MODE": ("batch_mode", lambda x: x.lower() in ("true", "1", "yes")),
    "G3_PREPLAN_MODE": ("preplan_mode", lambda x: x.lower() in ("true", "1", "yes")),
    "G3_PREPLAN_PROVIDER": ("preplan_provider", str),
    "G3_PREPLAN_MODEL": ("preplan_model", str),
    "G3_TEST_WRITER_PROVIDER": ("test_writer_provider", str),
    "G3_TEST_WRITER_MODEL": ("test_writer_model", str),
```

- [ ] **Step 4: Update `_UNSAFE_GLOBAL_DEFAULT_KEYS`**

Change:
```python
_UNSAFE_GLOBAL_DEFAULT_KEYS = {
    "batch_mode",
    "tdd_mode",
    "code_review",
    "preplan_mode",
    "claude_home",
}
```
To:
```python
_UNSAFE_GLOBAL_DEFAULT_KEYS = {
    "code_review",
    "claude_home",
}
```

- [ ] **Step 5: Remove from `resolve_config()` normalization block**

Find the `for key in (...)` loop that normalizes provider names (~lines 475-492). Remove `"test_writer_provider"` and `"preplan_provider"` from the tuple.

- [ ] **Step 6: Verify config loads without errors**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -c "
from src.config import Config, resolve_config
c = Config()
assert not hasattr(c, 'tdd_mode'), 'tdd_mode still present'
assert not hasattr(c, 'batch_mode'), 'batch_mode still present'
assert not hasattr(c, 'preplan_mode'), 'preplan_mode still present'
assert not hasattr(c, 'test_writer_provider'), 'test_writer_provider still present'
c2 = resolve_config({'working_dir': '.'})
print('Config OK')
"
```

Expected: `Config OK`

- [ ] **Step 7: Commit**

```bash
git add src/config.py
git commit -m "config: remove batch_mode, tdd, preplan, test_writer fields"
```

---

## Task 2: Role Router — Remove Dead Roles

**Files:**
- Modify: `src/role_router.py`

- [ ] **Step 1: Remove from `_ROLE_CONFIG_MAP`**

Change:
```python
_ROLE_CONFIG_MAP: dict[str, tuple[str, str]] = {
    "player": ("player_provider", "player_model"),
    "coach": ("coach_provider", "coach_model"),
    "test_writer": ("test_writer_provider", "test_writer_model"),
    "preplanner": ("preplan_provider", "preplan_model"),
    "reviewer": ("review_provider", "review_model"),
    "coach_fallback": ("coach_fallback_provider", "coach_fallback_model"),
    "judge": ("batch_judge_provider", "batch_judge_model"),
}
```
To:
```python
_ROLE_CONFIG_MAP: dict[str, tuple[str, str]] = {
    "player": ("player_provider", "player_model"),
    "coach": ("coach_provider", "coach_model"),
    "reviewer": ("review_provider", "review_model"),
    "coach_fallback": ("coach_fallback_provider", "coach_fallback_model"),
    "judge": ("batch_judge_provider", "batch_judge_model"),
}
```

- [ ] **Step 2: Fix `provider_name_for()` — remove test_writer fallback**

Change:
```python
        if not name:
            if role in ("test_writer", "coach_fallback"):
                return self.config.coach_provider
```
To:
```python
        if not name:
            if role == "coach_fallback":
                return self.config.coach_provider
```

- [ ] **Step 3: Fix `provider_for()` — remove test_writer branch**

Change:
```python
        if not name:
            if role == "test_writer":
                name = self.config.coach_provider
            elif role == "coach_fallback":
                name = self.config.coach_provider
            elif role == "judge":
```
To:
```python
        if not name:
            if role == "coach_fallback":
                name = self.config.coach_provider
            elif role == "judge":
```

- [ ] **Step 4: Fix `switch_role()` snapshot — remove dead fields**

Find the `snapshot = {...}` dict in `switch_role()` (~lines 124-145) and remove:
```python
            "test_writer_provider": getattr(self.config, "test_writer_provider", ""),
            "test_writer_model": getattr(self.config, "test_writer_model", ""),
```

- [ ] **Step 5: Verify role router imports cleanly**

```bash
python -c "
from src.role_router import RoleRouter, _ROLE_CONFIG_MAP
assert 'test_writer' not in _ROLE_CONFIG_MAP
assert 'preplanner' not in _ROLE_CONFIG_MAP
assert 'player' in _ROLE_CONFIG_MAP
assert 'judge' in _ROLE_CONFIG_MAP
print('RoleRouter OK')
"
```

Expected: `RoleRouter OK`

- [ ] **Step 6: Commit**

```bash
git add src/role_router.py
git commit -m "role_router: remove test_writer and preplanner roles"
```

---

## Task 3: Coach Player — Remove Dead Methods and Branches

**Files:**
- Modify: `src/coach_player.py`

- [ ] **Step 1: Remove dead top-level imports**

At the top of the file, remove:
```python
import shlex        # line 7
import subprocess   # line 9
from src.personas import PersonaRegistry  # line 27
```

Also remove from the `from src.prompts import (...)` block:
```python
    TEST_WRITER_SYSTEM_PROMPT,
    build_test_writer_prompt,
```

- [ ] **Step 2: Fix `_persona_registry` annotation in `__init__`**

Change line ~98:
```python
        self._persona_registry: PersonaRegistry | None = None
```
To:
```python
        self._persona_registry = None
```

- [ ] **Step 3: Remove TDD/preplan from `_verify_providers_ready()`**

Find and remove these lines:
```python
        if self.config.tdd_mode:
            roles.append("test_writer")

        if self.config.preplan_mode:
            roles.append("preplanner")
```

- [ ] **Step 4: Remove `_run_phase_zero()` method**

Delete the entire method spanning lines ~371-477 (approx 107 lines). The method starts with:
```python
    async def _run_phase_zero(self, raw_plan: str) -> tuple[list, list]:
```
and ends before `async def run(self)`.

- [ ] **Step 5: Remove preplan branch from `run()`**

Find and delete the entire block:
```python
        if self.config.preplan_mode:
            enriched_items, _phases = await self._run_phase_zero(self.requirements)
            if enriched_items:
                plan_items = enriched_items
        else:
            self._persona_registry = None
```

Do not replace it with anything. `_persona_registry` is already `None` from `__init__` and is never changed now that `_run_phase_zero()` is gone.

- [ ] **Step 6: Remove TDD test_writer block from `run()`**

Find and delete the `tests_written` variable and TDD block (~lines 557-578):
```python
                tests_written = False  # TDD: tests written once per step
```
And the entire block:
```python
                # --- TDD Mode: Test Writer phase (once per step) ---
                if self.config.tdd_mode and not tests_written:
                    streaming_ui.print_test_writer_header(step_num, total_steps)
                    test_prompt = build_test_writer_prompt(...)
                    try:
                        ...
                    except ...:
                        ...
                    tests_written = True
```
(Delete from the comment line through `tests_written = True`)

- [ ] **Step 7: Remove TDD test-run gate from `run()`**

Find and delete (~lines 697-705):
```python
                    # --- TDD Mode: Run tests after player attempt ---
                    if self.config.tdd_mode:
                        test_passed, test_output = await self._run_tests()
                        streaming_ui.print_tdd_status(test_passed, test_output)
                        if not test_passed:
                            feedback = Feedback(
                                f"1. Tests are still failing:\n{test_output[:500]}\n"
                                "2. Fix the implementation until tests pass."
                            )
                            continue  # Skip coach, retry player
```

- [ ] **Step 8: Remove `_run_tests()` and `_detect_test_command()` methods**

Delete both methods starting at ~line 1130 to the end of `_detect_test_command()`.

- [ ] **Step 9: Verify coach_player imports cleanly and session initializes**

```bash
python -c "
from src.config import Config
from src.coach_player import CoachPlayerSession
c = Config()
assert not hasattr(c, 'tdd_mode')
print('CoachPlayerSession import OK')
"
```

Expected: `CoachPlayerSession import OK`

- [ ] **Step 10: Commit**

```bash
git add src/coach_player.py
git commit -m "coach_player: remove _run_phase_zero, TDD branches, _run_tests"
```

---

## Task 4: Constants — Remove Dead Constants

**Files:**
- Modify: `src/constants.py`

- [ ] **Step 1: Remove three constants**

Find and delete these three lines:
```python
DEFAULT_TEST_TIMEOUT_S = 60      # line 14
DEFAULT_PREPLAN_TIMEOUT_S = 120  # line 15
TEST_WRITER_MAX_TURNS = 15       # line 48
```

- [ ] **Step 2: Verify no remaining imports of removed constants**

```bash
grep -r "DEFAULT_TEST_TIMEOUT_S\|DEFAULT_PREPLAN_TIMEOUT_S\|TEST_WRITER_MAX_TURNS" \
    /Users/terobyte/Desktop/Projects/Active/tero/src/ --include="*.py"
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/constants.py
git commit -m "constants: remove DEFAULT_TEST_TIMEOUT_S, DEFAULT_PREPLAN_TIMEOUT_S, TEST_WRITER_MAX_TURNS"
```

---

## Task 5: Prompts — Remove Dead Prompt Functions

**Files:**
- Modify: `src/prompts.py`

- [ ] **Step 1: Remove `TEST_WRITER_SYSTEM_PROMPT`**

Find the constant starting at line ~130:
```python
TEST_WRITER_SYSTEM_PROMPT = """You are a Test Architect..."""
```
Delete it entirely (multiline string, find the closing `"""` and delete everything between).

- [ ] **Step 2: Remove `build_test_writer_prompt()`**

Find and delete the function starting at line ~308:
```python
def build_test_writer_prompt(
```
Delete the entire function.

- [ ] **Step 3: Remove `PREPLANNER_SYSTEM_PROMPT`**

Find the constant starting at line ~372:
```python
PREPLANNER_SYSTEM_PROMPT = """You are a Plan Polisher..."""
```
Delete it entirely.

- [ ] **Step 4: Remove `build_preplan_prompt()`**

Find and delete the function starting at line ~407:
```python
def build_preplan_prompt(raw_plan: str, roles: list[dict]) -> str:
```
Delete the entire function.

- [ ] **Step 5: Verify prompts module loads cleanly**

```bash
python -c "
from src.prompts import build_coach_step_prompt, build_player_step_prompt
print('prompts OK')
"
```

Expected: `prompts OK`

- [ ] **Step 6: Commit**

```bash
git add src/prompts.py
git commit -m "prompts: remove TDD and preplan prompt functions"
```

---

## Task 6: Streaming UI — Remove Dead Helpers

**Files:**
- Modify: `src/streaming.py`

- [ ] **Step 1: Remove four dead functions**

Find and delete each of these functions entirely:
```python
def print_preplanner_header(model_name: str = "") -> None:    # ~line 210
def print_preplan_result(...)                                  # ~line 216
def print_test_writer_header(step_num: int, total_steps: int):# ~line 312
def print_tdd_status(tests_passed: bool, test_output: str):   # ~line 317
```

- [ ] **Step 2: Verify streaming module loads cleanly**

```bash
python -c "
from src import streaming
print('streaming OK')
"
```

Expected: `streaming OK`

- [ ] **Step 3: Commit**

```bash
git add src/streaming.py
git commit -m "streaming: remove preplan and TDD UI helpers"
```

---

## Task 7: Menu — Slim Down to 7 Items

**Files:**
- Modify: `src/menu.py`

This is the largest UI change. The goal is the menu in the spec:
```
▶   Запустить   (auto-saves)
    Player
    Coach
    Escalation
    Judge
    Файл плана
    Макс. попыток
    Context Limit
✗   Выход
```

- [ ] **Step 1: Remove dead module-level constants**

Remove:
```python
BATCH_ROLE_LABELS = {
    "batch_pre": "Pre-Coach",
    "batch_judge": "Judge",
    "batch_post": "Post-Coach",
    "test_writer": "TestWriter",    # ← this whole dict goes
}

REVIEW_PROVIDER_PRESETS = {
    "Следовать Coach": "",
    **PROVIDER_PRESETS,
}
```

Also remove `"test_writer"` from `BATCH_ROLE_LABELS` (the entire dict, since no callers remain after menu cleanup).

- [ ] **Step 2: Remove dead helper functions**

Delete these functions entirely from `menu.py`:
- `_format_batch_retry_counts()`
- `_parse_batch_retry_counts()`
- `_launch_debugger()`
- `_review_effective_label()`

- [ ] **Step 3: Rewrite `_questionary_menu()` — remove dead display vars and choices**

The new questionary menu body. Replace the entire while-loop content with:

```python
def _questionary_menu(config: Config) -> Config | None:
    import questionary

    while True:
        coach_display = (
            short_model_name(config.coach_model) if config.coach_model else "по умолчанию"
        )
        player_display = (
            short_model_name(config.player_model) if config.player_model else "по умолчанию"
        )
        fallback_display = _provider_model_label(
            config.coach_fallback_provider, config.coach_fallback_model
        )
        judge_provider, judge_model = _effective_provider_model(
            config.batch_judge_provider, config.batch_judge_model
        )
        judge_display = _provider_model_label(judge_provider, judge_model)
        wd_display = config.working_dir.replace(str(Path.home()), "~")

        choices = [
            questionary.Choice("▶   Запустить", value="start"),
            questionary.Separator("─── провайдеры ──────────────────────────"),
            questionary.Choice(
                f"    Player:         {config.player_provider} ({player_display})",
                value="player_provider",
            ),
            questionary.Choice(
                f"    Coach:          {config.coach_provider} ({coach_display})",
                value="coach_provider",
            ),
            questionary.Choice(
                f"    Escalation:     {fallback_display}",
                value="coach_fallback",
            ),
            questionary.Choice(
                f"    Judge:          {judge_display}",
                value="batch_judge",
            ),
            questionary.Separator("─── настройки ───────────────────────────"),
            questionary.Choice(
                f"    Файл плана:     {config.plan_file}", value="plan_file"
            ),
            questionary.Choice(
                f"    Макс. попыток:  {config.max_turns} (на шаг)", value="max_turns"
            ),
            questionary.Choice(
                f"    Context Limit:  {_format_context_limit(config.context_limit)}",
                value="context_limit",
            ),
            questionary.Separator("─────────────────────────────────────────"),
            questionary.Choice("✗   Выход", value="quit"),
        ]

        answer = questionary.select(
            "⚙  tero — настройка  (↑↓ выбор, Enter)",
            choices=choices,
            use_shortcuts=False,
        ).ask()

        if answer is None or answer == "quit":
            return None

        if answer == "start":
            try:
                _save_global_default(config)
            except OSError:
                pass
            return config

        config = _edit_setting_questionary(config, answer)
```

- [ ] **Step 4: Slim `_edit_setting_questionary()` — remove dead elif branches**

Remove all these elif branches from `_edit_setting_questionary()`:
- `elif setting == "tdd_mode":`
- `elif setting == "preplan_mode":`
- `elif setting == "preplan_provider":`
- `elif setting in ("batch_pre", "batch_post", "test_writer"):`  (keep `"batch_judge"` handling)
- `elif setting == "batch_review_schedule":`
- `elif setting == "code_review":`
- `elif setting == "review_provider":`
- `elif setting == "verbose":`
- `elif setting == "autonomous":`
- `elif setting == "batch_mode":`
- `elif setting == "working_dir":`

Keep:
- `if setting == "player_provider":`
- `elif setting == "coach_provider":` (with `_sync_batch_roles_with_coach`)
- `elif setting == "coach_fallback":`
- `elif setting == "batch_judge":` — rename the `prefix_map` to just handle judge:
  ```python
  elif setting == "batch_judge":
      config = _questionary_select_provider_model(
          config, "batch_judge_provider", "batch_judge_model", "Judge"
      )
  ```
- `elif setting == "max_turns":`
- `elif setting == "plan_file":`
- `elif setting == "context_limit":`

- [ ] **Step 5: Rewrite `_fallback_menu()` with same slim structure**

Replace the print statements and elif chain in `_fallback_menu()` to mirror the questionary menu:

```python
def _fallback_menu(config: Config) -> Config | None:
    print("\n⚙  tero — настройка")
    print("  (установи questionary для красивого меню: pip install questionary)\n")

    while True:
        coach_display = (
            short_model_name(config.coach_model) if config.coach_model else "по умолчанию"
        )
        player_display = (
            short_model_name(config.player_model) if config.player_model else "по умолчанию"
        )
        wd_display = config.working_dir.replace(str(Path.home()), "~")
        fallback_display = _provider_model_label(
            config.coach_fallback_provider, config.coach_fallback_model
        )
        judge_display = _provider_model_label(
            config.batch_judge_provider, config.batch_judge_model
        )
        print(f"  [p] Player:        {config.player_provider} ({player_display})")
        print(f"  [c] Coach:         {config.coach_provider} ({coach_display})")
        print(f"  [f] Escalation:    {fallback_display}")
        print(f"  [j] Judge:         {judge_display}")
        print(f"  [1] Файл плана:    {config.plan_file}")
        print(f"  [2] Макс. попыток: {config.max_turns}")
        print(f"  [3] Context Limit: {_format_context_limit(config.context_limit)}")
        print(f"  [Enter] Запустить (сохраняет настройки)")
        print(f"  [q] Выход\n")

        answer = input("  › ").strip().lower()

        if answer == "":
            try:
                _save_global_default(config)
            except OSError:
                pass
            return config
        if answer == "q":
            return None
        elif answer == "p":
            config = _fallback_select_provider_model(
                config, "player_provider", "player_model", "Player"
            )
        elif answer == "c":
            previous_provider = config.coach_provider
            previous_model = config.coach_model
            config = _fallback_select_provider_model(
                config, "coach_provider", "coach_model", "Coach"
            )
            config = _sync_batch_roles_with_coach(config, previous_provider, previous_model)
        elif answer == "f":
            config = _fallback_select_provider_model(
                config,
                "coach_fallback_provider",
                "coach_fallback_model",
                "Fallback",
                provider_choices=FALLBACK_PROVIDER_PRESETS,
            )
        elif answer == "j":
            config = _fallback_select_provider_model(
                config, "batch_judge_provider", "batch_judge_model", "Judge"
            )
        elif answer == "1":
            val = input(f"  Файл [{config.plan_file}]: ").strip()
            if val:
                config = Config(**{**config.__dict__, "plan_file": val})
        elif answer == "2":
            val = input(f"  Макс. попыток [{config.max_turns}]: ").strip()
            if val.isdigit():
                config = Config(**{**config.__dict__, "max_turns": int(val)})
        elif answer == "3":
            print("  [a] Авто  [1] 200K  [2] 500K  [3] 1M  [4] Вручную")
            choice = input("  › ").strip().lower()
            if choice == "a":
                config = Config(**{**config.__dict__, "context_limit": _DEFAULT_CONTEXT_LIMIT})
            elif choice == "1":
                config = Config(**{**config.__dict__, "context_limit": 200_000})
            elif choice == "2":
                config = Config(**{**config.__dict__, "context_limit": 500_000})
            elif choice == "3":
                config = Config(**{**config.__dict__, "context_limit": 1_000_000})
            elif choice == "4":
                raw = input("  Лимит в токенах: ").strip()
                if raw.isdigit():
                    config = Config(**{**config.__dict__, "context_limit": int(raw)})
        print()
```

- [ ] **Step 6: Remove the `run_debugger_menu()` call from `run_settings_menu()` dispatch**

The debugger menu is only accessed via `tero debug`, not from the main menu. Remove any reference to `run_debugger_menu()` or `start_debug` from the main menu flow.

- [ ] **Step 7: Verify menu imports and initializes**

```bash
python -c "
from src.menu import run_settings_menu
print('menu OK')
"
```

Expected: `menu OK`

- [ ] **Step 8: Commit**

```bash
git add src/menu.py
git commit -m "menu: slim to 7 items, auto-save on start, remove dead UI"
```

---

## Task 8: CLI — Remove Args, Hardcode Batch

**Files:**
- Modify: `src/cli_entry.py`

- [ ] **Step 1: Remove dead entries from `resolve_go_config()`**

Remove these lines from the dict passed to `resolve_config()`:
```python
            "tdd_mode": getattr(args, "tdd_mode", None),
            "test_command": getattr(args, "test_command", None),
            "test_timeout_s": getattr(args, "test_timeout_s", None),
            "batch_mode": getattr(args, "batch_mode", None),
            # Pre-Planner (Phase 0)
            "preplan_provider": getattr(args, "preplan_provider", None) or None,
            "preplan_model": getattr(args, "preplan_model", None) or None,
            "preplan_mode": getattr(args, "preplan_mode", None),
```

- [ ] **Step 2: Remove CLI arguments from `build_parser()`**

Find and delete these `go_parser.add_argument(...)` calls:
```python
    go_parser.add_argument("--batch", action="store_true", dest="batch_mode", default=None)
    go_parser.add_argument("--tdd", dest="tdd_mode", action="store_true", default=None)
    go_parser.add_argument("--test-command", type=str, default=None)
    go_parser.add_argument("--test-timeout-s", type=int, default=None, dest="test_timeout_s")
    # Pre-Planner block (~lines 203-229):
    go_parser.add_argument("--preplan-provider", ...)
    go_parser.add_argument("--preplan-model", ...)
    preplan_group = go_parser.add_mutually_exclusive_group()
    preplan_group.add_argument("--preplan", ...)
    preplan_group.add_argument("--no-preplan", ...)
```

- [ ] **Step 3: Hardcode BatchExecutor in `run_go()`**

Replace the entire `if config.batch_mode: ... else: ...` dispatch:

```python
# Remove this:
        if config.batch_mode:
            items = parse_requirements(requirements)
            phases = []
            if config.preplan_mode and hasattr(session, "_run_phase_zero"):
                enriched_items, phases = await session._run_phase_zero(requirements)
                if enriched_items:
                    items = enriched_items
            tracker = PlanTracker(items)
            if phases:
                tracker.phases = phases
            executor = BatchExecutor(session, tracker, session.router)
            await executor.run()
            sys.exit(0)

        result = await session.run()
        sys.exit(0 if result.approved else 1)

# Replace with:
        items = parse_requirements(requirements)
        tracker = PlanTracker(items)
        executor = BatchExecutor(session, tracker, session.router)
        await executor.run()
        sys.exit(0)
```

- [ ] **Step 4: Verify CLI parses correctly**

```bash
python -m src.cli_entry go --help
```

Expected: help text without `--batch`, `--tdd`, `--preplan` flags.

```bash
python -c "
from src.cli_entry import build_parser
p = build_parser()
# --batch should now be unknown
try:
    p.parse_args(['go', '--batch'])
    print('FAIL: --batch still accepted')
except SystemExit:
    print('CLI OK: --batch rejected as expected')
"
```

Expected: `CLI OK: --batch rejected as expected`

- [ ] **Step 5: Commit**

```bash
git add src/cli_entry.py
git commit -m "cli: remove batch/tdd/preplan args, hardcode BatchExecutor dispatch"
```

---

## Task 9: Tests — Delete and Update

**Files:**
- Delete: `tests/test_tdd_provider_routing.py`
- Delete: `tests/test_preplan_integration.py`
- Modify: `tests/test_role_router.py`
- Modify: `tests/test_config_defaults.py`
- Modify: `tests/test_coach_player.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_menu_bugs.py`

- [ ] **Step 1: Delete test files that test removed features**

```bash
git rm tests/test_tdd_provider_routing.py
git rm tests/test_preplan_integration.py
```

- [ ] **Step 2: Update `tests/test_role_router.py`**

Remove any test that creates a router and passes `test_writer` or `preplanner` as a role, or asserts those roles exist in `_ROLE_CONFIG_MAP`.

```bash
grep -n "test_writer\|preplanner" tests/test_role_router.py
```

For each match, delete the test function or the specific assertion.

- [ ] **Step 3: Update `tests/test_config_defaults.py`**

Remove assertions like:
```python
assert c.batch_mode == False
assert c.tdd_mode == False
assert c.preplan_mode == False
```

```bash
grep -n "batch_mode\|tdd_mode\|preplan_mode\|test_writer" tests/test_config_defaults.py
```

Delete each matched line.

- [ ] **Step 4: Update `tests/test_coach_player.py`**

Remove test cases that test TDD or preplan behavior:

```bash
grep -n "tdd_mode\|preplan_mode\|test_writer\|_run_phase_zero\|_run_tests" tests/test_coach_player.py
```

For each match, delete the test function or the specific Config field kwarg.

- [ ] **Step 5: Update `tests/test_cli.py`**

Remove test cases that test `--batch`, `--tdd`, `--preplan` CLI args:

```bash
grep -n "batch_mode\|tdd_mode\|preplan_mode\|test_writer\|--batch\|--tdd\|--preplan" tests/test_cli.py
```

For each match, delete the affected test or assertion.

- [ ] **Step 6: Update `tests/test_menu_bugs.py`**

Remove test cases for removed menu items:

```bash
grep -n "tdd_mode\|preplan\|batch_mode\|test_writer" tests/test_menu_bugs.py
```

- [ ] **Step 7: Run full test suite**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all tests pass. Fix any test that fails due to missing config fields before committing.

- [ ] **Step 8: Commit**

```bash
git add -u tests/
git commit -m "tests: delete TDD/preplan test files, update remaining tests"
```

---

## Final Verification

- [ ] **Smoke test: menu launches**

```bash
python -c "
from src.config import Config
from src.menu import run_settings_menu
print('Menu module loads OK')
print('Config fields:', [f for f in Config.__dataclass_fields__ if 'batch' in f or 'tdd' in f or 'preplan' in f])
"
```

Expected output shows only batch retry/role fields (no `batch_mode`, no `tdd_mode`, no `preplan_mode`).

- [ ] **Smoke test: full CLI entrypoint**

```bash
python -m src.cli_entry go --help
python -m src.cli_entry debug --help
python -m src.cli_entry history --help
```

Expected: all three help texts show without errors.

- [ ] **Final test run**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass.
