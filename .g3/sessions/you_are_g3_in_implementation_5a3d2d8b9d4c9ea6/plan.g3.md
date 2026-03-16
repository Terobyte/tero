# Plan: top10-features-implementation

**Status**: Plan 'top10-features-implementation' rev 1 (approved at rev 1): 0/9 done, 1 doing, 0 blocked, 8 todo

## Plan Data

```yaml
plan_id: top10-features-implementation
revision: 1
approved_revision: 1
items:
- id: I1
  description: WebRTC Leak Prevention - Add flags to browser_stealth.py
  state: todo
  touches:
  - src/stealth/browser_stealth.py
  - tests/stealth/test_webrtc_leak.py
  checks:
    happy:
      desc: WebRTC flags present in browser launch args
      target: src/stealth/browser_stealth.py
    negative:
    - desc: WebRTC ICE candidates empty or only relay type
      target: tests/stealth/test_webrtc_leak.py
    boundary:
    - desc: check_fingerprint_exposure includes WebRTC check
      target: src/stealth/browser_stealth.py
- id: I2
  description: Fingerprint Scripts - Canvas/Audio/Battery spoofing
  state: todo
  touches:
  - src/stealth/fingerprint_scripts.py
  - tests/stealth
  checks:
    happy:
      desc: Canvas noise injection generates different hashes
      target: src/stealth/fingerprint_scripts.py
    negative:
    - desc: Audio noise injection works
      target: tests/stealth/test_audio_spoofing.py
    boundary:
    - desc: Battery API returns laptop-like values
      target: tests/stealth/test_battery_spoof.py
- id: I3
  description: Geo-Location Consistency Check
  state: todo
  touches:
  - src/stealth/geo_consistency.py
  - tests/stealth/test_geo_consistency.py
  checks:
    happy:
      desc: Timezone matches IP geolocation
      target: src/stealth/geo_consistency.py
    negative:
    - desc: Mismatch detected when timezone differs from IP
      target: tests/stealth/test_geo_consistency.py
    boundary:
    - desc: Unknown region returns None
      target: tests/stealth/test_geo_consistency.py
- id: I4
  description: Slug Collection - Common Crawl CDX scripts
  state: todo
  touches:
  - scripts/collect_cdx_slugs.py
  - scripts/validate_slugs.py
  - tests/scripts/test_cdx_slug_collector.py
  checks:
    happy:
      desc: Slugs extracted from CDX API responses
      target: scripts/collect_cdx_slugs.py
    negative:
    - desc: Junk slugs filtered out
      target: tests/scripts/test_cdx_slug_collector.py
    boundary:
    - desc: Slug length bounds enforced
      target: tests/scripts/test_cdx_slug_collector.py
- id: I5
  description: Screening Batch Validation - QuestionIntent, SessionContext, BatchValidator
  state: todo
  touches:
  - src/applier/universal_screening
  - config/critical_intents.json
  - tests/applier/test_batch_validator.py
  checks:
    happy:
      desc: Critical questions detected correctly
      target: src/applier/universal_screening/question_intent.py
    negative:
    - desc: Critical errors returned for bad answers
      target: tests/applier/test_batch_validator.py
    boundary:
    - desc: Empty session returns no corrections
      target: src/applier/universal_screening/batch_validator.py
- id: I6
  description: LinkedIn Voyager API client
  state: todo
  touches:
  - src/platforms/linkedin_voyager.py
  - tests/platforms/test_linkedin_voyager.py
  checks:
    happy:
      desc: Jobs parsed from Voyager response
      target: src/platforms/linkedin_voyager.py
    negative:
    - desc: Malformed elements handled gracefully
      target: tests/platforms/test_linkedin_voyager.py
    boundary:
    - desc: Empty response returns empty list
      target: src/platforms/linkedin_voyager.py
- id: I7
  description: RAG + BERT Embeddings - SemanticAnswerBank
  state: todo
  touches:
  - src/applier/semantic_answer_bank.py
  - src/applier/answer_bank.py
  - tests/applier/test_semantic_answer_bank.py
  checks:
    happy:
      desc: Semantic match finds similar questions
      target: src/applier/semantic_answer_bank.py
    negative:
    - desc: Unrelated question returns None
      target: tests/applier/test_semantic_answer_bank.py
    boundary:
    - desc: Regex match takes priority over semantic
      target: tests/applier/test_semantic_answer_bank.py
- id: I8
  description: Indeed GraphQL API client
  state: todo
  touches:
  - src/platforms/indeed_api.py
  - tests/platforms/test_indeed_api.py
  checks:
    happy:
      desc: Jobs parsed from GraphQL response
      target: src/platforms/indeed_api.py
    negative:
    - desc: 401 error handled gracefully
      target: src/platforms/indeed_api.py
    boundary:
    - desc: Empty results handled
      target: src/platforms/indeed_api.py
- id: I9
  description: Create project structure and base classes
  state: doing
  touches:
  - src/__init__.py
  - src/stealth/__init__.py
  - src/platforms/__init__.py
  - src/applier/__init__.py
  - config
  - data
  checks:
    happy:
      desc: All directories created
      target: src/
    negative:
    - desc: Base scraper class exists
      target: src/platforms/base_scraper.py
    boundary:
    - desc: Requirements file updated
      target: requirements.txt
```
