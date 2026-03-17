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


---



## Обзор

Расширение tero (G3 coach-player loop) четырьмя независимыми фичами:

- [x] **CCG Multi-Account** — два аккаунта Blackbox (ccg / ccg2) с разными токенами, параллельная работа
- [ ] **Codex Provider** — новый провайдер через ai-cli-proxy-api (OpenAI-compatible)
- [ ] **TDD Mode** — toggle: Test Writer пишет тесты перед имплементацией
- [ ] **Code Review** — toggle: финальный review через Codex после Coach approval

Фичи независимы друг от друга. TDD и Code Review — тогглы в меню, можно включать по отдельности или оба сразу.

---

## Часть 0: CCG Multi-Account (ccg / ccg2)

### 0.1 Цель

Поддержка двух независимых Blackbox аккаунтов, чтобы Player и Coach могли работать на разных ключах параллельно, без блокировки друг друга rate limits.

### 0.2 Текущее состояние

- [ ] Launcher scripts `launcher/ccg` и `launcher/ccg2` уже используют разные токены и CLAUDE_HOME
- [ ] `BLACKBOX_ACCOUNT_A_TOKEN` → ccg (Account A, `~/.claude-glm-a`)
- [ ] `BLACKBOX_ACCOUNT_B_TOKEN` → ccg2 (Account B, `~/.claude-glm-b`)
- [ ] Но `CcgEnv.from_env()` всегда читает только `ACCOUNT_A_TOKEN`
- [ ] `create_provider("ccg2")` создаёт тот же CcgProvider с тем же env — баг

### 0.3 Решение: два CcgEnv

**config.py — добавить `from_env_b()` и общий `_build()`:**

```python
@dataclass
class CcgEnv:
    base_url: str
    auth_token: str
    model: str
    small_model: str
    claude_home: str

    @classmethod
    def from_env(cls, claude_home: str = "~/.claude-glm") -> "CcgEnv":
        """Account A (default)."""
        return cls._build(
            token_vars=["ANTHROPIC_AUTH_TOKEN", "BLACKBOX_ACCOUNT_A_TOKEN"],
            claude_home=claude_home,
        )

    @classmethod
    def from_env_b(cls, claude_home: str = "~/.claude-glm-b") -> "CcgEnv":
        """Account B (second key)."""
        return cls._build(
            token_vars=["BLACKBOX_ACCOUNT_B_TOKEN"],
            claude_home=claude_home,
        )

    @classmethod
    def _build(cls, token_vars: list[str], claude_home: str) -> "CcgEnv":
        token = ""
        for var in token_vars:
            if val := os.environ.get(var):
                token = val
                break
        return cls(
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.blackbox.ai"),
            auth_token=token,
            model=os.environ.get("ANTHROPIC_MODEL", "blackboxai/z-ai/glm-5"),
            small_model=os.environ.get("ANTHROPIC_SMALL_FAST_MODEL", "minimax-2.5"),
            claude_home=os.path.expanduser(claude_home),
        )
```

### 0.4 Провайдер ccg2

**providers/__init__.py — обновить create_provider():**

```python
if provider_name in ("ccg", "ccg2"):
    if ccg_env is None:
        raise ValueError("CcgEnv required for CCG provider")
    return CcgProvider(ccg_env)
```

`ccg2` не отдельная реализация провайдера, а тот же `CcgProvider` с другим `CcgEnv`.
Выбор Account A / Account B должен происходить **снаружи фабрики**: в `CoachPlayerSession`,
launcher scripts или тестах. Фабрика не должна сама решать, какой ключ читать из env.

### 0.5 CoachPlayerSession — два env

**coach_player.py:**

```python
class CoachPlayerSession:
    def __init__(self, config, requirements, plan_file_path=""):
        # ...
        self.ccg_env = CcgEnv.from_env(config.claude_home)      # Account A
        self.ccg_env_b = CcgEnv.from_env_b()                     # Account B

        self.player_provider = create_provider(
            config.player_provider,
            self.ccg_env if config.player_provider == "ccg" else self.ccg_env_b,
            provider_configs.get(config.player_provider),
        )
        self.coach_provider = create_provider(
            config.coach_provider,
            self.ccg_env if config.coach_provider == "ccg" else self.ccg_env_b,
            provider_configs.get(config.coach_provider),
        )
```

Если Player=ccg, Coach=ccg2 → каждый использует свой токен, свой CLAUDE_HOME → параллельная работа без конфликтов.
Если нужно, env для `ccg2` можно передавать явно извне; `ccg2` здесь выступает как отдельный provider id
для выбора в CLI/menu, но implementation class остаётся той же.

### 0.6 Меню

```python
PROVIDER_PRESETS = {
    "CCG  (Blackbox A)": "ccg",
    "CCG2 (Blackbox B)": "ccg2",
    "Claude Pro (native)": "claude",
    "Codex (GPT via proxy)": "codex",
}
```

### 0.7 CLI

```bash
tero go --player-provider=ccg --coach-provider=ccg2
# Player на Account A, Coach на Account B — параллельно
```

