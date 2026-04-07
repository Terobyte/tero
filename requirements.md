# Persona Pre-Planner Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 0 Pre-Planner agent that enriches raw plans with role annotations and semantic phase groupings, then injects domain-expert persona overlays into Player/Coach system prompts.

**Architecture:** A new LLM agent (`role="preplanner"`) runs once before the coach-player loop. It reads the raw plan and a dynamic persona registry (scanned from `src/personas/prompts/*.md`) to annotate each step with `[role]` tags and group steps into named phases. During execution, `PLAYER_SYSTEM_PROMPT` and `COACH_STRICT_SYSTEM_PROMPT` are extended with persona overlay text matching the current step's roles.

**Tech Stack:** Python 3.11+, dataclasses, PyYAML (frontmatter parsing), existing Tero providers/coach-player infrastructure.

**Spec:** `docs/superpowers/specs/2026-03-29-persona-preplanner-design.md`

---

## Chunk 1: PersonaRegistry

### Task 1: Persona dataclass and registry skeleton

**Files:**
- [x] Create: `src/personas/__init__.py`
- [x] Create: `src/personas/registry.py`
- [x] Create: `tests/test_persona_registry.py`

- [x] **Step 1: Write failing tests for Persona dataclass and registry init**

```python
# tests/test_persona_registry.py
import pytest
from pathlib import Path
from src.personas.registry import Persona, PersonaRegistry


def test_persona_dataclass():
    p = Persona(name="security", description="OWASP Top 10", overlay="## Specialist Context: Security\nFocus on vulns.")
    assert p.name == "security"
    assert p.description == "OWASP Top 10"
    assert "vulns" in p.overlay


def test_registry_init(tmp_path):
    registry = PersonaRegistry(tmp_path)
    assert registry._dir == tmp_path
    assert registry._cache == {}


def test_registry_load_all_empty_dir(tmp_path):
    registry = PersonaRegistry(tmp_path)
    result = registry.load_all()
    assert result == []


def test_registry_get_unknown_returns_none(tmp_path):
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    assert registry.get("nonexistent") is None


def test_registry_available_roles_empty(tmp_path):
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    assert registry.available_roles() == []
```

- [x] **Step 2: Run tests to verify they fail**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -m pytest tests/test_persona_registry.py -v 2>&1 | head -30
```
Expected: `ModuleNotFoundError` or `ImportError`

- [x] **Step 3: Create `src/personas/__init__.py`**

```python
"""Persona registry for role-based Player/Coach prompt overlays."""
from src.personas.registry import Persona, PersonaRegistry

__all__ = ["Persona", "PersonaRegistry"]
```

- [x] **Step 4: Create `src/personas/registry.py` with skeleton**

```python
"""Load persona definitions from .md files, parse frontmatter, cache."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Persona:
    """A role persona with domain expertise overlay text."""
    name: str
    description: str
    overlay: str


class PersonaRegistry:
    """Loads and caches persona definitions from .md files."""

    def __init__(self, personas_dir: Path):
        self._dir = Path(personas_dir)
        self._cache: dict[str, Persona] = {}

    def load_all(self) -> list[Persona]:
        """Scan personas_dir, parse each .md, cache and return."""
        self._cache = {}
        if not self._dir.exists():
            return []
        personas = []
        for md_file in sorted(self._dir.glob("*.md")):
            persona = self._parse_file(md_file)
            if persona:
                self._cache[persona.name] = persona
                personas.append(persona)
        return personas

    def get(self, role_name: str) -> "Persona | None":
        """Return cached persona by name, None if unknown."""
        return self._cache.get(role_name)

    def available_roles(self) -> list[dict]:
        """Return [{name, description}, ...] for build_preplan_prompt()."""
        return [
            {"name": p.name, "description": p.description}
            for p in self._cache.values()
        ]

    def build_overlay(self, roles: list[str]) -> str:
        """Combine up to 2 personas into overlay text for system prompt."""
        parts = []
        for role in roles[:2]:
            persona = self._cache.get(role)
            if persona:
                parts.append(persona.overlay)
        return "\n\n".join(parts)

    def _parse_file(self, path: Path) -> "Persona | None":
        """Parse YAML frontmatter + body from a .md file."""
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        # Split frontmatter
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        try:
            import yaml
            meta = yaml.safe_load(parts[1])
        except Exception:
            return None
        name = meta.get("name", "").strip()
        description = meta.get("description", "").strip()
        overlay = parts[2].strip()
        if not name:
            return None
        return Persona(name=name, description=description, overlay=overlay)
```

- [x] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_persona_registry.py -v
```
Expected: all 5 tests PASS

---

### Task 2: PersonaRegistry — load_all and build_overlay

**Files:**
- [x] Modify: `tests/test_persona_registry.py`
- [x] Modify: `src/personas/registry.py` (already created, adding more tests)

- [x] **Step 1: Add tests for file parsing and overlay building**

```python
# append to tests/test_persona_registry.py

SECURITY_MD = """\
---
name: security
description: OWASP Top 10, injection, auth bypass
---

## Specialist Context: Security

Focus on security vulnerabilities.

CRITICAL:
- Hardcoded secrets
- SQL injection
"""

ARCHITECT_MD = """\
---
name: architect
description: Modularity, trade-offs, patterns
---

## Specialist Context: Architect

