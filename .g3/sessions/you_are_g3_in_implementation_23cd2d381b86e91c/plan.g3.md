# Plan: indeed-form-qa-system

**Status**: Plan 'indeed-form-qa-system' rev 2 (approved at rev 2): 7/7 done, 0 doing, 0 blocked, 0 todo

## Plan Data

```yaml
plan_id: indeed-form-qa-system
revision: 2
approved_revision: 2
items:
- id: I0
  description: Create data/candidate/profile.yaml with default profile structure
  state: done
  touches:
  - data/candidate/profile.yaml
  checks:
    happy:
      desc: Profile YAML file exists with all required sections
      target: data/candidate/profile.yaml
    negative:
    - desc: Missing required fields have sensible defaults
      target: data/candidate/profile.yaml
    boundary:
    - desc: Empty values allowed for optional fields
      target: data/candidate/profile.yaml
  evidence:
  - data/candidate/profile.yaml
  notes: Created profile.yaml with personal, experience, certifications, work_conditions, work_auth, salary, availability, ai_context sections
- id: I1
  description: Create src/bot/ directory and ProfileInterviewer for Telegram /setup command
  state: done
  touches:
  - src/bot/__init__.py
  - src/bot/profile_interviewer.py
  checks:
    happy:
      desc: ProfileInterviewer class with interview flow and inline keyboards
      target: src/bot/profile_interviewer.py
    negative:
    - desc: Handles user skipping questions gracefully
      target: src/bot/profile_interviewer.py
    boundary:
    - desc: Interview can be restarted to update only answered questions
      target: src/bot/profile_interviewer.py
  evidence:
  - src/bot/__init__.py
  - src/bot/profile_interviewer.py
  notes: Created ProfileInterviewer with inline keyboards, question blocks for certifications/work_conditions/tech/open-ended
- id: I2
  description: Create AnswerBank with extended KEYWORD_PATTERNS and profile.yaml loading
  state: done
  touches:
  - src/applier/answer_bank.py
  checks:
    happy:
      desc: AnswerBank matches certifications, work conditions, tech skills patterns
      target: src/applier/answer_bank.py
    negative:
    - desc: Unknown question returns None without crashing
      target: src/applier/answer_bank.py
    boundary:
    - desc: Dropdown values round UP (2 years → 3-5 years tier)
      target: src/applier/answer_bank.py
  evidence:
  - src/applier/answer_bank.py
  notes: Extended KEYWORD_PATTERNS with 50+ regex patterns for certifications, work conditions, tech skills; profile.yaml loading; dropdown rounding UP logic
- id: I3
  description: Create AIAnswerer with Gemini Flash primary and Claude fallback
  state: done
  touches:
  - src/applier/ai_answerer.py
  checks:
    happy:
      desc: AIAnswerer returns structured answer with confidence
      target: src/applier/ai_answerer.py
    negative:
    - desc: API failure returns graceful error
      target: src/applier/ai_answerer.py
    boundary:
    - desc: Cached questions skip API call
      target: src/applier/ai_answerer.py
  evidence:
  - src/applier/ai_answerer.py
  notes: Created AIAnswerer with Gemini Flash primary (GEMINI_API_KEY in .env), Claude Haiku fallback, question hashing for cache
- id: I4
  description: Create IndeedScreeningHandler for form question detection and answering
  state: done
  touches:
  - src/applier/indeed_screening.py
  checks:
    happy:
      desc: Detects fieldset/legend radio groups, select dropdowns, text inputs
      target: src/applier/indeed_screening.py
    negative:
    - desc: Handles missing elements gracefully
      target: src/applier/indeed_screening.py
    boundary:
    - desc: Empty form returns empty question list
      target: src/applier/indeed_screening.py
  evidence:
  - src/applier/indeed_screening.py
  notes: Created IndeedScreeningHandler with find_all_questions (fieldset/legend, select, inputs, textarea, checkbox), answer_question with 3-tier fallback
- id: I5
  description: Create data files - custom_answers.json and unknown_questions.json
  state: done
  touches:
  - data/custom_answers.json
  - data/unknown_questions.json
  checks:
    happy:
      desc: JSON files with valid structure
      target: data/custom_answers.json
    negative:
    - desc: Corrupted JSON handled with fresh file creation
      target: data/custom_answers.json
    boundary:
    - desc: Empty initial files have valid empty structures
      target: data/unknown_questions.json
  evidence:
  - data/custom_answers.json
  - data/unknown_questions.json
  notes: Created custom_answers.json with default answers, unknown_questions.json as empty array
- id: I6
  description: Create .env.example with all required variables
  state: done
  touches:
  - .env.example
  checks:
    happy:
      desc: .env.example contains all certification, work condition, and tech variables
      target: .env.example
    negative:
    - desc: Missing variables have clear placeholder values
      target: .env.example
    boundary:
    - desc: Sensitive keys marked as empty strings
      target: .env.example
  evidence:
  - .env.example
  notes: Created .env.example with API keys, applicant info, certifications (Yes/No), work conditions (Yes/No), tech experience (years)
```
