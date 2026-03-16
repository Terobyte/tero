# scripts/run_slug_collection.py
"""
Unified slug collection runner — collects company slugs from all sources.

Sources:
1. Greenhouse job boards (CDX archive)
2. Lever job boards (CDX archive)
3. Ashby job boards (CDX archive)
4. BambooHR job boards (CDX archive)
5. Workday tenants (CDX archive)
6. SmartRecruiters boards (CDX archive)

Usage:
    python scripts/run_slug_collection.py --all
    python scripts/run_slug_collection.py --source greenhouse --limit 5000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import unquote
from typing import Optional

try:
    from loguru import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("data/companies")

# Patterns to reject as invalid slugs
INVALID_SLUG_PATTERNS = [
    # URL-encoded garbage
    r'^%[0-9A-Fa-f]{2}',  # Starts with URL encoding
    r'%[0-9A-Fa-f]{2}',   # Contains URL encoding
    r'\\x[0-9A-Fa-f]{2}', # Hex escapes
    r'\\u[0-9A-Fa-f]{4}', # Unicode escapes
    
    # File extensions
    r'\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot|html?|xml|json|txt|pdf)$',
    
    # Generic paths
    r'^(ads\.txt|app-ads\.txt|robots\.txt|sitemap\.xml|favicon\.ico)$',
    r'^(well-known|\.well-known)$',
    
    # Technical fragments
    r'^[\.,;:\-\+\&\|=\!\@\#\$\%\^\*\(\)\[\]\{\}\<\>]',
    r'(function\(|\.prototype\.|\.style\.|innerHTML|className|createElement)',
    r'(\.append\(|\.prepend\(|\.remove\(|\.replace\()',
    
    # Numeric or gibberish
    r'^[0-9\-\.,]+$',  # Only numbers/punctuation
    r'^[a-f0-9]{8,}$',  # Hex strings (like hash fragments)
    r'^[a-f0-9]{20,}$', # Long hex strings
    
    # HTML/JS fragments
    r'(\<|\>|\/script|\/style|onclick|onerror|target=)',
    r'(&lt;|&gt;|&amp;|&quot;|&#)',
    
    # Common non-company paths
    r'^(api|v1|v2|jobs|careers|apply|search|admin|login|logout|register)$',
    r'^(assets|static|images|img|css|js|scripts|fonts|vendor)$',
    r'^(en|en-us|en-US|de|de-DE|fr|fr-FR|es|es-ES|ko|ko-KR|no|no-NO)$',
    r'^(main|app|index|home|about|contact|privacy|terms)$',
    
    # CDN/cache fragments
    r'^_next', r'^_ipx', r'^\.netlify',
    r'^cached', r'^cache',
    
    # Very short slugs (most company names are at least 3 chars)
    r'^.{1,2}$',
]

# Compile patterns for efficiency
INVALID_SLUG_RE = re.compile('|'.join(f'({p})' for p in INVALID_SLUG_PATTERNS), re.IGNORECASE)

# Known good slugs to always accept (whitelist for common legit company names)
KNOWN_GOOD_PATTERNS = [
    r'^[a-z][a-z0-9\-]+[a-z0-9]$',  # Standard slug format like "stripe", "airbnb-inc"
]

KNOWN_GOOD_RE = re.compile('|'.join(KNOWN_GOOD_PATTERNS), re.IGNORECASE)


def is_valid_slug(slug: str) -> bool:
    """
    Validate a slug to filter out garbage entries.
    
    Returns True if the slug looks like a legitimate company identifier.
    """
    if not slug or not isinstance(slug, str):
        return False
    
    # Decode any URL encoding
    decoded = unquote(slug)
    
    # Basic checks
    if len(decoded) < 3 or len(decoded) > 100:
        return False
    
    # Check against invalid patterns
    if INVALID_SLUG_RE.search(decoded):
        return False
    
    # Must contain at least one letter (not just numbers/symbols)
    if not re.search(r'[a-zA-Z]', decoded):
        return False
    
    # Check for common garbage patterns that might slip through
    if decoded.startswith('.') or decoded.startswith('_'):
        return False
    
    if decoded.startswith('data:image'):
        return False
    
    # Looks like a valid slug
    return True


def extract_slug_from_url(url: str, domain_pattern: str) -> Optional[str]:
    """
    Extract a company slug from a URL.
    
    Args:
        url: The full URL from CDX
        domain_pattern: Pattern to match (e.g., "boards.greenhouse.io")
    
    Returns:
        Slug string or None if extraction fails
    """
    if not url or domain_pattern not in url:
        return None
    
    try:
        # Normalize URL
        url = url.lower().strip()
        
        # Remove protocol
        if '://' in url:
            url = url.split('://', 1)[1]
        
        # Find the domain and extract path
        if domain_pattern in url:
            parts = url.split(domain_pattern, 1)
            if len(parts) > 1:
                path = parts[1].lstrip('/')
                
                # Get first path segment (the slug)
                slug = path.split('/')[0].split('?')[0].split('#')[0]
                slug = slug.strip()
                
                # Validate
                if is_valid_slug(slug):
                    return slug
    except Exception:
        pass
    
    return None


async def collect_greenhouse_cdx(limit: int = 10000) -> list[str]:
    """
    Collect Greenhouse board slugs from CDX archive.
    
    Uses wildcard pattern *.boards.greenhouse.io to find company subdomains.
    """
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    # Key fix: use wildcard to get subdomains, not paths
    params = {
        "url": "*.boards.greenhouse.io",
        "output": "json",
        "fl": "original",
        "collapse": "host",  # Collapse by host, not urlkey
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            
            # Skip header row if present
            if data and isinstance(data[0], list) and data[0][0] == 'original':
                data = data[1:]
            
            for row in data:
                raw_url = row[0] if isinstance(row, list) else row.get("original", "")
                slug = extract_slug_from_url(raw_url, "boards.greenhouse.io")
                if slug:
                    slugs.add(slug)
            
            logger.info(f"Greenhouse CDX: {len(slugs)} valid slugs from {len(data)} URLs")
        except Exception as e:
            logger.error(f"Greenhouse CDX failed: {e}")

    return sorted(slugs)


async def collect_lever_cdx(limit: int = 8000) -> list[str]:
    """Collect Lever board slugs from CDX archive."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.jobs.lever.co",
        "output": "json",
        "fl": "original",
        "collapse": "host",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            
            if data and isinstance(data[0], list) and data[0][0] == 'original':
                data = data[1:]
            
            for row in data:
                raw_url = row[0] if isinstance(row, list) else row.get("original", "")
                slug = extract_slug_from_url(raw_url, "jobs.lever.co")
                if slug:
                    slugs.add(slug)
            
            logger.info(f"Lever CDX: {len(slugs)} valid slugs from {len(data)} URLs")
        except Exception as e:
            logger.error(f"Lever CDX failed: {e}")

    return sorted(slugs)