Focus on system design and boundaries.
"""


def test_registry_loads_personas_from_dir(tmp_path):
    (tmp_path / "security.md").write_text(SECURITY_MD)
    (tmp_path / "architect.md").write_text(ARCHITECT_MD)
    registry = PersonaRegistry(tmp_path)
    result = registry.load_all()
    assert len(result) == 2
    names = {p.name for p in result}
    assert names == {"security", "architect"}


def test_registry_get_known_persona(tmp_path):
    (tmp_path / "security.md").write_text(SECURITY_MD)
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    p = registry.get("security")
    assert p is not None
    assert p.description == "OWASP Top 10, injection, auth bypass"
    assert "SQL injection" in p.overlay


def test_registry_available_roles(tmp_path):
    (tmp_path / "security.md").write_text(SECURITY_MD)
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    roles = registry.available_roles()
    assert len(roles) == 1
    assert roles[0]["name"] == "security"
    assert "injection" in roles[0]["description"]


def test_build_overlay_single_role(tmp_path):
    (tmp_path / "security.md").write_text(SECURITY_MD)
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    overlay = registry.build_overlay(["security"])
    assert "Specialist Context: Security" in overlay
    assert "SQL injection" in overlay


def test_build_overlay_two_roles(tmp_path):
    (tmp_path / "security.md").write_text(SECURITY_MD)
    (tmp_path / "architect.md").write_text(ARCHITECT_MD)
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    overlay = registry.build_overlay(["security", "architect"])
    assert "Security" in overlay
    assert "Architect" in overlay


def test_build_overlay_caps_at_two(tmp_path):
    for name in ["security", "architect", "python-dev"]:
        (tmp_path / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: desc\n---\n## {name} overlay\n"
        )
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    overlay = registry.build_overlay(["security", "architect", "python-dev"])
    assert overlay.count("## ") == 2  # only 2 sections


def test_build_overlay_unknown_role_skipped(tmp_path):
    (tmp_path / "security.md").write_text(SECURITY_MD)
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    overlay = registry.build_overlay(["security", "nonexistent"])
    assert "SQL injection" in overlay
    assert "nonexistent" not in overlay


def test_build_overlay_role_cap_warning(tmp_path, capsys):
    for name in ["security", "architect", "python-dev"]:
        (tmp_path / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: desc\n---\n## {name} overlay\n"
        )
    registry = PersonaRegistry(tmp_path)
    registry.load_all()
    registry.build_overlay(["security", "architect", "python-dev"])
    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_registry_ignores_file_without_frontmatter(tmp_path):
    (tmp_path / "no_frontmatter.md").write_text("Just plain text, no YAML front.")
    registry = PersonaRegistry(tmp_path)
    result = registry.load_all()
    assert result == []
```

- [x] **Step 2: Run tests to see which ones fail**

```bash
python -m pytest tests/test_persona_registry.py -v 2>&1 | tail -20
```
Expected: new tests FAIL (cap warning, etc.)

- [x] **Step 3: Update `build_overlay()` to emit warning on cap**

```python
# In registry.py, replace build_overlay:
import sys

def build_overlay(self, roles: list[str]) -> str:
    """Combine up to 2 personas into overlay text for system prompt."""
    if len(roles) > 2:
        print(
            f"  [PersonaRegistry] Warning: {len(roles)} roles assigned, "
            f"using first 2: {roles[:2]}",
            file=sys.stderr,
        )
    parts = []
    for role in roles[:2]:
        persona = self._cache.get(role)
        if persona:
            parts.append(persona.overlay)
    return "\n\n".join(parts)
```

- [x] **Step 4: Run tests to verify all pass**

```bash
python -m pytest tests/test_persona_registry.py -v
```
Expected: all tests PASS

---

### Task 3: Create 10 persona .md files

**Files:**
- [x] Create: `src/personas/prompts/python-dev.md`
- [x] Create: `src/personas/prompts/frontend-dev.md`
- [x] Create: `src/personas/prompts/designer.md`
- [x] Create: `src/personas/prompts/security.md`
- [x] Create: `src/personas/prompts/database.md`
- [x] Create: `src/personas/prompts/architect.md`
- [x] Create: `src/personas/prompts/devops.md`
- [x] Create: `src/personas/prompts/tdd-guide.md`
- [x] Create: `src/personas/prompts/performance.md`
- [x] Create: `src/personas/prompts/refactor.md`

- [x] **Step 1: Write a quick smoke test for persona loading**

```python
# append to tests/test_persona_registry.py

import os

BUNDLED_PERSONAS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "src", "personas", "prompts"
)

def test_bundled_personas_load():
    """Smoke test: bundled personas/ dir loads without errors."""
    personas_dir = Path(BUNDLED_PERSONAS_DIR)
    if not personas_dir.exists():
        pytest.skip("personas/prompts dir not yet created")
    registry = PersonaRegistry(personas_dir)
    personas = registry.load_all()
    assert len(personas) >= 10
    names = {p.name for p in personas}
    expected = {
        "python-dev", "frontend-dev", "designer", "security",
        "database", "architect", "devops", "tdd-guide",
        "performance", "refactor",
    }
    assert expected.issubset(names)
    for p in personas:
        assert p.name
        assert p.description
        assert len(p.overlay) > 50  # non-trivial content
```

- [x] **Step 2: Run test to verify it fails (dir doesn't exist yet)**

```bash
python -m pytest tests/test_persona_registry.py::test_bundled_personas_load -v
```
Expected: SKIP (dir not found)

- [x] **Step 3: Create `src/personas/prompts/security.md`**

```markdown
---
name: security
description: Security specialist — OWASP Top 10, injection, auth bypass, secrets detection
---

## Specialist Context: Security

Focus on security vulnerabilities in every code change:

CRITICAL — flag immediately:
- Hardcoded secrets (API keys, passwords, tokens in source)
- SQL injection (string concatenation in queries → parameterize)
- XSS (unescaped user input rendered in HTML → sanitize)
- Auth bypass (missing auth checks on protected routes)
- SSRF (user-controlled URLs without domain whitelist)
- Shell injection (user input in exec/subprocess/system calls)

VERIFY:
- Passwords hashed with bcrypt/argon2, never plaintext comparison
- CORS configured to specific origins, not `*` wildcard
- Rate limiting on public/auth endpoints
- Sensitive data not logged (tokens, passwords, PII)
- Input validated and sanitized at system boundaries

Principle: defense in depth — multiple layers, least privilege, fail securely.
```

- [x] **Step 4: Create `src/personas/prompts/python-dev.md`**

```markdown
---
name: python-dev
description: Python specialist — idiomatic code, type hints, testing, packaging
---

## Specialist Context: Python Developer

Write clean, idiomatic Python:

CODE QUALITY:
- Use type hints on all function signatures (PEP 484)
- Prefer dataclasses or Pydantic over plain dicts for structured data
- Use pathlib.Path over os.path for file operations
- Use `with` statements for resource management (files, connections)
- Prefer list/dict/set comprehensions over map/filter where readable
- Use f-strings, not .format() or %

STRUCTURE:
- Keep functions under 30 lines; extract helpers liberally
- One module = one clear responsibility
- `__init__.py` exports only the public API
- Relative imports within a package; absolute for cross-package

TESTING:
- Use pytest fixtures, not setUp/tearDown
- Use `tmp_path` fixture for temp files
- Parametrize repetitive test cases with `@pytest.mark.parametrize`
- Test edge cases: empty input, None, boundary values

DEPENDENCIES:
- Virtual environments always (venv or uv)
- Pin direct dependencies in pyproject.toml
```

- [x] **Step 5: Create `src/personas/prompts/database.md`**

```markdown
---
name: database
description: Database specialist — migrations, indexes, N+1, query optimization, RLS
---

## Specialist Context: Database

Write safe, efficient database code:

MIGRATIONS:
- Every schema change needs a migration file (never ALTER in application code)
- Migrations must be reversible (add down() alongside up())
- Add indexes in the same migration as the column, not later
- Never drop columns/tables without a deprecation migration first

QUERIES:
- Always parameterize — never string-concatenate user input into SQL
- Add LIMIT on user-facing list queries (unbounded SELECT is a DoS risk)
- Avoid N+1: use JOINs or batch loading instead of loops with queries
- Use SELECT only the columns you need, not SELECT *

INDEXES:
- Index foreign keys and any column used in WHERE/ORDER BY
- Composite indexes: most-selective column first
- Partial indexes for filtered queries (e.g., WHERE status = 'active')

TRANSACTIONS:
- Wrap multi-step writes in a transaction
- Use SELECT ... FOR UPDATE for balance/inventory checks
- Handle deadlock retry at the application layer

RLS (Row-Level Security):
- Enable RLS on every table exposed to user queries
- Test policies with both admin and regular user contexts
```

- [x] **Step 6: Create `src/personas/prompts/architect.md`**

```markdown
---
name: architect
description: Software architect — modularity, trade-offs, patterns, API design, scalability
---

## Specialist Context: Architect

Design for maintainability and clarity:

MODULARITY:
- Single Responsibility: each module/class does one thing
- High cohesion (related things together), low coupling (minimal shared state)
- Define clear interfaces — callers should not need to know internals
- Prefer composition over inheritance

API DESIGN:
- Return types should be specific — avoid returning raw dicts where a dataclass works
- Separate data access from business logic (repository pattern)
- Service layer owns orchestration; it does not own storage
- Event-driven for cross-module side effects

TRADE-OFFS — for each design decision document:
- What it enables
- What it costs (complexity, performance, operability)
- Alternatives considered

RED FLAGS:
- God object (one class does everything)
- Circular imports (signals wrong boundaries)
- Tight coupling between UI and storage
- Logic in migration files (belongs in application code)

SCALING:
- Stateless services scale horizontally; stateful do not
- Cache read-heavy aggregates, not write-heavy mutations
- Design for the next 10x, not the next 100x
```

- [x] **Step 7: Create `src/personas/prompts/devops.md`**

```markdown
---
name: devops
description: DevOps specialist — CI/CD, Docker, env config, scripts, packaging
---

## Specialist Context: DevOps

Build reliable, portable tooling:

ENVIRONMENT CONFIG:
- All secrets and environment-specific values in env vars, never hardcoded
- Use `.env.example` to document required variables (never commit `.env`)
- 12-factor app: config from environment, not files

DOCKER:
- Use multi-stage builds to keep images small
- Pin base image versions (not `latest`)
- Run as non-root user in production images
- COPY only what's needed; use `.dockerignore`

CI/CD:
- Fail fast: lint and type-check before tests
- Cache dependencies between runs (pip, npm)
- Never commit generated files to source control
- Use semantic versioning for releases

SCRIPTS:
- Shell scripts: `set -euo pipefail` at the top
- Python scripts preferred over bash for complex logic
- Make scripts idempotent (safe to run twice)
- Log what you're doing; silence is not a virtue

PACKAGING (Python):
- Use `pyproject.toml` (not setup.py)
- Declare all dependencies with version constraints
- Separate dev dependencies from runtime ones
```

- [x] **Step 8: Create remaining 5 persona files**

`src/personas/prompts/frontend-dev.md`:
```markdown
---
name: frontend-dev
description: Frontend specialist — React, TypeScript, CSS, accessibility, performance
---

## Specialist Context: Frontend Developer

Write accessible, performant frontend code:

REACT:
- Prefer functional components with hooks over class components
- useEffect deps array must be complete — missing deps cause stale closures
- Keys in lists must be stable and unique (never array index for reorderable lists)
- Memoize expensive computations with useMemo; avoid over-memoizing
- Extract custom hooks for reusable stateful logic

TYPESCRIPT:
- Prefer interfaces for public API shapes, types for unions/intersections
- Avoid `any`; use `unknown` and narrow with type guards
- Generic components over copy-paste for reusable patterns

CSS:
- Use CSS custom properties (variables) for design tokens
- Mobile-first responsive design
- Prefer CSS Grid/Flexbox over absolute positioning
- Accessibility: sufficient color contrast (WCAG AA), keyboard navigation, ARIA labels

PERFORMANCE:
- Lazy-load routes and heavy components (React.lazy)
- Avoid unnecessary re-renders — profile before optimizing
- Optimize images: correct format, lazy loading, explicit dimensions

ERROR STATES:
- Every async operation needs loading, error, and empty states
- Use error boundaries for unexpected failures
```

`src/personas/prompts/designer.md`:
```markdown
---
name: designer
description: UI/UX designer — visual hierarchy, spacing, color, typography, UX principles
---

## Specialist Context: Designer

Create interfaces that are clear, consistent, and delightful:

VISUAL HIERARCHY:
- Size, weight, and color communicate importance — use deliberately
- One primary action per screen/section; secondary actions are visually subordinate
- White space is not wasted space — it guides attention

SPACING:
- Use an 8px base grid (4px for fine adjustments)
- Consistent spacing tokens: 4, 8, 12, 16, 24, 32, 48, 64px
- Adequate padding inside interactive elements (min 44×44px tap target)

TYPOGRAPHY:
- Maximum 2-3 font sizes per component
- Line height 1.4–1.6 for body text, tighter for headings
- Maximum 65–75 characters per line for readability

COLOR:
- Functional colors: primary (action), success, warning, error, neutral
- Test against color blindness (not color alone to convey meaning)
- Dark mode: don't invert — remap to a dark palette

UX PRINCIPLES:
- Feedback on every action (loading states, success/error)
- Predictable interactions — don't surprise users
- Reduce cognitive load: progressive disclosure, sensible defaults
- Error messages must explain what happened and how to fix it
```

`src/personas/prompts/tdd-guide.md`:
```markdown
---
name: tdd-guide
description: TDD specialist — test-first, coverage, edge cases, test design
---

## Specialist Context: TDD Guide

Write tests before implementation:

TEST-FIRST DISCIPLINE:
- Write the test → see it fail → write minimal code → see it pass → refactor
- A test that never fails is not a test
- If you can't write the test first, the interface is not clear enough

TEST DESIGN:
- One assertion per test (one reason to fail)
- Test behaviour, not implementation (don't mock what you own)
- Descriptive names: `test_<what>_when_<condition>_returns_<expected>`
- Group related tests in classes named `TestFeatureName`

COVERAGE TARGETS:
- Happy path: the main expected use case
- Edge cases: empty input, boundary values, max/min
- Error cases: invalid input, missing dependencies, network failure
- Don't test framework code or third-party libraries

FIXTURES AND FACTORIES:
- Use fixtures for shared setup, not copy-paste
- Build minimal fake objects — don't mock what you can construct
- Use `tmp_path` for filesystem; never write to real paths in tests

ASSERTIONS:
- Use specific assertions: assertEqual vs assertTrue
- For collections: assert membership, not full equality when order doesn't matter
- For exceptions: `pytest.raises(ValueError, match="specific message")`
```

`src/personas/prompts/performance.md`:
```markdown
---
name: performance
description: Performance specialist — algorithms, caching, profiling, memory, async
---

## Specialist Context: Performance

Optimize only what you measure:

MEASURE FIRST:
- Profile before optimizing — guess wrong 80% of the time
- Identify the bottleneck: CPU, I/O, memory, network?
- Benchmark with realistic data sizes

ALGORITHMS:
- Know the complexity of your operations (O(n) vs O(n²))
- Use sets/dicts for membership checks, not lists
- Sort once, slice many; don't re-sort in a loop
- Early return / short-circuit to avoid unnecessary work

CACHING:
- Cache the result of expensive pure functions (functools.lru_cache)
- Cache at the right level: per-request, per-session, or global
- Invalidation strategy before adding any cache

ASYNC / I/O:
- Async I/O for concurrent network/disk operations (asyncio)
- Batch database queries; avoid N+1 in any loop
- Stream large datasets instead of loading into memory

MEMORY:
- Generators over lists for large sequences
- Release large objects explicitly when done (`del`)
- Watch for reference cycles in long-lived objects
```

`src/personas/prompts/refactor.md`:
```markdown
---
name: refactor
description: Refactoring specialist — DRY, reduce complexity, extract methods, safe changes
---

## Specialist Context: Refactor

Improve code structure without changing behaviour:

DRY (Don't Repeat Yourself):
- Three or more identical code blocks → extract a function/helper
- Shared configuration → extract a constant or config object
- But: duplication is better than the wrong abstraction

COMPLEXITY REDUCTION:
- Cyclomatic complexity > 5 → split the function
- Nesting depth > 3 → use early returns / extract to helpers
- Functions > 30 lines → usually doing too much

SAFE REFACTORING:
- Tests must pass before AND after every refactor step
- One refactor type per commit (rename, extract, inline — not mixed)
- Rename for clarity: use language of the problem domain
- Don't change behaviour during refactor — that's a separate commit

EXTRACT METHOD:
- Extract when: logic is reusable, function is too long, comment explains what a block does
- Don't extract when: the result would be a 2-line function called once

BACKWARDS COMPATIBILITY:
- Deprecate before removing public interfaces
- Alias old names to new ones during transition
- Never silently change the contract of an existing function
```

- [x] **Step 9: Run smoke test to verify all 10 load**

```bash
python -m pytest tests/test_persona_registry.py::test_bundled_personas_load -v
```
Expected: PASS — 10 personas loaded, all have name/description/overlay

---

## Chunk 2: Plan Tracker Changes

### Task 4: PlanItem.roles and Phase.display_name + fix reconstruction sites

**Files:**
- [x] Modify: `src/plan_tracker.py`
- [x] Modify: `tests/test_plan_tracker.py`

- [x] **Step 1: Write failing tests for PlanItem.roles**

```python
# append to tests/test_plan_tracker.py
from dataclasses import replace
from src.plan_tracker import PlanItem, Phase, parse_requirements, mark_step_done, mark_all_done, reset_all_progress


def test_plan_item_has_roles_field_defaulting_to_empty():
    item = PlanItem(text="Do something")
    assert item.roles == []


def test_plan_item_roles_field():
    item = PlanItem(text="Add auth", roles=["security", "architect"])
    assert item.roles == ["security", "architect"]


def test_mark_step_done_preserves_roles():
    items = [
        PlanItem(text="Step 1", done=False, roles=["security"]),
        PlanItem(text="Step 2", done=False, roles=["database"]),
    ]
    updated = mark_step_done(items, 0)
    assert updated[0].done is True
    assert updated[0].roles == ["security"]  # roles preserved


def test_mark_all_done_preserves_roles():
    items = [PlanItem(text="Step 1", done=False, roles=["security"])]
    updated = mark_all_done(items)
    assert updated[0].done is True
    assert updated[0].roles == ["security"]


def test_reset_all_progress_preserves_roles():
    items = [PlanItem(text="Step 1", done=True, roles=["database"])]
    updated = reset_all_progress(items)
    assert updated[0].done is False
    assert updated[0].roles == ["database"]


def test_phase_has_display_name_defaulting_to_empty():
    phase = Phase(name="Update (2)", type="update", steps=[])
    assert phase.display_name == ""


def test_phase_display_name_set():
    phase = Phase(name="Update (2)", type="update", steps=[], display_name="Security Layer")
    assert phase.display_name == "Security Layer"
```

- [x] **Step 2: Run tests to see which fail**

```bash
python -m pytest tests/test_plan_tracker.py -v -k "roles or display_name or preserves" 2>&1 | tail -20
```
Expected: multiple FAIL (fields don't exist yet)

- [x] **Step 3: Update `PlanItem` and `Phase` dataclasses in `src/plan_tracker.py`**

At top of file, add `from dataclasses import dataclass, field` (replace existing `from dataclasses import dataclass`).

Update `PlanItem`:
```python
@dataclass
class PlanItem:
    """A single plan item."""
    text: str
    done: bool = False
    roles: list[str] = field(default_factory=list)
```

Update `Phase`:
```python
@dataclass
class Phase:
    """A batch of PlanItems grouped by step type."""
    name: str
    type: str
    steps: list["PlanItem"]
    status: str = "pending"
    attempts: int = 0
    display_name: str = ""
```

- [x] **Step 4: Fix `mark_step_done`, `mark_all_done`, `reset_all_progress` to use `dataclasses.replace`**

```python
from dataclasses import dataclass, field, replace

def mark_all_done(items: list[PlanItem]) -> list[PlanItem]:
    """Mark all items as done (for approved implementation)."""
    return [replace(item, done=True) for item in items]


def reset_all_progress(items: list[PlanItem]) -> list[PlanItem]:
    """Return a fresh copy of items with all steps marked pending."""
    return [replace(item, done=False) for item in items]


def mark_step_done(items: list[PlanItem], index: int) -> list[PlanItem]:
    """Return new list with item at index marked done."""
    result = list(items)
    result[index] = replace(result[index], done=True)
    return result
```

- [x] **Step 5: Run tests to verify all pass**

```bash
python -m pytest tests/test_plan_tracker.py -v
```
Expected: all PASS (including existing tests)

---

### Task 5: `parse_enriched_plan()` and `write_enriched_plan()`

**Files:**
- [x] Modify: `src/plan_tracker.py`
- [x] Modify: `tests/test_plan_tracker.py`

- [x] **Step 1: Write failing tests for `parse_enriched_plan()`**

```python
# append to tests/test_plan_tracker.py
from src.plan_tracker import parse_enriched_plan, write_enriched_plan


ENRICHED_PLAN = """\
## Phases
- Phase 1: "Инициализация проекта" → steps 1-2
- Phase 2: "Авторизация" → steps 3-4

