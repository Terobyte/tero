# Plan: applier-refactoring

**Status**: Plan 'applier-refactoring' rev 2 (approved at rev 1): 0/5 done, 1 doing, 0 blocked, 4 todo

## Plan Data

```yaml
plan_id: applier-refactoring
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create shared config.py for common constants
  state: doing
  touches:
  - src/applier/config.py
  checks:
    happy:
      desc: Config file with RESUME_PATH, COVER_LETTER_PATH, SCREENSHOTS_DIR
      target: src/applier/config.py
    negative:
    - desc: Missing env vars use defaults
      target: src/applier/config.py
    boundary:
    - desc: Path objects returned correctly
      target: src/applier/config.py
  notes: ''
- id: I2
  description: Create universal_screening package (Phase C)
  state: todo
  touches:
  - src/applier/universal_screening/
  checks:
    happy:
      desc: Package with models, question_finder, radio_filler, answer_router, handler
      target: src/applier/universal_screening/__init__.py
    negative:
    - desc: Handles missing question elements gracefully
      target: src/applier/universal_screening/question_finder.py
    boundary:
    - desc: Empty page returns empty question list
      target: src/applier/universal_screening/question_finder.py
  notes: ''
- id: I3
  description: Create indeed package (Phase B)
  state: todo
  touches:
  - src/applier/indeed/
  checks:
    happy:
      desc: Package with applier, captcha, config, locators, form_filler, navigation
      target: src/applier/indeed/__init__.py
    negative:
    - desc: Captcha handling fails gracefully
      target: src/applier/indeed/captcha.py
    boundary:
    - desc: Empty frame detection returns None
      target: src/applier/indeed/locators.py
  notes: ''
- id: I4
  description: Create greenhouse package (Phase A)
  state: todo
  touches:
  - src/applier/greenhouse/
  checks:
    happy:
      desc: Package with applier, combobox_engine, form_filler, validator, security, file_uploader
      target: src/applier/greenhouse/__init__.py
    negative:
    - desc: Combobox strategies fall back correctly
      target: src/applier/greenhouse/combobox_engine.py
    boundary:
    - desc: Empty required fields validation works
      target: src/applier/greenhouse/validator.py
  notes: ''
- id: I5
  description: Update applier __init__.py with PEP 562 lazy imports
  state: todo
  touches:
  - src/applier/__init__.py
  checks:
    happy:
      desc: Lazy imports work without loading Playwright
      target: src/applier/__init__.py
    negative:
    - desc: Invalid attribute raises AttributeError
      target: src/applier/__init__.py
    boundary:
    - desc: Multiple imports of same class return same object
      target: src/applier/__init__.py
  notes: ''
```
