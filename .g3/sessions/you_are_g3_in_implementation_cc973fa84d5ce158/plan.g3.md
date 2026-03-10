# Plan: careerbot-implementation

**Status**: Plan 'careerbot-implementation' rev 4 (approved at rev 1): 5/5 done, 0 doing, 0 blocked, 0 todo

## Plan Data

```yaml
plan_id: careerbot-implementation
revision: 4
approved_revision: 1
items:
- id: I1
  description: Remove Glassdoor from scraper and config
  state: done
  touches:
  - src/platforms/jobspy_scraper.py
  - config/settings.json
  checks:
    happy:
      desc: Scraper runs without Glassdoor
      target: jobspy_scraper.SITES
    negative:
    - desc: No glassdoor references in codebase
      target: rg output
    boundary:
    - desc: Config still has other platforms
      target: settings.json
  evidence:
  - src/platforms/jobspy_scraper.py
  - config/settings.json
  notes: Glassdoor removed from SITES list, config clean
- id: I2
  description: Create ashby_apply.py with screenshot capability
  state: done
  touches:
  - src/applier/ashby_apply.py
  checks:
    happy:
      desc: Applies and saves screenshot
      target: ashby_apply.apply_to_job
    negative:
    - desc: Handles missing URL gracefully
      target: ashby_apply
    boundary:
    - desc: Screenshot path stored in DB
      target: db.update_status
  evidence:
  - src/applier/ashby_apply.py
  notes: Full Playwright implementation with screenshot capture
- id: I3
  description: Create greenhouse_apply.py with screenshot capability
  state: done
  touches:
  - src/applier/greenhouse_apply.py
  checks:
    happy:
      desc: Applies and saves screenshot
      target: greenhouse_apply.apply_to_job
    negative:
    - desc: Handles form validation errors
      target: greenhouse_apply
    boundary:
    - desc: Screenshot naming includes platform
      target: screenshot_path format
  evidence:
  - src/applier/greenhouse_apply.py
  notes: Full Playwright implementation with screenshot capture
- id: I4
  description: Create lever_apply.py and bamboohr_apply.py with screenshot capability
  state: done
  touches:
  - src/applier/lever_apply.py
  - src/applier/bamboohr_apply.py
  checks:
    happy:
      desc: Both appliers work with screenshots
      target: lever_apply, bamboohr_apply
    negative:
    - desc: Handle platform-specific errors
      target: error handling
    boundary:
    - desc: Screenshot directory exists
      target: data/screenshots/
  evidence:
  - src/applier/lever_apply.py
  - src/applier/bamboohr_apply.py
  notes: Both modules with full Playwright implementation and screenshot capture
- id: I5
  description: Create gui.py Tkinter desktop application
  state: done
  touches:
  - gui.py
  checks:
    happy:
      desc: GUI starts and displays interface
      target: gui.py main()
    negative:
    - desc: Handles DB connection errors
      target: load_applied_jobs
    boundary:
    - desc: Threading prevents UI freeze
      target: ScanThread
  evidence:
  - gui.py
  notes: Full Tkinter GUI with threading, provider checkboxes, scan functionality, and proof display
```
