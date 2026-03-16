# Plan: careerbot-fixes

**Status**: Plan 'careerbot-fixes' rev 9 (approved at rev 1): 2/8 done, 1 doing, 0 blocked, 5 todo

## Plan Data

```yaml
plan_id: careerbot-fixes
revision: 9
approved_revision: 1
items:
- id: F1
  description: Fix Tier 4 coordinate click - LLM returns box numbers but coordinates are never used for clicking
  state: done
  touches:
  - src/applier/selector_engine.py
  - src/ai/llm_chain.py
  checks:
    happy:
      desc: Tier 4 returns callable that clicks at coordinates (x, y) from bounding boxes
      target: src/applier/selector_engine.py
    negative:
    - desc: Returns None when LLM cannot identify element
      target: src/applier/selector_engine.py
    - desc: Handles invalid box numbers gracefully
      target: src/applier/selector_engine.py
    boundary:
    - desc: Works when bounding boxes have zero dimensions
      target: src/applier/selector_engine.py
  evidence:
  - src/applier/selector_engine.py:340-380
  - src/ai/llm_chain.py:570-640
  notes: Tier 4 now uses bounding boxes with coordinate extraction from LLM response
- id: F2
  description: Fix element.page bug in radio_filler.py strategies - Playwright Locator has no .page attribute
  state: done
  touches:
  - src/applier/radio_filler.py
  checks:
    happy:
      desc: Strategies receive page parameter and use it correctly
      target: src/applier/radio_filler.py
    negative:
    - desc: Handles missing page parameter gracefully
      target: src/applier/radio_filler.py
    boundary:
    - desc: Works with both Locator and ElementHandle
      target: src/applier/radio_filler.py
  evidence:
  - src/applier/radio_filler.py:63-85
  - src/applier/radio_filler.py:130-165
  notes: All strategies now receive `page` parameter instead of using element.page
- id: F3
  description: Implement BlackboxAI and OpenRouter provider API calls - currently return None with warning
  state: doing
  touches:
  - src/ai/llm_chain.py
  checks:
    happy:
      desc: BlackboxAI and OpenRouter make actual API calls when API keys are set
      target: src/ai/llm_chain.py
    negative:
    - desc: Returns None when API key is missing
      target: src/ai/llm_chain.py
    - desc: Handles rate limit errors by trying next provider
      target: src/ai/llm_chain.py
    boundary:
    - desc: Timeout handling with proper error propagation
      target: src/ai/llm_chain.py
  notes: ''
- id: F4
  description: Fix ApplicationBudget.record_llm_call signature mismatch
  state: todo
  touches:
  - src/applier/queue_manager.py
  checks:
    happy:
      desc: record_llm_call accepts provider, tokens, and cost parameters
      target: src/applier/queue_manager.py
    negative:
    - desc: Handles missing parameters gracefully
      target: src/applier/queue_manager.py
    boundary:
    - desc: Tracks cumulative costs correctly
      target: src/applier/queue_manager.py
  notes: ''
- id: F5
  description: Create ApplicationPipeline orchestrator connecting all components
  state: todo
  touches:
  - src/applier/pipeline.py
  checks:
    happy:
      desc: Pipeline orchestrates RetryPolicy, ApplicationBudget, StrategyStore, FormStateMachine, LLMChain
      target: src/applier/pipeline.py
    negative:
    - desc: Handles component failures gracefully
      target: src/applier/pipeline.py
    boundary:
    - desc: Respects budget limits and timeout constraints
      target: src/applier/pipeline.py
  notes: ''
- id: F6
  description: Add unit tests for verified clicks, StrategyStore, and RetryPolicy
  state: todo
  touches:
  - tests/
  checks:
    happy:
      desc: Tests pass for verify_element_state, fill_checkbox_verified, StrategyStore
      target: tests/
    negative:
    - desc: Tests fail when verification logic is broken
      target: tests/
    boundary:
    - desc: Tests cover edge cases like stale elements and empty options
      target: tests/
  notes: ''
- id: F7
  description: Add form_submitted metric to ApplicationResult for tracking completion
  state: todo
  touches:
  - src/models/job.py
  checks:
    happy:
      desc: ApplicationResult tracks form_submitted boolean
      target: src/models/job.py
    negative:
    - desc: form_submitted defaults to False
      target: src/models/job.py
    boundary:
    - desc: form_submitted tracked even when some fields failed
      target: src/models/job.py
  notes: ''
- id: F8
  description: Add requirements.txt with all dependencies
  state: todo
  touches:
  - requirements.txt
  checks:
    happy:
      desc: All imports resolve with pip install -r requirements.txt
      target: requirements.txt
    negative:
    - desc: Missing dependencies cause ImportError
      target: requirements.txt
    boundary:
    - desc: Version pinning for critical packages
      target: requirements.txt
  notes: ''
```
