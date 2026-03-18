"""Configuration: defaults -> .g3/config.yaml -> env -> CLI args."""

import json
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
    account_label: str = "blackbox-a"  # For display/logging

    @classmethod
    def from_env(cls, claude_home: str = "~/.claude-glm-a") -> "CcgEnv":
        """Create CcgEnv from environment for the default Account A."""
        token = (
            os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("BLACKBOX_ACCOUNT_A_TOKEN")
            or ""
        )
        return cls(
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.blackbox.ai"),
            auth_token=token,
            model=os.environ.get("ANTHROPIC_MODEL", "blackboxai/z-ai/glm-5"),
            small_model=os.environ.get("ANTHROPIC_SMALL_FAST_MODEL", "minimax-2.5"),
            claude_home=os.path.expanduser(claude_home),
        )

    @classmethod
    def from_env_b(cls, claude_home: str = "~/.claude-glm-b") -> "CcgEnv":
        """Create CcgEnv for the second Blackbox account."""
        return cls(
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.blackbox.ai"),
            auth_token=os.environ.get("BLACKBOX_ACCOUNT_B_TOKEN", ""),
            model=os.environ.get("ANTHROPIC_MODEL", "blackboxai/z-ai/glm-5"),
            small_model=os.environ.get("ANTHROPIC_SMALL_FAST_MODEL", "minimax-2.5"),
            claude_home=os.path.expanduser(claude_home),
            account_label="blackbox-b",
        )

    @classmethod
    def from_claude_home(cls, claude_home: str, account_label: str = "blackbox-a") -> "CcgEnv":
        """Load CCG settings from a Claude home directory, falling back to env."""
        expanded_home = os.path.expanduser(claude_home)
        settings_path = Path(expanded_home) / "settings.json"

        env_values: dict[str, str] = {}
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text())
                raw_env = data.get("env", {})
                if isinstance(raw_env, dict):
                    env_values = {str(k): str(v) for k, v in raw_env.items()}
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                env_values = {}

        token = (
            env_values.get("ANTHROPIC_AUTH_TOKEN")
            or env_values.get("BLACKBOX_ACCOUNT_A_TOKEN")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("BLACKBOX_ACCOUNT_A_TOKEN")
            or ""
        )
        model = (
            env_values.get("ANTHROPIC_MODEL")
            or os.environ.get("ANTHROPIC_MODEL")
            or "blackboxai/z-ai/glm-5"
        )
        small_model = (
            env_values.get("ANTHROPIC_SMALL_FAST_MODEL")
            or os.environ.get("ANTHROPIC_SMALL_FAST_MODEL")
            or "minimax-2.5"
        )
        base_url = (
            env_values.get("ANTHROPIC_BASE_URL")
            or os.environ.get("ANTHROPIC_BASE_URL")
            or "https://api.blackbox.ai"
        )

        return cls(
            base_url=base_url,
            auth_token=token,
            model=model,
            small_model=small_model,
            claude_home=expanded_home,
            account_label=account_label,
        )

    @classmethod
    def for_account(
        cls,
        account_name: str,
        provider_config: dict | None = None,
    ) -> "CcgEnv":
        """Create CcgEnv for a specific account (ccg or ccg2).

        Args:
            account_name: Provider name ("ccg" or "ccg2")
            provider_config: Optional config from .g3/config.yaml providers section
                Supports: auth_token, base_url, model, small_model, claude_home, account_label

        Returns:
            CcgEnv with the appropriate token and settings
        """
        provider_config = provider_config or {}

        # Map provider name to token env var and defaults
        if account_name == "ccg2":
            token_env_names = ("BLACKBOX_ACCOUNT_B_TOKEN",)
            default_home = "~/.claude-glm-b"
            default_label = "blackbox-b"
        else:  # ccg (default)
            token_env_names = ("ANTHROPIC_AUTH_TOKEN", "BLACKBOX_ACCOUNT_A_TOKEN")
            default_home = "~/.claude-glm-a"
            default_label = "blackbox-a"

        # Token priority: config > account-specific env var only
        # Do NOT fall back to other account tokens - each account must have its own token
        token = (
            provider_config.get("auth_token")
            or next((os.environ.get(env_name, "") for env_name in token_env_names if os.environ.get(env_name)), "")
            or ""
        )

        # Use provider config values if available, otherwise defaults
        claude_home = provider_config.get("claude_home", default_home)
        account_label = provider_config.get("account_label", default_label)

        # Base URL and model: config > env > defaults
        base_url = (
            provider_config.get("base_url")
            or os.environ.get("ANTHROPIC_BASE_URL")
            or "https://api.blackbox.ai"
        )
        model = (
            provider_config.get("model")
            or os.environ.get("ANTHROPIC_MODEL")
            or "blackboxai/z-ai/glm-5"
        )
        small_model = (
            provider_config.get("small_model")
            or os.environ.get("ANTHROPIC_SMALL_FAST_MODEL")
            or "minimax-2.5"
        )

        return cls(
            base_url=base_url,
            auth_token=token,
            model=model,
            small_model=small_model,
            claude_home=os.path.expanduser(claude_home),
            account_label=account_label,
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
    player_provider: str = "ccg"  # "ccg" | "ccg2" | "claude" | "codex"
    coach_provider: str = "ccg"   # "ccg" | "ccg2" | "claude" | "codex"
    player_model: str = ""        # model for player (empty = provider default)
    batch_mode: bool = False      # --batch / G3_BATCH_MODE
    batch_pre_judge_attempts: int = 3
    batch_judge_attempts: int = 1
    batch_post_judge_attempts: int = 1
    agent_a_workspace: str = "g"
    agent_b_workspace: str = "g1"
    worktree_mode: str = "auto"
    max_rounds: int = 3
    timeout_s: int = 600
    run_tests: bool = True
    run_bug_detection: bool = True
    judge: str = "claude"
    agent_a: str = "ccg"
    agent_b: str = "ccg2"
    ask_feedback: bool = False

    # TDD Mode (Phase 2)
    tdd_mode: bool = False
    test_command: str = ""  # empty = auto-detect
    test_timeout_s: int = 60

    # Code Review (Phase 3)
    code_review: bool = False
    review_provider: str = ""  # empty = auto-detect (codex if available, else coach)
    review_model: str = ""

    # Coach fallback (Phase 7)
    coach_retry_max: int = 2
    coach_fallback_provider: str = "claude"
    coach_fallback_model: str = ""

    # Context Management
    context_limit: int = 110_000
    compact_threshold: float = 0.85
    max_continuation_attempts: int = 2
    player_turns_per_session: int = 8  # max CCG internal turns per player call


@dataclass
class ProviderConfig:
    """Compatibility shim for orchestrator/provider configuration."""
    type: str = ""
    config: dict | None = None


ResolvedConfig = Config


# Known context window sizes (tokens) by model name pattern.
# Matched in order — first substring hit wins.
_MODEL_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("claude-opus-4",    200_000),
    ("claude-sonnet-4",  200_000),
    ("claude-haiku-4",   200_000),
    ("claude-3-5",       200_000),
    ("claude-3",         200_000),
    ("glm-5",             98_000),
    ("kimi-k2",          131_072),
    ("kimi",             128_000),
    ("gpt-5",            128_000),
    ("gpt-4o",           128_000),
    ("gpt-4",            128_000),
    ("codex",            128_000),
    ("minimax",           40_960),
]


