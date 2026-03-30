# Persona Pre-Planner Design
**Date:** 2026-03-29
**Scope:** Phase 0 Pre-Planner agent with dynamic persona registry, enriched plan format, and persona overlay for Player/Coach system prompts

---

## Problem

### 1. All steps get the same system prompt
In `coach_player.py` line ~769, every Player step receives `PLAYER_SYSTEM_PROMPT` regardless of whether the step is about security, database, frontend, or devops. A step like "Add JWT authentication middleware" gets the same generic prompt as "Create pyproject.toml". This leaves domain expertise on the table.

### 2. Dashboard phase names are meaningless
`plan_tracker.py:171-179` — `_make_phase()` truncates the first step's text to 45 characters:
```
Phase 7: Update (4) · return self._filter_or_exclude(False, *args,…
Phase 8: Update (4) · return None
```
Users see raw code fragments instead of meaningful descriptions.

### 3. Phase grouping is naive
`auto_group_phases()` groups by keyword-detected step type (create/update/test/review). It doesn't understand semantic relationships — steps about the same feature may land in different phases.

---

## Solution: Phase 0 Pre-Planner

A new LLM agent that runs **once before** the coach-player loop. It reads the raw plan, assigns roles from a dynamic persona registry, groups steps into semantically meaningful phases, and outputs an enriched plan. One LLM call solves all three problems.

### Architecture

```
User's raw plan
       ↓
  PersonaRegistry.load_all()  →  scans personas/prompts/*.md
       ↓
  build_preplan_prompt(raw_plan, available_roles)
       ↓
  _run_turn(role="player", system_prompt=PREPLANNER_SYSTEM_PROMPT,
            provider_override=preplan_provider, model_override=preplan_model)
       ↓
  parse_enriched_plan(result.text)  →  (PlanItems with roles, Phases with display_name)
       ↓
  write_enriched_plan(enriched_path, result.text)  →  saves to .g3/enriched-plan.md (NOT original)
       ↓
  Coach-Player loop:
    Player system_prompt = PLAYER_SYSTEM_PROMPT + persona overlay
    Coach system_prompt  = COACH_STRICT_SYSTEM_PROMPT + review focus overlay
```

---

## New Components

### 1. `src/personas/` — Persona Registry

**Files:**
- `src/personas/__init__.py` — exports `PersonaRegistry`, `Persona`
- `src/personas/registry.py` — core registry logic
- `src/personas/prompts/*.md` — persona definition files (10 for first release)

**`Persona` dataclass:**
```python
@dataclass
class Persona:
    name: str          # "security"
    description: str   # "OWASP Top 10, injection, auth bypass, secrets detection"
    overlay: str       # 20-40 lines of domain expertise
```

**`PersonaRegistry` class:**
```python
class PersonaRegistry:
    def __init__(self, personas_dir: Path):
        self._dir = personas_dir
        self._cache: dict[str, Persona] = {}

    def load_all(self) -> list[Persona]:
        """Scan personas_dir, parse each .md frontmatter + body, cache."""

    def get(self, role_name: str) -> Persona | None:
        """Return cached persona by name. None if unknown (soft fallback)."""

    def available_roles(self) -> list[dict]:
        """Return [{name, description}, ...] for build_preplan_prompt()."""

    def build_overlay(self, roles: list[str]) -> str:
        """Combine 1-2 personas into overlay text.

        If roles=["security", "architect"]:
        → "## Specialist Context: Security\n{text}\n\n## Specialist Context: Architect\n{text}"

        Unknown roles silently skipped. Max 2 overlays to limit context size.
        """
```

**Persona file format (`personas/prompts/security.md`):**
```markdown
---
name: security
description: Security specialist — OWASP Top 10, injection, auth bypass, secrets detection
---

## Specialist Context: Security

Focus on security vulnerabilities in every code change:

CRITICAL — flag immediately:
- Hardcoded secrets (API keys, passwords, tokens)
- SQL injection (string concatenation in queries → parameterize)
- XSS (unescaped user input in HTML → sanitize)
- Auth bypass (missing auth checks on routes)
- SSRF (user-controlled URLs without whitelist)
- Shell injection (user input in exec/system calls)

VERIFY:
- Passwords hashed with bcrypt/argon2, never plaintext
- CORS configured to specific origins, not wildcard
- Rate limiting on public endpoints
- Sensitive data not logged (tokens, PII)
- Input validated at system boundaries

Principle: defense in depth — multiple layers, least privilege, fail securely.
```

