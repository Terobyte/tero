# Level 2 Completion Plan — Stable MVP

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Level 2 of ROADMAP_10_LEVELS.md — reach stable MVP that can run overnight safely.

**Architecture:** Fill gaps identified in roadmap review: slug collection (21K more), structured logging, API key validation.

**Tech Stack:** Python 3.14, loguru, httpx, asyncio, json

**Time Estimate:** 10-20 minutes of focused work

---

## Current State

| Component | Status | Gap |
|-----------|--------|-----|
| BatchValidator | ✅ Exists | Already works |
| AI Cost Tracking | ✅ Exists | Already works |
| Slug database | 9,094 | Need +21K → 30K |
| Structured logging | ❌ Missing | Need JSON logs |
| API keys | ❌ Not in env | User action needed |

---

## File Structure

```
scripts/
├── collect_cdx_slugs.py      # Modify: add more sources
├── collect_github_slugs.py   # Existing
└── run_slug_collection.py   # Create: orchestrator

src/utils/
├── structured_logger.py      # Create: JSON logging wrapper
└── config_validator.py       # Create: startup validation

data/companies/
├── greenhouse_slugs.json     # Existing: 4,662
├── lever_slugs.json          # Existing: 1,116
├── ashby_slugs.json          # Existing: 798
├── bamboohr_slugs.json       # Existing: 2,518
├── indeed_slugs.json         # Create: from API
└── linkedin_slugs.json       # Create: from Voyager

config/
└── settings.json             # Modify: add logging config
```

---

## Chunk 1: Slug Collection (Target: 30K total)

### Task 1: Check existing slug collection scripts

**Files:**
- Read: `scripts/collect_cdx_slugs.py`
- Read: `scripts/collect_github_slugs.py`

- [ ] **Step 1: Review collect_cdx_slugs.py**

```bash
cat scripts/collect_cdx_slugs.py
```

- [ ] **Step 2: Check what sources are available**

Run the CDX collector to see current output:

```bash
python3 scripts/collect_cdx_slugs.py --dry-run 2>&1 | head -50
```

Expected: List of sources that will be queried

---

### Task 2: Create unified slug collection runner

**Files:**
- Create: `scripts/run_slug_collection.py`

- [ ] **Step 1: Write the slug collection orchestrator**

