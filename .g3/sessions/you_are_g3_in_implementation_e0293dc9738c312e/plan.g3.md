# Plan: top-10-features-implementation

**Status**: Plan 'top-10-features-implementation' rev 2 (approved at rev 1): 0/10 done, 1 doing, 0 blocked, 9 todo

## Plan Data

```yaml
plan_id: top-10-features-implementation
revision: 2
approved_revision: 1
items:
- id: I1
  description: Implement WebRTC Leak Prevention in browser_stealth.py
  state: doing
  touches:
  - src/stealth/browser_stealth.py
  - tests/stealth/test_webrtc_leak.py
  checks:
    happy:
      desc: WebRTC flags present and working
      target: src/stealth/browser_stealth.py
    negative:
    - desc: Missing flags detected by test
      target: tests/stealth/test_webrtc_leak.py
    boundary:
    - desc: ICE candidates empty with flags
      target: tests/stealth/test_webrtc_leak.py
  notes: ''
- id: I2
  description: Implement Screening Batch Validation (QuestionIntent, SessionContext, BatchValidator)
  state: todo
  touches:
  - src/applier/universal_screening/
  - config/critical_intents.json
  - tests/applier/test_batch_validator.py
  checks:
    happy:
      desc: Critical questions detected and validated
      target: src/applier/universal_screening/batch_validator.py
    negative:
    - desc: Invalid answers trigger corrections
      target: tests/applier/test_batch_validator.py
    boundary:
    - desc: Empty session returns empty corrections
      target: src/applier/universal_screening/batch_validator.py
  notes: ''
- id: I3
  description: Implement Slug Collection from Common Crawl CDX API
  state: todo
  touches:
  - scripts/collect_cdx_slugs.py
  - scripts/validate_slugs.py
  - tests/scripts/test_cdx_slug_collector.py
  checks:
    happy:
      desc: Slugs extracted from CDX responses
      target: scripts/collect_cdx_slugs.py
    negative:
    - desc: Invalid URLs filtered out
      target: scripts/collect_cdx_slugs.py
    boundary:
    - desc: Empty CDX response handled
      target: scripts/collect_cdx_slugs.py
  notes: ''
- id: I4
  description: Implement LinkedIn Voyager API client
  state: todo
  touches:
  - src/platforms/linkedin_voyager.py
  - tests/platforms/test_linkedin_voyager.py
  checks:
    happy:
      desc: Jobs parsed from Voyager API response
      target: src/platforms/linkedin_voyager.py
    negative:
    - desc: 401 error handled gracefully
      target: src/platforms/linkedin_voyager.py
    boundary:
    - desc: Empty response returns no jobs
      target: src/platforms/linkedin_voyager.py
  notes: ''
- id: I5
  description: Implement RAG + BERT Embeddings for SemanticAnswerBank
  state: todo
  touches:
  - src/applier/semantic_answer_bank.py
  - src/applier/answer_bank.py
  - tests/applier/test_semantic_answer_bank.py
  checks:
    happy:
      desc: Semantic search finds similar questions
      target: src/applier/semantic_answer_bank.py
    negative:
    - desc: Unrelated questions return None
      target: src/applier/semantic_answer_bank.py
    boundary:
    - desc: Regex match takes priority over semantic
      target: src/applier/semantic_answer_bank.py
  notes: ''
- id: I6
  description: Implement Canvas Noise Injection v2
  state: todo
  touches:
  - src/stealth/fingerprint_scripts.py
  - tests/stealth/test_canvas_noise.py
  checks:
    happy:
      desc: Canvas hash differs across sessions
      target: src/stealth/fingerprint_scripts.py
    negative:
    - desc: Invalid seed handled
      target: src/stealth/fingerprint_scripts.py
    boundary:
    - desc: Same seed produces same hash
      target: src/stealth/fingerprint_scripts.py
  notes: ''
- id: I7
  description: Implement AudioContext Fingerprint Spoofing
  state: todo
  touches:
  - src/stealth/fingerprint_scripts.py
  - tests/stealth/test_audio_spoofing.py
  checks:
    happy:
      desc: Audio hash differs across sessions
      target: src/stealth/fingerprint_scripts.py
    negative:
    - desc: Audio API not available handled
      target: src/stealth/fingerprint_scripts.py
    boundary:
    - desc: Noise level imperceptible
      target: src/stealth/fingerprint_scripts.py
  notes: ''
- id: I8
  description: Implement Battery API Spoofing
  state: todo
  touches:
  - src/stealth/fingerprint_scripts.py
  - tests/stealth/test_battery_spoof.py
  checks:
    happy:
      desc: Battery returns realistic laptop values
      target: src/stealth/fingerprint_scripts.py
    negative:
    - desc: No battery API handled
      target: src/stealth/fingerprint_scripts.py
    boundary:
    - desc: Level in 65-95% range
      target: src/stealth/fingerprint_scripts.py
  notes: ''
- id: I9
  description: Implement Indeed GraphQL / Mobile API
  state: todo
  touches:
  - src/platforms/indeed_api.py
  - tests/platforms/test_indeed_api.py
  checks:
    happy:
      desc: Jobs parsed from GraphQL response
      target: src/platforms/indeed_api.py
    negative:
    - desc: 401 token expired handled
      target: src/platforms/indeed_api.py
    boundary:
    - desc: Empty results handled
      target: src/platforms/indeed_api.py
  notes: ''
- id: I10
  description: Implement Geo-Location Consistency Check
  state: todo
  touches:
  - src/stealth/geo_consistency.py
  - tests/stealth/test_geo_consistency.py
  checks:
    happy:
      desc: Matching timezone returns consistent=True
      target: src/stealth/geo_consistency.py
    negative:
    - desc: Mismatching timezone detected
      target: src/stealth/geo_consistency.py
    boundary:
    - desc: Unknown IP returns consistent=True
      target: src/stealth/geo_consistency.py
  notes: ''
```