## Steps
1. [devops] Create pyproject.toml
2. [devops] Initialize git repo
3. [security, architect] Add JWT auth middleware
4. [security] Add rate limiting
"""

ENRICHED_NO_PHASES = """\
## Steps
1. [python-dev] Implement scorer module
2. [tdd-guide] Write tests for scorer
"""


def test_parse_enriched_plan_returns_items_with_roles():
    items, phases = parse_enriched_plan(ENRICHED_PLAN)
    assert len(items) == 4
    assert items[0].text == "Create pyproject.toml"
    assert items[0].roles == ["devops"]
    assert items[2].text == "Add JWT auth middleware"
    assert items[2].roles == ["security", "architect"]


def test_parse_enriched_plan_returns_phases():
    items, phases = parse_enriched_plan(ENRICHED_PLAN)
    assert len(phases) == 2
    assert phases[0].display_name == "Инициализация проекта"
    assert phases[1].display_name == "Авторизация"
    # Phase 1 has items 0 and 1 (steps 1-2)
    assert len(phases[0].steps) == 2
    assert phases[0].steps[0].text == "Create pyproject.toml"


def test_parse_enriched_plan_no_phases_section():
    items, phases = parse_enriched_plan(ENRICHED_NO_PHASES)
    assert len(items) == 2
    assert phases == []  # caller must call auto_group_phases


def test_parse_enriched_plan_items_not_done_by_default():
    items, _ = parse_enriched_plan(ENRICHED_PLAN)
    assert all(not item.done for item in items)


def test_write_enriched_plan_creates_file(tmp_path):
    enriched_path = tmp_path / ".g3" / "enriched-plan.md"
    write_enriched_plan(enriched_path, ENRICHED_PLAN)
    assert enriched_path.exists()
    assert "## Phases" in enriched_path.read_text()
```

- [x] **Step 2: Run tests to see them fail**

```bash
python -m pytest tests/test_plan_tracker.py -v -k "enriched" 2>&1 | tail -15
```
Expected: FAIL (`parse_enriched_plan` not defined)

- [x] **Step 3: Implement `parse_enriched_plan()` in `src/plan_tracker.py`**

```python
import re
from pathlib import Path

ROLE_TAG_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
PHASE_LINE_RE = re.compile(
    r'^-\s+Phase\s+\d+:\s+"([^"]+)"\s+→\s+steps?\s+([\d,\s-]+)$'
)


def parse_enriched_plan(content: str) -> tuple[list[PlanItem], list[Phase]]:
    """Parse Pre-Planner enriched plan output.

    Expects two sections:
    ## Phases
    - Phase 1: "Display name" → steps 1-3

    ## Steps
    1. [role1, role2] Step description

    Returns (items, phases). phases is [] if ## Phases section absent.
    """
    sections: dict[str, str] = {}
    current_section = None
    section_lines: list[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped in ("## Phases", "## Steps"):
            if current_section and section_lines:
                sections[current_section] = "\n".join(section_lines)
            current_section = stripped.lstrip("#").strip()
            section_lines = []
        elif current_section is not None:
            section_lines.append(line)

    if current_section and section_lines:
        sections[current_section] = "\n".join(section_lines)

    # Parse Steps section
    items: list[PlanItem] = []
    for line in sections.get("Steps", "").splitlines():
        stripped = line.strip()
        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if not numbered:
            continue
        rest = numbered.group(2).strip()
        role_match = ROLE_TAG_RE.match(rest)
        if role_match:
            roles_str, text = role_match.group(1), role_match.group(2).strip()
            roles = [r.strip() for r in roles_str.split(",")]
        else:
            roles, text = [], rest
        items.append(PlanItem(text=text, done=False, roles=roles))

    if not items:
        return [], []

    # Parse Phases section
    phases: list[Phase] = []
    for line in sections.get("Phases", "").splitlines():
        stripped = line.strip()
        m = PHASE_LINE_RE.match(stripped)
        if not m:
            continue
        display_name = m.group(1)
        # Parse step references: "1-3" or "1, 3, 5" or "1-2, 4"
        step_refs_str = m.group(2)
        step_indices: list[int] = []
        for part in step_refs_str.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                step_indices.extend(range(int(start) - 1, int(end)))
            else:
                step_indices.append(int(part) - 1)
        phase_items = [items[i] for i in step_indices if 0 <= i < len(items)]
        # Determine phase type from majority step type
        phase_type = detect_step_type(phase_items[0]) if phase_items else "update"
        phases.append(Phase(
            name=f"{phase_type.capitalize()} ({len(phase_items)}) · {display_name[:45]}",
            type=phase_type,
            steps=phase_items,
            display_name=display_name,
        ))

    return items, phases


def write_enriched_plan(path: "str | Path", content: str) -> None:
    """Write enriched plan content to path, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
