# Plan: careerbot-resilience-gaps

**Status**: Plan 'careerbot-resilience-gaps' rev 3 (approved at rev 1): 2/10 done, 1 doing, 0 blocked, 7 todo

## Plan Data

```yaml
plan_id: careerbot-resilience-gaps
revision: 3
approved_revision: 1
items:
- id: I1
  description: Create FormStateMachine with DOM snapshot/restore and answer backtracking
  state: done
  touches:
  - src/applier/form_state_machine.py
  checks:
    happy:
      desc: FSM saves DOM state before step, can rollback on failure
      target: form_state_machine.py
    negative:
    - desc: FSM tries alternate answers when first answer fails
      target: form_state_machine.py
    boundary:
    - desc: FSM escalates to AI Full Form Mode after 3 failures
      target: form_state_machine.py
  evidence:
  - src/applier/form_state_machine.py
  notes: File exists with DOMSnapshot, FormStateMachine, backtracking logic. Fixed import path from 'radio_filler' to 'applier.radio_filler'
- id: I2
  description: Add find_in_all_frames() recursive iframe search to SelectorEngine
  state: done
  touches:
  - src/applier/selector_engine.py
  checks:
    happy:
      desc: Function finds elements in nested iframes up to max_depth
      target: selector_engine.py
    negative:
    - desc: Returns None when element not found in any frame
      target: selector_engine.py
    boundary:
    - desc: Respects max_depth limit to prevent infinite recursion
      target: selector_engine.py
  evidence:
  - src/applier/selector_engine.py
  notes: Function added with recursive frame search
- id: I3
  description: Integrate AI fallbacks into application pipeline
  state: todo
  touches:
  - src/applier/radio_filler.py
  - src/applier/selector_engine.py
  - src/ai/llm_chain.py
  checks:
    happy:
      desc: ai_click_fallback() called when all DOM strategies fail
      target: radio_filler.py
    negative:
    - desc: Rate limit on one provider falls back to next in chain
      target: llm_chain.py
    boundary:
    - desc: Circuit breaker stops infinite AI calls when budget exhausted
      target: llm_chain.py
  notes: ai_click_fallback exists but needs integration
- id: I4
  description: Complete provider stubs for BlackboxAI and OpenRouter
  state: doing
  touches:
  - src/ai/llm_chain.py
  checks:
    happy:
      desc: _call_blackboxai() makes actual API call and returns response
      target: llm_chain.py
    negative:
    - desc: API errors are caught and converted to appropriate error types
      target: llm_chain.py
    boundary:
    - desc: Missing API key returns None gracefully
      target: llm_chain.py
  notes: Implementing actual API calls for BlackboxAI (Kimi) and OpenRouter (DeepSeek)
- id: I5
  description: Create unit tests for verified clicks, retry policy, and strategy store
  state: todo
  touches:
  - tests/test_verified_clicks.py
  - tests/test_retry_policy.py
  - tests/test_strategy_store.py
  checks:
    happy:
      desc: Tests pass for verify_element_state (all 3 methods)
      target: test_verified_clicks.py
    negative:
    - desc: Tests verify retry behavior on transient vs permanent errors
      target: test_retry_policy.py
    boundary:
    - desc: Tests verify TTL expiration in StrategyStore
      target: test_strategy_store.py
  notes: Tests directory is empty; need comprehensive test coverage
- id: I6
  description: Create ApplicationPipeline orchestrator integrating all resilience components
  state: todo
  touches:
  - src/applier/application_pipeline.py
  checks:
    happy:
      desc: Pipeline processes job through DOM → AI → HITL levels
      target: application_pipeline.py
    negative:
    - desc: Pipeline handles retry on transient errors with backoff
      target: application_pipeline.py
    boundary:
    - desc: Pipeline stops at budget limit and triggers HITL
      target: application_pipeline.py
  notes: Central orchestrator connecting all phases
- id: I7
  description: Fix ai_choose_option duplication and create shared AI utilities module
  state: todo
  touches:
  - src/ai/ai_utils.py
  - src/applier/radio_filler.py
  - src/ai/llm_chain.py
  checks:
    happy:
      desc: Single ai_choose_option implementation in ai_utils.py
      target: ai_utils.py
    negative:
    - desc: Both modules import from shared location
      target: radio_filler.py
    boundary:
    - desc: Function handles empty options list gracefully
      target: ai_utils.py
  notes: ai_choose_option duplicated in llm_chain.py and imported in radio_filler.py
- id: I8
  description: Implement ApplicationBudget circuit breaker (Phase 2.5)
  state: todo
  touches:
  - src/applier/application_budget.py
  checks:
    happy:
      desc: Budget limits LLM calls per job to 30
      target: application_budget.py
    negative:
    - desc: Budget exhausted triggers HITL fallback
      target: application_budget.py
    boundary:
    - desc: Daily budget resets at midnight
      target: application_budget.py
  notes: 'Critical: Without this, runaway API costs are unbounded'
- id: I9
  description: Add CDP port parameter to StealthBrowser for Phase 6
  state: todo
  touches:
  - src/stealth/stealth_browser.py
  checks:
    happy:
      desc: StealthBrowser accepts cdp_port parameter
      target: stealth_browser.py
    negative:
    - desc: Invalid CDP port raises appropriate error
      target: stealth_browser.py
    boundary:
    - desc: CDP connection fallback to storage_state on failure
      target: stealth_browser.py
  notes: P1 item from coach review
- id: I10
  description: Add retry_count and retry_after fields to Job model
  state: todo
  touches:
  - src/models/job.py
  - src/applier/queue_manager.py
  checks:
    happy:
      desc: Job model has retry_count, retry_after, last_error_type fields
      target: job.py
    negative:
    - desc: Invalid error_type rejected
      target: job.py
    boundary:
    - desc: retry_after handles timezone correctly
      target: job.py
  notes: Required for RetryPolicy database integration
```