### 2. Initial Persona Set (10 files)

| File | Source from ECC | Key expertise |
|------|----------------|---------------|
| `python-dev.md` | `agents/python-reviewer.md` | Pythonic patterns, type hints, venvs, imports |
| `frontend-dev.md` | `agents/typescript-reviewer.md` + `skills/frontend-patterns/` | React, CSS, accessibility, performance |
| `designer.md` | `skills/design-system/` + `skills/frontend-design/` | Visual hierarchy, spacing, color, UX principles |
| `security.md` | `agents/security-reviewer.md` | OWASP Top 10, injection, XSS, secrets, auth |
| `database.md` | `agents/database-reviewer.md` | Migrations, indexes, N+1, RLS, normalization |
| `architect.md` | `agents/architect.md` | Modularity, trade-offs, patterns, scaling |
| `devops.md` | `skills/deployment-patterns/` + `skills/docker-patterns/` | CI/CD, Docker, env config, scripts |
| `tdd-guide.md` | `agents/tdd-guide.md` | Test-first, coverage, edge cases, test structure |
| `performance.md` | `agents/performance-optimizer.md` | Algorithms, caching, profiling, memory |
| `refactor.md` | `agents/refactor-cleaner.md` | DRY, extract method, reduce complexity |

**Conversion principle:** Extract only domain expertise (20-40 lines). Strip workflow instructions, output formatting, and general rules already in `PLAYER_SYSTEM_PROMPT`.

### 3. `src/prompts.py` — Pre-Planner Prompt

**New constant `PREPLANNER_SYSTEM_PROMPT`:**
```
You are a Plan Polisher. Your job is to enrich a raw implementation
plan with role annotations and phase groupings.

INPUT: A raw plan with numbered/checkbox steps.
AVAILABLE ROLES: {roles_list}

YOUR TASKS:
1. Read each step and assign 1-2 roles from the available list
2. Group steps into logical phases with human-readable names
3. Clean up step descriptions — make them clear and concise
4. Preserve the original intent — do NOT add, remove, or reorder steps
5. If a step doesn't match any role, use [general]

OUTPUT FORMAT (strict):

## Phases
- Phase 1: "Phase name" → steps 1-3
- Phase 2: "Phase name" → steps 4-5

## Steps
1. [role] Clean step description
2. [role1, role2] Clean step description
3. [role] Clean step description

RULES:
- Output ONLY the enriched plan, no commentary
- Phase names: short (3-5 words), descriptive, in the project language
- Every step MUST have at least one [role] tag
- Use ONLY roles from the available list — do NOT invent new roles
- Keep step count identical to input
- Steps that don't match any role get [general]
```

**New builder `build_preplan_prompt()`:**
```python
def build_preplan_prompt(raw_plan: str, roles: list[dict]) -> str:
    roles_list = "\n".join(
        f"- {r['name']}: {r['description']}" for r in roles
    )
    return f"""## Available Roles
{roles_list}

## Raw Plan
{raw_plan}

Enrich this plan following the output format."""
```

### 4. `src/config.py` — New Config Fields

```python
# Pre-Planner (Phase 0)
preplan_mode: bool = True
preplan_provider: str = "black"
preplan_model: str = ""
preplan_timeout_s: int = 120
```

**Environment variables:**
```
G3_PREPLAN_MODE=true/false
G3_PREPLAN_PROVIDER=black
G3_PREPLAN_MODEL=glm-5
G3_PREPLAN_TIMEOUT_S=120
```

**CLI arguments:** `--preplan-provider`, `--preplan-model`, `--no-preplan`

### 5. `src/plan_tracker.py` — Enriched Plan Parsing

**Modified `PlanItem`:**
```python
@dataclass
class PlanItem:
    text: str
    done: bool = False
    roles: list[str] = field(default_factory=list)  # NEW
```

**Modified `Phase`:**
```python
@dataclass
class Phase:
    name: str
    type: str
    steps: list[PlanItem]
    status: str = "pending"
    attempts: int = 0
    display_name: str = ""  # NEW — human-readable name from Pre-Planner
```

