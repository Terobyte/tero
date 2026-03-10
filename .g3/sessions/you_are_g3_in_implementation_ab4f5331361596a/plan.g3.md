# Plan: universal-ai-screening-implementation

**Status**: Plan 'universal-ai-screening-implementation' rev 2 (approved at rev 1): 4/10 done, 2 doing, 0 blocked, 4 todo

## Plan Data

```yaml
plan_id: universal-ai-screening-implementation
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create directory structure and base infrastructure
  state: done
  touches:
  - src/
  - src/ai/
  - src/applier/
  - src/utils/
  - data/
  - tests/
  checks:
    happy:
      desc: All directories created with __init__.py files
      target: infrastructure
    negative:
    - desc: Handles permission errors gracefully
      target: infrastructure
    boundary:
    - desc: Works on fresh workspace
      target: infrastructure
  evidence:
  - src/ai/__init__.py
  - src/utils/__init__.py
  notes: Directories and __init__.py files created
- id: I2
  description: Create FormPatternStore (Task 3)
  state: done
  touches:
  - src/applier/form_pattern_store.py
  checks:
    happy:
      desc: Pattern saves and loads correctly
      target: FormPatternStore
    negative:
    - desc: Returns None for non-existent patterns
      target: FormPatternStore
    - desc: Does not overwrite confirmed with pending
      target: FormPatternStore
    boundary:
    - desc: Handles empty fingerprint
      target: FormPatternStore
  evidence:
  - src/applier/form_pattern_store.py
  notes: File already exists with complete implementation
- id: I3
  description: Create human_review.py (Task 6a)
  state: done
  touches:
  - src/applier/human_review.py
  checks:
    happy:
      desc: Menu displays correctly with 4 variants
      target: human_review
    negative:
    - desc: Handles EOFError gracefully
      target: human_review
    boundary:
    - desc: bot_mode skips menu
      target: human_review
  evidence:
  - src/applier/human_review.py
  notes: File already exists with complete implementation
- id: I4
  description: Create ai_answerer.py with ask_variants (Task 6b)
  state: done
  touches:
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: ask_variants returns 4 variants with best index
      target: ai_answerer
    negative:
    - desc: Fallback to ask() on JSON parse error
      target: ai_answerer
    boundary:
    - desc: Empty profile returns hardcoded fallback
      target: ai_answerer
  evidence:
  - src/applier/ai_answerer.py
  notes: File already exists with ask_variants() method
- id: I5
  description: Create universal_ai_filler.py with Gemini methods (Task 2)
  state: doing
  touches:
  - src/applier/universal_ai_filler.py
  checks:
    happy:
      desc: extract_semantic_snapshot returns questions dict
      target: universal_ai_filler
    negative:
    - desc: analyze_and_map handles empty snapshot
      target: universal_ai_filler
    boundary:
    - desc: execute_actions uses label fallback chain
      target: universal_ai_filler
  notes: ''
- id: I6
  description: Create indeed_screening.py with HITL integration (Task 6c)
  state: todo
  touches:
  - src/applier/indeed_screening.py
  checks:
    happy:
      desc: answer_question uses HITL for textarea
      target: indeed_screening
    negative:
    - desc: Returns None when no answer found
      target: indeed_screening
    boundary:
    - desc: bot_mode auto-selects best variant
      target: indeed_screening
  notes: ''
- id: I7
  description: Create applier files (lever, workday, ashby, bamboohr, indeed, indeed_router)
  state: todo
  touches:
  - src/applier/lever_apply.py
  - src/applier/workday_apply.py
  - src/applier/ashby_apply.py
  - src/applier/bamboohr_apply.py
  - src/applier/indeed_apply.py
  - src/applier/indeed_router.py
  checks:
    happy:
      desc: All appliers use IndeedScreeningHandler
      target: appliers
    negative:
    - desc: Handles missing page/frame gracefully
      target: appliers
    boundary:
    - desc: Unknown ATS falls back to LLM
      target: indeed_router
  notes: ''
- id: I8
  description: Create supporting infrastructure (llm_client, helpers)
  state: doing
  touches:
  - src/ai/llm_client.py
  - src/utils/helpers.py
  checks:
    happy:
      desc: GeminiClient initializes and generates content
      target: llm_client
    negative:
    - desc: Retry on rate limit
      target: llm_client
    boundary:
    - desc: Empty profile.yaml handled
      target: helpers
  notes: ''
- id: I9
  description: Create data files
  state: todo
  touches:
  - data/form_patterns.json
  - data/custom_answers.json
  - data/candidate/profile.yaml
  checks:
    happy:
      desc: All data files created with valid JSON/YAML
      target: data_files
    negative:
    - desc: Invalid JSON handled gracefully
      target: data_files
    boundary:
    - desc: Empty files created if missing
      target: data_files
  notes: ''
- id: I10
  description: Create integration tests
  state: todo
  touches:
  - tests/test_form_pattern_store.py
  - tests/test_human_review.py
  - tests/test_ai_answerer.py
  checks:
    happy:
      desc: All tests pass
      target: tests
    negative:
    - desc: Tests handle missing dependencies
      target: tests
    boundary:
    - desc: Edge cases covered
      target: tests
  notes: ''
```
