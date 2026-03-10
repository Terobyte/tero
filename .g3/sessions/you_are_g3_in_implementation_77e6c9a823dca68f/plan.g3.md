# Plan: gui-refactor

**Status**: Plan 'gui-refactor' rev 3 (approved at rev 1): 4/7 done, 1 doing, 0 blocked, 2 todo

## Plan Data

```yaml
plan_id: gui-refactor
revision: 3
approved_revision: 1
items:
- id: I1
  description: Create src/gui/ package structure
  state: done
  touches:
  - src/gui/__init__.py
  checks:
    happy:
      desc: Package imports work
      target: src/gui/__init__.py
    negative:
    - desc: Missing __init__.py causes import error
      target: src/gui/__init__.py
    boundary:
    - desc: Empty __init__.py is valid
      target: src/gui/__init__.py
  evidence:
  - src/gui/__init__.py
  notes: Created empty __init__.py for package
- id: I2
  description: Create src/gui/db_helpers.py with DB helpers
  state: done
  touches:
  - src/gui/db_helpers.py
  checks:
    happy:
      desc: load_config, fetch_applied_jobs, save_applied_to_db work
      target: src/gui/db_helpers.py
    negative:
    - desc: Missing DB returns empty list
      target: src/gui/db_helpers.py
    boundary:
    - desc: Broken JSON in raw_data handled gracefully
      target: src/gui/db_helpers.py
  evidence:
  - src/gui/db_helpers.py
  notes: Implemented config/DB helper functions with error handling
- id: I3
  description: Create src/gui/scan_service.py with scan logic
  state: done
  touches:
  - src/gui/scan_service.py
  checks:
    happy:
      desc: run_scan_thread executes scan
      target: src/gui/scan_service.py
    negative:
    - desc: Scan errors handled gracefully
      target: src/gui/scan_service.py
    boundary:
    - desc: Indeed5 limits to 5 jobs
      target: src/gui/scan_service.py
  evidence:
  - src/gui/scan_service.py
  notes: Implemented scan service with asyncio threading and BambooHR integration
- id: I4
  description: Create src/gui/apply_service.py with apply logic
  state: done
  touches:
  - src/gui/apply_service.py
  checks:
    happy:
      desc: run_apply_thread routes to correct ATS applier
      target: src/gui/apply_service.py
    negative:
    - desc: Unsupported ATS returns error
      target: src/gui/apply_service.py
    boundary:
    - desc: Unknown ATS defaults to IndeedRouter
      target: src/gui/apply_service.py
  evidence:
  - src/gui/apply_service.py
  notes: Implemented apply service with ATS routing
- id: I5
  description: Create tests for all service modules
  state: doing
  touches:
  - tests/gui/test_db_helpers.py
  - tests/gui/test_scan_service.py
  - tests/gui/test_apply_service.py
  checks:
    happy:
      desc: pytest tests/gui/ -v passes
      target: tests/gui/
    negative:
    - desc: Tests handle missing DB/config gracefully
      target: tests/gui/test_db_helpers.py
    boundary:
    - desc: Mock external dependencies
      target: tests/gui/test_scan_service.py
  notes: ''
- id: I6
  description: Create minimal gui.py with imports
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: GUI imports from src.gui.* work
      target: gui.py
    negative:
    - desc: Missing imports cause clear errors
      target: gui.py
    boundary:
    - desc: CareerBotGUI class remains intact
      target: gui.py
  notes: ''
- id: I7
  description: Verify all imports and run final tests
  state: todo
  touches:
  - src/gui/
  - tests/gui/
  checks:
    happy:
      desc: All module imports succeed
      target: src/gui/
    negative:
    - desc: Missing modules raise ImportError
      target: src/gui/
    boundary:
    - desc: Circular imports avoided
      target: src/gui/
  notes: ''
```