def get_context_window(model: str) -> int:
    """Return the context window size for a model, or 0 if unknown."""
    lower = model.lower()
    for pattern, size in _MODEL_CONTEXT_WINDOWS:
        if pattern in lower:
            return size
    return 0


def short_model_name(model: str) -> str:
    """Get short display name from a model string."""
    m = model.lower()
    if m == "gpt-5.4-medium":
        return "GPT-5.4 MED"
    if m == "gpt-5.4-high":
        return "GPT-5.4 HIGH"
    if m == "gpt-5.4-ultra-high":
        return "GPT-5.4 ULTRA"
    if "opus" in m:
        return "OPUS"
    if "sonnet" in m:
        return "SONNET"
    if "haiku" in m:
        return "HAIKU"
    if "gpt-5.4" in m:
        return "GPT-5.4"
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


def _config_search_paths(working_dir: str | Path = ".") -> list[Path]:
    """Return config.yaml candidates from bundled -> global -> working dir."""
    working_path = Path(working_dir).expanduser().resolve()
    bundled_root = Path(__file__).resolve().parent.parent

    candidates = [
        bundled_root / ".g3" / "config.yaml",
        Path("~/.g3/config.yaml").expanduser(),
        working_path / ".g3" / "config.yaml",
    ]

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(candidate)
    return deduped


