# Plan: workday-indeed-auto-apply

**Status**: Plan 'workday-indeed-auto-apply' rev 3 (approved at rev 1): 0/7 done, 1 doing, 0 blocked, 6 todo

## Plan Data

```yaml
plan_id: workday-indeed-auto-apply
revision: 3
approved_revision: 1
items:
- id: I1
  description: Add "Apply" button and dispatch logic to GUI
  state: doing
  touches:
  - gui.py
  checks:
    happy:
      desc: Apply button appears for scanned jobs and dispatches to correct applier
      target: gui.py
    negative:
    - desc: Shows error message when job URL is missing
      target: gui.py
    boundary:
    - desc: Handles unknown ATS types gracefully
      target: gui.py
  notes: ''
- id: I2
  description: Enhance WorkdayApplier with improved Education and Experience handlers
  state: todo
  touches:
  - src/applier/workday_apply.py
  checks:
    happy:
      desc: Fills education and experience fields correctly
      target: src/applier/workday_apply.py
    negative:
    - desc: Handles missing education/experience data gracefully
      target: src/applier/workday_apply.py
    boundary:
    - desc: Supports multiple education/experience entries
      target: src/applier/workday_apply.py
  notes: ''
- id: I3
  description: Improve WorkdayApplier custom questions handling
  state: todo
  touches:
  - src/applier/workday_apply.py
  checks:
    happy:
      desc: Fills custom questions from AnswerBank
      target: src/applier/workday_apply.py
    negative:
    - desc: Handles unknown question types without crashing
      target: src/applier/workday_apply.py
    boundary:
    - desc: Supports various question formats (radio, checkbox, text, select)
      target: src/applier/workday_apply.py
  notes: ''
- id: I4
  description: Add 60-second fallback for Workday validation errors
  state: todo
  touches:
  - src/applier/workday_apply.py
  checks:
    happy:
      desc: Pauses for manual input when validation errors detected
      target: src/applier/workday_apply.py
    negative:
    - desc: Continues after timeout even with errors
      target: src/applier/workday_apply.py
    boundary:
    - desc: Detects multiple error messages simultaneously
      target: src/applier/workday_apply.py
  notes: ''
- id: I5
  description: Create AnswerBank module for user data management
  state: todo
  touches:
  - src/applier/answer_bank.py
  checks:
    happy:
      desc: Loads user data from env/yaml and provides answers
      target: src/applier/answer_bank.py
    negative:
    - desc: Returns None for missing fields
      target: src/applier/answer_bank.py
    boundary:
    - desc: Handles both simple and complex question types
      target: src/applier/answer_bank.py
  notes: ''
- id: I6
  description: Integrate IndeedRouter into GUI dispatch
  state: todo
  touches:
  - gui.py
  - src/applier/indeed_router.py
  checks:
    happy:
      desc: Indeed jobs route to correct ATS applier
      target: gui.py
    negative:
    - desc: Shows error for unknown ATS after routing
      target: gui.py
    boundary:
    - desc: Handles Indeed Easy Apply jobs separately
      target: gui.py
  notes: ''
- id: I7
  description: Create Indeed Easy Apply module
  state: todo
  touches:
  - src/applier/indeed_apply.py
  checks:
    happy:
      desc: Applies to Indeed Easy Apply jobs successfully
      target: src/applier/indeed_apply.py
    negative:
    - desc: Handles iframe loading errors
      target: src/applier/indeed_apply.py
    boundary:
    - desc: Detects and switches to apply iframe
      target: src/applier/indeed_apply.py
  notes: ''
```