async def collect_ashby_cdx(limit: int = 5000) -> list[str]:
    """Collect Ashby board slugs from CDX archive."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.jobs.ashbyhq.com",
        "output": "json",
        "fl": "original",
        "collapse": "host",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            
            if data and isinstance(data[0], list) and data[0][0] == 'original':
                data = data[1:]
            
            for row in data:
                raw_url = row[0] if isinstance(row, list) else row.get("original", "")
                slug = extract_slug_from_url(raw_url, "jobs.ashbyhq.com")
                if slug:
                    slugs.add(slug)
            
            logger.info(f"Ashby CDX: {len(slugs)} valid slugs from {len(data)} URLs")
        except Exception as e:
            logger.error(f"Ashby CDX failed: {e}")

    return sorted(slugs)


async def collect_workday_cdx(limit: int = 8000) -> list[str]:
    """Collect Workday tenant slugs from CDX archive."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.myworkdayjobs.com",
        "output": "json",
        "fl": "original",
        "collapse": "host",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            
            if data and isinstance(data[0], list) and data[0][0] == 'original':
                data = data[1:]
            
            for row in data:
                raw_url = row[0] if isinstance(row, list) else row.get("original", "")
                slug = extract_slug_from_url(raw_url, "myworkdayjobs.com")
                if slug:
                    slugs.add(slug)
            
            logger.info(f"Workday CDX: {len(slugs)} valid slugs from {len(data)} URLs")
        except Exception as e:
            logger.error(f"Workday CDX failed: {e}")

    return sorted(slugs)