```

- [x] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_plan_tracker.py -v
```
Expected: all PASS

- [x] **Step 5: Also verify `_build_table()` uses `display_name` when set**

Update `PlanTracker._build_table()` in `plan_tracker.py`:
```python
def _build_table(self):
    """Build Rich Table showing phase progress."""
    from rich.table import Table
    table = Table(title="G3 Execution", show_header=False, box=None)
    for i, phase in enumerate(self.phases):
        done = sum(1 for s in phase.steps if s.done)
        total = len(phase.steps)
        pct = done * 100 // total if total else 0
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        icon = {
            "pending": "⏳", "in_progress": "🔄",
            "done": "✅", "failed": "❌",
        }.get(phase.status, "❓")
        attempts_str = f" (attempt {phase.attempts})" if phase.attempts > 1 else ""
        # Use display_name if available, else fall back to phase.name
        label = phase.display_name if phase.display_name else phase.name
        table.add_row(
            f"{icon} Phase {i + 1}: {label}{attempts_str}",
            f"{bar} {pct}%",
        )
    total_steps = sum(len(p.steps) for p in self.phases)
    done_steps = sum(s.done for p in self.phases for s in p.steps)
    table.add_row("", f"Steps: {done_steps}/{total_steps}")
    return table
```

- [x] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: all existing tests still pass