`--player-provider` и `--coach-provider` choices: `["ccg", "ccg2", "claude", "codex"]`

### 0.8 Display Names

```python
class CcgProvider:
    @property
    def display_name(self) -> str:
        # Определяем аккаунт по claude_home
        home = self.env.claude_home
        if "glm-b" in home:
            account = "B"
        else:
            account = "A"
        model = ...  # existing logic
        return f"CCG-{account} ({model})"
```

### 0.9 Use Cases

| Player | Coach | Зачем |
|--------|-------|-------|
| ccg | ccg2 | Параллельная работа, no rate limit conflicts |
| ccg2 | ccg | Если Account A занят другим проектом |
| ccg | ccg | Один аккаунт (как сейчас) |
| ccg | claude | CCG для Player, Claude Pro для Coach |

### 0.10 Тесты

- [ ] `CcgEnv.from_env()` читает `BLACKBOX_ACCOUNT_A_TOKEN`
- [ ] `CcgEnv.from_env_b()` читает `BLACKBOX_ACCOUNT_B_TOKEN`
- [ ] `create_provider("ccg2")` создаёт CcgProvider с env_b
- [ ] Разные `claude_home` у ccg и ccg2

---

## Часть 1: Codex Provider

### 1.1 Цель

Добавить третий провайдер `codex` для Player и Coach, работающий через локальный ai-cli-proxy-api (Go-прокси с OAuth для OpenAI Codex).

### 1.2 Архитектура

```
tero (Python)
  ↓
providers/codex_proxy.py
  ↓ HTTP (OpenAI-compatible API)
ai-cli-proxy-api (Go) — localhost:8317
  ↓ OAuth
OpenAI Codex (GPT-5.4, GPT-4o, codex-mini)
```

### 1.3 Провайдер: `CodexProxyProvider`

**Файл:** `g3/src/providers/codex_proxy.py`

**Интерфейс:** реализует `AgentProvider` protocol (base.py):
- [ ] `async run(prompt, system_prompt, working_dir, max_turns, model)` → AsyncIterator
- [ ] `check_ready()` → tuple[bool, str]
- [ ] `display_name` → str

**Конфигурация:**

```python
@dataclass
class CodexProxyConfig:
    base_url: str = "http://127.0.0.1:8317"
    api_key: str = "g3-local-key"
    default_model: str = "gpt-5.4"
```

**Реализация run():**
- [ ] HTTP POST к `/v1/chat/completions` с `stream: true`
- [ ] SSE parsing (data: ... линии)
- [ ] Формирование messages: `[{"role": "system", ...}, {"role": "user", ...}]`
- [ ] Yield адаптированных сообщений через message_adapter
- [ ] Использовать `httpx.AsyncClient` для async streaming

**Реализация check_ready():**
- [ ] GET `/v1/models` на прокси
- [ ] Проверить что есть хотя бы одна модель с `"gpt"` **или** `"codex"` в ID
- [ ] Если прокси не отвечает → `(False, "Proxy not reachable at {base_url}. Run: cd ai-cli-proxy-api && go run ./cmd/server")`
- [ ] Если нет моделей → `(False, "No Codex models. Run: ai-cli-proxy-api login codex")`

### 1.4 Адаптация сообщений

ai-cli-proxy-api возвращает OpenAI-формат SSE:

```json
{"choices": [{"delta": {"content": "текст"}, "finish_reason": null}]}
```

Нужен адаптер OpenAI SSE → AdaptedMessage (уже есть TextBlock, ToolUseBlock, ToolResultBlock в message_adapter.py).

**Новая функция в message_adapter.py:**

```python
def adapt_openai_sse_chunk(chunk: dict) -> AdaptedMessage | None:
    """Convert OpenAI SSE chunk to AdaptedMessage."""
```

**Логика:**
- [ ] `choices[0].delta.content` → TextBlock
- [ ] `choices[0].delta.tool_calls` → ToolUseBlock (если прокси поддерживает)
- [ ] `finish_reason == "stop"` → финальное сообщение
- [ ] Накопление partial content в буфер для streaming display

### 1.5 Интеграция в фабрику провайдеров

**Файл:** `g3/src/providers/__init__.py`

Добавить в `create_provider()`:

```python
if provider_name == "codex":
    from .codex_proxy import CodexProxyProvider, CodexProxyConfig
    codex_cfg = CodexProxyConfig(
        base_url=provider_config.get("base_url", "http://127.0.0.1:8317"),
        api_key=provider_config.get("api_key", "g3-local-key"),
        default_model=provider_config.get("default_model", "gpt-5.4"),
    )
    return CodexProxyProvider(codex_cfg)
```

### 1.6 Config & CLI

**config.py — расширить choices:**
- [ ] `player_provider` и `coach_provider`: добавить `"codex"` к допустимым значениям
- [ ] Env vars: `G3_PLAYER_PROVIDER=codex`, `G3_COACH_PROVIDER=codex`

