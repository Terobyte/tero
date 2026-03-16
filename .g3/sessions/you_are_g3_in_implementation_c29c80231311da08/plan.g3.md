# Plan: careerbot-features-1-7

**Status**: Plan 'careerbot-features-1-7' rev 2 (approved at rev 1): 3/10 done, 2 doing, 0 blocked, 5 todo

## Plan Data

```yaml
plan_id: careerbot-features-1-7
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create project structure (src/ directories, config/, data/)
  state: done
  touches:
  - src/
  - config/
  - data/
  checks:
    happy:
      desc: All directories exist with __init__.py files
      target: src/
    negative:
    - desc: Missing directories handled gracefully
      target: src/
    boundary:
    - desc: Empty directories created without errors
      target: data/
  evidence:
  - src/utils/
  - src/ai/
  - config/
  - data/
  notes: Directories already exist from previous session
- id: I2
  description: Feature 1 - AI Cost Tracking (cost_tracker.py)
  state: done
  touches:
  - src/utils/cost_tracker.py
  checks:
    happy:
      desc: CostTracker singleton tracks LLM costs correctly
      target: src/utils/cost_tracker.py
    negative:
    - desc: Invalid provider falls back to default pricing
      target: src/utils/cost_tracker.py::CostTracker.track
    boundary:
    - desc: Zero tokens returns zero cost
      target: src/utils/cost_tracker.py::CostTracker.track
  evidence:
  - src/utils/cost_tracker.py
  notes: Already implemented from previous session
- id: I3
  description: Feature 2 - Batch Validation Phase 2-5 (snapshot, correction_executor, policy, audit_log)
  state: doing
  touches:
  - src/applier/universal_screening/
  checks:
    happy:
      desc: StepSnapshot captures form state for re-fill
      target: src/applier/universal_screening/snapshot.py
    negative:
    - desc: CorrectionExecutor handles missing locators gracefully
      target: src/applier/universal_screening/correction_executor.py
    boundary:
    - desc: Empty corrections list handled without errors
      target: src/applier/universal_screening/policy.py
- id: I4
  description: Feature 3 - Vector Knowledge Base (ChromaDB)
  state: todo
  touches:
  - src/ai/knowledge_base.py
  checks:
    happy:
      desc: VectorKnowledgeBase indexes and searches PKB chunks
      target: src/ai/knowledge_base.py
    negative:
    - desc: ChromaDB not installed raises ImportError
      target: src/ai/knowledge_base.py
    boundary:
    - desc: Empty collection returns empty results
      target: src/ai/knowledge_base.py::VectorKnowledgeBase.search_pkb
- id: I5
  description: Feature 4 - Gap Recommender
  state: todo
  touches:
  - src/ai/gap_recommender.py
  checks:
    happy:
      desc: GapRecommender generates actionable recommendations
      target: src/ai/gap_recommender.py
    negative:
    - desc: LLM failure returns fallback recommendations
      target: src/ai/gap_recommender.py::GapRecommender.recommend
    boundary:
    - desc: Empty gaps list returns empty report
      target: src/ai/gap_recommender.py::GapRecommender.recommend
- id: I6
  description: Feature 5 - Natural Language Generator (NLG)
  state: todo
  touches:
  - src/ai/nlg.py
  checks:
    happy:
      desc: NLG generates unique job-specific answers
      target: src/ai/nlg.py
    negative:
    - desc: LLM failure returns fallback answer
      target: src/ai/nlg.py::NaturalLanguageGenerator.generate
    boundary:
    - desc: Max_words truncates long answers
      target: src/ai/nlg.py::NaturalLanguageGenerator.generate
- id: I7
  description: Feature 6 - RAG + Embeddings Pipeline
  state: todo
  touches:
  - src/ai/embeddings.py
  checks:
    happy:
      desc: EmbeddingsPipeline performs hybrid search
      target: src/ai/embeddings.py
    negative:
    - desc: Missing rank_bm25 falls back to semantic-only
      target: src/ai/embeddings.py
    boundary:
    - desc: Empty index returns empty results
      target: src/ai/embeddings.py::EmbeddingsPipeline.search
- id: I8
  description: Feature 7 - Match Rate Calculator
  state: done
  touches:
  - src/ai/match_rate.py
  checks:
    happy:
      desc: MatchRateCalculator produces detailed match report
      target: src/ai/match_rate.py
    negative:
    - desc: Missing profile handled with defaults
      target: src/ai/match_rate.py::MatchRateCalculator.calculate
    boundary:
    - desc: Perfect match scores 100
      target: src/ai/match_rate.py::MatchRateCalculator.calculate
  evidence:
  - src/ai/match_rate.py
  notes: Already implemented from previous session
- id: I9
  description: Create supporting modules (llm_client, orchestrator, main.py)
  state: todo
  touches:
  - src/ai/llm_client.py
  - src/ai/orchestrator.py
  - main.py
  checks:
    happy:
      desc: LLM client and orchestrator work with features
      target: src/ai/
    negative:
    - desc: Missing dependencies handled gracefully
      target: src/ai/
    boundary:
    - desc: Empty inputs handled without errors
      target: src/ai/llm_client.py
- id: I10
  description: Create config files (settings.json with cost_limits)
  state: doing
  touches:
  - config/settings.json
  checks:
    happy:
      desc: Config files load correctly
      target: config/settings.json
    negative:
    - desc: Missing config uses defaults
      target: config/settings.json
    boundary:
    - desc: Empty config file handled
      target: config/settings.json
```