async def collect_smartrecruiters_cdx(limit: int = 5000) -> list[str]:
    """Collect SmartRecruiters board slugs."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.jobs.smartrecruiters.com",
        "output": "json",
        "fl": "original",
        "collapse": "host",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            
            if data and isinstance(data[0], list) and data[0][0] == 'original':
                data = data[1:]
            
            for row in data:
                raw_url = row[0] if isinstance(row, list) else row.get("original", "")
                slug = extract_slug_from_url(raw_url, "jobs.smartrecruiters.com")
                if slug:
                    slugs.add(slug)
            
            logger.info(f"SmartRecruiters CDX: {len(slugs)} valid slugs from {len(data)} URLs")
        except Exception as e:
            logger.error(f"SmartRecruiters CDX failed: {e}")

    return sorted(slugs)


async def collect_bamboohr_cdx(limit: int = 5000) -> list[str]:
    """Collect BambooHR board slugs."""
    import httpx

    url = "http://web.archive.org/cdx/search/cdx"
    params = {
        "url": "*.bamboohr.com/jobs",
        "output": "json",
        "fl": "original",
        "collapse": "host",
        "limit": limit,
    }

    slugs = set()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
            
            if data and isinstance(data[0], list) and data[0][0] == 'original':
                data = data[1:]
            
            for row in data:
                raw_url = row[0] if isinstance(row, list) else row.get("original", "")
                # BambooHR URLs are like https://company.bamboohr.com/jobs
                if ".bamboohr.com" in raw_url:
                    # Extract subdomain (company name)
                    parts = raw_url.split("://", 1)
                    if len(parts) > 1:
                        host = parts[1].split("/")[0]
                        subdomain = host.split(".")[0]
                        if is_valid_slug(subdomain):
                            slugs.add(subdomain)
            
            logger.info(f"BambooHR CDX: {len(slugs)} valid slugs from {len(data)} URLs")
        except Exception as e:
            logger.error(f"BambooHR CDX failed: {e}")

    return sorted(slugs)


def save_slugs(slugs: list[str], name: str) -> int:
    """Save slugs to JSON file. Returns count of slugs saved."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}_slugs.json"

    # Merge with existing
    existing = set()
    if path.exists():
        try:
            existing = set(json.loads(path.read_text()))
        except Exception:
            pass

    # Filter and merge
    valid_slugs = set(s for s in slugs if is_valid_slug(s))
    merged = sorted(existing | valid_slugs)
    
    path.write_text(json.dumps(merged, indent=2))
    logger.info(f"Saved {len(merged)} slugs to {path} (+{len(valid_slugs)} new)")
    
    return len(merged)


async def collect_all(limit_per_source: int = 5000) -> dict[str, int]:
    """Collect from all sources."""

    tasks = [
        ("greenhouse", collect_greenhouse_cdx(limit_per_source)),
        ("lever", collect_lever_cdx(limit_per_source)),
        ("ashby", collect_ashby_cdx(limit_per_source)),
        ("workday", collect_workday_cdx(limit_per_source)),
        ("smartrecruiters", collect_smartrecruiters_cdx(limit_per_source)),
        ("bamboohr", collect_bamboohr_cdx(limit_per_source)),
    ]

    results = {}
    for name, coro in tasks:
        logger.info(f"Collecting {name}...")
        slugs = await coro
        count = save_slugs(slugs, name)
        results[name] = count

    return results


def validate_existing_files() -> dict[str, dict]:
    """Validate existing slug files and report garbage ratio."""
    results = {}
    
    for path in OUTPUT_DIR.glob("*_slugs.json"):
        try:
            slugs = json.loads(path.read_text())
            valid = [s for s in slugs if is_valid_slug(s)]
            invalid = [s for s in slugs if not is_valid_slug(s)]
            
            results[path.stem] = {
                "total": len(slugs),
                "valid": len(valid),
                "invalid": len(invalid),
                "invalid_ratio": len(invalid) / len(slugs) if slugs else 0,
                "sample_invalid": invalid[:5] if invalid else [],
            }
        except Exception as e:
            results[path.stem] = {"error": str(e)}
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Collect company slugs")
    parser.add_argument("--all", action="store_true", help="Collect from all sources")
    parser.add_argument("--source", type=str, help="Single source to collect")
    parser.add_argument("--limit", type=int, default=5000, help="Limit per source")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be collected")
    parser.add_argument("--validate", action="store_true", help="Validate existing slug files")
    args = parser.parse_args()

    if args.validate:
        results = validate_existing_files()
        print("\n=== Slug File Validation ===\n")
        for name, data in sorted(results.items()):
            if "error" in data:
                print(f"{name}: ERROR - {data['error']}")
            else:
                pct = data['invalid_ratio'] * 100
                status = "✅" if pct < 10 else "⚠️" if pct < 50 else "❌"
                print(f"{name}: {status} {data['valid']}/{data['total']} valid ({pct:.1f}% garbage)")
                if data['sample_invalid']:
                    print(f"  Sample invalid: {data['sample_invalid']}")
        return

    if args.dry_run:
        print("Would collect from: greenhouse, lever, ashby, workday, smartrecruiters, bamboohr")
        print(f"Limit per source: {args.limit}")
        print("\nWith validation enabled - garbage slugs will be filtered out")
        return

    if args.all:
        results = asyncio.run(collect_all(args.limit))
        total = sum(results.values())
        print(f"\n=== Collection Complete ===")
        print(f"Total slugs collected: {total}")
        print("\nBy source:")
        for name, count in sorted(results.items()):
            print(f"  {name}: {count}")
    elif args.source:
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
            elif args.source == "bamboohr":
                return await collect_bamboohr_cdx(args.limit)
            else:
                logger.error(f"Unknown source: {args.source}")
                return []

        slugs = asyncio.run(run_one())
        count = save_slugs(slugs, args.source)
        print(f"Collected {count} {args.source} slugs")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
