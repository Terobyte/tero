# Plan: level2-stable-mvp

**Status**: Plan 'level2-stable-mvp' rev 2 (approved at rev 1): 0/7 done, 1 doing, 0 blocked, 6 todo

## Plan Data

```yaml
plan_id: level2-stable-mvp
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create unified slug collection runner script
  state: doing
  touches:
  - scripts/run_slug_collection.py
  checks:
    happy:
      desc: Script runs and collects slugs from CDX archive
      target: scripts/run_slug_collection.py
    negative:
    - desc: Script handles network errors gracefully
      target: scripts/run_slug_collection.py
    boundary:
    - desc: Script handles empty responses without crashing
      target: scripts/run_slug_collection.py
  notes: ''
- id: I2
  description: Create structured JSON logging module
  state: todo
  touches:
  - src/utils/structured_logger.py
  checks:
    happy:
      desc: Logger outputs JSON format when configured
      target: src/utils/structured_logger.py
    negative:
    - desc: Logger handles invalid log paths gracefully
      target: src/utils/structured_logger.py
    boundary:
    - desc: Logger handles missing log directory
      target: src/utils/structured_logger.py
  notes: ''
- id: I3
  description: Create config validator for startup checks
  state: todo
  touches:
  - src/utils/config_validator.py
  checks:
    happy:
      desc: Validator detects missing API keys
      target: src/utils/config_validator.py
    negative:
    - desc: Validator handles empty env gracefully
      target: src/utils/config_validator.py
    boundary:
    - desc: Validator reports warnings for optional keys
      target: src/utils/config_validator.py
  notes: ''
- id: I4
  description: Create BatchValidator stub for import verification
  state: todo
  touches:
  - src/applier/universal_screening/batch_validator.py
  checks:
    happy:
      desc: BatchValidator can be imported
      target: src/applier/universal_screening/batch_validator.py
    negative:
    - desc: Handles invalid input validation
      target: src/applier/universal_screening/batch_validator.py
    boundary:
    - desc: Handles empty batch validation
      target: src/applier/universal_screening/batch_validator.py
  notes: ''
- id: I5
  description: Create CostTracker stub for import verification
  state: todo
  touches:
  - src/utils/cost_tracker.py
  checks:
    happy:
      desc: CostTracker can be imported
      target: src/utils/cost_tracker.py
    negative:
    - desc: Handles negative cost gracefully
      target: src/utils/cost_tracker.py
    boundary:
    - desc: Handles zero costs
      target: src/utils/cost_tracker.py
  notes: ''
- id: I6
  description: Create ResumeTailor stub for import verification
  state: todo
  touches:
  - src/ai/resume_tailor.py
  checks:
    happy:
      desc: ResumeTailor can be imported
      target: src/ai/resume_tailor.py
    negative:
    - desc: Handles missing API key gracefully
      target: src/ai/resume_tailor.py
    boundary:
    - desc: Handles empty resume text
      target: src/ai/resume_tailor.py
  notes: ''
- id: I7
  description: Update config/settings.json with logging config
  state: todo
  touches:
  - config/settings.json
  checks:
    happy:
      desc: Settings file contains logging configuration
      target: config/settings.json
    negative:
    - desc: Handles malformed JSON gracefully
      target: config/settings.json
    boundary:
    - desc: Handles missing config file
      target: config/settings.json
  notes: ''
```
