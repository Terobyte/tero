# Plan: greenhouse-phase-a-completion

**Status**: Plan 'greenhouse-phase-a-completion' rev 4 (approved at rev 1): 2/6 done, 1 doing, 0 blocked, 3 todo

## Plan Data

```yaml
plan_id: greenhouse-phase-a-completion
revision: 4
approved_revision: 1
items:
- id: I1
  description: Update 3 consumer imports to use new greenhouse package
  state: done
  touches:
  - src/applier/easy_apply.py
  - src/applier/indeed_router.py
  - src/applier/queue_manager.py
  checks:
    happy:
      desc: All 3 files import from src.applier.greenhouse
      target: greenhouse imports
    negative:
    - desc: Old import path still exists in any file
      target: greenhouse_apply imports
    boundary:
    - desc: Import works with lazy loading pattern
      target: lazy imports
  evidence:
  - src/applier/easy_apply.py:473
  - src/applier/indeed_router.py:135
  - src/applier/queue_manager.py:403
  notes: Updated all 3 consumer imports to use new package path
- id: I2
  description: Add GreenhouseApplier to __getattr__ in src/applier/__init__.py for PEP 562 lazy loading
  state: done
  touches:
  - src/applier/__init__.py
  checks:
    happy:
      desc: GreenhouseApplier accessible via lazy import
      target: lazy loading
    negative:
    - desc: Top-level import breaks scraper-bot
      target: lazy loading
    boundary:
    - desc: AttributeError for non-existent attributes
      target: __getattr__
  evidence:
  - src/applier/__init__.py:58-63
  notes: Added GreenhouseApplier to __getattr__ following PEP 562 pattern
- id: I3
  description: Delete old src/applier/greenhouse_apply.py after all consumers updated
  state: doing
  touches:
  - src/applier/greenhouse_apply.py
  checks:
    happy:
      desc: File no longer exists
      target: file deletion
    negative:
    - desc: Any import still references old file
      target: imports
    boundary:
    - desc: No stale bytecode files remain
      target: __pycache__
  notes: ''
- id: I4
  description: Remove IndeedScreeningHandler backward compat alias
  state: todo
  touches:
  - src/applier/universal_screening/__init__.py
  checks:
    happy:
      desc: Alias removed from __init__.py
      target: alias removal
    negative:
    - desc: Any import still uses old name
      target: imports
    boundary:
    - desc: Module still exports UniversalScreeningHandler
      target: exports
  notes: ''
- id: I5
  description: Create E2E tests for combobox baseline
  state: todo
  touches:
  - tests/e2e/test_combobox_baseline.py
  checks:
    happy:
      desc: Playwright tests exist for ReactComboboxDriver
      target: e2e tests
    negative:
    - desc: Tests fail to import modules
      target: imports
    boundary:
    - desc: Tests work with real browser
      target: playwright
  notes: ''
- id: I6
  description: Create universal screening tests with HTML fixtures
  state: todo
  touches:
  - tests/test_universal_screening.py
  - tests/fixtures/
  checks:
    happy:
      desc: Tests exist for question_finder
      target: unit tests
    negative:
    - desc: Tests fail on invalid HTML
      target: error handling
    boundary:
    - desc: Tests work with Playwright page.set_content()
      target: playwright
  notes: ''
```
