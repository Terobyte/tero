"""Configuration: defaults -> .g3/config.yaml -> env -> CLI args."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.constants import (
    DEFAULT_MAX_TURNS,
    DEFAULT_MAX_ROUNDS,
    DEFAULT_PLAYER_TIMEOUT_S,
    DEFAULT_COACH_TIMEOUT_S,
    DEFAULT_DUEL_TIMEOUT_S,
    DEFAULT_CHAIN_RETRY_WAIT_S,
    DEFAULT_CHAIN_MAX_RETRIES,
    DEFAULT_BATCH_PRE_JUDGE_ATTEMPTS,
    DEFAULT_BATCH_JUDGE_ATTEMPTS,
    DEFAULT_BATCH_POST_JUDGE_ATTEMPTS,
    DEFAULT_COACH_RETRY_MAX,
    DEFAULT_MAX_CONTINUATION_ATTEMPTS,
    DEFAULT_MAX_REVIEW_ITERATIONS,
    DEFAULT_COMPACT_THRESHOLD,
    DEFAULT_CONTEXT_LIMIT,
    DEFAULT_DEBUG_LIMIT_VALUE,
    DEFAULT_DEBUG_VICTORY_THRESHOLD,
    DEFAULT_LDB_LIMIT_VALUE,
    DEFAULT_LDB_TIMEOUT_S,
    EXIT_AGENT_TIMEOUT,
)


_UNSAFE_GLOBAL_DEFAULT_KEYS = {
    "code_review",
    "claude_home",
}


def _read_export_from_zshrc(env_name: str) -> str:
    """Best-effort fallback for keys exported only in ~/.zshrc."""
    zshrc_path = Path.home() / ".zshrc"
    if not zshrc_path.exists():
        return ""

    prefix = f"export {env_name}="
    try:
        for raw_line in reversed(zshrc_path.read_text().splitlines()):
            line = raw_line.strip()
            if not line.startswith(prefix):
                continue

            value = line[len(prefix) :].strip()
            if not value:
                return ""

            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                # Strip quotes — # inside quotes is preserved
                value = value[1:-1]
            elif "#" in value:
                # Only strip comment if value is not fully quoted
                value = value.split("#", 1)[0].rstrip()
            return value
    except OSError:
        return ""

    return ""


@dataclass
class Config:
    """Resolved configuration."""

    max_turns: int = DEFAULT_MAX_TURNS
    autonomous: bool = False
    verbose: bool = False
    plan_file: str = "requirements.md"
    working_dir: str = "."
    player_timeout_s: int = DEFAULT_PLAYER_TIMEOUT_S
    coach_timeout_s: int = DEFAULT_COACH_TIMEOUT_S
    claude_home: str = "~/.claude-zai"
    coach_model: str = ""  # empty = use default model from env

    # Provider selection (NEW)
    player_provider: str = "zai"  # "zai" | "claude" | "codex" | "opencode" | "kilo"
    coach_provider: str = "zai"  # "zai" | "claude" | "codex" | "opencode" | "kilo"
    player_model: str = ""  # model for player (empty = provider default)
    batch_pre_judge_attempts: int = DEFAULT_BATCH_PRE_JUDGE_ATTEMPTS
    batch_judge_attempts: int = DEFAULT_BATCH_JUDGE_ATTEMPTS
    batch_post_judge_attempts: int = DEFAULT_BATCH_POST_JUDGE_ATTEMPTS
    agent_a_workspace: str = "g"
    agent_b_workspace: str = "g1"
    worktree_mode: str = "auto"
    max_rounds: int = DEFAULT_MAX_ROUNDS
    timeout_s: int = DEFAULT_DUEL_TIMEOUT_S
    run_tests: bool = True
    run_bug_detection: bool = True
    run_lint: bool = True
    run_types: bool = True
    run_compile: bool = True
    judge: str = "claude"
    agent_a: str = "zai"
    agent_b: str = "zai"
    ask_feedback: bool = False

    # Code Review (Phase 3)
    code_review: bool = False
    review_provider: str = ""  # empty = follow coach_provider
    review_model: str = ""

    # Coach fallback (Phase 7)
    coach_retry_max: int = DEFAULT_COACH_RETRY_MAX
    coach_fallback_provider: str = "claude"
    coach_fallback_model: str = ""

    # Context Management
    context_limit: int = DEFAULT_CONTEXT_LIMIT
    compact_threshold: float = DEFAULT_COMPACT_THRESHOLD
    max_continuation_attempts: int = DEFAULT_MAX_CONTINUATION_ATTEMPTS

    # Batch role providers + models (configurable per slot)
    batch_pre_provider: str = "zai"
    batch_pre_model: str = ""  # fixed provider default
    batch_judge_provider: str = "codex"  # native Codex CLI judge by default
    batch_judge_model: str = "gpt-5.4"  # pin judge to gpt-5.4; reasoning effort
    # is forced to "medium" by the codex provider factory (see providers/registry.py)
    batch_post_provider: str = "zai"
    batch_post_model: str = ""  # fixed provider default

    # Code review loop
    max_review_iterations: int = DEFAULT_MAX_REVIEW_ITERATIONS

    # Provider fallback chain
    player_fallback_chain: str = ""  # comma-separated: "codex,zai"
    coach_fallback_chain: str = ""  # comma-separated: "codex,zai"
    chain_retry_wait_s: float = DEFAULT_CHAIN_RETRY_WAIT_S
    chain_max_retries: int = DEFAULT_CHAIN_MAX_RETRIES
    debug_player_provider: str = "zai"
    debug_tester_provider: str = "claude"
    debug_fixer_provider: str = "codex"
    debug_synthesizer_provider: str = "opencode"
    debug_player_model: str = ""
    debug_tester_model: str = ""
    debug_fixer_model: str = ""
    debug_synthesizer_model: str = ""
    debug_intensity: str = "medium"
    debug_limit_mode: str = "infinite"
    debug_limit_value: int = DEFAULT_DEBUG_LIMIT_VALUE
    debug_victory_threshold: int = DEFAULT_DEBUG_VICTORY_THRESHOLD
    debug_failing_test: str = ""
    debug_level: str = "block"
    debug_file: str = ""
    debug_entry: str = ""
    debug_all: bool = False

    ldb_input_provider: str = "claude"
    ldb_player_provider: str = "claude"
    ldb_tester_provider: str = "claude"
    ldb_fixer_provider: str = "codex"
    ldb_input_model: str = ""
    ldb_player_model: str = ""
    ldb_tester_model: str = ""
    ldb_fixer_model: str = ""
    ldb_mode: int = 2
    ldb_target_file: str = ""
    ldb_target_entry: str = ""
    ldb_test_input: str = ""
    ldb_scope_all: bool = False
    ldb_max_iterations: int = DEFAULT_LDB_LIMIT_VALUE
    ldb_timeout_s: int = DEFAULT_LDB_TIMEOUT_S


@dataclass
class ProviderConfig:
    """Compatibility shim for orchestrator/provider configuration."""

    name: str = ""
    type: str = ""
    config: dict | None = None


ResolvedConfig = Config


_ENV_MAP = {
    "G3_MAX_TURNS": ("max_turns", int),
    "G3_AUTONOMOUS": ("autonomous", lambda x: x.lower() == "true"),
    "G3_PLAYER_PROVIDER": ("player_provider", str),
    "G3_COACH_PROVIDER": ("coach_provider", str),
    "G3_PLAYER_MODEL": ("player_model", str),
    "G3_COACH_MODEL": ("coach_model", str),
    "G3_BATCH_PRE_JUDGE_ATTEMPTS": ("batch_pre_judge_attempts", int),
    "G3_BATCH_JUDGE_ATTEMPTS": ("batch_judge_attempts", int),
    "G3_BATCH_POST_JUDGE_ATTEMPTS": ("batch_post_judge_attempts", int),
    # Code Review
    "G3_CODE_REVIEW": ("code_review", lambda x: x.lower() in ("true", "1", "yes")),
    "G3_REVIEW_PROVIDER": ("review_provider", str),
    "G3_REVIEW_MODEL": ("review_model", str),
    # Coach fallback
    "G3_COACH_RETRY_MAX": ("coach_retry_max", int),
    "G3_COACH_FALLBACK_PROVIDER": ("coach_fallback_provider", str),
    "G3_COACH_FALLBACK_MODEL": ("coach_fallback_model", str),
    # Batch roles
    "G3_BATCH_PRE_PROVIDER": ("batch_pre_provider", str),
    "G3_BATCH_PRE_MODEL": ("batch_pre_model", str),
    "G3_BATCH_JUDGE_PROVIDER": ("batch_judge_provider", str),
    "G3_BATCH_JUDGE_MODEL": ("batch_judge_model", str),
    "G3_BATCH_POST_PROVIDER": ("batch_post_provider", str),
    "G3_BATCH_POST_MODEL": ("batch_post_model", str),
    "G3_MAX_REVIEW_ITERATIONS": ("max_review_iterations", int),
    # Context management
    "G3_CONTEXT_LIMIT": ("context_limit", int),
    "G3_COMPACT_THRESHOLD": ("compact_threshold", float),
    "G3_MAX_CONTINUATION_ATTEMPTS": ("max_continuation_attempts", int),
    # Provider fallback chain
    "G3_PLAYER_FALLBACK_CHAIN": ("player_fallback_chain", str),
    "G3_COACH_FALLBACK_CHAIN": ("coach_fallback_chain", str),
    "G3_CHAIN_RETRY_WAIT_S": ("chain_retry_wait_s", float),
    "G3_CHAIN_MAX_RETRIES": ("chain_max_retries", int),
    # Debugger
    "G3_DEBUG_PLAYER_PROVIDER": ("debug_player_provider", str),
    "G3_DEBUG_TESTER_PROVIDER": ("debug_tester_provider", str),
    "G3_DEBUG_FIXER_PROVIDER": ("debug_fixer_provider", str),
    "G3_DEBUG_SYNTHESIZER_PROVIDER": ("debug_synthesizer_provider", str),
    "G3_DEBUG_PLAYER_MODEL": ("debug_player_model", str),
    "G3_DEBUG_TESTER_MODEL": ("debug_tester_model", str),
    "G3_DEBUG_FIXER_MODEL": ("debug_fixer_model", str),
    "G3_DEBUG_SYNTHESIZER_MODEL": ("debug_synthesizer_model", str),
    "G3_DEBUG_INTENSITY": ("debug_intensity", str),
    "G3_DEBUG_LIMIT_MODE": ("debug_limit_mode", str),
    "G3_DEBUG_LIMIT_VALUE": ("debug_limit_value", int),
    "G3_DEBUG_VICTORY_THRESHOLD": ("debug_victory_threshold", int),
    "G3_DEBUG_LEVEL": ("debug_level", str),
    "G3_LDB_INPUT_PROVIDER": ("ldb_input_provider", str),
    "G3_LDB_PLAYER_PROVIDER": ("ldb_player_provider", str),
    "G3_LDB_TESTER_PROVIDER": ("ldb_tester_provider", str),
    "G3_LDB_FIXER_PROVIDER": ("ldb_fixer_provider", str),
    "G3_LDB_INPUT_MODEL": ("ldb_input_model", str),
    "G3_LDB_PLAYER_MODEL": ("ldb_player_model", str),
    "G3_LDB_TESTER_MODEL": ("ldb_tester_model", str),
    "G3_LDB_FIXER_MODEL": ("ldb_fixer_model", str),
    "G3_LDB_MODE": ("ldb_mode", int),
    "G3_LDB_TARGET_FILE": ("ldb_target_file", str),
    "G3_LDB_TARGET_ENTRY": ("ldb_target_entry", str),
    "G3_LDB_TEST_INPUT": ("ldb_test_input", str),
    "G3_LDB_SCOPE_ALL": ("ldb_scope_all", lambda x: x.lower() in ("true", "1", "yes")),
    "G3_LDB_MAX_ITERATIONS": ("ldb_max_iterations", int),
    "G3_LDB_TIMEOUT_S": ("ldb_timeout_s", int),
}


# Known context window sizes (tokens) by model name pattern.
# Matched in order — first substring hit wins.
_MODEL_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("claude-opus-4", 1_000_000),
    ("claude-sonnet-4", 1_000_000),
    ("claude-haiku-4", 1_000_000),
    ("claude-3-5", 200_000),
    ("claude-3", 200_000),
    ("glm-5.1", 204_800),
    ("glm-4.7", 204_800),
    ("glm-5", 98_000),
    ("kimi-k2", 131_072),
    ("kimi", 128_000),
    ("gpt-5.4", 128_000),
    ("gpt-5", 128_000),
    ("o3", 128_000),
    ("o4-mini", 128_000),
    ("gpt-4o", 128_000),
    ("gpt-4", 128_000),
    ("codex", 1_000_000),
    ("xiaomi/mimo-v2-pro:free", 1_048_576),
    ("mimo", 131_072),
    ("minimax/minimax-m2.5:free", 262_144),
    ("minimax-m2", 1_000_000),
    ("nemotron", 131_072),
    ("minimax", 40_960),
    # Short aliases used by _MODEL_ALIASES in claude_native.py
    ("opus", 1_000_000),
    ("sonnet", 1_000_000),
    ("haiku", 1_000_000),
]


def get_context_window(model: str) -> int:
    """Return the context window size for a model, or 0 if unknown."""
    lower = model.lower()
    for pattern, size in _MODEL_CONTEXT_WINDOWS:
        if pattern in lower:
            return size
    return 0


# Sentinel: context_limit == this value means "auto-detect from model window"
_DEFAULT_CONTEXT_LIMIT = DEFAULT_CONTEXT_LIMIT


def get_effective_context_limit(
    model: str, configured_limit: int, provider=None
) -> int:
    """Derive context limit from model window, unless user explicitly overrode."""
    window = get_context_window(model)
    if configured_limit != _DEFAULT_CONTEXT_LIMIT:
        # User/menu set it — respect, but cap to model's actual window when known.
        # Prevents e.g. "1M" setting from being used with a 204K z.ai model.
        if window > 0:
            return min(configured_limit, window)
        return configured_limit
    if window > 0:
        return window  # full window — compact_threshold handles the reduction
    # Fallback: derive from provider class name (e.g. CodexProvider → "codexprovider"
    # contains "codex" as substring → 1M). Handles providers with empty default_model.
    if provider is not None:
        cls_hint = type(provider).__name__.lower()
        window = get_context_window(cls_hint)
        if window > 0:
            return window
    return _DEFAULT_CONTEXT_LIMIT  # unknown model and no provider hint — safe fallback


def _normalize_provider_name(value: str) -> str:
    """Normalize supported Z.AI shorthands onto the canonical provider name."""
    aliases = {
        "z.ai": "zai",
        "glm51": "zai",
        "glm-51": "zai",
        "glm-5.1": "zai",
        "glm-5.1-zai": "zai",
        "glm47": "zai",
        "glm-47": "zai",
        "glm-4.7": "zai",
        "glm47lite": "zai",
        "glm-4.7-lite": "zai",
        "lite": "zai",
        "glm5turbo": "zai",
        "glm-5turbo": "zai",
        "glm-5-turbo": "zai",
    }
    return aliases.get(value, value)


def short_model_name(model: str) -> str:
    """Get short display name from a model string."""
    m = model.lower()
    if not m or m == "default":
        return "DEFAULT"
    if m == "o3":
        return "o3"
    if m == "o4-mini":
        return "o4-mini"
    if "opus" in m:
        return "OPUS"
    if "sonnet" in m:
        return "SONNET"
    if "haiku" in m:
        return "HAIKU"
    if "gpt-5.4" in m:
        return "GPT-5.4"
    if "glm-5.1" in m:
        return "GLM-5.1"
    if "glm-4.7" in m:
        return "GLM-4.7"
    if "glm-5-turbo" in m:
        return "GLM-5-T"
    if "mimo" in m:
        return "MIMO"
    if "minimax" in m:
        return "MINIMAX"
    if "glm" in m:
        glm_pos = m.find("glm")
        rest = m[glm_pos + 3 :].lstrip("-")
        ver = rest.split("-")[0]
        return f"GLM-{ver}" if ver else "GLM"
    if "kimi" in m:
        return "KIMI"
    return model.split("/")[-1].upper()[:10]


def _load_yaml(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    return {}


def _config_search_paths(
    working_dir: str | Path = ".",
    *,
    include_global: bool = True,
) -> list[Path]:
    """Return config.yaml candidates from bundled -> optional global -> working dir."""
    working_path = Path(working_dir).expanduser().resolve()
    bundled_root = Path(__file__).resolve().parent.parent

    candidates = [
        bundled_root / ".g3" / "config.yaml",
        working_path / ".g3" / "config.yaml",
    ]
    if include_global:
        candidates.insert(1, Path("~/.g3/config.yaml").expanduser())

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(candidate)
    return deduped


def load_merged_settings(
    working_dir: str | Path = ".",
    *,
    include_global: bool = False,
) -> dict:
    """Merge config defaults from bundled -> optional global -> working dir."""
    merged: dict = {}
    for path in _config_search_paths(working_dir, include_global=include_global):
        merged.update(_load_yaml(path))
    return merged


def _load_defaults_section(path: Path) -> dict:
    """Load just the defaults section from one config file."""
    data = _load_yaml(path)
    defaults = data.get("defaults", {})
    return defaults if isinstance(defaults, dict) else {}


def _filter_global_defaults(defaults: dict) -> dict:
    """Drop global defaults that should not silently change per-project runtime mode."""
    return {
        key: value
        for key, value in defaults.items()
        if key not in _UNSAFE_GLOBAL_DEFAULT_KEYS
    }


def load_provider_configs(
    working_dir: str | Path = ".",
    *,
    include_global: bool = True,
) -> dict:
    """Merge provider configs from bundled -> optional global -> working dir."""
    providers: dict = {}
    for path in _config_search_paths(working_dir, include_global=include_global):
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

    bundled_root = Path(__file__).resolve().parent.parent
    bundled_config = bundled_root / ".g3" / "config.yaml"
    global_config = Path("~/.g3/config.yaml").expanduser()
    project_config = Path(working_dir) / ".g3" / "config.yaml"

    # Merge defaults in source order so projects can still override global user
    # preferences, but global execution-mode toggles do not silently alter runs.
    defaults.update(_load_defaults_section(bundled_config))
    defaults.update(_filter_global_defaults(_load_defaults_section(global_config)))
    defaults.update(_load_defaults_section(project_config))

    # Env overrides
    for env_key, (cfg_key, conv) in _ENV_MAP.items():
        if val := os.environ.get(env_key):
            defaults[cfg_key] = conv(val)

    # CLI overrides (highest priority, skip None)
    defaults.update({k: v for k, v in cli_args.items() if v is not None})
    defaults["working_dir"] = working_dir

    for key in (
        "player_provider",
        "coach_provider",
        "agent_a",
        "agent_b",
        "batch_pre_provider",
        "batch_judge_provider",
        "batch_post_provider",
        "coach_fallback_provider",
        "review_provider",
        "debug_player_provider",
        "debug_tester_provider",
        "debug_fixer_provider",
        "debug_synthesizer_provider",
        "ldb_input_provider",
        "ldb_player_provider",
        "ldb_tester_provider",
        "ldb_fixer_provider",
    ):
        if key in defaults and defaults[key]:
            defaults[key] = _normalize_provider_name(str(defaults[key]))

    # Provider config
    project = load_merged_settings(working_dir, include_global=True)
    provider = project.get("provider", {})
    # Any top-level key in the project config that is NOT a known section header
    # is a candidate for a user typo (e.g. "runn_tests" instead of "run_tests").
    # Collect them into defaults so the unknown-key warning can catch them.
    _KNOWN_SECTIONS = {"defaults", "provider"}
    for key, val in project.items():
        if key not in _KNOWN_SECTIONS and key not in defaults:
            defaults[key] = val

    valid_fields = set(Config.__dataclass_fields__)
    unknown = set(defaults) - valid_fields
    if unknown:
        import warnings

        warnings.warn(
            f"Unknown config keys (possible typos): {sorted(unknown)}",
            UserWarning,
            stacklevel=2,
        )
    return Config(**{k: v for k, v in defaults.items() if k in valid_fields})
