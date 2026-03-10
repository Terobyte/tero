# Plan: indeed-mtd-implementation

**Status**: Plan 'indeed-mtd-implementation' rev 6 (approved at rev 1): 7/8 done, 1 doing, 0 blocked, 0 todo

## Plan Data

```yaml
plan_id: indeed-mtd-implementation
revision: 6
approved_revision: 1
items:
- id: I0
  description: Create project structure and base files (directories, __init__.py files, requirements.txt)
  state: done
  touches:
  - src
  - src/applier
  - src/platforms
  - src/ai
  - data
  - tests
  checks:
    happy:
      desc: All directories and base files exist
      target: project_root
    negative:
    - desc: No import errors when importing modules
      target: src/applier/__init__.py
    boundary:
    - desc: Data directory exists for cache/stats files
      target: data/
  evidence:
  - src/applier/__init__.py
  - src/platforms/__init__.py
  - src/ai/__init__.py
  - data/
  notes: Directories and base __init__.py files exist
- id: I1
  description: Implement Phase 0 - SelectorEngine with 4-tier resolution, honeypot filter, and cache
  state: done
  touches:
  - src/applier/selector_engine.py
  - src/ai/gemini_vision.py
  checks:
    happy:
      desc: SelectorEngine.find() resolves elements via 4 tiers
      target: selector_engine.py
    negative:
    - desc: Returns None when no element found
      target: selector_engine.py
    - desc: Honeypot elements are filtered out
      target: selector_engine.py
    boundary:
    - desc: Cache TTL expires after 24h
      target: selector_engine.py
    - desc: LLM budget gate prevents runaway costs
      target: selector_engine.py
  evidence:
  - src/applier/selector_engine.py
  - src/ai/gemini_vision.py
  notes: 659 lines, full 4-tier resolution, honeypot JS filter, async cache with lock
- id: I2
  description: Implement Phase 1 - IndeedScraper with direct search, JSON-LD, and HTML fallback
  state: done
  touches:
  - src/platforms/indeed_scraper.py
  - src/platforms/base_scraper.py
  checks:
    happy:
      desc: search_jobs() returns Job objects
      target: indeed_scraper.py
    negative:
    - desc: Handles network errors gracefully
      target: indeed_scraper.py
    - desc: Falls back to HTML parsing when JSON-LD missing
      target: indeed_scraper.py
    boundary:
    - desc: Rate limiting prevents 429 errors
      target: indeed_scraper.py
  evidence:
  - src/platforms/indeed_scraper.py
  notes: 605 lines, direct Indeed search primary, JSON-LD extraction, HTML fallback, optional SERP
- id: I3
  description: Implement Phase 3 - SelectorHealthMonitor with degradation detection and persistence
  state: done
  touches:
  - src/applier/selector_health.py
  checks:
    happy:
      desc: record() tracks tier hits and misses
      target: selector_health.py
    negative:
    - desc: Debounced writes prevent disk thrashing
      target: selector_health.py
    boundary:
    - desc: Stats persist across restarts
      target: selector_health.py
    - desc: check_degradation() alerts when hit rate < 30%
      target: selector_health.py
  evidence:
  - src/applier/selector_health.py
  notes: 234 lines, stats tracking, debounced persistence, LLM budget gate
- id: I4
  description: Implement Phase 2 - IndeedApplier with SelectorEngine integration, dynamic sitekey, semantic fingerprint
  state: done
  touches:
  - src/applier/indeed_apply.py
  checks:
    happy:
      desc: Uses SelectorEngine for all element finding
      target: indeed_apply.py
    negative:
    - desc: Early exit on already_applied/job_expired
      target: indeed_apply.py
    - desc: Falls back through all 4 tiers
      target: indeed_apply.py
    boundary:
    - desc: _extract_sitekey() works for multiple extraction paths
      target: indeed_apply.py
  evidence:
  - src/applier/indeed_apply.py
  notes: 456 lines, SelectorEngine integration, dynamic sitekey extraction, semantic fingerprint
- id: I5
  description: Implement Phase 4 - IndeedRouter with degradation awareness and graceful fallback
  state: done
  touches:
  - src/applier/indeed_router.py
  checks:
    happy:
      desc: Injects SelectorEngine and HealthMonitor
      target: indeed_router.py
    negative:
    - desc: Returns open_url for manual fill on degradation
      target: indeed_router.py
    boundary:
    - desc: check_degradation() called once at start
      target: indeed_router.py
  evidence:
  - src/applier/indeed_router.py
  notes: 130 lines, degradation awareness, early exit on error modals, manual URL fallback
- id: I6
  description: Create unit tests for SelectorEngine, HealthMonitor, and IndeedScraper
  state: done
  touches:
  - tests/test_selector_engine.py
  - tests/test_selector_health.py
  - tests/test_indeed_scraper.py
  checks:
    happy:
      desc: All tests pass
      target: tests/
    negative:
    - desc: Honeypot test covers all 6 conditions
      target: tests/test_selector_engine.py
    boundary:
    - desc: SERP test skipped in CI without ENABLE_SERP
      target: tests/test_indeed_scraper.py
  evidence:
  - tests/test_selector_engine.py
  - tests/test_selector_health.py
  - tests/test_indeed_scraper.py
  notes: SelectorEngine 26/26 pass, IndeedScraper has 4 failures to fix
- id: I7
  description: Fix 4 IndeedScraper test failures in HTML fallback parsing
  state: doing
  touches:
  - src/platforms/indeed_scraper.py
  - tests/test_indeed_scraper.py
  checks:
    happy:
      desc: All IndeedScraper tests pass
      target: tests/test_indeed_scraper.py
    negative:
    - desc: Handles both og:description and name="description" meta tags
      target: indeed_scraper.py:_parse_html_fallback
    - desc: Handles JS-wrapped mosaic data structure
      target: indeed_scraper.py:_parse_html_fallback
    boundary:
    - desc: MAX_RPM accessible as module constant
      target: tests/test_indeed_scraper.py::test_max_rpm_constant_defined
  evidence:
  - tests/test_indeed_scraper.py
  notes: Fixing meta tag parsing, mosaic data extraction, company selector, and MAX_RPM test
```