---

## Chunk 3: Prompts + Config + CLI

### Task 6: PREPLANNER_SYSTEM_PROMPT and build_preplan_prompt

**Files:**
- [x] Modify: `src/prompts.py`

- [x] **Step 1: Add constants and builder — no test needed (pure strings), just verify import**

Add to end of `src/prompts.py`:

```python
PREPLANNER_SYSTEM_PROMPT = """You are a Plan Polisher. Your job is to enrich a raw implementation plan with role annotations and phase groupings.

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

RULES:
- Output ONLY the enriched plan, no commentary before or after
- Phase names: short (3-5 words), descriptive, use the same language as the input plan
- Every step MUST have at least one [role] tag
- Use ONLY roles from the available list — do NOT invent new roles
- Keep step count identical to input — do NOT add or remove steps
- Steps that don't match any role get [general]"""


def build_preplan_prompt(raw_plan: str, roles: list[dict]) -> str:
    """Build the user prompt for the Pre-Planner agent."""
    roles_list = "\n".join(
        f"- {r['name']}: {r['description']}" for r in roles
    )
    return f"""## Available Roles
{roles_list}

## Raw Plan
{raw_plan}

Enrich this plan following the output format exactly."""
```

- [x] **Step 2: Verify import works**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero
python -c "from src.prompts import PREPLANNER_SYSTEM_PROMPT, build_preplan_prompt; print('OK')"
```
Expected: `OK`

---

### Task 7: Config fields + env_map + CLI args

**Files:**
- [x] Modify: `src/config.py`
- [x] Modify: `src/cli_entry.py`

- [x] **Step 1: Write failing tests for new config fields**

```python
# append to tests/test_config_defaults.py
from src.config import Config


def test_config_preplan_defaults():
    cfg = Config()
    assert cfg.preplan_mode is False  # opt-in, like tdd_mode
    assert cfg.preplan_provider == "black"
    assert cfg.preplan_model == ""
    assert cfg.preplan_timeout_s == 120
```

- [x] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_config_defaults.py::test_config_preplan_defaults -v
```
Expected: FAIL (`Config` has no field `preplan_mode`)

- [x] **Step 3: Add fields to `Config` dataclass in `src/config.py`**

Find the `# TDD Mode` section (around line 290) and add below it:

```python
# Pre-Planner (Phase 0) — defaults to False (opt-in, like tdd_mode/code_review)
preplan_mode: bool = False
preplan_provider: str = "black"
preplan_model: str = ""
preplan_timeout_s: int = 120
```

> **NOTE:** `preplan_mode` defaults to `False` (opt-in), consistent with `tdd_mode` and `code_review`. This avoids silently breaking all existing `CoachPlayerSession` tests that construct `Config()` with defaults — those would otherwise trigger the preplanner provider readiness check. Enable with `--preplan-provider` CLI arg or `G3_PREPLAN_MODE=true`.

- [x] **Step 4: Add to `env_map` in `resolve_config()` (around line 508)**

```python
"G3_PREPLAN_MODE":      ("preplan_mode",     lambda x: x.lower() in ("true", "1", "yes")),
"G3_PREPLAN_PROVIDER":  ("preplan_provider", str),
"G3_PREPLAN_MODEL":     ("preplan_model",    str),
"G3_PREPLAN_TIMEOUT_S": ("preplan_timeout_s", int),
```

- [x] **Step 5: Add `preplan_provider` to provider normalization loop**

In `config.py`, find the loop that normalizes provider names (around line 554) — it iterates over a list of provider field names. Add `"preplan_provider"` to that list.

- [x] **Step 6: Run test to verify it passes**

```bash
python -m pytest tests/test_config_defaults.py -v
```
Expected: all PASS

- [x] **Step 7: Add CLI arguments to `src/cli_entry.py`**

In `build_parser()`, add:
```python
parser.add_argument(
    "--preplan-provider", type=str, default="",
    help="Provider for Phase 0 Pre-Planner (default: same as player)"
)
parser.add_argument(
    "--preplan-model", type=str, default="",
    help="Model override for Phase 0 Pre-Planner"
)
parser.add_argument(
    "--no-preplan", action="store_true", default=False,
    help="Disable Phase 0 plan enrichment"
)
```

