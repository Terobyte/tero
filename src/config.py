"""Configuration: defaults -> .g3/config.yaml -> env -> CLI args."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class CcgEnv:
    """Environment variables for ccg (Blackbox.ai via Claude CLI)."""
    base_url: str
    auth_token: str
    model: str
    small_model: str
    claude_home: str

    @classmethod
    def from_env(cls, claude_home: str = "~/.claude-glm") -> "CcgEnv":
        token = (
            os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("BLACKBOX_ACCOUNT_A_TOKEN")
            or ""
        )
        return cls(
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.blackbox.ai"),
            auth_token=token,
            model=os.environ.get("ANTHROPIC_MODEL", "blackboxai/z-ai/glm-5"),
            small_model=os.environ.get("ANTHROPIC_SMALL_FAST_MODEL", "kimi-k2.5"),
            claude_home=os.path.expanduser(claude_home),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_AUTH_TOKEN": self.auth_token,
            "ANTHROPIC_MODEL": self.model,
            "ANTHROPIC_SMALL_FAST_MODEL": self.small_model,
            "CLAUDE_HOME": self.claude_home,
        }


@dataclass
class Config:
    """Resolved configuration."""
    max_turns: int = 10
    autonomous: bool = False
    verbose: bool = False
    plan_file: str = "requirements.md"
    working_dir: str = "."
    player_timeout_s: int = 600
    coach_timeout_s: int = 300
    claude_home: str = "~/.claude-glm"
    coach_model: str = ""  # empty = use default model from env

    # Provider selection (NEW)
    player_provider: str = "ccg"  # "ccg" | "claude"
    coach_provider: str = "ccg"   # "ccg" | "claude"
    player_model: str = ""        # model for player (empty = provider default)


def short_model_name(model: str) -> str:
    """Get short display name from a model string."""
    m = model.lower()
    if "opus" in m:
        return "OPUS"
    if "sonnet" in m:
        return "SONNET"
    if "haiku" in m:
        return "HAIKU"
    if "glm" in m:
        return "GLM-5"
    if "kimi" in m:
        return "KIMI"
    if not model:
        return "DEFAULT"
    return model.split("/")[-1].upper()[:10]


def _load_yaml(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def resolve_config(cli_args: dict) -> Config:
    """Merge: defaults -> .g3/config.yaml -> env -> CLI args."""
    defaults = {}
    working_dir = cli_args.get("working_dir") or "."
    working_dir = str(Path(working_dir).expanduser().resolve())

    # Load project config
    project = _load_yaml(Path(working_dir) / ".g3" / "config.yaml")
    defaults.update(project.get("defaults", {}))

    # Env overrides
    env_map = {
        "G3_MAX_TURNS": ("max_turns", int),
        "G3_AUTONOMOUS": ("autonomous", lambda x: x.lower() == "true"),
        "G3_PLAYER_PROVIDER": ("player_provider", str),
        "G3_COACH_PROVIDER": ("coach_provider", str),
        "G3_PLAYER_MODEL": ("player_model", str),
        "G3_COACH_MODEL": ("coach_model", str),
    }
    for env_key, (cfg_key, conv) in env_map.items():
        if val := os.environ.get(env_key):
            defaults[cfg_key] = conv(val)

    # CLI overrides (highest priority, skip None values)
    defaults.update({k: v for k, v in cli_args.items() if v is not None})
    defaults["working_dir"] = working_dir

    # Provider config
    provider = project.get("provider", {})
    if claude_home := provider.get("claude_home"):
        defaults["claude_home"] = claude_home

    valid_fields = Config.__dataclass_fields__
    return Config(**{k: v for k, v in defaults.items() if k in valid_fields})
