# Plan: blind-custom-site-applier

**Status**: Plan 'blind-custom-site-applier' rev 7 (approved at rev 1): 13/24 done, 2 doing, 0 blocked, 9 todo

## Plan Data

```yaml
plan_id: blind-custom-site-applier
revision: 7
approved_revision: 1
items:
- id: I1
  description: Create src/applier/blind/ package structure
  state: done
  touches:
  - src/applier/blind/
  checks:
    happy:
      desc: Package imports correctly
      target: src/applier/blind/__init__.py
    negative:
    - desc: Graceful error on missing dependencies
      target: src/applier/blind/__init__.py
    boundary:
    - desc: All submodules exported
      target: src/applier/blind/__init__.py
  evidence:
  - src/applier/blind/__init__.py
  notes: 'Package created with all exports: BlindApplier, ReconAgent, PageNavigator, VisionFormFiller, WizardEngine, OutcomeDetector, SitePatternStore, ReplayRecorder'
- id: I2
  description: KimiVisionClient with image+text support
  state: done
  touches:
  - src/ai/kimi_vision.py
  checks:
    happy:
      desc: Vision client generates with images
      target: src/ai/kimi_vision.py
    negative:
    - desc: Handles API errors gracefully
      target: src/ai/kimi_vision.py
    boundary:
    - desc: Supports multiple image formats
      target: src/ai/kimi_vision.py
  evidence:
  - src/ai/kimi_vision.py
  notes: KimiVisionClient supports generate_with_image, find_element_bbox, analyze_page_type, verify_form_state, detect_outcome
- id: I3
  description: ReconAgent for site reconnaissance (Phase 0)
  state: done
  touches:
  - src/applier/blind/recon_agent.py
  checks:
    happy:
      desc: Analyzes page and returns SiteSchema
      target: src/applier/blind/recon_agent.py
    negative:
    - desc: Handles page load failures
      target: src/applier/blind/recon_agent.py
    boundary:
    - desc: Works without vision client fallback
      target: src/applier/blind/recon_agent.py
  evidence:
  - src/applier/blind/recon_agent.py
  notes: ReconAgent extracts DOM, takes screenshots, uses vision analysis, builds SiteSchema
- id: I4
  description: PageNavigator with state machine (Phase 1)
  state: done
  touches:
  - src/applier/blind/page_navigator.py
  checks:
    happy:
      desc: Navigates from listing to form
      target: src/applier/blind/page_navigator.py
    negative:
    - desc: Handles blocked sites gracefully
      target: src/applier/blind/page_navigator.py
    boundary:
    - desc: Max navigation steps limit
      target: src/applier/blind/page_navigator.py
  evidence:
  - src/applier/blind/page_navigator.py
  notes: PageNavigator with ApplyButtonFinder (4-tier discovery), NavigationState state machine, vision-click support
- id: I5
  description: VisionFormFiller with 3-pass strategy (Phase 2)
  state: done
  touches:
  - src/applier/blind/vision_form_filler.py
  - src/applier/blind/field_finder.py
  checks:
    happy:
      desc: Fills form fields from profile
      target: src/applier/blind/vision_form_filler.py
    negative:
    - desc: Handles missing fields gracefully
      target: src/applier/blind/vision_form_filler.py
    boundary:
    - desc: Custom dropdowns/file uploads handled
      target: src/applier/blind/field_finder.py
  evidence:
  - src/applier/blind/vision_form_filler.py
  - src/applier/blind/field_finder.py
  notes: VisionFormFiller with DOM extraction, AI mapping, vision verification; BlindFieldFinder for custom components
- id: I6
  description: WizardEngine for multi-step forms (Phase 3)
  state: done
  touches:
  - src/applier/blind/wizard_engine.py
  checks:
    happy:
      desc: Handles multi-step wizard forms
      target: src/applier/blind/wizard_engine.py
    negative:
    - desc: Loop protection prevents infinite loops
      target: src/applier/blind/wizard_engine.py
    boundary:
    - desc: Max steps limit enforced
      target: src/applier/blind/wizard_engine.py
  evidence:
  - src/applier/blind/wizard_engine.py
  notes: WizardEngine with StepTracker, NextButtonFinder, ProgressIndicatorDetector, loop protection
- id: I7
  description: OutcomeDetector for success/failure detection (Phase 4)
  state: done
  touches:
  - src/applier/blind/outcome_detector.py
  checks:
    happy:
      desc: Detects successful submission
      target: src/applier/blind/outcome_detector.py
    negative:
    - desc: Detects errors and captchas
      target: src/applier/blind/outcome_detector.py
    boundary:
    - desc: Low confidence results marked for review
      target: src/applier/blind/outcome_detector.py
  evidence:
  - src/applier/blind/outcome_detector.py
  notes: OutcomeDetector with URL patterns, text signals, vision analysis, DOM change detection
- id: I8
  description: SitePatternStore for learning (Phase 5)
  state: done
  touches:
  - src/applier/blind/site_pattern_store.py
  - src/applier/blind/replay_recorder.py
  checks:
    happy:
      desc: Stores and retrieves workflows
      target: src/applier/blind/site_pattern_store.py
    negative:
    - desc: Handles disk errors gracefully
      target: src/applier/blind/site_pattern_store.py
    boundary:
    - desc: Concurrent access safe
      target: src/applier/blind/site_pattern_store.py
  evidence:
  - src/applier/blind/site_pattern_store.py
  - src/applier/blind/replay_recorder.py
  notes: SitePatternStore with workflows, apply button patterns, wizard structures, domain stats; ReplayRecorder for action recording
- id: I9
  description: BlindApplier main orchestrator (Phase 6)
  state: done
  touches:
  - src/applier/blind/blind_applier.py
  checks:
    happy:
      desc: Full apply flow works
      target: src/applier/blind/blind_applier.py
    negative:
    - desc: Delegates to known ATS handlers
      target: src/applier/blind/blind_applier.py
    boundary:
    - desc: Vision budget enforced
      target: src/applier/blind/blind_applier.py
  evidence:
  - src/applier/blind/blind_applier.py
  notes: BlindApplier orchestrates all components, supports workflow replay, ATS delegation, notifications
- id: I10
  description: QueueManager integration with BlindApplier route
  state: done
  touches:
  - src/applier/queue_manager.py
  checks:
    happy:
      desc: Routes custom sites to BlindApplier
      target: src/applier/queue_manager.py
    negative:
    - desc: Handles import errors gracefully
      target: src/applier/queue_manager.py
    boundary:
    - desc: Concurrent job processing
      target: src/applier/queue_manager.py
  evidence:
  - src/applier/queue_manager.py
  notes: QueueManager._apply_blind() method routes to BlindApplier, handles result types
- id: I11
  description: ATSDetector extension for custom sites
  state: done
  touches:
  - src/applier/ats_detector.py
  checks:
    happy:
      desc: Detects custom career sites
      target: src/applier/ats_detector.py
    negative:
    - desc: Returns unknown for unrecognizable sites
      target: src/applier/ats_detector.py
    boundary:
    - desc: All major ATS patterns covered
      target: src/applier/ats_detector.py
  evidence:
  - src/applier/ats_detector.py
  notes: ATSDetector.detect() returns ATSDetectionResult, is_likely_custom_careers_page() for custom detection
- id: I12
  description: Data directories and file structure
  state: done
  touches:
  - data/
  checks:
    happy:
      desc: Data directories exist
      target: data/
    negative:
    - desc: Creates missing directories
      target: data/
    boundary:
    - desc: Supports custom data paths
      target: data/
  evidence:
  - data/blind_site_workflows/
  - data/screenshots/
  notes: Created data/blind_site_patterns.json, data/blind_site_workflows/, data/blind_site_stats.json paths
- id: I13
  description: Implement AdaptiveLearner class (Section 7.5)
  state: done
  touches:
  - src/applier/blind/adaptive_learner.py
  checks:
    happy:
      desc: Learns cross-domain patterns
      target: src/applier/blind/adaptive_learner.py
    negative:
    - desc: Handles missing data gracefully
      target: src/applier/blind/adaptive_learner.py
    boundary:
    - desc: Returns default strategy when no prior knowledge
      target: src/applier/blind/adaptive_learner.py
  evidence:
  - src/applier/blind/adaptive_learner.py
  - src/applier/blind/__init__.py
  notes: AdaptiveLearner implemented with CrossDomainPattern, PriorKnowledge, LearningSample; exported in __init__.py
- id: I14
  description: Fix budget limiter to prevent AI calls when exceeded
  state: doing
  touches:
  - src/applier/blind/blind_applier.py
  checks:
    happy:
      desc: Budget check prevents AI calls
      target: src/applier/blind/blind_applier.py
    negative:
    - desc: Budget exceeded returns appropriate error
      target: src/applier/blind/blind_applier.py
    boundary:
    - desc: Budget resets per application
      target: src/applier/blind/blind_applier.py
  notes: ''
- id: I15
  description: Complete GmailReader integration for email confirmation
  state: todo
  touches:
  - src/applier/blind/outcome_detector.py
  - src/applier/gmail_reader.py
  checks:
    happy:
      desc: Email confirmation works with GmailReader
      target: src/applier/blind/outcome_detector.py
    negative:
    - desc: Timeout handled gracefully
      target: src/applier/blind/outcome_detector.py
    boundary:
    - desc: Multiple emails checked in sequence
      target: src/applier/blind/outcome_detector.py
  notes: ''
- id: I16
  description: Add Telegram notifications for blind apply results
  state: todo
  touches:
  - src/applier/blind/blind_applier.py
  - src/applier/telegram_notifier.py
  checks:
    happy:
      desc: Telegram notification sent on result
      target: src/applier/blind/blind_applier.py
    negative:
    - desc: Telegram errors dont block apply flow
      target: src/applier/blind/blind_applier.py
    boundary:
    - desc: Rate limited notifications
      target: src/applier/blind/blind_applier.py
  notes: ''
- id: I17
  description: Create AI client implementations (KimiClient, GeminiClient, ClaudeClient, GLM5Client)
  state: todo
  touches:
  - src/ai/llm_client.py
  checks:
    happy:
      desc: All AI clients work for text generation
      target: src/ai/llm_client.py
    negative:
    - desc: API errors handled with fallbacks
      target: src/ai/llm_client.py
    boundary:
    - desc: Rate limiting per client
      target: src/ai/llm_client.py
  notes: Missing implementations for referenced AI clients
- id: I18
  description: Create GmailReader for email confirmation checking
  state: todo
  touches:
  - src/applier/gmail_reader.py
  checks:
    happy:
      desc: Reads emails for confirmation
      target: src/applier/gmail_reader.py
    negative:
    - desc: Handles auth errors gracefully
      target: src/applier/gmail_reader.py
    boundary:
    - desc: Rate limited API calls
      target: src/applier/gmail_reader.py
  notes: Missing file referenced by outcome_detector.py
- id: I19
  description: Fix import paths to use absolute imports consistently
  state: todo
  touches:
  - src/applier/blind/blind_applier.py
  - src/applier/blind/recon_agent.py
  - src/applier/blind/page_navigator.py
  checks:
    happy:
      desc: All imports work from any context
      target: src/applier/blind/
    negative:
    - desc: Import errors caught gracefully
      target: src/applier/blind/
    boundary:
    - desc: Works when run as module or script
      target: src/applier/blind/
  notes: Relative imports like 'from ...stealth.browser_stealth' may fail
- id: I20
  description: Wire AdaptiveLearner into BlindApplier main flow
  state: todo
  touches:
  - src/applier/blind/blind_applier.py
  checks:
    happy:
      desc: AdaptiveLearner provides prior knowledge
      target: src/applier/blind/blind_applier.py
    negative:
    - desc: Falls back when learner unavailable
      target: src/applier/blind/blind_applier.py
    boundary:
    - desc: Learning samples recorded
      target: src/applier/blind/blind_applier.py
  notes: AdaptiveLearner not wired into BlindApplier's main flow
- id: I21
  description: Use config for data directory paths instead of hardcoded
  state: todo
  touches:
  - src/applier/blind/site_pattern_store.py
  - src/applier/blind/replay_recorder.py
  - src/applier/blind/blind_applier.py
  checks:
    happy:
      desc: Paths come from config
      target: src/applier/blind/
    negative:
    - desc: Default paths work without config
      target: src/applier/blind/
    boundary:
    - desc: Custom paths override defaults
      target: src/applier/blind/
  notes: Hardcoded 'data/' strings should use config
- id: I22
  description: Fix BlindApplier.__init__ to accept headed parameter in config
  state: doing
  touches:
  - src/applier/blind/blind_applier.py
  - src/applier/queue_manager.py
  checks:
    happy:
      desc: QueueManager can pass headed to BlindApplier
      target: src/applier/queue_manager.py
    negative:
    - desc: Invalid config values handled
      target: src/applier/blind/blind_applier.py
    boundary:
    - desc: Default config works
      target: src/applier/blind/blind_applier.py
  notes: QueueManager._apply_blind passes headed= but BlindApplier.__init__ expects it in config
- id: I23
  description: Fix _delegate_to_known_ats to return ApplicationResult enum
  state: todo
  touches:
  - src/applier/blind/blind_applier.py
  - src/applier/queue_manager.py
  checks:
    happy:
      desc: Delegation returns correct enum type
      target: src/applier/blind/blind_applier.py
    negative:
    - desc: Unknown ATS types handled
      target: src/applier/blind/blind_applier.py
    boundary:
    - desc: All ATS types covered
      target: src/applier/blind/blind_applier.py
  notes: Returns ApplyResult but QueueManager expects ApplicationResult
- id: I24
  description: Integrate AnswerBank and AIAnswerer into VisionFormFiller
  state: todo
  touches:
  - src/applier/blind/vision_form_filler.py
  - src/applier/answer_bank.py
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: Questions answered using AnswerBank
      target: src/applier/blind/vision_form_filler.py
    negative:
    - desc: Missing answers handled gracefully
      target: src/applier/blind/vision_form_filler.py
    boundary:
    - desc: AIAnswerer fallback works
      target: src/applier/blind/vision_form_filler.py
  notes: VisionFormFiller mentions integration but doesn't import/use them
```
