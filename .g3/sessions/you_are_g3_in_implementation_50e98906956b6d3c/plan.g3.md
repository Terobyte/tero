# Plan: blind-custom-site-applier

**Status**: Plan 'blind-custom-site-applier' rev 9 (approved at rev 1): 14/15 done, 1 doing, 0 blocked, 0 todo

## Plan Data

```yaml
plan_id: blind-custom-site-applier
revision: 9
approved_revision: 1
items:
- id: I1
  description: Create package structure and models
  state: done
  touches:
  - src/applier/blind/__init__.py
  - src/applier/blind/models.py
  checks:
    happy:
      desc: Package imports work
      target: src/applier/blind/__init__.py
    negative:
    - desc: Invalid model raises error
      target: src/applier/blind/models.py
    boundary:
    - desc: Empty optional fields handled
      target: src/applier/blind/models.py
  evidence:
  - src/applier/blind/__init__.py
  - src/applier/blind/models.py
  notes: Created package structure with dataclasses for SiteSchema, ApplyResult, WizardResult, etc.
- id: I2
  description: Implement KimiVisionClient for image+text AI
  state: done
  touches:
  - src/ai/kimi_vision.py
  checks:
    happy:
      desc: Vision client generates response from image
      target: src/ai/kimi_vision.py
    negative:
    - desc: Invalid image raises proper error
      target: src/ai/kimi_vision.py
    boundary:
    - desc: Large images handled gracefully
      target: src/ai/kimi_vision.py
  evidence:
  - src/ai/kimi_vision.py
  notes: Implemented KimiVisionClient with async image+text prompt support, bbox detection, and fallback chain
- id: I3
  description: Implement ReconAgent for site reconnaissance
  state: done
  touches:
  - src/applier/blind/recon_agent.py
  checks:
    happy:
      desc: ReconAgent analyzes page and returns SiteSchema
      target: src/applier/blind/recon_agent.py
    negative:
    - desc: Invalid page handled gracefully
      target: src/applier/blind/recon_agent.py
    boundary:
    - desc: Pages with no forms handled
      target: src/applier/blind/recon_agent.py
  evidence:
  - src/applier/blind/recon_agent.py
  notes: Implemented ReconAgent with DOM extraction, vision analysis via Kimi 2.5, and SiteSchema generation
- id: I4
  description: Implement PageNavigator for navigation state machine
  state: done
  touches:
  - src/applier/blind/page_navigator.py
  checks:
    happy:
      desc: Navigator finds and clicks apply button
      target: src/applier/blind/page_navigator.py
    negative:
    - desc: No apply button returns failure
      target: src/applier/blind/page_navigator.py
    boundary:
    - desc: Multiple apply buttons handled
      target: src/applier/blind/page_navigator.py
  evidence:
  - src/applier/blind/page_navigator.py
  notes: Implemented PageNavigator with 4-tier Apply button discovery, vision click, and state machine
- id: I5
  description: Implement VisionFormFiller and BlindFieldFinder
  state: done
  touches:
  - src/applier/blind/vision_form_filler.py
  - src/applier/blind/field_finder.py
  checks:
    happy:
      desc: Form filler fills visible fields
      target: src/applier/blind/vision_form_filler.py
    negative:
    - desc: Missing profile data handled
      target: src/applier/blind/vision_form_filler.py
    boundary:
    - desc: Empty form handled
      target: src/applier/blind/vision_form_filler.py
  evidence:
  - src/applier/blind/vision_form_filler.py
  - src/applier/blind/field_finder.py
  notes: Implemented 3-pass form filling strategy with DOM extraction, AI mapping, and vision verification
- id: I6
  description: Implement WizardEngine for multi-step forms
  state: done
  touches:
  - src/applier/blind/wizard_engine.py
  checks:
    happy:
      desc: Wizard completes all steps
      target: src/applier/blind/wizard_engine.py
    negative:
    - desc: Step failure handled gracefully
      target: src/applier/blind/wizard_engine.py
    boundary:
    - desc: Max steps limit enforced
      target: src/applier/blind/wizard_engine.py
  evidence:
  - src/applier/blind/wizard_engine.py
  notes: Implemented WizardEngine with step detection, loop protection, and Next button discovery
- id: I7
  description: Implement OutcomeDetector for success/failure detection
  state: done
  touches:
  - src/applier/blind/outcome_detector.py
  checks:
    happy:
      desc: Success page detected correctly
      target: src/applier/blind/outcome_detector.py
    negative:
    - desc: Error page detected correctly
      target: src/applier/blind/outcome_detector.py
    boundary:
    - desc: Loading state handled
      target: src/applier/blind/outcome_detector.py
  evidence:
  - src/applier/blind/outcome_detector.py
  notes: Multi-modal detection with URL patterns, text signals, and vision analysis
- id: I8
  description: Implement SitePatternStore and ReplayRecorder for learning
  state: done
  touches:
  - src/applier/blind/site_pattern_store.py
  - src/applier/blind/replay_recorder.py
  checks:
    happy:
      desc: Patterns saved and loaded
      target: src/applier/blind/site_pattern_store.py
    negative:
    - desc: Corrupt data handled
      target: src/applier/blind/site_pattern_store.py
    boundary:
    - desc: Large workflows handled
      target: src/applier/blind/replay_recorder.py
  evidence:
  - src/applier/blind/site_pattern_store.py
  - src/applier/blind/replay_recorder.py
  notes: Site pattern storage with workflow recording and replay capabilities
- id: I9
  description: Implement BlindApplier orchestrator
  state: done
  touches:
  - src/applier/blind/blind_applier.py
  checks:
    happy:
      desc: Full apply flow completes
      target: src/applier/blind/blind_applier.py
    negative:
    - desc: Blocked sites handled
      target: src/applier/blind/blind_applier.py
    boundary:
    - desc: Cached workflows replayed
      target: src/applier/blind/blind_applier.py
  evidence:
  - src/applier/blind/blind_applier.py
  notes: Main orchestrator coordinating all blind applier components
- id: I10
  description: Create minimal StealthBrowser standalone implementation
  state: done
  touches:
  - src/stealth/browser_stealth.py
  - src/stealth/__init__.py
  checks:
    happy:
      desc: Browser context created and page loaded
      target: src/stealth/browser_stealth.py
    negative:
    - desc: Browser launch failure handled
      target: src/stealth/browser_stealth.py
    boundary:
    - desc: Multiple contexts handled
      target: src/stealth/browser_stealth.py
  evidence:
  - src/stealth/browser_stealth.py
  notes: Created minimal standalone browser wrapper using playwright
- id: I11
  description: Create minimal ATSDetector and AnswerBank implementations
  state: done
  touches:
  - src/applier/ats_detector.py
  - src/applier/answer_bank.py
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: ATS detected from URL patterns
      target: src/applier/ats_detector.py
    negative:
    - desc: Unknown ATS returns None
      target: src/applier/ats_detector.py
    boundary:
    - desc: Partial ATS match handled
      target: src/applier/ats_detector.py
  evidence:
  - src/applier/ats_detector.py
  - src/applier/answer_bank.py
  - src/applier/ai_answerer.py
  notes: Minimal implementations for integration
- id: I12
  description: Add unit tests for core modules
  state: done
  touches:
  - tests/test_kimi_vision.py
  - tests/test_recon_agent.py
  - tests/test_blind_applier.py
  checks:
    happy:
      desc: Tests pass for KimiVisionClient and ReconAgent
      target: tests/test_recon_agent.py
    negative:
    - desc: Error cases tested
      target: tests/test_kimi_vision.py
    boundary:
    - desc: Edge cases tested
      target: tests/test_blind_applier.py
  evidence:
  - tests/test_kimi_vision.py
  notes: Sprint 1 requirement - unit tests for core modules
- id: I13
  description: Create QueueManager with _apply_blind routing (Phase 6)
  state: done
  touches:
  - src/applier/queue_manager.py
  - src/applier/__init__.py
  checks:
    happy:
      desc: Unknown ATS routes to BlindApplier
      target: src/applier/queue_manager.py
    negative:
    - desc: Blind apply failure handled gracefully
      target: src/applier/queue_manager.py
    boundary:
    - desc: Known ATS skips BlindApplier
      target: src/applier/queue_manager.py
  evidence:
  - src/applier/queue_manager.py
  notes: CRITICAL - main integration point for using BlindApplier
- id: I14
  description: Create missing dependencies (GmailReader, LearningEngine)
  state: done
  touches:
  - src/applier/gmail_reader.py
  - src/learning/learning_engine.py
  - src/learning/__init__.py
  checks:
    happy:
      desc: Imports resolve without error
      target: src/applier/gmail_reader.py
    negative:
    - desc: Missing credentials handled
      target: src/applier/gmail_reader.py
    boundary:
    - desc: API rate limits handled
      target: src/learning/learning_engine.py
  evidence:
  - src/applier/gmail_reader.py
  - src/learning/learning_engine.py
  notes: Stub implementations to fix import errors
- id: I15
  description: Fix coach-identified issues and add integration tests
  state: doing
  touches:
  - tests/test_kimi_vision.py
  - src/applier/blind/models.py
  - tests/test_integration_blind.py
  checks:
    happy:
      desc: All tests pass
      target: tests/test_kimi_vision.py
    negative:
    - desc: Error cases tested
      target: tests/test_integration_blind.py
    boundary:
    - desc: Mock browser tests work
      target: tests/test_integration_blind.py
  notes: Fix test_analyze_with_dom_context, OutcomeResult type confusion, and add integration tests
```
