# Plan: gui-missing-features

**Status**: Plan 'gui-missing-features' rev 4 (approved at rev 1): 0/7 done, 1 doing, 0 blocked, 6 todo

## Plan Data

```yaml
plan_id: gui-missing-features
revision: 4
approved_revision: 1
items:
- id: I1
  description: Add _log() method to CareerBotGUI class
  state: doing
  touches:
  - gui.py
  checks:
    happy:
      desc: _log() method writes messages to a log widget
      target: gui.py
    negative:
    - desc: Handles missing log widget gracefully
      target: gui.py
    boundary:
    - desc: Handles very long messages
      target: gui.py
- id: I2
  description: Add Profile Window with Resume tab (_open_profile_window method)
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: Profile window opens with Resume tab
      target: gui.py
    negative:
    - desc: Handles missing profile.yaml
      target: gui.py
    boundary:
    - desc: Empty profile shows default values
      target: gui.py
- id: I3
  description: Add tailor_btn button to job column
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: Button appears next to apply button
      target: gui.py
    negative:
    - desc: Button disabled when no job selected
      target: gui.py
    boundary:
    - desc: Button state updates correctly
      target: gui.py
- id: I4
  description: Add _evaluate_tailoring() method with live progress indicator
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: Shows 5-bar progress indicator and results
      target: gui.py
    negative:
    - desc: Handles API errors gracefully
      target: gui.py
    boundary:
    - desc: Window close during evaluation handled
      target: gui.py
- id: I5
  description: Add qa_btn button to top section
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: Button appears in top panel
      target: gui.py
    negative:
    - desc: Button disabled when no questions
      target: gui.py
    boundary:
    - desc: Button visible from start
      target: gui.py
- id: I6
  description: Add _open_qa_review() method for reviewing AI answers
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: Window shows questions with confirm/override buttons
      target: gui.py
    negative:
    - desc: Handles missing unknown_questions.json
      target: gui.py
    boundary:
    - desc: Empty questions list shows message
      target: gui.py
- id: I7
  description: Ensure data files exist (profile.yaml, unknown_questions.json)
  state: todo
  touches:
  - data/candidate/profile.yaml
  - data/unknown_questions.json
  checks:
    happy:
      desc: Files exist with valid structure
      target: data/
    negative:
    - desc: Missing files created on demand
      target: data/
    boundary:
    - desc: Empty files handled gracefully
      target: data/
```
