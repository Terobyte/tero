# Plan: workday-indeed-auto-apply

**Status**: Plan 'workday-indeed-auto-apply' rev 2 (approved at rev 1): 1/6 done, 1 doing, 0 blocked, 4 todo

## Plan Data

```yaml
plan_id: workday-indeed-auto-apply
revision: 2
approved_revision: 1
items:
- id: I1
  description: Add Workday dispatch to GUI (_apply_job_async method)
  state: done
  touches:
  - gui.py
  checks:
    happy:
      desc: Workday URL triggers WorkdayApplier.apply()
      target: gui.py
    negative:
    - desc: Unknown ATS shows error message
      target: gui.py
    boundary:
    - desc: Empty job URL handled gracefully
      target: gui.py
  evidence:
  - gui.py:578-743
  notes: Added _start_apply method that creates new thread with async event loop, detects ATS type from URL, and dispatches to correct applier including Workday.
- id: I2
  description: Update IndeedRouter to use correct applier interfaces
  state: doing
  touches:
  - src/applier/indeed_router.py
  checks:
    happy:
      desc: Indeed job routes to correct ATS applier
      target: src/applier/indeed_router.py
    negative:
    - desc: Unknown ATS returns error without crashing
      target: src/applier/indeed_router.py
    boundary:
    - desc: Timeout handling for slow redirects
      target: src/applier/indeed_router.py
- id: I3
  description: Add Indeed platform handling in GUI dispatcher
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: Indeed jobs use IndeedRouter
      target: gui.py
    negative:
    - desc: Indeed Easy Apply shows appropriate message
      target: gui.py
    boundary:
    - desc: Mixed platform jobs handled correctly
      target: gui.py
- id: I4
  description: Create AnswerBank module for user profile data
  state: todo
  touches:
  - src/applier/answer_bank.py
  checks:
    happy:
      desc: Loads user data from env/yaml
      target: src/applier/answer_bank.py
    negative:
    - desc: Missing config file returns empty dict
      target: src/applier/answer_bank.py
    boundary:
    - desc: Partial config loads available fields
      target: src/applier/answer_bank.py
- id: I5
  description: Add StealthBrowser integration to WorkdayApplier
  state: todo
  touches:
  - src/applier/workday_apply.py
  checks:
    happy:
      desc: Uses patchright with stealth mode
      target: src/applier/workday_apply.py
    negative:
    - desc: Browser launch failure handled gracefully
      target: src/applier/workday_apply.py
    boundary:
    - desc: Headless vs headed mode configurable
      target: src/applier/workday_apply.py
- id: I6
  description: Add HumanSimulator for realistic typing in Workday
  state: todo
  touches:
  - src/applier/workday_apply.py
  checks:
    happy:
      desc: Fields filled with human-like delays
      target: src/applier/workday_apply.py
    negative:
    - desc: Field fill errors logged but don't crash
      target: src/applier/workday_apply.py
    boundary:
    - desc: Long text handled with chunking
      target: src/applier/workday_apply.py
```
