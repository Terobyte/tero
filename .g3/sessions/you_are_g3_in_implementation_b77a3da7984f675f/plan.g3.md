# Plan: ai-answerer-implementation

**Status**: Plan 'ai-answerer-implementation' rev 3 (approved at rev 1): 7/7 done, 0 doing, 0 blocked, 0 todo

## Plan Data

```yaml
plan_id: ai-answerer-implementation
revision: 3
approved_revision: 1
items:
- id: I1
  description: Create project structure and base module with constants
  state: done
  touches:
  - src/applier/__init__.py
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: Module imports successfully with all constants defined
      target: src/applier/ai_answerer.py
    negative:
    - desc: Invalid preamble markers are rejected
      target: src/applier/ai_answerer.py
    boundary:
    - desc: Empty marker tuple handled gracefully
      target: src/applier/ai_answerer.py
  evidence:
  - src/applier/ai_answerer.py:1-50
  notes: Module created with _PREAMBLE_MARKERS (16 phrases), _ABBREVIATIONS (14 abbreviations), _PREAMBLE_FIELD_TYPES={'textarea'} only
- id: I2
  description: Implement SYSTEM_PROMPT with rule
  state: done
  touches:
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: SYSTEM_PROMPT contains 5 few-shot examples and forbidden phrases list
      target: src/applier/ai_answerer.py
    negative:
    - desc: Handles profile with curly braces via .replace()
      target: src/applier/ai_answerer.py
    boundary:
    - desc: JSON format uses single braces not double
      target: src/applier/ai_answerer.py
  evidence:
  - src/applier/ai_answerer.py:105-180
  notes: 'SYSTEM_PROMPT has rule #6 with forbidden phrases, 5 few-shot examples with ❌/✅ format, uses .replace() for profile substitution'
- id: I3
  description: Implement VARIANTS_PROMPT with anti-preamble requirements
  state: done
  touches:
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: VARIANTS_PROMPT contains forbidden phrases and mini-examples
      target: src/applier/ai_answerer.py
    negative:
    - desc: Invalid variant format handled
      target: src/applier/ai_answerer.py
    boundary:
    - desc: Compact format preserves variant diversity
      target: src/applier/ai_answerer.py
  evidence:
  - src/applier/ai_answerer.py:83-103
  notes: VARIANTS_PROMPT has forbidden phrases list and mini-example with ❌/✅ format
- id: I4
  description: Implement _find_sentence_end and _strip_preamble functions
  state: done
  touches:
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: Functions handle all preamble markers and abbreviations
      target: src/applier/ai_answerer.py
    negative:
    - desc: Never returns empty string, falls back to original
      target: src/applier/ai_answerer.py
    boundary:
    - desc: Handles chained preambles and edge cases
      target: src/applier/ai_answerer.py
  evidence:
  - src/applier/ai_answerer.py:35-80
  notes: 'Both functions implemented with 5 safety guarantees: empty string fallback, min 10 chars, abbreviation skipping, chained preambles, case-insensitive'
- id: I5
  description: Implement AIAnswerer class with ask() and ask_variants()
  state: done
  touches:
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: ask() applies _strip_preamble to textarea fields
      target: src/applier/ai_answerer.py
    negative:
    - desc: Handles JSON parse errors gracefully
      target: src/applier/ai_answerer.py
    boundary:
    - desc: ask_variants() strips preamble from all textarea variants
      target: src/applier/ai_answerer.py
  evidence:
  - src/applier/ai_answerer.py:185-350
  notes: AIAnswerer class with ask() and ask_variants() methods, postprocessing applies _strip_preamble to textarea only, telemetry logging for raw_answer/ai_answered/preamble_stripped/provider
- id: I6
  description: Create unit tests for _strip_preamble and _find_sentence_end
  state: done
  touches:
  - tests/applier/test_strip_preamble.py
  checks:
    happy:
      desc: All 40+ test cases pass
      target: tests/applier/test_strip_preamble.py
    negative:
    - desc: Edge cases with abbreviations handled correctly
      target: tests/applier/test_strip_preamble.py
    boundary:
    - desc: Empty and short strings handled safely
      target: tests/applier/test_strip_preamble.py
  evidence:
  - tests/applier/test_strip_preamble.py
  notes: '36 unit tests covering all edge cases: abbreviations, chained preambles, case-insensitivity, safety guards'
- id: I7
  description: Create integration tests for AIAnswerer with _strip_preamble
  state: done
  touches:
  - tests/applier/test_ai_answerer_kimi.py
  checks:
    happy:
      desc: Integration tests verify preamble stripping in ask() flow
      target: tests/applier/test_ai_answerer_kimi.py
    negative:
    - desc: Radio/select fields are not stripped
      target: tests/applier/test_ai_answerer_kimi.py
    boundary:
    - desc: Empty variants handled gracefully
      target: tests/applier/test_ai_answerer_kimi.py
  evidence:
  - tests/applier/test_ai_answerer_kimi.py
  notes: Added TestPreambleInAskFlow (3 tests), TestPreambleFieldTypes (2 tests), TestStripPreambleIntegration (4 tests) - 9 integration tests total
```
