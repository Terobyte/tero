# Plan: workday-indeed-auto-apply

**Status**: Plan 'workday-indeed-auto-apply' rev 2 (approved at rev 1): 0/6 done, 1 doing, 0 blocked, 5 todo

## Plan Data

```yaml
plan_id: workday-indeed-auto-apply
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create ATSDetector module for ATS type detection from URL and HTML
  state: doing
  touches:
  - src/applier/ats_detector.py
  checks:
    happy:
      desc: ATSDetector correctly identifies Workday, Greenhouse, Lever from URLs and HTML
      target: ats_detector.py
    negative:
    - desc: Returns 'unknown' for unrecognized URLs/HTML
      target: ats_detector.py
    boundary:
    - desc: Handles malformed URLs gracefully
      target: ats_detector.py
  notes: ''
- id: I2
  description: Create WorkdayApplier module with Personal Info, Education, Experience steps
  state: todo
  touches:
  - src/applier/workday_apply.py
  checks:
    happy:
      desc: Fills Personal Info step with user data from AnswerBank
      target: workday_apply.py
    negative:
    - desc: Handles missing required fields with 60s fallback
      target: workday_apply.py
    boundary:
    - desc: Handles multi-page wizard with Next/Back navigation
      target: workday_apply.py
  notes: ''
- id: I3
  description: Add Workday dispatch to GUI (_apply_job_async)
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: Workday jobs trigger WorkdayApplier.apply()
      target: gui.py
    negative:
    - desc: Unknown ATS shows error message
      target: gui.py
    boundary:
    - desc: Handles async execution correctly
      target: gui.py
  notes: ''
- id: I4
  description: Create IndeedRouter for smart ATS detection and routing
  state: todo
  touches:
  - src/applier/indeed_router.py
  checks:
    happy:
      desc: Opens Indeed URL, detects actual ATS, routes to correct applier
      target: indeed_router.py
    negative:
    - desc: Returns error for unknown ATS after detection
      target: indeed_router.py
    boundary:
    - desc: Handles redirect chains correctly
      target: indeed_router.py
  notes: ''
- id: I5
  description: Add Indeed routing to GUI dispatch
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: Indeed jobs use IndeedRouter for smart routing
      target: gui.py
    negative:
    - desc: Failed detection returns actionable error
      target: gui.py
    boundary:
    - desc: Works with mixed ATS from Indeed results
      target: gui.py
  notes: ''
- id: I6
  description: Create AnswerBank module for user profile data
  state: todo
  touches:
  - src/applier/answer_bank.py
  checks:
    happy:
      desc: Loads user data from env/yaml and provides answers by label
      target: answer_bank.py
    negative:
    - desc: Missing config returns reasonable defaults
      target: answer_bank.py
    boundary:
    - desc: Handles special characters in values
      target: answer_bank.py
  notes: ''
```
