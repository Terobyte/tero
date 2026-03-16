# Plan: applier-refactor

**Status**: Plan 'applier-refactor' rev 2 (approved at rev 1): 1/7 done, 1 doing, 0 blocked, 5 todo

## Plan Data

```yaml
plan_id: applier-refactor
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create git tag pre-refactor for rollback safety
  state: done
  touches:
  - git
  checks:
    happy:
      desc: Git tag pre-refactor exists
      target: git
    negative:
    - desc: Cannot create duplicate tag
      target: git
    boundary:
    - desc: Tag points to correct commit
      target: git
  evidence:
  - .git/refs/tags/pre-refactor
  notes: Created git tag pre-refactor as rollback safety net
- id: I2
  description: 'Phase C: Create universal_screening package with models.py and question_finder.py'
  state: doing
  touches:
  - src/applier/universal_screening/
  checks:
    happy:
      desc: Package imports successfully
      target: src/applier/universal_screening/__init__.py
    negative:
    - desc: Handles missing Playwright types gracefully
      target: src/applier/universal_screening/models.py
    boundary:
    - desc: Question dataclass has all required fields
      target: src/applier/universal_screening/models.py
  notes: ''
- id: I3
  description: 'Phase C: Create radio_filler.py with click/fill mechanics'
  state: todo
  touches:
  - src/applier/universal_screening/radio_filler.py
  checks:
    happy:
      desc: Radio buttons can be clicked
      target: src/applier/universal_screening/radio_filler.py
    negative:
    - desc: Returns False when no match found
      target: src/applier/universal_screening/radio_filler.py
    boundary:
    - desc: Handles both input[radio] and div[role=radio]
      target: src/applier/universal_screening/radio_filler.py
  notes: ''
- id: I4
  description: 'Phase C: Create answer_router.py with AnswerRouter class'
  state: todo
  touches:
  - src/applier/universal_screening/answer_router.py
  checks:
    happy:
      desc: AnswerRouter answers questions via AnswerBank/AI pipeline
      target: src/applier/universal_screening/answer_router.py
    negative:
    - desc: Returns None when no answer available
      target: src/applier/universal_screening/answer_router.py
    boundary:
    - desc: Uses HITL for open-ended questions
      target: src/applier/universal_screening/answer_router.py
  notes: ''
- id: I5
  description: 'Phase C: Create handler.py with UniversalScreeningHandler and backward compat alias'
  state: todo
  touches:
  - src/applier/universal_screening/handler.py
  - src/applier/universal_screening/__init__.py
  checks:
    happy:
      desc: UniversalScreeningHandler works as drop-in replacement
      target: src/applier/universal_screening/handler.py
    negative:
    - desc: IndeedScreeningHandler alias exists for backward compat
      target: src/applier/universal_screening/__init__.py
    boundary:
    - desc: _load_profile loads from YAML correctly
      target: src/applier/universal_screening/handler.py
  notes: ''
- id: I6
  description: 'Phase C: Update consumers and delete old indeed_screening.py'
  state: todo
  touches:
  - src/applier/indeed_apply.py
  - src/applier/indeed_router.py
  - src/applier/bamboohr_apply.py
  - src/applier/workday_apply.py
  - src/applier/lever_apply.py
  - src/applier/ashby_apply.py
  checks:
    happy:
      desc: All consumers import UniversalScreeningHandler
      target: src/applier/*.py
    negative:
    - desc: No import errors after refactoring
      target: src/applier/*.py
    boundary:
    - desc: IndeedScreeningHandler alias still works
      target: src/applier/universal_screening/__init__.py
  notes: ''
- id: I7
  description: 'Phase C: Verify with pytest and delete old file'
  state: todo
  touches:
  - src/applier/indeed_screening.py
  checks:
    happy:
      desc: Tests pass after refactoring
      target: tests/
    negative:
    - desc: Old file deleted without breaking imports
      target: src/applier/indeed_screening.py
    boundary:
    - desc: grep shows no IndeedScreeningHandler imports remain
      target: src/applier/
  notes: ''
```
