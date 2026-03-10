# Plan: careerbot-features

**Status**: Plan 'careerbot-features' rev 2 (approved at rev 1): 5/5 done, 0 doing, 0 blocked, 0 todo

## Plan Data

```yaml
plan_id: careerbot-features
revision: 2
approved_revision: 1
items:
- id: I1
  description: Implement _evaluate_tailoring() method in gui.py
  state: done
  touches:
  - gui.py
  checks:
    happy:
      desc: Opens Toplevel window, runs TailoringRunner, displays results with ||||| indicator
      target: gui.py
    negative:
    - desc: Shows error message when no job selected
      target: gui.py
    boundary:
    - desc: Handles window close during async operation
      target: gui.py
  evidence:
  - gui.py:777-907
  notes: Already implemented - method exists and works with TailoringRunner
- id: I2
  description: Add qa_btn button in _build_middle_section()
  state: done
  touches:
  - gui.py
  checks:
    happy:
      desc: Button appears in UI with correct styling (#fab387 orange)
      target: gui.py
    negative:
    - desc: Button handles missing _open_qa_review gracefully
      target: gui.py
    boundary:
    - desc: Button is properly packed in layout after Profile button
      target: gui.py
  evidence:
  - gui.py:164-176
  notes: Added "📋 Вопросы" button with orange (#fab387) background, calls _open_qa_review
- id: I3
  description: Implement _open_qa_review() method in gui.py
  state: done
  touches:
  - gui.py
  checks:
    happy:
      desc: Opens Q&A review window with filtering and confirm/override buttons
      target: gui.py
    negative:
    - desc: Handles missing unknown_questions.json file
      target: gui.py
    boundary:
    - desc: Handles empty questions list
      target: gui.py
  evidence:
  - gui.py:909-1011
  notes: Already implemented - method exists with filtering, confirm/override functionality
- id: I4
  description: Implement _open_profile_window() method with Resume tab
  state: done
  touches:
  - gui.py
  checks:
    happy:
      desc: Opens profile window with tabs including Resume tab
      target: gui.py
    negative:
    - desc: Handles missing resume.pdf file
      target: gui.py
    boundary:
    - desc: Resume upload only accepts PDF files
      target: gui.py
  evidence:
  - gui.py:1028-1173
  notes: Already implemented - has tabs for Profile, Resume, Skills with PDF upload
- id: I5
  description: Add profile_btn to open profile window
  state: done
  touches:
  - gui.py
  checks:
    happy:
      desc: Profile button opens profile window
      target: gui.py
    negative:
    - desc: Button handles multiple clicks gracefully
      target: gui.py
    boundary:
    - desc: Only one profile window can be open at a time
      target: gui.py
  evidence:
  - gui.py:164-176
  notes: Added "👤 Профиль" button with purple (#cba6f7) background, calls _open_profile_window
```
