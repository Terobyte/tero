# Plan: top10-features-implementation

**Status**: Plan 'top10-features-implementation' rev 3 (approved at rev 1): 2/6 done, 1 doing, 0 blocked, 3 todo

## Plan Data

```yaml
plan_id: top10-features-implementation
revision: 3
approved_revision: 1
items:
- id: I1
  description: Screening Batch Validation (Phases 2-5) - Create question_intent.py, session_context.py, batch_validator.py
  state: done
  touches:
  - src/applier/universal_screening/question_intent.py
  - src/applier/universal_screening/session_context.py
  - src/applier/universal_screening/batch_validator.py
  - config/critical_intents.json
  checks:
    happy:
      desc: Valid screening answers pass validation without corrections
      target: batch_validator.py
    negative:
    - desc: Critical error detected when visa sponsorship answered Yes
      target: batch_validator.py
    boundary:
    - desc: Empty session context returns empty corrections list
      target: batch_validator.py
  evidence:
  - src/applier/universal_screening/question_intent.py
  - src/applier/universal_screening/session_context.py
  - src/applier/universal_screening/batch_validator.py
  - config/critical_intents.json
  notes: Created all three modules with dataclasses and async validation
- id: I2
  description: Slug Collection - Common Crawl CDX scripts
  state: done
  touches:
  - scripts/collect_cdx_slugs.py
  - scripts/validate_slugs.py
  checks:
    happy:
      desc: CDX script extracts slugs from mock response
      target: scripts/collect_cdx_slugs.py
    negative:
    - desc: Invalid ATS name raises error
      target: scripts/collect_cdx_slugs.py
    boundary:
    - desc: Empty CDX response returns empty slug set
      target: scripts/collect_cdx_slugs.py
  evidence:
  - scripts/collect_cdx_slugs.py
  - scripts/validate_slugs.py
  notes: Created CDX collection and validation scripts with rate limiting
- id: I3
  description: LinkedIn Voyager API client
  state: doing
  touches:
  - src/platforms/linkedin_voyager.py
  - src/platforms/base_scraper.py
  checks:
    happy:
      desc: VoyagerSession loads and parses cookies correctly
      target: src/platforms/linkedin_voyager.py
    negative:
    - desc: Expired session returns 401 and logs error
      target: src/platforms/linkedin_voyager.py
    boundary:
    - desc: Empty API response returns empty job list
      target: src/platforms/linkedin_voyager.py
  notes: ''
- id: I4
  description: RAG + BERT Embeddings - SemanticAnswerBank
  state: todo
  touches:
  - src/applier/semantic_answer_bank.py
  - src/applier/answer_bank.py
  checks:
    happy:
      desc: Semantic search finds answer for variant question
      target: src/applier/semantic_answer_bank.py
    negative:
    - desc: Unrelated question returns None
      target: src/applier/semantic_answer_bank.py
    boundary:
    - desc: Regex match returns first without loading BERT
      target: src/applier/semantic_answer_bank.py
  notes: ''
- id: I5
  description: Indeed GraphQL / Mobile API client
  state: todo
  touches:
  - src/platforms/indeed_api.py
  checks:
    happy:
      desc: IndeedAPISession creates correct headers with Bearer token
      target: src/platforms/indeed_api.py
    negative:
    - desc: 401 response logs token expired error
      target: src/platforms/indeed_api.py
    boundary:
    - desc: Empty GraphQL response returns empty job list
      target: src/platforms/indeed_api.py
  notes: ''
- id: I6
  description: Create tests for all new modules
  state: todo
  touches:
  - tests/applier/test_batch_validator.py
  - tests/scripts/test_cdx_slug_collector.py
  - tests/platforms/test_linkedin_voyager.py
  - tests/applier/test_semantic_answer_bank.py
  - tests/platforms/test_indeed_api.py
  - tests/stealth/test_geo_consistency.py
  checks:
    happy:
      desc: All tests pass
      target: tests/
    negative:
    - desc: Tests mock external dependencies
      target: tests/
    boundary:
    - desc: Slow tests marked with pytest.mark.slow
      target: tests/
  notes: ''
```