def load_merged_settings(working_dir: str | Path = ".") -> dict:
    """Merge config files from global -> bundled -> working dir."""
    merged: dict = {}
    for path in _config_search_paths(working_dir):
        merged.update(_load_yaml(path))
    return merged


def load_provider_configs(working_dir: str | Path = ".") -> dict:
    """Merge provider configs from global -> bundled -> working dir."""
    providers: dict = {}
    for path in _config_search_paths(working_dir):
        data = _load_yaml(path)
        raw_providers = data.get("providers", {})
        if isinstance(raw_providers, dict):
            for name, config in raw_providers.items():
                if isinstance(config, dict):
                    existing = providers.get(name, {})
                    providers[name] = {**existing, **config}
    return providers


def resolve_config(cli_args: dict) -> Config:
    """Merge: defaults -> .g3/config.yaml -> env -> CLI args."""
    defaults = {}
    working_dir = cli_args.get("working_dir") or "."
    working_dir = str(Path(working_dir).expanduser().resolve())

    # Load merged config: global -> bundled -> working dir
    project = load_merged_settings(working_dir)
    defaults.update(project.get("defaults", {}))

    # Env overrides
    env_map = {
        "G3_MAX_TURNS": ("max_turns", int),
        "G3_AUTONOMOUS": ("autonomous", lambda x: x.lower() == "true"),
        "G3_PLAYER_PROVIDER": ("player_provider", str),
        "G3_COACH_PROVIDER": ("coach_provider", str),
        "G3_PLAYER_MODEL": ("player_model", str),
        "G3_COACH_MODEL": ("coach_model", str),
        "G3_BATCH_MODE": ("batch_mode", lambda x: x.lower() in ("true", "1", "yes")),
        "G3_BATCH_PRE_JUDGE_ATTEMPTS": ("batch_pre_judge_attempts", int),
        "G3_BATCH_JUDGE_ATTEMPTS": ("batch_judge_attempts", int),
        "G3_BATCH_POST_JUDGE_ATTEMPTS": ("batch_post_judge_attempts", int),
        # TDD Mode
        "G3_TDD_MODE": ("tdd_mode", lambda x: x.lower() in ("true", "1", "yes")),
        "G3_TEST_COMMAND": ("test_command", str),
        "G3_TEST_TIMEOUT_S": ("test_timeout_s", int),
        # Code Review
        "G3_CODE_REVIEW": ("code_review", lambda x: x.lower() in ("true", "1", "yes")),
        "G3_REVIEW_PROVIDER": ("review_provider", str),
        "G3_REVIEW_MODEL": ("review_model", str),
        # Coach fallback
        "G3_COACH_RETRY_MAX": ("coach_retry_max", int),
        "G3_COACH_FALLBACK_PROVIDER": ("coach_fallback_provider", str),
        "G3_COACH_FALLBACK_MODEL": ("coach_fallback_model", str),
        # Context Management
        "G3_CONTEXT_LIMIT": ("context_limit", int),
        "G3_COMPACT_THRESHOLD": ("compact_threshold", float),
        "G3_MAX_CONTINUATION_ATTEMPTS": ("max_continuation_attempts", int),
        "G3_PLAYER_TURNS_PER_SESSION": ("player_turns_per_session", int),
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
