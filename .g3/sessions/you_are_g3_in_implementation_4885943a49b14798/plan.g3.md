# Plan: screening-batch-validation

**Status**: Plan 'screening-batch-validation' rev 4 (approved at rev 1): 4/11 done, 1 doing, 0 blocked, 6 todo

## Plan Data

```yaml
plan_id: screening-batch-validation
revision: 4
approved_revision: 1
items:
- id: I1
  description: Fix policy.py to properly load JSON config format
  state: done
  touches:
  - src/applier/universal_screening/policy.py
  checks:
    happy:
      desc: Policy loads critical intents from config
      target: policy.py::_load_config
    negative:
    - desc: Missing config file uses sensible defaults
      target: policy.py::_load_config
    boundary:
    - desc: Empty config array results in empty critical intents
      target: policy.py::_load_config
  evidence:
  - src/applier/universal_screening/policy.py:68-85
  notes: Fixed config loading to handle object array format with intent fields
- id: I2
  description: Create batch_validator.py with Claude Sonnet integration
  state: done
  touches:
  - src/applier/universal_screening/batch_validator.py
  checks:
    happy:
      desc: Validator approves correct step answers
      target: batch_validator.py::validate
    negative:
    - desc: Validator rejects wrong sponsorship answer
      target: batch_validator.py::validate
    boundary:
    - desc: Malformed JSON triggers retry logic
      target: batch_validator.py::_parse_response
  evidence:
  - src/applier/universal_screening/batch_validator.py
  notes: Created BatchStepValidator with Claude Sonnet integration, retry logic, and malformed output handling
- id: I3
  description: Create correction_executor.py with DOM and vision fallback
  state: done
  touches:
  - src/applier/universal_screening/correction_executor.py
  checks:
    happy:
      desc: Radio correction from Yes to No succeeds
      target: correction_executor.py::correct
    negative:
    - desc: Field not found returns failure
      target: correction_executor.py::correct
    boundary:
    - desc: Fuzzy match finds partial option string
      target: correction_executor.py::_fuzzy_match_option
  evidence:
  - src/applier/universal_screening/correction_executor.py
  notes: Created CorrectionExecutor with DOM-first, vision fallback, and rollback support
- id: I4
  description: Fix AnswerBank pattern order - sponsorship above work_authorization
  state: done
  touches:
  - src/applier/answer_bank.py
  checks:
    happy:
      desc: Sponsorship compound question returns requires_sponsorship
      target: answer_bank.py
    negative:
    - desc: Compound question with work_auth does not misclassify
      target: answer_bank.py
    boundary:
    - desc: Typo variant exisiting matches existing
      target: answer_bank.py
  evidence:
  - src/applier/answer_bank.py
  notes: Created AnswerBank with correct pattern order - sponsorship patterns checked BEFORE work_authorization
- id: I5
  description: Create unit tests for question_intent.py
  state: doing
  touches:
  - tests/screening/test_question_intent.py
  checks:
    happy:
      desc: classify sponsorship question correctly
      target: test_question_intent.py
    negative:
    - desc: unknown question returns unknown intent
      target: test_question_intent.py
    boundary:
    - desc: compound question sponsorship wins over work_auth
      target: test_question_intent.py
  notes: ''
- id: I6
  description: Create unit tests for policy.py
  state: todo
  touches:
  - tests/screening/test_policy.py
  checks:
    happy:
      desc: critical question triggers hard_stop
      target: test_policy.py
    negative:
    - desc: non-critical question allowed
      target: test_policy.py
    boundary:
    - desc: contradiction pair detection works
      target: test_policy.py
  notes: ''
- id: I7
  description: Create unit tests for batch_validator.py
  state: todo
  touches:
  - tests/screening/test_batch_validator.py
  checks:
    happy:
      desc: validator approves correct full step
      target: test_batch_validator.py
    negative:
    - desc: malformed output triggers retry
      target: test_batch_validator.py
    boundary:
    - desc: empty response applies failure strategy
      target: test_batch_validator.py
  notes: ''
- id: I8
  description: Create unit tests for correction_executor.py
  state: todo
  touches:
  - tests/screening/test_correction_executor.py
  checks:
    happy:
      desc: radio correction from Yes to No succeeds
      target: test_correction_executor.py
    negative:
    - desc: correction returns failure when field not found
      target: test_correction_executor.py
    boundary:
    - desc: fuzzy match handles partial strings
      target: test_correction_executor.py
  notes: ''
- id: I9
  description: Create unit tests for session_context.py
  state: todo
  touches:
  - tests/screening/test_session_context.py
  checks:
    happy:
      desc: record and retrieve intent answer
      target: test_session_context.py
    negative:
    - desc: cross-step contradiction detected
      target: test_session_context.py
    boundary:
    - desc: clear removes all recorded answers
      target: test_session_context.py
  notes: ''
- id: I10
  description: Create integration tests for Indeed sponsorship regression
  state: todo
  touches:
  - tests/screening/test_indeed_sponsorship_regression.py
  checks:
    happy:
      desc: exact sponsorship text returns No
      target: test_indeed_sponsorship_regression.py
    negative:
    - desc: compound question does not misclassify
      target: test_indeed_sponsorship_regression.py
    boundary:
    - desc: both questions validated together
      target: test_indeed_sponsorship_regression.py
  notes: ''
- id: I11
  description: Update config/settings.json with new screening keys
  state: todo
  touches:
  - config/settings.json
  checks:
    happy:
      desc: settings contain all required keys
      target: settings.json
    negative:
    - desc: missing keys use defaults
      target: settings.json
    boundary:
    - desc: extra keys ignored
      target: settings.json
  notes: ''
```