**g3.py — CLI args:**
- [ ] `--player-provider` choices: `["ccg", "ccg2", "claude", "codex"]`
- [ ] `--coach-provider` choices: `["ccg", "ccg2", "claude", "codex"]`

### 1.7 Меню

**menu.py — добавить Codex в PROVIDER_PRESETS:**

```python
PROVIDER_PRESETS = {
    "CCG (Blackbox/GLM-5)": "ccg",
    "Claude Pro (native)": "claude",
    "Codex (GPT via proxy)": "codex",
}

CODEX_MODEL_PRESETS = {
    "GPT-5.4 (strongest)": "gpt-5.4",
    "GPT-4o  (balanced)": "gpt-4o",
    "Codex Mini (fast)": "codex-mini",
}
```

При выборе Codex — показывать CODEX_MODEL_PRESETS для выбора модели.

### 1.8 .g3/config.yaml

```yaml
providers:
  codex:
    type: codex_proxy
    base_url: "http://127.0.0.1:8317"
    api_key: "g3-local-key"
    default_model: "gpt-5.4"
```

### 1.9 Зависимости

- [ ] `httpx` — для async HTTP (уже может быть, иначе добавить в requirements.txt)
- [ ] ai-cli-proxy-api должен быть запущен и авторизован

### 1.10 Тесты

- [ ] `tests/test_codex_proxy.py`:
  - [ ] Unit: CodexProxyConfig defaults
  - [ ] Unit: adapt_openai_sse_chunk() для text, tool_calls, finish_reason
  - [ ] Unit: check_ready() с mock httpx
  - [ ] Integration: create_provider("codex") возвращает CodexProxyProvider

---

## Часть 2: TDD Mode (Toggle)

### 2.1 Цель

Опциональный режим: перед имплементацией каждого шага, отдельный агент (Test Writer) генерирует тесты. Player затем имплементирует код так, чтобы тесты прошли.

### 2.2 Pipeline с TDD

**Стандартный цикл (без TDD):**
```
Player implements → Coach reviews → [approved | feedback → retry]
```

**С TDD toggle:**
```
Test Writer generates tests → Player implements (tests must pass) → Coach reviews → [approved | feedback → retry]
```

### 2.3 Кто пишет тесты

- [ ] Test Writer использует **coach_provider/coach_model** (тот же провайдер что и Coach)
- [ ] Отдельный system prompt: `TEST_WRITER_SYSTEM_PROMPT`
- [ ] Отдельный prompt builder: `build_test_writer_prompt()`

### 2.4 Test Writer System Prompt

```
TEST_WRITER_SYSTEM_PROMPT = """You are a Test Architect. Your job is to write comprehensive tests BEFORE implementation.

RULES:
- [ ] Read the requirement carefully
- [ ] Look at the existing codebase to understand the testing patterns, framework, and structure
- [ ] Write tests that will FAIL right now (the feature is not implemented yet)
- [ ] Tests must cover: happy path, edge cases, error handling
- [ ] Use the project's existing test framework and conventions
- [ ] Place tests in the correct test directory following project conventions
- [ ] Tests should be specific and verifiable — no vague assertions
- [ ] Do NOT implement the feature — only write tests

OUTPUT:
- [ ] Create test file(s) with all tests
- [ ] Print summary of what tests cover"""
```

### 2.5 Test Writer Prompt Builder

```python
def build_test_writer_prompt(
    current_step: str,
    step_num: int,
    total_steps: int,
    completed_steps: list[str],
) -> str:
```

Содержимое:
- [ ] Текущий шаг (что нужно реализовать)
- [ ] Контекст уже сделанных шагов
- [ ] Инструкция: напиши тесты которые проверят что этот шаг реализован правильно

### 2.6 Модификация Player Prompt (при TDD)

Когда TDD включен, player prompt дополняется:

```
## Tests Already Written
Tests have been created for this step. Your implementation MUST pass all tests.
Run the tests after implementation to verify.
```

### 2.6.1 Enforced test run

TDD режим должен быть **обязательным**, а не только prompt hint.

После каждого Player attempt, но **до** Coach turn, система запускает тесты:

```python
if self.config.tdd_mode:
    test_result = await self._run_tests_for_step(step.text)
    streaming_ui.print_tdd_status(test_result.passed, test_result.summary)

    if not test_result.passed:
        feedback = Feedback(
            "1. The tests written for this step are still failing.\n"
            f"2. Test output summary:\n{test_result.summary}\n"
            "3. Fix the implementation until the tests pass."
        )
        continue  # skip coach, retry player
```

Требования к этому шагу:
- [ ] Если тесты падают, Coach **не запускается**
- [ ] Если тесты проходят, только тогда начинается Coach review
- [ ] Использовать существующую test command проекта, если она однозначно определяется
- [ ] Разрешить override через config: `test_command` (если пусто, использовать autodetect)

### 2.7 Config

```python
@dataclass
class Config:
    # ... existing fields ...
    tdd_mode: bool = False  # TDD toggle
    test_command: str = ""  # empty = auto-detect project test command
```