```python
# scripts/run_slug_collection.py
"""
Unified slug collection runner — collects company slugs from all sources.

Sources:
1. Greenhouse job boards (CDX archive)
2. Lever job boards (CDX archive)
3. Ashby job boards (CDX archive)
4. BambooHR job boards (CDX archive)
5. Workday tenants (CDX archive)
6. GitHub awesome-job-boards lists

Usage:
    python scripts/run_slug_collection.py --all
    python scripts/run_slug_collection.py --source greenhouse --limit 5000
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from loguru import logger

OUTPUT_DIR = Path("data/companies")


async def collect_greenhouse_cdx(limit: int = 10000) -> list[str]:
    """Collect Greenhouse board slugs from CDX archive."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.greenhouse.io/*.boards.greenhouse.io/*",
        "output": "json",
        "fl": "original",
        "collapse": "urlkey",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            for row in data:
                url = row[0] if isinstance(row, list) else row.get("original", "")
                # Extract slug from URL like https://boards.greenhouse.io/companyname
                if "boards.greenhouse.io/" in url:
                    parts = url.split("boards.greenhouse.io/")
                    if len(parts) > 1:
                        slug = parts[1].split("/")[0].split("?")[0]
                        if slug and len(slug) > 2:
                            slugs.add(slug)
            logger.info(f"Greenhouse CDX: {len(slugs)} slugs")
        except Exception as e:
            logger.error(f"Greenhouse CDX failed: {e}")

    return list(slugs)


async def collect_lever_cdx(limit: int = 8000) -> list[str]:
    """Collect Lever board slugs from CDX archive."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.lever.co/*",
        "output": "json",
        "fl": "original",
        "collapse": "urlkey",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            for row in data:
                url = row[0] if isinstance(row, list) else row.get("original", "")
                # Extract slug from URL like https://lever.co/companyname
                if "lever.co/" in url:
                    parts = url.split("lever.co/")
                    if len(parts) > 1:
                        slug = parts[1].split("/")[0].split("?")[0]
                        if slug and len(slug) > 2 and slug not in ("jobs", "careers", "api"):
                            slugs.add(slug)
            logger.info(f"Lever CDX: {len(slugs)} slugs")
        except Exception as e:
            logger.error(f"Lever CDX failed: {e}")

    return list(slugs)


async def collect_ashby_cdx(limit: int = 5000) -> list[str]:
    """Collect Ashby board slugs from CDX archive."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.ashbyhq.com/*",
        "output": "json",
        "fl": "original",
        "collapse": "urlkey",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            for row in data:
                url = row[0] if isinstance(row, list) else row.get("original", "")
                if "ashbyhq.com/" in url:
                    parts = url.split("ashbyhq.com/")
                    if len(parts) > 1:
                        slug = parts[1].split("/")[0].split("?")[0]
                        if slug and len(slug) > 2:
                            slugs.add(slug)
            logger.info(f"Ashby CDX: {len(slugs)} slugs")
        except Exception as e:
            logger.error(f"Ashby CDX failed: {e}")

    return list(slugs)


async def collect_workday_cdx(limit: int = 8000) -> list[str]:
    """Collect Workday tenant slugs from CDX archive."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.myworkdayjobs.com/*",
        "output": "json",
        "fl": "original",
        "collapse": "urlkey",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            for row in data:
                url = row[0] if isinstance(row, list) else row.get("original", "")
                if "myworkdayjobs.com/" in url:
                    parts = url.split("myworkdayjobs.com/")
                    if len(parts) > 1:
                        slug = parts[1].split("/")[0].split("?")[0]
                        if slug and len(slug) > 2:
                            slugs.add(slug)
            logger.info(f"Workday CDX: {len(slugs)} slugs")
        except Exception as e:
            logger.error(f"Workday CDX failed: {e}")

    return list(slugs)


async def collect_smartrecruiters_cdx(limit: int = 5000) -> list[str]:
    """Collect SmartRecruiters board slugs."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.smartrecruiters.com/*",
        "output": "json",
        "fl": "original",
        "collapse": "urlkey",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            for row in data:
                url = row[0] if isinstance(row, list) else row.get("original", "")
                if "smartrecruiters.com/" in url:
                    parts = url.split("smartrecruiters.com/")
                    if len(parts) > 1:
                        slug = parts[1].split("/")[0].split("?")[0]
                        if slug and len(slug) > 2:
                            slugs.add(slug)
            logger.info(f"SmartRecruiters CDX: {len(slugs)} slugs")
        except Exception as e:
            logger.error(f"SmartRecruiters CDX failed: {e}")

    return list(slugs)


def save_slugs(slugs: list[str], name: str) -> None:
    """Save slugs to JSON file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}_slugs.json"

    # Merge with existing
    existing = set()
    if path.exists():
        try:
            existing = set(json.loads(path.read_text()))
        except:
            pass

    merged = sorted(existing | set(slugs))
    path.write_text(json.dumps(merged, indent=2))
    logger.info(f"Saved {len(merged)} slugs to {path}")


async def collect_all(limit_per_source: int = 5000) -> dict[str, int]:
    """Collect from all sources."""

    tasks = [
        ("greenhouse", collect_greenhouse_cdx(limit_per_source)),
        ("lever", collect_lever_cdx(limit_per_source)),
        ("ashby", collect_ashby_cdx(limit_per_source)),
        ("workday", collect_workday_cdx(limit_per_source)),
        ("smartrecruiters", collect_smartrecruiters_cdx(limit_per_source)),
    ]

    results = {}
    for name, coro in tasks:
        slugs = await coro
        save_slugs(slugs, name)
        results[name] = len(slugs)

    return results


def main():
    parser = argparse.ArgumentParser(description="Collect company slugs")
    parser.add_argument("--all", action="store_true", help="Collect from all sources")
    parser.add_argument("--source", type=str, help="Single source to collect")
    parser.add_argument("--limit", type=int, default=5000, help="Limit per source")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be collected")
    args = parser.parse_args()

    if args.dry_run:
        print("Would collect from: greenhouse, lever, ashby, workday, smartrecruiters")
        print(f"Limit per source: {args.limit}")
        return

    if args.all:
        results = asyncio.run(collect_all(args.limit))
        total = sum(results.values())
        print(f"\nCollected {total} total slugs:")
        for name, count in sorted(results.items()):
            print(f"  {name}: {count}")
    elif args.source:
        # Run single source
        async def run_one():
            if args.source == "greenhouse":
                return await collect_greenhouse_cdx(args.limit)
            elif args.source == "lever":
                return await collect_lever_cdx(args.limit)
            elif args.source == "ashby":
                return await collect_ashby_cdx(args.limit)
            elif args.source == "workday":
                return await collect_workday_cdx(args.limit)
            elif args.source == "smartrecruiters":
                return await collect_smartrecruiters_cdx(args.limit)
            else:
                logger.error(f"Unknown source: {args.source}")
                return []

        slugs = asyncio.run(run_one())
        save_slugs(slugs, args.source)
        print(f"Collected {len(slugs)} {args.source} slugs")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test the script**

```bash
python3 scripts/run_slug_collection.py --dry-run
```

Expected: "Would collect from: greenhouse, lever, ashby, workday, smartrecruiters"

- [ ] **Step 3: Run collection for all sources**

```bash
python3 scripts/run_slug_collection.py --all --limit 5000
```

Expected: Collects ~25K new slugs in 2-3 minutes

- [ ] **Step 4: Verify total count**

```bash
python3 -c "
import json
from pathlib import Path
total = 0
for f in Path('data/companies').glob('*_slugs.json'):
    data = json.loads(f.read_text())
    total += len(data)
    print(f'{f.name}: {len(data)}')
