"""Загрузка и merge конфигурации: defaults → user → project → env → CLI."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""
    name: str
    type: str
    command: str
    claude_home: str | None = None
    account_label: str | None = None
    default_timeout: int = 600


@dataclass
class ResolvedConfig:
    """Merged configuration from all sources."""
    max_rounds: int = 3
    autonomous: bool = False
    run_tests: bool = True
    judge_mode: str = "single"
    selection: str = "best"
    agent_a: str = "ccg"
    agent_b: str = "ccg2"
    judge: str = "ccg"
    judge_2: str | None = None
    worktree_mode: str = "auto"
    verbose: bool = False
    dry_run: bool = False
    working_dir: str = "."
    plan_file: str = "requirements.md"
    run_bug_detection: bool = True
    ask_feedback: bool = True
    timeout_s: int = 600
    agent_a_workspace: str = "g"
    agent_b_workspace: str = "g1"


def load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file if it exists."""
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def load_providers(raw: dict[str, Any]) -> dict[str, ProviderConfig]:
    """Extract provider configurations from raw config dict."""
    result: dict[str, ProviderConfig] = {}
    for name, cfg in raw.get("providers", {}).items():
        result[name] = ProviderConfig(
            name=name,
            type=cfg["type"],
            command=cfg["command"],
            claude_home=cfg.get("claude_home"),
            account_label=cfg.get("account_label"),
            default_timeout=cfg.get("default_timeout", 600),
        )
    return result


def resolve_config(cli_args: dict[str, Any]) -> tuple[ResolvedConfig, dict[str, ProviderConfig]]:
    """Merge всех слоёв. Возвращает конфиг и провайдеры."""
    user_raw = load_yaml(Path.home() / ".config" / "g3" / "config.yaml")
    project_raw = load_yaml(Path(".g3") / "config.yaml")

    # Провайдеры: user < project (project переопределяет)
    providers = {**load_providers(user_raw), **load_providers(project_raw)}

    # Defaults: user < project < env < CLI
    defaults: dict[str, Any] = {
        "max_rounds": 3, "autonomous": False, "run_tests": True,
        "judge_mode": "single", "selection": "best",
        "agent_a": "ccg", "agent_b": "ccg2", "judge": "ccg",
        "run_bug_detection": True, "ask_feedback": True,
        "timeout_s": 600,
    }

    for layer in [user_raw.get("defaults", {}), project_raw.get("defaults", {})]:
        defaults.update({k: v for k, v in layer.items() if v is not None})

    env_map: dict[str, tuple[str, Any]] = {
        "G3_MAX_ROUNDS": ("max_rounds", int),
        "G3_DEFAULT_JUDGE": ("judge", str),
        "G3_AUTONOMOUS": ("autonomous", lambda x: x.lower() == "true"),
    }
    for env_key, (cfg_key, conv) in env_map.items():
        if val := os.environ.get(env_key):
            defaults[cfg_key] = conv(val)

    defaults.update({k: v for k, v in cli_args.items() if v is not None})
    valid_fields = ResolvedConfig.__dataclass_fields__
    cfg = ResolvedConfig(**{k: v for k, v in defaults.items() if k in valid_fields})
    return cfg, providers