**CLI:** `--tdd` flag
**Env:** `G3_TDD_MODE=true`
**Env:** `G3_TEST_COMMAND="pytest -q"`
**Config yaml:** `defaults.tdd_mode: true`, `defaults.test_command: "pytest -q"`

### 2.8 Меню

Добавить в меню тогглы:

```python
questionary.Separator("─── режимы ──────────────────────────────"),
questionary.Choice(f"    TDD Mode:       {'вкл' if config.tdd_mode else 'выкл'}", value="tdd_mode"),
questionary.Choice(f"    Code Review:    {'вкл' if config.code_review else 'выкл'}", value="code_review"),
```

Переключение — простой toggle (как verbose/autonomous).

### 2.9 Интеграция в CoachPlayerSession.run()

В цикле по шагам, **перед первой player attempt**:

```python
if self.config.tdd_mode:
    # --- Test Writer turn ---
    streaming_ui.print_test_writer_header(step_num, total_steps)

    test_prompt = build_test_writer_prompt(
        current_step=step.text,
        step_num=step_num,
        total_steps=total_steps,
        completed_steps=completed_steps,
    )

    await self._run_turn(
        role="test_writer",
        prompt=test_prompt,
        system_prompt=TEST_WRITER_SYSTEM_PROMPT,
        max_turns=15,
        timeout_s=self.config.coach_timeout_s,
        model_override=self.config.coach_model,
    )
```

Test Writer запускается **один раз на шаг** (не на каждую попытку). Тесты пишутся перед первой попыткой, дальше Player итерирует пока тесты не пройдут.
После **каждой** player attempt тесты прогоняются автоматически; только успешный test run пропускает шаг к Coach.

### 2.10 Streaming UI

Новые функции в streaming.py:

```python
def print_test_writer_header(step_num, total_steps):
    """Print header for test writer phase."""
    # 🧪 [Step 1/5] Test Writer generating tests...

def print_tdd_status(tests_passed: bool, test_output: str):
    """Print TDD test run results."""
```

### 2.11 Тесты

- [ ] `tests/test_tdd_mode.py`:
  - [ ] Config: tdd_mode=True парсится из CLI, env, yaml
  - [ ] Config: `test_command` парсится из CLI, env, yaml
  - [ ] Prompt builder: build_test_writer_prompt() содержит нужные поля
  - [ ] Flow: при tdd_mode=True вызывается test_writer перед player
  - [ ] Flow: если тесты упали, Coach не вызывается

---

## Часть 3: Code Review Toggle

### 3.1 Цель

Опциональный финальный review через отдельного агента (Codex/GPT) после того как Coach уже одобрил шаг. Ищет баги, security issues, best practices нарушения.

### 3.2 Pipeline с Code Review

**Стандартный цикл:**
```
Player → Coach → APPROVED → next step
```

**С Code Review toggle:**
```
Player → Coach → APPROVED → Code Reviewer → [ok | issues → feedback → retry]
```

### 3.3 Кто делает review

- [ ] По умолчанию: `codex` провайдер (если настроен)
- [ ] Fallback: тот же `coach_provider`
- [ ] Настраивается отдельно: `review_provider` / `review_model`

Для runtime-routing нужен отдельный provider slot:
- [ ] `player` → `self.player_provider`
- [ ] `coach` → `self.coach_provider`
- [ ] `test_writer` → `self.coach_provider` (или alias `self.test_writer_provider`)
- [ ] `reviewer` → `self.review_provider`

### 3.4 Config

```python
@dataclass
class Config:
    # ... existing fields ...
    code_review: bool = False       # Code Review toggle
    review_provider: str = ""       # empty = use codex if available, else coach_provider
    review_model: str = ""          # empty = provider default
```

**CLI:** `--code-review` flag, `--review-provider`, `--review-model`
**Env:** `G3_CODE_REVIEW=true`, `G3_REVIEW_PROVIDER=codex`

### 3.5 Code Reviewer System Prompt

```
CODE_REVIEWER_SYSTEM_PROMPT = """You are a Code Reviewer specializing in bug finding and security analysis.

You are reviewing code that has ALREADY been approved by a coach. Your job is to find issues
the coach missed.

FOCUS AREAS:
- [ ] Security vulnerabilities (injection, XSS, auth bypass, secrets in code)
- [ ] Logic bugs (off-by-one, race conditions, null handling)
- [ ] Performance issues (N+1 queries, memory leaks, blocking calls)
- [ ] Error handling gaps (unhandled exceptions, silent failures)
- [ ] Best practices violations specific to the language/framework

DO NOT review:
- [ ] Code style or formatting
- [ ] Naming conventions
- [ ] Minor refactoring suggestions

PROCESS:
- [ ] Read the changed/new files for the current step
- [ ] Analyze for the focus areas above
- [ ] If critical issues found → numbered list of issues
- [ ] If no critical issues → CODE_REVIEW_PASSED

Your verdict MUST end with either CODE_REVIEW_PASSED or a numbered list of critical issues."""
```