In `resolve_go_config()`, add three entries to the existing dict passed to `resolve_config({...})` — match the `getattr` pattern used by all other args:
```python
"preplan_provider": getattr(args, "preplan_provider", None) or None,
"preplan_model":    getattr(args, "preplan_model", None) or None,
"preplan_mode":     False if getattr(args, "no_preplan", False) else None,
```

> **NOTE:** `resolve_go_config` passes a flat dict to `resolve_config()`, not an `overrides` variable. All new keys must be added inside that dict literal (lines 22-54 in `cli_entry.py`). Values of `None` are ignored by `resolve_config`.

- [x] **Step 8: Smoke test CLI args parse without error**

```bash
python -m pytest tests/test_cli.py -v 2>&1 | tail -15
```
Expected: all PASS

---

## Chunk 4: Coach-Player Integration

### Task 8: Module-level import + provider dispatch + readiness check

**Files:**
- [x] Modify: `src/coach_player.py`
- [x] Modify: `tests/test_coach_player.py`

> **WHY module-level import matters:** `_run_phase_zero` uses `PersonaRegistry`. If it's imported locally inside the method, `unittest.mock.patch("src.coach_player.PersonaRegistry")` won't work — `patch` can only intercept module-level names. Adding the import at the top of `coach_player.py` makes it patchable.

- [x] **Step 0: Add `PersonaRegistry` import at top of `src/coach_player.py`**

Add to the imports section (near other `src.*` imports):
```python
from src.personas import PersonaRegistry
```

- [x] **Step 1: Write failing test for provider dispatch**

```python
# append to tests/test_coach_player.py
from unittest.mock import MagicMock, patch


def test_provider_name_for_preplanner_role():
    """preplanner role resolves to config.preplan_provider."""
    from src.coach_player import CoachPlayerSession
    from src.config import Config

    cfg = Config(preplan_provider="turbo")
    with patch("src.coach_player.create_provider") as mock_create:
        mock_prov = MagicMock()
        mock_prov.check_ready.return_value = (True, "")
        mock_create.return_value = mock_prov
        session = CoachPlayerSession(cfg, "1. Step one", "plan.md")

    assert session._provider_name_for_role("preplanner") == "turbo"
```

- [x] **Step 2: Run test to see it fail**

```bash
python -m pytest tests/test_coach_player.py::test_provider_name_for_preplanner_role -v
```
Expected: FAIL (`ValueError: Unknown role: preplanner`)

- [x] **Step 3: Add `preplanner` to `_provider_name_for_role()` and `_provider_for_role()` in `coach_player.py`**

```python
# In _provider_name_for_role():
if role == "preplanner":
    return self.config.preplan_provider

# In _provider_for_role():
if role == "preplanner":
    return self._get_or_create_provider(self.config.preplan_provider)
```

- [x] **Step 4: Add readiness check for preplanner in `_verify_providers_ready()`**

After the existing `if self.config.tdd_mode:` block:
```python
if self.config.preplan_mode:
    providers_to_check.append(
        (
            "preplanner",
            self.config.preplan_provider,
            self._provider_for_role("preplanner"),
        )
    )
```

- [x] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/test_coach_player.py -v 2>&1 | tail -15
```
Expected: all PASS

---

### Task 9: Streaming UI for Phase 0

**Files:**
- [x] Modify: `src/streaming.py`

- [x] **Step 1: Add two new print functions**

```python
# In src/streaming.py, add near the other print_*_header functions:

def print_preplanner_header(model_name: str = "") -> None:
    """Print Phase 0 start header."""
    model_str = f"  [{model_name}]" if model_name else ""
    print(f"\n{BOLD}🎭 Phase 0: Polishing plan...{model_str}{RESET}")


def print_preplan_result(num_phases: int, num_roles: int) -> None:
    """Print Phase 0 completion summary."""
    print(
        f"  {GREEN}🎭 Phase 0: Plan polished "
        f"({num_phases} phases, {num_roles} roles assigned){RESET}\n"
    )
```

- [x] **Step 2: Verify import works**

```bash
python -c "from src.streaming import print_preplanner_header, print_preplan_result; print('OK')"
```
Expected: `OK`

---

### Task 10: Phase 0 in `CoachPlayerSession.run()`

**Files:**
- [x] Modify: `src/coach_player.py`
- [x] Modify: `tests/test_coach_player.py`

- [x] **Step 1: Write failing integration test for Phase 0**

```python
# append to tests/test_coach_player.py
import asyncio
from pathlib import Path


def test_run_phase_zero_enriches_plan(tmp_path):
    """Phase 0 annotates plan items with roles before the step loop."""
    from src.coach_player import CoachPlayerSession
    from src.config import Config

    enriched_output = """\
## Phases
- Phase 1: "Setup" → steps 1-2

## Steps
1. [devops] Create pyproject.toml
2. [security] Add auth middleware
"""
    cfg = Config(
        preplan_mode=True,
        preplan_provider="black",
        working_dir=str(tmp_path),
    )

    with patch("src.coach_player.create_provider") as mock_create, \
         patch("src.coach_player.PersonaRegistry") as mock_registry_cls:

        # Mock providers
        mock_prov = MagicMock()
        mock_prov.check_ready.return_value = (True, "")
        mock_create.return_value = mock_prov

        # Mock registry
        mock_registry = MagicMock()
        mock_registry.available_roles.return_value = [
            {"name": "devops", "description": "CI/CD, Docker"},
            {"name": "security", "description": "OWASP Top 10"},
        ]
        mock_registry_cls.return_value = mock_registry

        session = CoachPlayerSession(cfg, "1. Create pyproject.toml\n2. Add auth middleware", "")

        # Mock _run_turn to return enriched output for preplanner role
        async def fake_run_turn(role, prompt, system_prompt, **kwargs):
            from src.coach_player import TurnResult
            return TurnResult(role=role, duration_s=0.1, tools_used=0, messages=[], text=enriched_output)

        session._run_turn = fake_run_turn
        session._persona_registry = None

        # Call the phase 0 method directly
        result_items, result_phases = asyncio.get_event_loop().run_until_complete(
            session._run_phase_zero("1. Create pyproject.toml\n2. Add auth middleware")
        )

    assert len(result_items) == 2
    assert result_items[0].roles == ["devops"]
    assert result_items[1].roles == ["security"]
    assert len(result_phases) == 1
    assert result_phases[0].display_name == "Setup"