**New parser `parse_enriched_plan()`:**
```python
ROLE_TAG_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")

def parse_enriched_plan(content: str) -> tuple[list[PlanItem], list[Phase]]:
    """Parse Pre-Planner output into (items, phases).

    Handles two sections:

    ## Phases
    - Phase 1: "Настройка проекта" → steps 1-3

    ## Steps
    1. [security, architect] Add authentication middleware

    Fallback: if no ## Phases section, returns (items, []) and caller
    uses auto_group_phases().
    """
```

**New writer `write_enriched_plan()`:**
Saves the Pre-Planner's enriched output to the plan file so progress tracking works with the annotated steps.

**`_build_table()` change:** Use `phase.display_name` if set, else fall back to current snippet logic.

### 6. `src/coach_player.py` — Integration Points

**Point 1: Phase 0 before main loop (~line 620)**

After `parse_requirements()`, before the step loop:
- If `preplan_mode=True`: load registry, run Pre-Planner turn, parse enriched plan, store registry on self
- If `preplan_mode=False`: use existing `auto_group_phases()`, set registry to None

**Point 2: Player persona overlay (~line 769)**

```python
player_system = PLAYER_SYSTEM_PROMPT
if self._persona_registry and step.roles:
    overlay = self._persona_registry.build_overlay(step.roles)
    if overlay:
        player_system = f"{PLAYER_SYSTEM_PROMPT}\n\n{overlay}"
```

**Point 3: Coach persona overlay (~line 846)**

```python
coach_system = COACH_STRICT_SYSTEM_PROMPT
if self._persona_registry and step.roles:
    overlay = self._persona_registry.build_overlay(step.roles)
    if overlay:
        coach_system = f"{COACH_STRICT_SYSTEM_PROMPT}\n\n## Review Focus\n{overlay}"
```

**Point 4: Batch executor (~line 489)**

Same overlay logic for `PLAYER_BATCH_SYSTEM_PROMPT`. Phases already have `display_name` from Pre-Planner.

### 7. `src/streaming.py` — New UI Elements

```python
def print_preplanner_header():
    """Print: 🎭 Phase 0: Polishing plan...  [model_name]"""

def print_preplan_result(num_phases: int, num_roles: int):
    """Print: 🎭 Phase 0: Plan polished (6 phases, 10 roles assigned)"""
```

---

## Fallback Chain

| Scenario | Behavior |
|----------|----------|
| `preplan_mode=True`, Pre-Planner succeeds | Enriched plan + roles + phases |
| `preplan_mode=True`, Pre-Planner times out | Warning printed, fallback to `parse_requirements()` + `auto_group_phases()` |
| `preplan_mode=True`, Pre-Planner output unparseable | Warning printed, same fallback |
| `preplan_mode=False` | Current behavior unchanged |
| Role tag in plan but role not in registry | Overlay empty, Player works without specialization |
| Pre-Planner invents a role not in available list | `registry.get()` returns None, overlay skipped |

**No scenario leads to crash. Degradation is always soft.**

---

## Example End-to-End Flow

**Input plan (`plan.md`):**
```
1. Create pyproject.toml with FastAPI and pytest
2. Initialize git repo
3. Create user model with SQLAlchemy
4. Add JWT authentication middleware
5. Create API endpoints for CRUD users
6. Add rate limiting to public endpoints
7. Write tests for auth flow
8. Optimize database queries with indexes
```

**After Phase 0 — enriched plan written to `.g3/enriched-plan.md` (original `plan.md` untouched):**
```markdown
## Phases
- Phase 1: "Инициализация проекта" → steps 1-2
- Phase 2: "Модель данных" → steps 3
- Phase 3: "Авторизация и безопасность" → steps 4, 6
- Phase 4: "API эндпоинты" → steps 5
- Phase 5: "Тесты" → steps 7
- Phase 6: "Оптимизация БД" → steps 8

## Steps
1. [devops] Create pyproject.toml с зависимостями FastAPI, SQLAlchemy, pytest
2. [devops] Инициализация git-репозитория
3. [database, python-dev] Создать User модель с SQLAlchemy (id, email, hashed_password, created_at)
4. [security, architect] Добавить JWT authentication middleware с валидацией токенов
5. [python-dev] Создать CRUD эндпоинты для users (/users, /users/{id})
6. [security] Добавить rate limiting на публичные эндпоинты
7. [tdd-guide, security] Написать тесты для auth flow — happy path, expired token, invalid token
8. [database, performance] Оптимизация запросов — добавить индексы на email, created_at
```