### 3.6 Code Review Prompt Builder

```python
def build_code_review_prompt(
    current_step: str,
    step_num: int,
    total_steps: int,
) -> str:
```

Содержимое:
- [ ] Какой шаг был реализован
- [ ] Инструкция: проверь реализацию на баги и security issues
- [ ] Акцент на `git diff` чтобы смотреть именно изменения

### 3.7 Интеграция в CoachPlayerSession.run()

**После** Coach одобрил шаг (verdict == Approved), **перед** mark_step_done:

```python
if isinstance(verdict, Approved) and self.config.code_review:
    # --- Code Review turn ---
    streaming_ui.print_code_review_header(step_num, total_steps)

    review_prompt = build_code_review_prompt(
        current_step=step.text,
        step_num=step_num,
        total_steps=total_steps,
    )

    review_result = await self._run_turn(
        role="reviewer",
        prompt=review_prompt,
        system_prompt=CODE_REVIEWER_SYSTEM_PROMPT,
        max_turns=8,
        timeout_s=self.config.coach_timeout_s,
        model_override=self.config.review_model,
    )

    review_verdict = parse_review_output(review_result.messages)

    if isinstance(review_verdict, ReviewPassed):
        # Proceed to mark step done
        streaming_ui.print_review_passed(step_num)
    else:
        # Send review feedback back to player
        feedback = Feedback(review_verdict.text)
        streaming_ui.print_review_issues(review_verdict.text)
        step_approved = False  # force another player iteration
        continue
```

### 3.8 Review Verdict Parsing

**feedback.py — добавить:**

```python
@dataclass
class ReviewPassed:
    """Code review passed with no critical issues."""
    pass

@dataclass
class ReviewIssues:
    """Code review found issues."""
    text: str

def parse_review_output(messages: list) -> ReviewPassed | ReviewIssues:
    """Parse code reviewer output for verdict."""
    # Look for CODE_REVIEW_PASSED in final text
    # Otherwise extract issues list
```

### 3.9 Review Provider Resolution

В `CoachPlayerSession.__init__()`:

```python
if self.config.code_review:
    review_provider_name = self.config.review_provider
    if not review_provider_name:
        # Auto-detect: use codex if available, else coach
        codex_prov = create_provider("codex", self.ccg_env, provider_configs.get("codex"))
        ok, _ = codex_prov.check_ready()
        if ok:
            review_provider_name = "codex"
        else:
            review_provider_name = self.config.coach_provider

    self.review_provider = create_provider(
        review_provider_name,
        self.ccg_env if review_provider_name == "ccg" else self.ccg_env_b if review_provider_name == "ccg2" else None,
        provider_configs.get(review_provider_name),
    )
```

`review_provider` должен реально использоваться в `_run_turn()`. Недостаточно просто создать `self.review_provider`;
нужно обновить роутинг роли на провайдер.

Например:

```python
provider = {
    "player": self.player_provider,
    "coach": self.coach_provider,
    "test_writer": self.coach_provider,
    "reviewer": self.review_provider,
}[role]
```

### 3.10 Меню

В menu.py — добавить тогглы (вместе с TDD):

```python
questionary.Separator("─── режимы ──────────────────────────────"),
questionary.Choice(f"    TDD Mode:       {tdd_display}", value="tdd_mode"),
questionary.Choice(f"    Code Review:    {review_display}", value="code_review"),
```

При включении Code Review — опционально спросить review provider:

```python
if setting == "code_review":
    config.code_review = not config.code_review
    if config.code_review and not config.review_provider:
        # Ask for review provider
        choice = questionary.select(
            "Провайдер для Code Review:",
            choices=["Codex (auto-detect)", "Coach (same as coach)", "Выбрать..."],
        ).ask()
```

### 3.11 Streaming UI

```python
def print_code_review_header(step_num, total_steps):
    """🔍 [Step 1/5] Code Review (Codex/GPT-5.4)..."""

def print_review_passed(step_num):
    """✅ Code Review passed — no critical issues"""

def print_review_issues(issues_text):
    """⚠ Code Review found issues: ..."""
```

### 3.12 Сохранение результатов review

Результаты review сохраняются в `.g3/reviews/`:

```
.g3/reviews/
  step-1-review-2024-01-15.md
  step-2-review-2024-01-15.md
```

Формат:
```markdown
# Code Review — Step 1
- [ ] Provider: codex/gpt-5.4
- [ ] Verdict: PASSED | ISSUES_FOUND
- [ ] Issues: (если есть)
```

### 3.13 Тесты

- [ ] `tests/test_code_review.py`:
  - [ ] Config: code_review=True парсится из CLI, env, yaml
  - [ ] parse_review_output(): CODE_REVIEW_PASSED → ReviewPassed
  - [ ] parse_review_output(): numbered list → ReviewIssues
  - [ ] Flow: review вызывается только после Coach approval

---

## Часть 4: Полный Pipeline (все тогглы включены)