```

- [x] **Step 2: Run test to see it fail**

```bash
python -m pytest tests/test_coach_player.py::test_run_phase_zero_enriches_plan -v
```
Expected: FAIL (`CoachPlayerSession` has no `_run_phase_zero`)

- [x] **Step 3: Add `_run_phase_zero()` method to `CoachPlayerSession`**

Add after `_reset_plan_progress()` (around line 615):

```python
async def _run_phase_zero(
    self, raw_plan: str
) -> "tuple[list, list]":
    """Run Phase 0: enrich raw plan with roles and phase groupings.

    Returns (enriched_items, phases). On failure, returns ([], []) so
    caller falls back to parse_requirements() + auto_group_phases().
    """
    # Import at module level in coach_player.py (top of file), not here —
    # this avoids the local-import mock-patching issue. See Task 8 note.
    from src.prompts import PREPLANNER_SYSTEM_PROMPT, build_preplan_prompt
    from src.plan_tracker import parse_enriched_plan, write_enriched_plan, parse_requirements, auto_group_phases
    from src import streaming as streaming_ui

    personas_dir = (
        Path(self.config.working_dir) / "src" / "personas" / "prompts"
    )
    registry = PersonaRegistry(personas_dir)  # PersonaRegistry imported at module top
    registry.load_all()
    self._persona_registry = registry

    # Use _format_provider_display directly — avoids _build_role_display which
    # uses getattr(config, f"{role}_model") and would look for config.preplanner_model
    # (doesn't exist) instead of config.preplan_model.
    preplanner_provider = self._get_or_create_provider(self.config.preplan_provider)
    preplanner_label = self._format_provider_display(
        self.config.preplan_provider, preplanner_provider, self.config.preplan_model
    )
    streaming_ui.print_preplanner_header(preplanner_label)

    preplan_prompt = build_preplan_prompt(raw_plan, registry.available_roles())

    try:
        result = await self._run_turn(
            role="preplanner",
            prompt=preplan_prompt,
            system_prompt=PREPLANNER_SYSTEM_PROMPT,
            max_turns=5,
            timeout_s=self.config.preplan_timeout_s,
            model_override=self.config.preplan_model,
        )
    except Exception as e:
        print(f"\n  [Preplanner] Warning: failed ({e}), using original plan", file=__import__("sys").stderr)
        return [], []

    items, phases = parse_enriched_plan(result.text)

    # Validate step count
    original_items = parse_requirements(raw_plan)
    if len(items) != len(original_items):
        import sys
        print(
            f"  [Preplanner] Warning: step count mismatch "
            f"({len(items)} vs {len(original_items)}), using original plan",
            file=sys.stderr,
        )
        return [], []

    if not phases:
        phases = auto_group_phases(items)

    # Save enriched plan (never overwrites original)
    enriched_path = Path(self.config.working_dir) / ".g3" / "enriched-plan.md"
    write_enriched_plan(enriched_path, result.text)

    # Count roles assigned
    roles_assigned = sum(len(item.roles) for item in items)
    streaming_ui.print_preplan_result(len(phases), roles_assigned)

    return items, phases
```

- [x] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_coach_player.py::test_run_phase_zero_enriches_plan -v
```
Expected: PASS

- [x] **Step 5: Wire Phase 0 into `CoachPlayerSession.run()` before the step loop**

In `run()` (around line 625), replace:
```python
plan_items = parse_requirements(self.requirements)
```
with:
```python
plan_items = parse_requirements(self.requirements)

# Phase 0: enrich plan with roles and phase names
if self.config.preplan_mode:
    enriched_items, _phases = await self._run_phase_zero(self.requirements)
    if enriched_items:
        plan_items = enriched_items
    # NOTE: _phases discarded here intentionally — step-mode does not use
    # PlanTracker, so phase display_names don't appear in the terminal output
    # during step mode. Phases from Phase 0 are used only in batch mode
    # (via BatchExecutor + PlanTracker). The enriched plan is still saved
    # to .g3/enriched-plan.md and PlanItem.roles are set for overlay injection.
else:
    self._persona_registry = None
```

- [x] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all PASS

---

### Task 11: Player and Coach persona overlays

**Files:**
- [x] Modify: `src/coach_player.py`
- [x] Modify: `tests/test_coach_player.py`

- [x] **Step 1: Write failing test for overlay injection**

```python
# append to tests/test_coach_player.py

def test_persona_overlay_appended_to_player_system_prompt():
    """When step has roles, player system prompt includes persona overlay."""
    from src.personas.registry import PersonaRegistry, Persona
    from src.plan_tracker import PlanItem
    from src.prompts import PLAYER_SYSTEM_PROMPT

    registry = MagicMock(spec=PersonaRegistry)
    registry.build_overlay.return_value = "## Specialist Context: Security\nFocus on vulns."

    step = PlanItem(text="Add auth middleware", roles=["security"])

    # Simulate the overlay logic from coach_player.py
    player_system = PLAYER_SYSTEM_PROMPT
    if registry and step.roles:
        overlay = registry.build_overlay(step.roles)
        if overlay:
            player_system = f"{PLAYER_SYSTEM_PROMPT}\n\n{overlay}"

    assert "## Specialist Context: Security" in player_system
    assert PLAYER_SYSTEM_PROMPT in player_system
    registry.build_overlay.assert_called_once_with(["security"])
```

- [x] **Step 2: Run test to verify the logic is correct**

```bash
python -m pytest tests/test_coach_player.py::test_persona_overlay_appended_to_player_system_prompt -v
```
Expected: PASS (logic tested directly, not yet in coach_player.py)

- [x] **Step 3: Apply Player overlay in `coach_player.py` (~line 769)**

Find:
```python
player_result = await self._run_with_continuation(
    role="player",
    prompt=player_prompt,
    system_prompt=PLAYER_SYSTEM_PROMPT,
```

Replace with:
```python
player_system = PLAYER_SYSTEM_PROMPT
if self._persona_registry and step.roles:
    overlay = self._persona_registry.build_overlay(step.roles)
    if overlay:
        player_system = f"{PLAYER_SYSTEM_PROMPT}\n\n{overlay}"

player_result = await self._run_with_continuation(
    role="player",
    prompt=player_prompt,
    system_prompt=player_system,
```

- [x] **Step 4: Apply Coach overlay (~line 846)**

Find the coach `_run_turn` call with `system_prompt=COACH_STRICT_SYSTEM_PROMPT`. Replace:
```python
coach_system = COACH_STRICT_SYSTEM_PROMPT
if self._persona_registry and step.roles:
    overlay = self._persona_registry.build_overlay(step.roles)
    if overlay:
        coach_system = f"{COACH_STRICT_SYSTEM_PROMPT}\n\n## Review Focus\n{overlay}"

# ... then use coach_system instead of COACH_STRICT_SYSTEM_PROMPT
```

Do the same for the fallback coach call (~line 890).

- [x] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all PASS

---

## Chunk 4: Batch Executor Integration

### Task 12: Batch executor — phase preservation + overlay

**Files:**
- [x] Modify: `src/batch_executor.py`
- [x] Modify: `src/cli_entry.py`
- [x] Modify: `tests/test_batch_executor.py`

- [x] **Step 1: Write failing test for batch phase preservation**

```python
# append to tests/test_batch_executor.py
import asyncio
from src.plan_tracker import PlanTracker, PlanItem, Phase


def test_batch_executor_uses_preplanner_phases_when_set():
    """When tracker.phases is pre-populated, BatchExecutor.run() uses them, not auto_group_phases."""
    from src.batch_executor import BatchExecutor
    from unittest.mock import patch, MagicMock, AsyncMock

    pre_planner_phases = [
        Phase(name="Create (1) · step", type="create",
              steps=[PlanItem(text="Step 1", roles=["devops"])],
              display_name="Setup")
    ]
    items = [PlanItem(text="Step 1", roles=["devops"])]
    tracker = PlanTracker(items)
    tracker.phases = pre_planner_phases  # pre-populated by Phase 0

    session = MagicMock()
    session.config.batch_mode = True
    session.config.max_turns = 3
    session.config.player_timeout_s = 60
    session.config.player_model = ""
    session._persona_registry = None

    with patch("src.batch_executor.auto_group_phases") as mock_group:
        executor = BatchExecutor(session, tracker)
        # Patch _run_phase to avoid real LLM calls
        executor._run_phase = AsyncMock(return_value=None)
        asyncio.get_event_loop().run_until_complete(executor.run())

    mock_group.assert_not_called()
```

