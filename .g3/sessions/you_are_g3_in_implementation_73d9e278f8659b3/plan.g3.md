# Plan: careerbot-full-implementation

**Status**: Plan 'careerbot-full-implementation' rev 2 (approved at rev 1): 2/7 done, 1 doing, 0 blocked, 4 todo

## Plan Data

```yaml
plan_id: careerbot-full-implementation
revision: 2
approved_revision: 1
items:
- id: I1
  description: Create project structure and configuration
  state: done
  touches:
  - config/settings.json
  - data/
  - src/
  checks:
    happy:
      desc: Directory structure created with config
      target: config/settings.json
    negative:
    - desc: Config handles missing fields gracefully
      target: config/settings.json
    boundary:
    - desc: Config supports multiple job titles
      target: config/settings.json
  evidence:
  - config/settings.json
  notes: Created config with 4 job titles, no glassdoor references
- id: I2
  description: Create database schema with screenshot_path column
  state: done
  touches:
  - src/database/
  checks:
    happy:
      desc: Database created with jobs table including screenshot_path
      target: src/database/db.py
    negative:
    - desc: Database handles duplicate job_id gracefully
      target: src/database/db.py
    boundary:
    - desc: Database handles NULL screenshot_path for old records
      target: src/database/db.py
  evidence:
  - src/database/db.py
  notes: Complete database module with screenshot_path column
- id: I3
  description: Create scrapers (JobSpy without Glassdoor, Ashby, Greenhouse, Lever, BambooHR)
  state: doing
  touches:
  - src/platforms/
  - src/scrapers/
  checks:
    happy:
      desc: Scrapers find jobs from multiple platforms (Indeed, Ashby, GH, Lever, BambooHR)
      target: src/platforms/
    negative:
    - desc: Scraper handles API errors gracefully
      target: src/platforms/
    boundary:
    - desc: Scraper handles empty results
      target: src/platforms/
- id: I4
  description: Create appliers with screenshot proof functionality
  state: todo
  touches:
  - src/applier/
  checks:
    happy:
      desc: Applier saves screenshot after successful application
      target: src/applier/
    negative:
    - desc: Applier handles failed submissions gracefully
      target: src/applier/
    boundary:
    - desc: Applier handles missing screenshot directory
      target: src/applier/
- id: I5
  description: Create Tkinter desktop GUI
  state: todo
  touches:
  - gui.py
  checks:
    happy:
      desc: GUI displays scan results and proof section
      target: gui.py
    negative:
    - desc: GUI handles scan errors gracefully
      target: gui.py
    boundary:
    - desc: GUI handles empty database for proof section
      target: gui.py
- id: I6
  description: Create cleanup script to remove Glassdoor data
  state: todo
  touches:
  - scripts/cleanup_glassdoor.py
  checks:
    happy:
      desc: Script removes all Glassdoor records
      target: scripts/cleanup_glassdoor.py
    negative:
    - desc: Script requires confirmation before deletion
      target: scripts/cleanup_glassdoor.py
    boundary:
    - desc: Script handles empty database
      target: scripts/cleanup_glassdoor.py
- id: I7
  description: Create main entry point and documentation
  state: todo
  touches:
  - main.py
  - README.md
  - requirements.txt
  checks:
    happy:
      desc: Application runs with main.py
      target: main.py
    negative:
    - desc: Application handles missing dependencies
      target: requirements.txt
    boundary:
    - desc: README documents all features
      target: README.md
```