### 4.1 Полная последовательность

Когда и TDD Mode, и Code Review включены одновременно:

```
Для каждого шага:
  - [ ] [TDD]    Test Writer генерирует тесты (один раз)
  - [ ] [IMPL]   Player имплементирует (тесты должны пройти)  ─┐
  - [ ] [COACH]  Coach проверяет                                │ retry loop
  - [ ] [REVIEW] Code Reviewer проверяет (после Coach OK)       │
  └── feedback → retry from step 2 ──────────────────────────┘
  - [ ] [DONE]   Шаг помечен как выполненный
```

### 4.2 Retry Logic

- [ ] **Coach отклонил** → feedback идёт Player, retry с шага 2
- [ ] **Code Review нашёл issues** → feedback идёт Player, retry с шага 2
- [ ] **Тесты не переписываются** при retry (написаны один раз в шаге 1)
- [ ] max_turns применяется к общему числу Player attempts

### 4.3 Status Display

```
⚙  tero — настройка  (↑↓ выбор, Enter)
  ▶   Запустить
  ─── провайдеры ──────────────────────────
      Player:         ccg (GLM-5)
      Coach:          claude (SONNET)
  ─── режимы ──────────────────────────────
      TDD Mode:       выкл
      Code Review:    выкл
  ─── настройки ───────────────────────────
      Рабочая папка:  ~/project
      ...
```

### 4.4 Runtime Header

При запуске сессии отображать активные режимы:

```
--- tero coach-player ---
  Файл плана: requirements.md
  Шагов: 5  |  Макс. попыток на шаг: 10
  Player: CCG (GLM-5)  |  Coach: Claude Pro (sonnet)
  Режимы: TDD ✓  Code Review ✓ (Codex/GPT-5.4)
```

---

## Часть 5: Файловая структура изменений

### 5.1 Новые файлы

```
g3/src/providers/codex_proxy.py     — Codex провайдер
g3/tests/test_codex_proxy.py        — тесты Codex провайдера
g3/tests/test_tdd_mode.py           — тесты TDD mode
g3/tests/test_code_review.py        — тесты Code Review
g3/tests/test_ccg_multi.py          — тесты CCG multi-account
```

### 5.2 Изменяемые файлы

```
g3/src/config.py                    — CcgEnv.from_env_b(), tdd_mode, code_review, review_provider, review_model
                                   — test_command
g3/src/providers/__init__.py        — ccg2 + codex в create_provider()
g3/src/providers/message_adapter.py — добавить adapt_openai_sse_chunk()
g3/src/coach_player.py              — ccg_env_b, TDD и Code Review фазы в loop
g3/src/prompts.py                   — добавить TEST_WRITER и CODE_REVIEWER prompts
g3/src/menu.py                      — добавить ccg2, codex, тогглы в меню
g3/src/streaming.py                 — добавить UI функции для новых фаз
g3/src/feedback.py                  — добавить ReviewPassed, ReviewIssues, parse_review_output()
g3/g3.py                            — добавить CLI args: --tdd, --test-command, --code-review, --review-provider, ccg2 choice
g3/requirements.txt                 — добавить httpx (если нет)
```

---

## Часть 6: Порядок реализации

### Phase 0: CCG Multi-Account
- [ ] 0.1 Добавить `CcgEnv.from_env_b()` и `CcgEnv._build()` в config.py
- [ ] 0.2 Обновить `create_provider("ccg2")` — принимать выбранный env, без автоподмены внутри фабрики
- [ ] 0.3 Обновить CoachPlayerSession — передавать правильный env для ccg/ccg2
- [ ] 0.4 Добавить `ccg2` в CLI choices (g3.py)
- [ ] 0.5 Добавить CCG2 в меню (PROVIDER_PRESETS)
- [ ] 0.6 Display name: CCG-A / CCG-B
- [ ] 0.7 Тесты multi-account

### Phase 1: Codex Provider
- [ ] 1.1 Создать `codex_proxy.py` с CodexProxyConfig и CodexProxyProvider
- [ ] 1.2 Добавить `adapt_openai_sse_chunk()` в message_adapter.py
- [ ] 1.3 Добавить `codex` в `create_provider()` (__init__.py)
- [ ] 1.4 Добавить `codex` в CLI choices и config
- [ ] 1.5 Добавить Codex в меню (PROVIDER_PRESETS + CODEX_MODEL_PRESETS)
- [ ] 1.6 Тесты codex провайдера

### Phase 2: TDD Mode
- [ ] 2.1 Добавить `tdd_mode` в Config, CLI, env
- [ ] 2.1.1 Добавить `test_command` в Config, CLI, env
- [ ] 2.2 Добавить TEST_WRITER_SYSTEM_PROMPT и build_test_writer_prompt() в prompts.py
- [ ] 2.3 Добавить TDD toggle в меню
- [ ] 2.4 Добавить print_test_writer_header() в streaming.py
- [ ] 2.5 Интегрировать test_writer фазу в coach_player.py run()
- [ ] 2.6 Тесты TDD mode