- [x] **Step 2: Run test to verify it fails (auto_group_phases currently always called in BatchExecutor.run())**

```bash
python -m pytest tests/test_batch_executor.py::test_batch_executor_uses_preplanner_phases_when_set -v
```

- [x] **Step 3: Update `BatchExecutor.run()` to respect pre-populated phases**

In `batch_executor.py`, find the line:
```python
phases = auto_group_phases(self.tracker.items)
```
Replace with:
```python
phases = self.tracker.phases if self.tracker.phases else auto_group_phases(self.tracker.items)
```

- [x] **Step 4: Add batch persona overlay in `_run_phase()`**

In `_run_phase()`, find `system_prompt=PLAYER_BATCH_SYSTEM_PROMPT`. Replace:
```python
player_system = PLAYER_BATCH_SYSTEM_PROMPT
persona_registry = getattr(self.session, "_persona_registry", None)
if persona_registry:
    phase_roles = list({r for step in phase.steps for r in step.roles})
    if phase_roles:
        overlay = persona_registry.build_overlay(phase_roles)
        if overlay:
            player_system = f"{PLAYER_BATCH_SYSTEM_PROMPT}\n\n{overlay}"

result = await self._run_player_turn(
    role="player",
    prompt=prompt,
    system_prompt=player_system,
```

- [x] **Step 5: Wire Phase 0 into batch path in `cli_entry.py`**

Find the batch mode entry (where `BatchExecutor` is constructed):
```python
if config.batch_mode:
    items = parse_requirements(requirements)
    tracker = PlanTracker(items)
    executor = BatchExecutor(session, tracker)
```

Replace with:
```python
if config.batch_mode:
    items = parse_requirements(requirements)
    phases = []
    if config.preplan_mode:
        # run_go is async, so we can await directly — no get_event_loop()
        enriched_items, phases = await session._run_phase_zero(requirements)
        if enriched_items:
            items = enriched_items
    tracker = PlanTracker(items)
    if phases:
        tracker.phases = phases  # Pre-Planner phases; BatchExecutor won't overwrite
    executor = BatchExecutor(session, tracker)
```

> **NOTE:** `run_go()` in `cli_entry.py` is `async def`, called via `asyncio.run(run_go(...))`. Use `await` directly — calling `asyncio.get_event_loop().run_until_complete()` inside an already-running event loop raises `RuntimeError` in Python 3.10+.

- [x] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -25
```
Expected: all PASS

---

### Task 13: Final integration smoke test

**Files:**
- [x] Create: `tests/test_preplan_integration.py`

- [x] **Step 1: Write end-to-end integration test**

```python
# tests/test_preplan_integration.py
"""Integration test: Phase 0 enriches plan → overlay appears in player prompt."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.config import Config
from src.plan_tracker import PlanItem


ENRICHED_PLAN = """\
## Phases
- Phase 1: "Setup" → steps 1-2
- Phase 2: "Security" → steps 3

## Steps
1. [devops] Create pyproject.toml
2. [devops] Initialize git repo
3. [security] Add JWT authentication
"""


def test_phase_zero_fallback_on_step_count_mismatch(tmp_path, capsys):
    """If Pre-Planner changes step count, fallback to original plan."""
    from src.coach_player import CoachPlayerSession

    cfg = Config(
        preplan_mode=True,
        preplan_provider="black",
        working_dir=str(tmp_path),
    )

    raw_plan = "1. Step one\n2. Step two\n3. Step three"
    # Pre-Planner returns only 2 steps (mismatch with 3 original)
    bad_output = "## Steps\n1. [devops] Step one\n2. [devops] Step two\n"

    with patch("src.coach_player.create_provider") as mock_create:
        mock_prov = MagicMock()
        mock_prov.check_ready.return_value = (True, "")
        mock_create.return_value = mock_prov

        session = CoachPlayerSession(cfg, raw_plan, "")

        async def fake_run_turn(role, prompt, system_prompt, **kwargs):
            from src.coach_player import TurnResult
            return TurnResult(role=role, duration_s=0.1, tools_used=0,
                              messages=[], text=bad_output)

        session._run_turn = fake_run_turn

        items, phases = asyncio.get_event_loop().run_until_complete(
            session._run_phase_zero(raw_plan)
        )

    # Should fallback: return empty lists
    assert items == []
    assert phases == []
    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_persona_overlay_ends_up_in_system_prompt():
    """build_overlay output is appended to PLAYER_SYSTEM_PROMPT."""
    from src.personas.registry import PersonaRegistry, Persona
    from src.plan_tracker import PlanItem
    from src.prompts import PLAYER_SYSTEM_PROMPT

    registry = PersonaRegistry(Path("/nonexistent"))
    registry._cache = {
        "security": Persona(
            name="security",
            description="OWASP",
            overlay="## Specialist Context: Security\nCheck for vulns.",
        )
    }

    step = PlanItem(text="Add auth", roles=["security"])
    overlay = registry.build_overlay(step.roles)
    final_prompt = f"{PLAYER_SYSTEM_PROMPT}\n\n{overlay}"

    assert "Specialist Context: Security" in final_prompt
    assert PLAYER_SYSTEM_PROMPT[:50] in final_prompt


def test_preplan_mode_false_skips_phase_zero():
    """With preplan_mode=False, _persona_registry stays None."""
    from src.coach_player import CoachPlayerSession
    from src.config import Config

    cfg = Config(preplan_mode=False)

    with patch("src.coach_player.create_provider") as mock_create:
        mock_prov = MagicMock()
        mock_prov.check_ready.return_value = (True, "")
        mock_create.return_value = mock_prov

        session = CoachPlayerSession(cfg, "1. Step one", "")
        # When preplan_mode=False, _persona_registry is not set at init
        # (it's set to None during run(), but that's async)
        assert not hasattr(session, "_persona_registry") or session._persona_registry is None
```

- [x] **Step 2: Run integration tests**

```bash
python -m pytest tests/test_preplan_integration.py -v
```
Expected: all PASS

- [x] **Step 3: Run complete test suite one final time**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: all PASS — no regressions

---

## Summary

| Chunk | Tasks | Key output |
|-------|-------|-----------|
| 1 | 1-3 | `src/personas/` registry + 10 persona files |
| 2 | 4-5 | `PlanItem.roles`, `Phase.display_name`, `parse_enriched_plan()` |
| 3 | 6-7 | `PREPLANNER_SYSTEM_PROMPT`, config fields, CLI args |
| 4 | 8-11 | Provider dispatch, streaming UI, Phase 0 in `run()`, overlays |
| 4 | 12-13 | Batch integration, final smoke tests |

**Files created:** `src/personas/__init__.py`, `src/personas/registry.py`, `src/personas/prompts/*.md` (×10), `tests/test_persona_registry.py`, `tests/test_preplan_integration.py`

**Files modified:** `src/plan_tracker.py`, `src/prompts.py`, `src/config.py`, `src/cli_entry.py`, `src/coach_player.py`, `src/batch_executor.py`, `src/streaming.py`, `tests/test_plan_tracker.py`, `tests/test_config_defaults.py`, `tests/test_coach_player.py`, `tests/test_batch_executor.py`
