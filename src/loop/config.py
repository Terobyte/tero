"""v4 loop configuration and provider name rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KNOWN_PROVIDERS = ("muse", "claude", "codex", "gemini", "opencode", "cursor")

_ALIASES = {
    "cursor-cli": "cursor",
    "cursor-headless": "cursor",
    "headless": "cursor",
}


class UnknownProviderError(ValueError):
    """Provider name is not in the live set and is not a known alias."""


def normalize_provider(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        raise UnknownProviderError("empty provider name")
    canonical = _ALIASES.get(raw, raw)
    if canonical not in KNOWN_PROVIDERS:
        raise UnknownProviderError(canonical)
    return canonical


@dataclass
class LoopConfig:
    plan_file: str = "requirements.md"
    working_dir: str = "."
    player_provider: str = "muse"
    player_model: str = "muse-spark-1.3"
    player_effort: str = ""
    judge_provider: str = "codex"
    judge_model: str = "gpt-5.6-terra"
    judge_effort: str = ""
    tester_provider: str = "gemini"
    tester_model: str = "gemini-3.8-flash"
    tester_effort: str = ""
    escalation_provider: str = "claude"
    escalation_model: str = "opus"
    escalation_effort: str = ""
    test_command: str = ""
    test_command_scoped: str = ""
    player_timeout_s: int = 600
    judge_timeout_s: int = 600
    tester_timeout_s: int = 300
    test_timeout_s: int = 900
    max_rounds: int = 15
    verbose: bool = False
    no_menu: bool = False


def split_role_spec(value: str) -> tuple[str, str]:
    """Parse ``provider`` or ``provider:model``."""
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if ":" in raw:
        provider, model = raw.split(":", 1)
        return provider.strip(), model.strip()
    return raw, ""


def config_from_namespace(args) -> LoopConfig:
    """Build LoopConfig from argparse + optional already-resolved fields."""
    cfg = LoopConfig(
        plan_file=getattr(args, "plan", None) or LoopConfig.plan_file,
        working_dir=getattr(args, "working_dir", None) or ".",
        verbose=bool(getattr(args, "verbose", False)),
        no_menu=bool(getattr(args, "no_menu", False)),
        test_command=getattr(args, "test_command", None) or "",
        test_command_scoped=getattr(args, "test_command_scoped", None) or "",
    )
    if getattr(args, "max_rounds", None):
        cfg.max_rounds = int(args.max_rounds)

    player = split_role_spec(getattr(args, "player_spec", None) or "")
    judge = split_role_spec(getattr(args, "judge_spec", None) or "")
    tester = split_role_spec(getattr(args, "tester_spec", None) or "")
    escalation = split_role_spec(getattr(args, "escalation_spec", None) or "")

    if player[0]:
        cfg.player_provider = normalize_provider(player[0])
    if player[1]:
        cfg.player_model = player[1]
    if judge[0]:
        cfg.judge_provider = normalize_provider(judge[0])
    if judge[1]:
        cfg.judge_model = judge[1]
    if tester[0]:
        cfg.tester_provider = normalize_provider(tester[0])
    if tester[1]:
        cfg.tester_model = tester[1]
    if getattr(args, "no_escalation", False):
        cfg.escalation_provider = ""
        cfg.escalation_model = ""
    else:
        if escalation[0]:
            cfg.escalation_provider = normalize_provider(escalation[0])
        if escalation[1]:
            cfg.escalation_model = escalation[1]

    return cfg


def apply_saved_config(loop: LoopConfig, saved: Any, args: Any | None = None) -> LoopConfig:
    """Copy menu/yaml roles onto the v4 loop. CLI specs win when set."""
    args = args or object()
    working_dir = getattr(saved, "working_dir", "") or ""
    if working_dir:
        loop.working_dir = working_dir
    if not getattr(args, "plan", None):
        plan = getattr(saved, "plan_file", "") or ""
        if plan:
            loop.plan_file = plan

    if not getattr(args, "player_spec", None):
        provider = getattr(saved, "player_provider", "") or ""
        if provider:
            loop.player_provider = normalize_provider(provider)
        model = getattr(saved, "player_model", "") or ""
        if model:
            loop.player_model = model
        effort = getattr(saved, "player_effort", "") or ""
        if effort:
            loop.player_effort = effort

    if not getattr(args, "judge_spec", None):
        provider = (
            getattr(saved, "judge_provider", "")
            or getattr(saved, "batch_judge_provider", "")
            or ""
        )
        model = (
            getattr(saved, "judge_model", "")
            or getattr(saved, "batch_judge_model", "")
            or ""
        )
        if provider:
            loop.judge_provider = normalize_provider(provider)
        if model:
            loop.judge_model = model
        effort = (
            getattr(saved, "judge_effort", "")
            or getattr(saved, "batch_judge_effort", "")
            or ""
        )
        if effort:
            loop.judge_effort = effort

    if not getattr(args, "tester_spec", None):
        provider = getattr(saved, "tester_provider", "") or ""
        model = getattr(saved, "tester_model", "") or ""
        if provider:
            loop.tester_provider = normalize_provider(provider)
        if model:
            loop.tester_model = model
        effort = getattr(saved, "tester_effort", "") or ""
        if effort:
            loop.tester_effort = effort

    if not getattr(args, "test_command", None):
        loop.test_command = getattr(saved, "test_command", "") or ""
    if not getattr(args, "test_command_scoped", None):
        loop.test_command_scoped = getattr(saved, "test_command_scoped", "") or ""

    if getattr(args, "no_escalation", False):
        loop.escalation_provider = ""
        loop.escalation_model = ""
        loop.escalation_effort = ""
    elif not getattr(args, "escalation_spec", None):
        provider = (
            getattr(saved, "escalation_provider", "")
            or getattr(saved, "coach_fallback_provider", "")
            or ""
        )
        model = (
            getattr(saved, "escalation_model", "")
            or getattr(saved, "coach_fallback_model", "")
            or ""
        )
        loop.escalation_provider = normalize_provider(provider) if provider else ""
        loop.escalation_model = model
        loop.escalation_effort = (
            getattr(saved, "escalation_effort", "")
            or getattr(saved, "coach_fallback_effort", "")
            or ""
        )

    if getattr(saved, "verbose", False):
        loop.verbose = True
    return loop