### Phase 3: Code Review
- [ ] 3.1 Добавить `code_review`, `review_provider`, `review_model` в Config, CLI, env
- [ ] 3.2 Добавить CODE_REVIEWER_SYSTEM_PROMPT и build_code_review_prompt() в prompts.py
- [ ] 3.3 Добавить ReviewPassed, ReviewIssues, parse_review_output() в feedback.py
- [ ] 3.4 Добавить Code Review toggle в меню
- [ ] 3.5 Добавить print_code_review_header/passed/issues в streaming.py
- [ ] 3.6 Интегрировать review фазу в coach_player.py run()
- [ ] 3.7 Добавить сохранение review результатов в .g3/reviews/
- [ ] 3.8 Тесты Code Review

### Phase 4: Integration
- [ ] 4.1 Полный pipeline: TDD + Code Review вместе
- [ ] 4.2 Runtime header с отображением активных режимов
- [ ] 4.3 End-to-end тест всех режимов

---

## Часть 7: Coach Silent Failure Fix

### 7.1 Проблема

GLM-5 (через CCG provider) регулярно завершает сессию без финального текстового сообщения —
последний SDK message является tool result, а не текстом. Реже такое случается с Sonnet.

Текущее поведение системы при отсутствии вердикта:
- [ ] `parse_coach_output` возвращает `Feedback("Coach produced no output — проверь текущую реализацию...")`
- [ ] Этот текст идёт **Player**-у как якобы реальный фидбек
- [ ] Player начинает "чинить" код который может быть идеальным
- [ ] Цикл крутится до `max_turns` — шаг не одобряется никогда

### 7.2 Два отдельных бага

**Баг A: `_is_assistant_message` в feedback.py**

```python
# Сейчас:
msg_type = type(msg).__name__  # → "AdaptedMessage", не "AssistantMessage"
if msg_type == "AssistantMessage":  # ВСЕГДА False
    return True
# Fallback проверяет только наличие .content — срабатывает для ВСЕХ AdaptedMessage
# включая role="tool" (tool results)
if hasattr(msg, "content") and not hasattr(msg, "tool_use_id"):
    return True
```

Если последнее сообщение в очереди — `AdaptedMessage(role="tool", ...)`, оно ошибочно
принимается за ассистента. `_extract_text_from_message` не находит `.text` в ToolResultBlock
и возвращает пустую строку → срабатывает "no output" fallback.

**Фикс:**
```python
def _is_assistant_message(msg) -> bool:
    # SDK native type
    if type(msg).__name__ == "AssistantMessage":
        return True
    # AdaptedMessage — проверять role явно
    if hasattr(msg, "role"):
        return msg.role == "assistant"
    return False
```

**Баг Б: "нет вердикта" неотличимо от "нет ответа"**

Текущий `parse_coach_output` возвращает одинаковый `Feedback(...)` в двух разных ситуациях:
- [ ] Coach не выдал вообще ничего (0 assistant messages)
- [ ] Coach написал текст, но без `IMPLEMENTATION_APPROVED` и без нумерованного списка

Ситуация 2 — это реальный фидбек (пусть и плохо структурированный) и его нужно передавать Player.
Ситуация 1 — это сбой coach, player здесь не при чём.

### 7.3 Новый тип вердикта: `NoVerdict`

**feedback.py — добавить:**

```python
@dataclass
class NoVerdict:
    """Coach завершил работу без вердикта (нет текста / не ответил).

    Это НЕ фидбек для Player. Это сигнал что coach нужно повторить.
    """
    pass

Verdict = Approved | Feedback | NoVerdict
```

**Обновить `parse_coach_output`:**

```python
def parse_coach_output(messages: list) -> Verdict:
    last_assistant_msg = None
    for msg in messages:
        if _is_assistant_message(msg):
            last_assistant_msg = msg

    if last_assistant_msg is None:
        return NoVerdict()

    text = _extract_text_from_message(last_assistant_msg)

    if not text.strip():
        return NoVerdict()

    if "IMPLEMENTATION_APPROVED" in text:
        return Approved()

    return Feedback(text)
```

### 7.4 Coach retry логика в coach_player.py

Когда verdict == `NoVerdict`, проблема на стороне coach, а не player.
Решение: повторить **только coach** turn, не трогая player.

```python
# В CoachPlayerSession.run(), после coach turn:
COACH_RETRY_MAX = 2  # или из config

for coach_attempt in range(1, COACH_RETRY_MAX + 1):
    coach_result = await self._run_turn(role="coach", ...)
    verdict = parse_coach_output(coach_result.messages)

    if not isinstance(verdict, NoVerdict):
        break  # получили реальный вердикт

    if coach_attempt < COACH_RETRY_MAX:
        streaming_ui.print_coach_no_verdict_retry(coach_attempt, COACH_RETRY_MAX)
    else:
        # Исчерпали повторы — coach не смог выдать вердикт
        streaming_ui.print_coach_silent_skip()
        verdict = Approved()  # пропускаем шаг (см. 7.5)
```

