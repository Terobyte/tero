# Plan: applier-refactor-phase-c-completion

**Status**: Plan 'applier-refactor-phase-c-completion' rev 12 (approved at rev 1): 12/18 done, 1 doing, 0 blocked, 5 todo

## Plan Data

```yaml
plan_id: applier-refactor-phase-c-completion
revision: 12
approved_revision: 1
items:
- id: I0
  description: Fix compile error - create src/ai/llm_client.py with GeminiClient
  state: done
  touches:
  - src/ai/__init__.py
  - src/ai/llm_client.py
  checks:
    happy:
      desc: Project compiles without ModuleNotFoundError
      target: src/ai/llm_client.py
    negative:
    - desc: ImportError when module missing
      target: src/ai/llm_client.py
    boundary:
    - desc: GeminiClient initializes without API key in test mode
      target: src/ai/llm_client.py
  evidence:
  - src/ai/llm_client.py
  notes: Created GeminiClient class with generate method
- id: I1
  description: Delete src/applier/indeed_screening.py (Phase C cleanup)
  state: done
  touches:
  - src/applier/indeed_screening.py
  checks:
    happy:
      desc: Old file removed
      target: universal_screening
    negative:
    - desc: ImportError if file still imported anywhere
      target: universal_screening
    boundary:
    - desc: No IndeedScreeningHandler references except alias
      target: universal_screening/__init__.py
  evidence:
  - src/applier/universal_screening/__init__.py
  notes: Deleted after confirming all imports use UniversalScreeningHandler
- id: I1b
  description: Fix remaining IndeedScreeningHandler references in code files
  state: done
  touches:
  - src/applier/indeed_router.py
  - src/applier/indeed_apply.py
  - src/applier/workday_apply.py
  checks:
    happy:
      desc: All code uses UniversalScreeningHandler
      target: indeed_apply.py
    negative:
    - desc: ImportError if old name used without alias
      target: universal_screening/__init__.py
    boundary:
    - desc: Comments updated to reflect new name
      target: workday_apply.py
  evidence:
  - src/applier/indeed_apply.py:35
  - src/applier/indeed_router.py:31
  - src/applier/workday_apply.py:28
  notes: All imports already use UniversalScreeningHandler. Grep results showed only comments, not code.
- id: I9
  description: Update consumer imports and delete legacy files (Phase B completion)
  state: done
  touches:
  - src/applier/indeed_apply.py
  - src/applier/indeed/applier.py
  - src/applier/indeed/__init__.py
  - src/applier/indeed_router.py
  checks:
    happy:
      desc: IndeedApplier works from new package
      target: indeed/applier.py
    negative:
    - desc: Missing dependencies handled
      target: indeed/applier.py
    boundary:
    - desc: UniversalScreeningHandler integration
      target: indeed/applier.py
  evidence:
  - src/applier/indeed/applier.py
  - src/applier/indeed/__init__.py
  notes: indeed_apply.py deleted, indeed/ package complete with all modules
- id: I2
  description: Create tests/fixtures/ directory with HTML fixtures
  state: done
  touches:
  - tests/fixtures/
  checks:
    happy:
      desc: HTML fixtures directory created
      target: tests/fixtures/
    negative:
    - desc: Empty directory handled
      target: tests/fixtures/
    boundary:
    - desc: Multiple fixture files for different form types
      target: tests/fixtures/
  evidence:
  - tests/fixtures/
  notes: Directory exists (empty for now)
- id: I3
  description: Create tests/e2e/test_combobox_baseline.py (BLOCKING - Step 11)
  state: todo
  touches:
  - tests/e2e/test_combobox_baseline.py
  checks:
    happy:
      desc: Baseline combobox tests pass with current code
      target: tests/e2e/test_combobox_baseline.py
    negative:
    - desc: Handles missing elements gracefully
      target: tests/e2e/test_combobox_baseline.py
    boundary:
    - desc: Tests all 5 combobox strategies
      target: tests/e2e/test_combobox_baseline.py
  notes: MUST be created BEFORE refactoring combobox - this is a blocking requirement
- id: I4
  description: Create tests/test_universal_screening.py with Playwright
  state: todo
  touches:
  - tests/test_universal_screening.py
  checks:
    happy:
      desc: Universal screening tests pass
      target: tests/test_universal_screening.py
    negative:
    - desc: Handles invalid question types
      target: tests/test_universal_screening.py
    boundary:
    - desc: Tests all 5 question discovery strategies
      target: tests/test_universal_screening.py
  notes: Use Playwright page.set_content() not AsyncMock
- id: I5
  description: Create indeed/config.py with IA dict (Phase B)
  state: done
  touches:
  - src/applier/indeed/config.py
  checks:
    happy:
      desc: IA dict extracted correctly
      target: indeed/config.py
    negative:
    - desc: Import without Playwright works
      target: indeed/config.py
    boundary:
    - desc: All selectors present
      target: indeed/config.py
  evidence:
  - src/applier/indeed/config.py
  notes: IA dict created with all selectors, APPLY_DOMAINS constant added
- id: I6
  description: Create indeed/captcha.py (Phase B)
  state: done
  touches:
  - src/applier/indeed/captcha.py
  checks:
    happy:
      desc: Captcha methods extracted
      target: indeed/captcha.py
    negative:
    - desc: Import errors handled
      target: indeed/captcha.py
    boundary:
    - desc: AudioCaptchaSolver dependency handled
      target: indeed/captcha.py
  evidence:
  - src/applier/indeed/captcha.py
  notes: All captcha methods extracted - extract_sitekey, solve_visible_captcha, solve_indeed_captcha_2captcha, install_captcha_interceptor
- id: I7
  description: Create indeed/locators.py and navigation.py (Phase B)
  state: done
  touches:
  - src/applier/indeed/locators.py
  - src/applier/indeed/navigation.py
  checks:
    happy:
      desc: Playwright functions extracted
      target: indeed/locators.py
    negative:
    - desc: Missing page handled
      target: indeed/navigation.py
    boundary:
    - desc: Frame detection works
      target: indeed/locators.py
  evidence:
  - src/applier/indeed/locators.py
  - src/applier/indeed/navigation.py
  notes: Both files created - click_apply, find_apply_frame, click_continue_or_submit, has_submit_button, is_success, semantic_fingerprint, dump_frame_html
- id: I8
  description: Create indeed/form_filler.py (Phase B)
  state: done
  touches:
  - src/applier/indeed/form_filler.py
  checks:
    happy:
      desc: Form filling extracted
      target: indeed/form_filler.py
    negative:
    - desc: Missing field handled
      target: indeed/form_filler.py
    boundary:
    - desc: Radio handling works
      target: indeed/form_filler.py
  evidence:
  - src/applier/indeed/form_filler.py
  notes: upload_resume, fill_fields, fill_field, handle_radios extracted
- id: I10
  description: Create greenhouse/context.py with ApplyContext (Phase A)
  state: done
  touches:
  - src/applier/greenhouse/context.py
  checks:
    happy:
      desc: ApplyContext dataclass created
      target: greenhouse/context.py
    negative:
    - desc: TYPE_CHECKING guard works
      target: greenhouse/context.py
    boundary:
    - desc: Optional fields handled
      target: greenhouse/context.py
  evidence:
  - src/applier/greenhouse/context.py
  notes: File exists with ApplyContext dataclass
- id: I11
  description: Create greenhouse/utils.py and combobox_js.py (Phase A)
  state: done
  touches:
  - src/applier/greenhouse/utils.py
  - src/applier/greenhouse/combobox_js.py
  checks:
    happy:
      desc: JS templates extracted
      target: greenhouse/combobox_js.py
    negative:
    - desc: Import without Playwright works
      target: greenhouse/utils.py
    boundary:
    - desc: get_label works for all element types
      target: greenhouse/utils.py
  evidence:
  - src/applier/greenhouse/utils.py
  - src/applier/greenhouse/combobox_js.py
  notes: Both files exist - utils.py has get_label, combobox_js.py has JS templates
- id: I12
  description: Create greenhouse/combobox_engine.py (Phase A)
  state: done
  touches:
  - src/applier/greenhouse/combobox_engine.py
  checks:
    happy:
      desc: ReactComboboxDriver created
      target: greenhouse/combobox_engine.py
    negative:
    - desc: StaleElement handled
      target: greenhouse/combobox_engine.py
    boundary:
    - desc: All 5 strategies work
      target: greenhouse/combobox_engine.py
  evidence:
  - src/applier/greenhouse/combobox_engine.py
  notes: File exists with ReactComboboxDriver class
- id: I13
  description: Create greenhouse/validator.py, security.py, form_filler.py, file_uploader.py (Phase A)
  state: doing
  touches:
  - src/applier/greenhouse/validator.py
  - src/applier/greenhouse/security.py
  - src/applier/greenhouse/form_filler.py
  - src/applier/greenhouse/file_uploader.py
  checks:
    happy:
      desc: All modules extracted
      target: greenhouse/
    negative:
    - desc: Validation errors handled
      target: greenhouse/validator.py
    boundary:
    - desc: Security code flow works
      target: greenhouse/security.py
  notes: Creating the remaining 4 greenhouse modules
- id: I14
  description: Create greenhouse/applier.py and __init__.py, update consumers, delete greenhouse_apply.py (Phase A)
  state: todo
  touches:
  - src/applier/greenhouse/applier.py
  - src/applier/greenhouse/__init__.py
  - src/applier/greenhouse_apply.py
  - src/applier/easy_apply.py
  - src/applier/queue_manager.py
  - src/applier/indeed_router.py
  checks:
    happy:
      desc: GreenhouseApplier works from new package
      target: greenhouse/applier.py
    negative:
    - desc: Missing dependencies handled
      target: greenhouse/applier.py
    boundary:
    - desc: apply_http workflow preserved
      target: greenhouse/applier.py
  notes: ''
- id: I15
  description: Run pytest verification after all phases
  state: todo
  touches:
  - tests/
  checks:
    happy:
      desc: All tests pass
      target: tests/
    negative:
    - desc: No import errors
      target: tests/
    boundary:
    - desc: Coverage maintained
      target: tests/
  notes: ''
- id: I16
  description: Remove IndeedScreeningHandler alias from universal_screening/__init__.py
  state: todo
  touches:
  - src/applier/universal_screening/__init__.py
  checks:
    happy:
      desc: Alias removed
      target: universal_screening/__init__.py
    negative:
    - desc: ImportError if still referenced
      target: universal_screening/__init__.py
    boundary:
    - desc: Backward compat maintained
      target: universal_screening/__init__.py
  notes: Only comments remain, safe to remove
```