**Dashboard during execution:**
```
🎭 Phase 0: Plan polished (6 phases, 10 roles assigned)
✅ Phase 1: [devops] Инициализация проекта         ████████████████████ 100%
✅ Phase 2: [database] Модель данных                ████████████████████ 100%
🔄 Phase 3: [security] Авторизация и безопасность   ██████████░░░░░░░░░░ 50%
⏳ Phase 4: [python-dev] API эндпоинты              ░░░░░░░░░░░░░░░░░░░░ 0%
⏳ Phase 5: [tdd-guide] Тесты                       ░░░░░░░░░░░░░░░░░░░░ 0%
⏳ Phase 6: [database] Оптимизация БД               ░░░░░░░░░░░░░░░░░░░░ 0%
```

**Player on step 4 receives:**
```
System prompt:
  [PLAYER_SYSTEM_PROMPT — all CRITICAL RULES, format, cleanup]

  ## Specialist Context: Security
  Focus on security vulnerabilities...
  CRITICAL — flag immediately: hardcoded secrets, SQL injection, XSS...

  ## Specialist Context: Architect
  Focus on modularity, separation of concerns, clear interfaces...

User prompt:
  ## Current Task — Step 4/8
  [security, architect] Добавить JWT authentication middleware с валидацией токенов
  ...
```

---

## Files Changed Summary

| File | Action | Description |
|------|--------|-------------|
| `src/personas/__init__.py` | CREATE | Export PersonaRegistry, Persona |
| `src/personas/registry.py` | CREATE | Load .md files, parse frontmatter, build overlays |
| `src/personas/prompts/*.md` | CREATE | 10 persona files converted from ECC agents |
| `src/prompts.py` | MODIFY | Add PREPLANNER_SYSTEM_PROMPT + build_preplan_prompt() |
| `src/config.py` | MODIFY | Add preplan_mode, preplan_provider, preplan_model, preplan_timeout_s |
| `src/plan_tracker.py` | MODIFY | PlanItem.roles, Phase.display_name, parse_enriched_plan(), write_enriched_plan() |
| `src/coach_player.py` | MODIFY | Phase 0 call before loop, persona overlay for Player + Coach |
| `src/batch_executor.py` | MODIFY | Persona overlay for batch Player |
| `src/streaming.py` | MODIFY | print_preplanner_header(), print_preplan_result() |
| `src/cli_entry.py` | MODIFY | --preplan-provider, --preplan-model, --no-preplan CLI args |
| `tests/test_plan_tracker.py` | MODIFY | Tests for parse_enriched_plan(), PlanItem.roles |
| `tests/test_persona_registry.py` | CREATE | Tests for PersonaRegistry |

---

## Spec Review Fixes

Issues found by code review and resolved below.

### Fix 1: Plan file safety — no overwrite (CRITICAL)

**Problem:** Original spec wrote enriched plan back to `plan.md`, risking data loss if Pre-Planner produces bad output.

**Fix:** Enriched plan is saved to `.g3/enriched-plan.md`, never touching the original. The original `plan.md` is only modified by `write_checklist_back()` for checkbox progress (existing behavior). Phase 0 reads from `plan.md`, writes to `.g3/enriched-plan.md`. The coach-player loop reads the enriched file when it exists.

### Fix 2: Parser dispatch — explicit routing (CRITICAL)

**Problem:** `parse_requirements()` would parse `- Phase 1: "..." → steps 1-3` lines as phantom PlanItems if called on an enriched file.

**Fix:** The two parsers never compete. Decision logic:
```python
# In CoachPlayerSession.run():
if self.config.preplan_mode:
    # Phase 0 produces enriched output → parse_enriched_plan()
    plan_items, phases = parse_enriched_plan(result.text)
else:
    # No Phase 0 → old path
    plan_items = parse_requirements(self.requirements)
    phases = auto_group_phases(plan_items)
```
`parse_requirements()` is never called on enriched content. `parse_enriched_plan()` is only called on Pre-Planner output (in-memory string, not file). No sentinel markers needed.

### Fix 3: PlanItem field preservation (CRITICAL)

**Problem:** `mark_step_done()`, `mark_all_done()`, `reset_all_progress()` construct new `PlanItem(text=..., done=...)` without `roles`, silently dropping role annotations.