print(f'TOTAL: {total}')
"
```

Expected: 30K+ total slugs

- [ ] **Step 5: Commit slug collection**

```bash
git add scripts/run_slug_collection.py data/companies/*_slugs.json
git commit -m "feat(scripts): add unified slug collection runner, collect 30K+ slugs

- New: scripts/run_slug_collection.py — CDX archive crawler
- Sources: Greenhouse, Lever, Ashby, Workday, SmartRecruiters
- Result: 30K+ company slugs for blind applier

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 2: Structured JSON Logging

### Task 3: Create structured logger wrapper

**Files:**
- Create: `src/utils/structured_logger.py`
- Modify: `config/settings.json`

- [ ] **Step 1: Write the structured logger module**

```python
# src/utils/structured_logger.py
"""
Structured JSON logging for CareerBot.

Enables:
- JSON logs for machine parsing
- Easy integration with logging aggregators (Grafana Loki, ELK)
- Human-readable console output in dev mode

Usage:
    from src.utils.structured_logger import get_logger
    logger = get_logger(__name__)

    logger.info("job_applied", job_id="abc123", company="Stripe", ats="greenhouse")
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


def json_sink(message: dict) -> None:
    """Custom sink that outputs JSON lines."""
    record = message["record"]
    output = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
    }
    # Add extra fields
    if record["extra"]:
        output["extra"] = record["extra"]
    print(json.dumps(output), file=sys.stderr)


def setup_logging(
    json_output: bool = False,
    log_file: str | None = None,
    level: str = "INFO",
) -> None:
    """
    Configure loguru for structured logging.

    Args:
        json_output: If True, output JSON lines to stderr
        log_file: Optional file path for logs
        level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    # Remove default handler
    logger.remove()

    if json_output:
        # JSON to stderr
        logger.add(
            json_sink,
            level=level,
            format="{message}",
        )
    else:
        # Human-readable to stderr
        logger.add(
            sys.stderr,
            level=level,
            format="<green>{time:HH:mm:ss}</green> | <level>{level:8}</level> | <cyan>{module:20}</cyan> - {message}",
            colorize=True,
        )

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_path),
            level=level,
            rotation="10 MB",
            retention="7 days",
            serialize=True,  # JSON format in file
        )

    logger.info("Logging configured", json_output=json_output, level=level)


def get_logger(name: str = __name__):
    """Get a logger instance with module name bound."""
    return logger.bind(module=name)
```

- [ ] **Step 2: Test the structured logger**

```bash
python3 -c "
from src.utils.structured_logger import setup_logging, get_logger

# Test human-readable mode
setup_logging(json_output=False, level='DEBUG')
log = get_logger('test')
log.info('test_message', key='value', count=42)

# Test JSON mode
print('---')
setup_logging(json_output=True, level='DEBUG')
log2 = get_logger('test_json')
log2.info('json_test', action='apply', company='Stripe')
"
```

Expected:
- First output: colored human-readable line
- Second output: JSON line with all fields

- [ ] **Step 3: Add logging config to settings.json**

Read current settings and add logging section:

```bash
cat config/settings.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
data['logging'] = {
    'json_output': False,
    'log_file': 'data/logs/careerbot.jsonl',
    'level': 'INFO'
}
print(json.dumps(data, indent=2))
" > config/settings.json.tmp && mv config/settings.json.tmp config/settings.json
```

- [ ] **Step 4: Commit structured logger**

```bash
git add src/utils/structured_logger.py config/settings.json
git commit -m "feat(logging): add structured JSON logging support

- New: src/utils/structured_logger.py
- Config: logging.json_output, logging.log_file, logging.level
- Supports both human-readable console and JSON file output

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 3: API Key Validation

### Task 4: Create config validator

**Files:**
- Create: `src/utils/config_validator.py`

- [ ] **Step 1: Write the config validator**

```python
# src/utils/config_validator.py
"""
Configuration validator — checks required env vars and API keys at startup.

Raises clear errors before the app runs, not in the middle of a job apply.

Usage:
    from src.utils.config_validator import validate_config
    validate_config()  # Raises if missing critical keys
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class ConfigIssue:
    key: str
    severity: str  # "critical", "warning"
    message: str
    fix_hint: str


REQUIRED_KEYS = {
    # Critical for any operation
    "APPLICANT_EMAIL": ("critical", "Email for job applications"),
    "APPLICANT_FIRST_NAME": ("critical", "First name for applications"),
    "APPLICANT_LAST_NAME": ("critical", "Last name for applications"),

    # AI providers (at least one needed)
    "GEMINI_API_KEY": ("ai", "Gemini API for JD parsing (cheap)"),
    "ANTHROPIC_API_KEY": ("ai", "Claude API for resume tailoring (expensive)"),
    "OPENAI_API_KEY": ("ai", "OpenAI API for various tasks"),

    # Optional but recommended
    "TELEGRAM_BOT_TOKEN": ("warning", "Telegram notifications"),
    "TELEGRAM_CHAT_ID": ("warning", "Telegram chat ID"),
    "LINKEDIN_EMAIL": ("warning", "LinkedIn scraping"),
    "LINKEDIN_PASSWORD": ("warning", "LinkedIn scraping"),
    "TWOCAPTCHA_API_KEY": ("warning", "Captcha solving"),
}


def validate_config(strict: bool = False) -> list[ConfigIssue]:
    """
    Validate configuration and return list of issues.

    Args:
        strict: If True, raise on any critical issue

    Returns:
        List of ConfigIssue objects
    """
    issues = []

    # Check critical keys
    has_ai_key = False
    for key, (severity, description) in REQUIRED_KEYS.items():
        value = os.environ.get(key, "")
        if not value:
            if severity == "critical":
                issues.append(ConfigIssue(
                    key=key,
                    severity="critical",
                    message=f"Missing required: {key}",
                    fix_hint=f"Add to .env: {key}=your_value_here",
                ))
            elif severity == "ai":
                # Track that we have at least one AI key
                pass
            else:
                issues.append(ConfigIssue(
                    key=key,
                    severity="warning",
                    message=f"Missing optional: {key}",
                    fix_hint=f"Add to .env if needed: {key}=your_value_here",
                ))
        elif severity == "ai":
            has_ai_key = True

    # Check that at least one AI key is set
    if not has_ai_key:
        issues.append(ConfigIssue(
            key="AI_API_KEY",
            severity="critical",
            message="No AI API key configured",
            fix_hint="Add at least one: GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY",
        ))

    if strict:
        critical = [i for i in issues if i.severity == "critical"]
        if critical:
            print("❌ Configuration errors:", file=sys.stderr)
            for issue in critical:
                print(f"  {issue.key}: {issue.message}", file=sys.stderr)
                print(f"    Fix: {issue.fix_hint}", file=sys.stderr)
            raise SystemExit(1)

    return issues


def print_config_report() -> None:
    """Print a human-readable config status report."""
    issues = validate_config(strict=False)

    print("📋 Configuration Status:\n")

    # Check each key
    for key, (severity, description) in REQUIRED_KEYS.items():
        value = os.environ.get(key, "")
        if value:
            status = "✅"
        elif severity == "critical":
            status = "❌"
        elif severity == "ai":
            status = "⚠️" if any(i.key == "AI_API_KEY" for i in issues) else "✅"
        else:
            status = "⚠️"
        print(f"  {status} {key:<25} {description}")

    # Summary
    critical = [i for i in issues if i.severity == "critical"]
    warnings = [i for i in issues if i.severity == "warning"]

    print(f"\n  Summary: {len(critical)} critical, {len(warnings)} warnings")

    if critical:
        print("\n  ❌ Critical issues prevent operation:")
        for issue in critical:
            print(f"     {issue.fix_hint}")


if __name__ == "__main__":
    print_config_report()
```

- [ ] **Step 2: Test the validator**

```bash
python3 src/utils/config_validator.py
```

Expected: Shows config status with warnings for missing keys

- [ ] **Step 3: Commit config validator**

```bash
git add src/utils/config_validator.py
git commit -m "feat(config): add startup configuration validator

- New: src/utils/config_validator.py
- Checks required env vars before app runs
- Reports missing API keys with fix hints
- Run: python src/utils/config_validator.py

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Final Verification

### Task 5: Verify Level 2 completion

- [ ] **Step 1: Run all verifications**

```bash
echo "=== Level 2 Verification ==="
echo

echo "1. Slug count:"
python3 -c "
import json
from pathlib import Path
total = sum(len(json.loads(f.read_text())) for f in Path('data/companies').glob('*_slugs.json'))
print(f'   Total slugs: {total}')
print(f'   Target: 30,000')
print(f'   Status: {\"✅ PASS\" if total >= 30000 else \"⚠️ NEED \" + str(30000-total) + \" more\"}')"

echo
echo "2. BatchValidator import:"
python3 -c "from src.applier.universal_screening.batch_validator import BatchStepValidator; print('   ✅ PASS')" 2>&1 || echo "   ❌ FAIL"

echo
echo "3. Structured logger import:"
python3 -c "from src.utils.structured_logger import setup_logging; print('   ✅ PASS')" 2>&1 || echo "   ❌ FAIL"

echo
echo "4. Config validator import:"
python3 -c "from src.utils.config_validator import validate_config; print('   ✅ PASS')" 2>&1 || echo "   ❌ FAIL"

echo
echo "5. Cost tracker import:"
python3 -c "from src.utils.cost_tracker import CostTracker; print('   ✅ PASS')" 2>&1 || echo "   ❌ FAIL"

echo
echo "6. Resume tailor import:"
python3 -c "from src.ai.resume_tailor import ResumeTailor; print('   ✅ PASS')" 2>&1 || echo "   ❌ FAIL"
```

- [ ] **Step 2: Update ROADMAP with current level**

Add a marker to `plans/ROADMAP_10_LEVELS.md` showing progress:

```markdown
## 📊 ШКАЛА ЗРЕЛОСТИ

```
  1 ██░░░░░░░░  Рабочий прототип ✅ COMPLETE
  2 ███░░░░░░░  Стабильный MVP ← ВЫ ЗДЕСЬ (теперь 90%+)
  3 ████░░░░░░  Telegram MVP — первые продажи
```
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: complete Level 2 requirements for stable MVP

- Slug database: 30K+ company slugs collected
- Structured logging: JSON output support
- Config validation: startup checks for API keys
- All Level 2 components verified working

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
git push
```

---

## User Action Required

After this plan is executed, the user must:

1. **Add API keys to `.env`:**
   ```bash
   # Copy template
   cp .env.example .env

   # Edit and add your keys
   nano .env
   ```

   Required keys:
   - `GEMINI_API_KEY` — for JD parsing (cheap)
   - `ANTHROPIC_API_KEY` — for resume tailoring (or `OPENAI_API_KEY`)
   - `APPLICANT_EMAIL`, `APPLICANT_FIRST_NAME`, `APPLICANT_LAST_NAME`

2. **Verify config:**
   ```bash
   python3 src/utils/config_validator.py
   ```

3. **Run the bot:**
   ```bash
   python main.py --test
   ```

---

*Plan generated: 2026-03-14*
