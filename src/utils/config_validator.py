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