**Fix:** Use `dataclasses.replace()` at all reconstruction sites:
```python
# Before (loses roles):
PlanItem(text=result[index].text, done=True)

# After (preserves all fields):
from dataclasses import replace
replace(items[index], done=True)
```

**Sites to update:**
- `mark_step_done()` — line 146
- `mark_all_done()` — line 127
- `reset_all_progress()` — line 132

Similarly for `Phase` — `_make_phase()` must pass `display_name` when constructing from Pre-Planner output.

### Fix 4: Provider dispatch — dedicated "preplanner" role (IMPORTANT)

**Problem:** `_provider_for_role("preplanner")` raises `ValueError` — role not registered.

**Fix:** Add "preplanner" to both `_provider_for_role()` and `_provider_name_for_role()` in `coach_player.py`. Phase 0 passes `role="preplanner"` explicitly — `_run_turn()` resolves the provider via `_provider_for_role("preplanner")`:

```python
# coach_player.py — _provider_name_for_role():
if role == "preplanner":
    return self.config.preplan_provider

# coach_player.py — _provider_for_role():
if role == "preplanner":
    return self._get_or_create_provider(self.config.preplan_provider)
```

Phase 0 call:
```python
result = await self._run_turn(
    role="preplanner",
    prompt=preplan_prompt,
    system_prompt=PREPLANNER_SYSTEM_PROMPT,
    max_turns=5,
    timeout_s=self.config.preplan_timeout_s,
    model_override=self.config.preplan_model,
)
```

`_run_turn()` already handles `provider_override`-less calls via `_provider_for_role(role)` — adding "preplanner" to that chain is all that's needed. No `provider_override` parameter required.

### Fix 5: Provider readiness check (IMPORTANT)

**Problem:** `_verify_providers_ready()` doesn't check the preplan provider at session startup.

**Fix:** Add to `_verify_providers_ready()`:
```python
if self.config.preplan_mode:
    providers_to_check.append(
        ("preplanner", self.config.preplan_provider,
         self._get_or_create_provider(self.config.preplan_provider))
    )
```

### Fix 6: Batch mode integration path (IMPORTANT)

**Problem:** Spec didn't specify how Phase 0 runs in batch mode or how `BatchExecutor` accesses persona overlay.

**Fix:** Phase 0 runs in `cli_entry.py` **before** `BatchExecutor` construction, for both step and batch modes:

```python
# cli_entry.py — unified entry:
plan_items = parse_requirements(requirements)

if config.preplan_mode:
    registry = PersonaRegistry(...)
    registry.load_all()
    # run Phase 0 via session._run_turn()
    enriched_result = await run_phase_zero(session, config, requirements, registry)
    plan_items, phases = parse_enriched_plan(enriched_result)
    session._persona_registry = registry
else:
    phases = auto_group_phases(plan_items)
    session._persona_registry = None

if config.batch_mode:
    tracker = PlanTracker(plan_items)
    tracker.phases = phases  # use Pre-Planner phases, not auto_group_phases()
    executor = BatchExecutor(session, tracker)
    await executor.run()
else:
    await session.run()  # uses plan_items with roles already set
```

`BatchExecutor._run_phase()` accesses overlay via `self.session._persona_registry`:
```python
player_system = PLAYER_BATCH_SYSTEM_PROMPT
if self.session._persona_registry:
    # Get roles from any step in the phase
    phase_roles = set()
    for step in phase.steps:
        phase_roles.update(step.roles)
    overlay = self.session._persona_registry.build_overlay(list(phase_roles))
    if overlay:
        player_system = f"{PLAYER_BATCH_SYSTEM_PROMPT}\n\n{overlay}"
```

### Fix 7: Role truncation warning (MINOR)

**Problem:** `build_overlay()` silently drops roles beyond the max-2 cap.

**Fix:** Log a warning to stderr:
```python
if len(roles) > 2:
    print(f"  [PersonaRegistry] Warning: {len(roles)} roles assigned, using first 2: {roles[:2]}", file=sys.stderr)
```

### Fix 8: Persona directory — configurable with fallback (MINOR)

**Problem:** Hardcoded persona path doesn't allow project-specific personas.

**Fix:** Search path order:
1. `.g3/personas/` (project-specific, user-created)
2. `src/personas/prompts/` (bundled with Tero)

