# Plan: careerbot-integration

**Status**: Plan 'careerbot-integration' rev 2 (approved at rev 1): 0/6 done, 1 doing, 0 blocked, 5 todo

## Plan Data

```yaml
plan_id: careerbot-integration
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create src/bot/main_bot.py with aiogram Dispatcher and router registration
  state: doing
  touches:
  - src/bot/main_bot.py
  checks:
    happy:
      desc: main_bot.py starts Telegram bot with all routers registered
      target: src/bot/main_bot.py
    negative:
    - desc: Handles missing bot token gracefully
      target: src/bot/main_bot.py
    boundary:
    - desc: Supports graceful shutdown
      target: src/bot/main_bot.py
  notes: ''
- id: I2
  description: Create src/utils/scheduler.py with APScheduler tasks
  state: todo
  touches:
  - src/utils/scheduler.py
  checks:
    happy:
      desc: Scheduler runs daily insights at 9:00 and weekly market report Friday 18:00
      target: src/utils/scheduler.py
    negative:
    - desc: Handles missing bot instance gracefully
      target: src/utils/scheduler.py
    boundary:
    - desc: Scheduler persists across restarts
      target: src/utils/scheduler.py
  notes: ''
- id: I3
  description: Create src/ai/orchestrator.py with ResumeTailor integration
  state: todo
  touches:
  - src/ai/orchestrator.py
  checks:
    happy:
      desc: Orchestrator integrates ResumeTailor for job applications
      target: src/ai/orchestrator.py
    negative:
    - desc: Handles LLM failures gracefully
      target: src/ai/orchestrator.py
    boundary:
    - desc: Works without LLM client (scoring only)
      target: src/ai/orchestrator.py
  notes: ''
- id: I4
  description: Create data/jobs.json sample file for MarketInsights
  state: todo
  touches:
  - data/jobs.json
  checks:
    happy:
      desc: Sample jobs.json loads correctly in MarketInsights
      target: data/jobs.json
    negative:
    - desc: Handles malformed JSON gracefully
      target: src/analytics/market_insights.py
    boundary:
    - desc: Sample covers various ATS types and salary ranges
      target: data/jobs.json
  notes: ''
- id: I5
  description: Create data/candidate/profile.yaml for ProgressiveProfiling
  state: todo
  touches:
  - data/candidate/profile.yaml
  checks:
    happy:
      desc: Profile YAML loads correctly in ProgressiveProfiling
      target: data/candidate/profile.yaml
    negative:
    - desc: Handles missing file gracefully
      target: src/insights/progressive_profiling.py
    boundary:
    - desc: Partial profile tracks completion correctly
      target: data/candidate/profile.yaml
  notes: ''
- id: I6
  description: Create data/ab_tests.json for ABTester persistence
  state: todo
  touches:
  - data/ab_tests.json
  checks:
    happy:
      desc: ABTester loads and saves experiment data
      target: data/ab_tests.json
    negative:
    - desc: Handles corrupted file by starting fresh
      target: src/learning/ab_tester.py
    boundary:
    - desc: Multiple experiments tracked correctly
      target: data/ab_tests.json
  notes: ''
```
