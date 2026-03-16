# Plan: screening-batch-validation

**Status**: Plan 'screening-batch-validation' rev 2 (approved at rev 1): 0/7 done, 1 doing, 0 blocked, 6 todo

## Plan Data

```yaml
plan_id: screening-batch-validation
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create core data models and snapshot classes
  state: doing
  touches:
  - src/applier/universal_screening/snapshot.py
  - src/applier/universal_screening/models.py
  checks:
    happy:
      desc: StepSnapshot and QuestionSnapshot dataclasses are importable
      target: src/applier/universal_screening/snapshot.py
    negative:
    - desc: Invalid data raises validation error
      target: src/applier/universal_screening/snapshot.py
    boundary:
    - desc: Empty questions list is handled
      target: src/applier/universal_screening/snapshot.py
  notes: ''
- id: I2
  description: Implement QuestionIntentClassifier with 3-tier strategy
  state: todo
  touches:
  - src/applier/universal_screening/question_intent.py
  checks:
    happy:
      desc: Sponsorship question classified correctly
      target: src/applier/universal_screening/question_intent.py
    negative:
    - desc: Unknown question returns unknown intent
      target: src/applier/universal_screening/question_intent.py
    boundary:
    - desc: Compound questions with sponsorship + work_auth prioritize sponsorship
      target: src/applier/universal_screening/question_intent.py
  notes: ''
- id: I3
  description: Implement CriticalQuestionPolicy with contradiction matrix
  state: todo
  touches:
  - src/applier/universal_screening/policy.py
  - config/critical_intents.json
  checks:
    happy:
      desc: Critical intents loaded from config
      target: src/applier/universal_screening/policy.py
    negative:
    - desc: Critical unknown intent blocks auto-answer
      target: src/applier/universal_screening/policy.py
    boundary:
    - desc: Suspicious pair authorized_to_work=No, requires_sponsorship=No flagged
      target: src/applier/universal_screening/policy.py
  notes: ''
- id: I4
  description: Implement SessionContext for cross-step contradiction tracking
  state: todo
  touches:
  - src/applier/universal_screening/session_context.py
  checks:
    happy:
      desc: Intent answers recorded and retrieved
      target: src/applier/universal_screening/session_context.py
    negative:
    - desc: Cross-step contradiction detected
      target: src/applier/universal_screening/session_context.py
    boundary:
    - desc: Same intent re-recorded with same answer is allowed
      target: src/applier/universal_screening/session_context.py
  notes: ''
- id: I5
  description: Implement BatchStepValidator with Claude Sonnet client
  state: todo
  touches:
  - src/applier/universal_screening/batch_validator.py
  - src/ai/llm_client.py
  checks:
    happy:
      desc: Validator approves correct step
      target: src/applier/universal_screening/batch_validator.py
    negative:
    - desc: Validator rejects wrong sponsorship answer with fix
      target: src/applier/universal_screening/batch_validator.py
    boundary:
    - desc: Malformed output handled with retry
      target: src/applier/universal_screening/batch_validator.py
  notes: ''
- id: I6
  description: Implement CorrectionExecutor with DOM and vision fallback
  state: todo
  touches:
  - src/applier/universal_screening/correction_executor.py
  checks:
    happy:
      desc: Radio correction applied via DOM
      target: src/applier/universal_screening/correction_executor.py
    negative:
    - desc: Failed correction returns failure result
      target: src/applier/universal_screening/correction_executor.py
    boundary:
    - desc: Fuzzy match finds correct option
      target: src/applier/universal_screening/correction_executor.py
  notes: ''
- id: I7
  description: Implement StepAuditLog and unit tests
  state: todo
  touches:
  - src/applier/universal_screening/audit_log.py
  - tests/screening/
  checks:
    happy:
      desc: Audit entries persisted
      target: src/applier/universal_screening/audit_log.py
    negative:
    - desc: Invalid entry logged with error
      target: src/applier/universal_screening/audit_log.py
    boundary:
    - desc: Metrics aggregated correctly
      target: src/applier/universal_screening/audit_log.py
  notes: ''
```