Merge both — project-specific personas override bundled ones by name.

Config field: `preplan_personas_dir: str = ""` (empty = use default search path).

### Fix 8b: Batch phase overwrite — conditional in BatchExecutor (IMPORTANT)

**Problem:** `BatchExecutor.run()` unconditionally calls `auto_group_phases(self.tracker.items)` at line ~380, which overwrites Pre-Planner phases set by `cli_entry.py`.

**Fix:** Replace unconditional call with conditional:
```python
# batch_executor.py — BatchExecutor.run():
# Before:
phases = auto_group_phases(self.tracker.items)

# After:
phases = self.tracker.phases if self.tracker.phases else auto_group_phases(self.tracker.items)
```

`PlanTracker.phases` is already a list field (initialized empty in `__init__`). When Phase 0 has run, `cli_entry.py` sets `tracker.phases = pre_planner_phases` before passing the tracker to `BatchExecutor`. When `preplan_mode=False`, `tracker.phases` is empty → `auto_group_phases()` runs as before. Zero behaviour change for the no-preplan path.

### Fix 8c: Config env_map and CLI arg wiring (IMPORTANT)

**Problem:** New config fields must be explicitly wired into `resolve_config()` env_map and `cli_entry.py`, otherwise env vars and CLI args are silently ignored.

**`src/config.py` — `env_map` additions:**
```python
"G3_PREPLAN_MODE":      ("preplan_mode",     lambda x: x.lower() in ("true", "1", "yes")),
"G3_PREPLAN_PROVIDER":  ("preplan_provider", str),
"G3_PREPLAN_MODEL":     ("preplan_model",    str),
"G3_PREPLAN_TIMEOUT_S": ("preplan_timeout_s", int),
```

**`src/config.py` — provider normalization loop** (lines 554-567): add `"preplan_provider"` to the list of provider name fields that go through alias normalization.

**`src/cli_entry.py` — `build_parser()` additions:**
```python
parser.add_argument("--preplan-provider", type=str, help="Provider for Phase 0 Pre-Planner")
parser.add_argument("--preplan-model",    type=str, help="Model for Phase 0 Pre-Planner")
parser.add_argument("--no-preplan",       action="store_true", help="Disable Phase 0")
```

**`src/cli_entry.py` — `resolve_go_config()` additions:**
```python
if args.preplan_provider: overrides["preplan_provider"] = args.preplan_provider
if args.preplan_model:    overrides["preplan_model"]    = args.preplan_model
if args.no_preplan:       overrides["preplan_mode"]     = False
```

### Fix 8d: Re-run behaviour for enriched plan (MINOR)

**Problem:** Spec didn't say what happens when `.g3/enriched-plan.md` already exists from a prior session.

**Fix:** Phase 0 always re-runs and overwrites `.g3/enriched-plan.md`. Rationale: the original `plan.md` may have changed (user edited it), and the enrichment is fast (one LLM call with turbo). Stale enrichment is more dangerous than the cost of re-running. If the user wants to skip Phase 0 on re-runs, they use `--no-preplan`.

### Fix 8e: parse_enriched_plan fallback handling (MINOR)

**Problem:** `parse_enriched_plan()` may return `(items, [])` when `## Phases` section is missing. Caller must handle this.

**Fix:** Caller code in `coach_player.py`:
```python
plan_items, phases = parse_enriched_plan(result.text)

# Validate step count matches original
if len(plan_items) != len(original_items):
    print(f"  [Preplanner] Warning: step count mismatch ({len(plan_items)} vs {len(original_items)}), using original plan", file=sys.stderr)
    plan_items = original_items
    phases = auto_group_phases(plan_items)
elif not phases:
    phases = auto_group_phases(plan_items)
```

`original_items` = result of `parse_requirements(self.requirements)` called before Phase 0. This guards against the LLM adding or removing steps.

### Fix 9: Test plan expansion (MINOR)

Additional test files needed:
- `tests/test_coach_player.py` — add tests for Phase 0 integration, overlay injection, fallback on failure
- `tests/test_batch_executor.py` — add tests for batch overlay, phase grouping from Pre-Planner
- `tests/test_prompts.py` — add tests for `build_preplan_prompt()`, `PREPLANNER_SYSTEM_PROMPT` format
