# Plan: careerbot-resilience-implementation

**Status**: Plan 'careerbot-resilience-implementation' rev 2 (approved at rev 1): 0/8 done, 1 doing, 0 blocked, 7 todo

## Plan Data

```yaml
plan_id: careerbot-resilience-implementation
revision: 2
approved_revision: 1
items:
- id: I1
  description: Fix __init__.py files to not import non-existent modules (temporary fix for compilation)
  state: doing
  touches:
  - src/applier/__init__.py
  - src/ai/__init__.py
  - src/stealth/__init__.py
  - src/hitl/__init__.py
  checks:
    happy:
      desc: All __init__.py files compile without errors
      target: src/*/__init__.py
    negative:
    - desc: Import of non-existent modules handled gracefully
      target: src/*/__init__.py
    boundary:
    - desc: Empty imports list doesn't break module
      target: src/*/__init__.py
  notes: ''
- id: I2
  description: Implement src/applier/radio_filler.py - verified checkbox/radio/select functions
  state: todo
  touches:
  - src/applier/radio_filler.py
  checks:
    happy:
      desc: fill_checkbox_verified uses 3-method verification
      target: src/applier/radio_filler.py::fill_checkbox_verified
    negative:
    - desc: Handles stale elements gracefully
      target: src/applier/radio_filler.py::verify_element_state
    boundary:
    - desc: Returns None when verification impossible
      target: src/applier/radio_filler.py::verify_element_state
  notes: ''
- id: I3
  description: Implement src/applier/selector_engine.py - 4-tier selector discovery with Shadow DOM
  state: todo
  touches:
  - src/applier/selector_engine.py
  checks:
    happy:
      desc: SelectorEngine discovers elements through 4 tiers
      target: src/applier/selector_engine.py::SelectorEngine
    negative:
    - desc: Handles stale element exceptions with retry
      target: src/applier/selector_engine.py
    boundary:
    - desc: Shadow DOM >>> piercing works
      target: src/applier/selector_engine.py::INTENT_REGISTRY
  notes: ''
- id: I4
  description: Implement src/applier/strategy_store.py - persist winning strategies
  state: todo
  touches:
  - src/applier/strategy_store.py
  checks:
    happy:
      desc: record_success persists strategy for domain
      target: src/applier/strategy_store.py::StrategyStore::record_success
    negative:
    - desc: TTL expiration removes old entries
      target: src/applier/strategy_store.py::StrategyStore
    boundary:
    - desc: Returns None when no winning strategy
      target: src/applier/strategy_store.py::StrategyStore::get_winning_strategy
  notes: ''
- id: I5
  description: Implement src/ai/llm_chain.py - AI fallback chain (Gemini → Kimi → DeepSeek)
  state: todo
  touches:
  - src/ai/llm_chain.py
  checks:
    happy:
      desc: call_chain_verified tries each provider in order
      target: src/ai/llm_chain.py::call_chain_verified
    negative:
    - desc: Handles RateLimitError gracefully
      target: src/ai/llm_chain.py
    boundary:
    - desc: Returns None when all providers fail
      target: src/ai/llm_chain.py::call_chain_verified
  notes: ''
- id: I6
  description: Implement src/ai/gemini_vision.py and src/ai/ai_answerer.py
  state: todo
  touches:
  - src/ai/gemini_vision.py
  - src/ai/ai_answerer.py
  checks:
    happy:
      desc: GeminiVision can analyze screenshots with bounding boxes
      target: src/ai/gemini_vision.py::GeminiVision
    negative:
    - desc: Handles API errors gracefully
      target: src/ai/gemini_vision.py
    boundary:
    - desc: Returns None on timeout
      target: src/ai/ai_answerer.py::AIAnswerer
  notes: ''
- id: I7
  description: Implement stealth modules (stealth_browser.py, human_simulator.py, warmup_tracker.py)
  state: todo
  touches:
  - src/stealth/stealth_browser.py
  - src/stealth/human_simulator.py
  - src/stealth/warmup_tracker.py
  checks:
    happy:
      desc: StealthBrowser creates browser with anti-detection
      target: src/stealth/stealth_browser.py::StealthBrowser
    negative:
    - desc: Handles browser crash gracefully
      target: src/stealth/stealth_browser.py
    boundary:
    - desc: WarmupTracker respects daily limits
      target: src/stealth/warmup_tracker.py::WarmupTracker
  notes: ''
- id: I8
  description: Implement src/hitl/telegram_handler.py - HITL escape valve
  state: todo
  touches:
  - src/hitl/telegram_handler.py
  checks:
    happy:
      desc: request_human_help sends Telegram message
      target: src/hitl/telegram_handler.py::request_human_help
    negative:
    - desc: Handles Telegram API errors gracefully
      target: src/hitl/telegram_handler.py
    boundary:
    - desc: Timeout returns False after N minutes
      target: src/hitl/telegram_handler.py::TelegramHandler
  notes: ''
```