### 7.5 Что делать если coach так и не ответил: Sonnet Fallback

Если после `coach_retry_max` попыток основной coach (GLM-5) всё равно не дал вердикт —
**один раз** вызвать fallback coach (Sonnet). После получения вердикта от Sonnet сессия
продолжается с основным coach как обычно.

```
GLM-5 → NoVerdict → retry → NoVerdict → retry → NoVerdict
  → Sonnet (один раз) → Approved / Feedback
  → следующая player attempt → GLM-5 coach снова
```

Fallback не заменяет основного coach навсегда — только закрывает этот конкретный
«мёртвый» вердикт. GLM-5 остаётся основным coach на следующих итерациях.

**Конфигурация:**
```python
@dataclass
class Config:
    coach_retry_max: int = 2             # повторов GLM-5 при NoVerdict перед escalation
    coach_fallback_provider: str = "claude"  # провайдер для fallback (Sonnet)
    coach_fallback_model: str = ""           # пусто = provider default
```

CLI: `--coach-fallback-provider=claude`, `--coach-fallback-model=...`
Env: `G3_COACH_FALLBACK_PROVIDER=claude`, `G3_COACH_FALLBACK_MODEL=...`

**Fallback provider инициализируется в `CoachPlayerSession.__init__()`:**

```python
self.coach_fallback_provider = create_provider(
    config.coach_fallback_provider,
    self.ccg_env,
    provider_configs.get(config.coach_fallback_provider),
) if config.coach_fallback_provider else None
```

**Логика в run():**

```python
for coach_attempt in range(1, coach_retry_max + 1):
    coach_result = await self._run_turn(role="coach", ...)
    verdict = parse_coach_output(coach_result.messages)

    if not isinstance(verdict, NoVerdict):
        break

    if coach_attempt < coach_retry_max:
        streaming_ui.print_coach_no_verdict_retry(coach_attempt, coach_retry_max)
else:
    # Основной coach молчит — вызвать fallback один раз
    streaming_ui.print_coach_fallback_escalation(self.coach_fallback_provider.display_name)
    fallback_result = await self._run_turn(
        role="coach_fallback",
        prompt=coach_prompt,
        system_prompt=COACH_STRICT_SYSTEM_PROMPT,
        max_turns=8,
        timeout_s=self.config.coach_timeout_s,
        model_override=self.config.coach_fallback_model,
    )
    verdict = parse_coach_output(fallback_result.messages)
    if isinstance(verdict, NoVerdict):
        # Даже Sonnet не ответил — редкий случай, завершить сессию с ошибкой
        raise RuntimeError("Fallback coach also produced no verdict")
```

`"coach_fallback"` роутится на `self.coach_fallback_provider` в `_run_turn`.

### 7.6 Streaming UI

```python
def print_coach_no_verdict_retry(attempt: int, max_attempts: int):
    """⚠ Coach не дал вердикт — повтор {attempt}/{max_attempts}..."""

def print_coach_fallback_escalation(fallback_name: str):
    """⚠ Coach молчит — передаю {fallback_name} для вердикта..."""
```

### 7.7 Тесты

- [ ] `tests/test_feedback.py` — обновить:
  - [ ] `parse_coach_output([])` → `NoVerdict()`
  - [ ] `parse_coach_output([AdaptedMessage(role="tool", ...)])` → `NoVerdict()` (не Feedback!)
  - [ ] `parse_coach_output([AdaptedMessage(role="assistant", content=[TextBlock("some text")])])` → `Feedback("some text")`
  - [ ] `parse_coach_output([AdaptedMessage(role="assistant", content=[TextBlock("IMPLEMENTATION_APPROVED")])])` → `Approved()`
  - [ ] `_is_assistant_message(AdaptedMessage(role="tool", ...))` → `False`
  - [ ] `_is_assistant_message(AdaptedMessage(role="assistant", ...))` → `True`

- [ ] `tests/test_coach_player.py` — добавить:
  - [ ] При `NoVerdict` × `coach_retry_max`: вызывается fallback provider (Sonnet)
  - [ ] Fallback возвращает `Approved` / `Feedback` → сессия продолжается нормально
  - [ ] Если и fallback возвращает `NoVerdict` → RuntimeError
  - [ ] После fallback вердикта следующая итерация использует снова основной coach

### 7.8 Порядок реализации

- [ ] 7.1 Фикс `_is_assistant_message` в feedback.py (role-based check)
- [ ] 7.2 Добавить `NoVerdict` в feedback.py
- [ ] 7.3 Обновить `parse_coach_output` — возвращать `NoVerdict` вместо fallback Feedback
- [ ] 7.4 Добавить `coach_retry_max`, `coach_fallback_provider`, `coach_fallback_model` в Config, CLI, env
- [ ] 7.5 Добавить coach retry loop + fallback escalation в coach_player.py
- [ ] 7.6 Добавить UI функции в streaming.py
- [ ] 7.7 Обновить тесты feedback + coach_player
