"""Configuration: defaults -> .tero/config.yaml -> env -> CLI args."""

from __future__ import annotations

import dataclasses
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

KNOWN_PROVIDERS = ("muse", "claude", "codex", "gemini", "opencode", "cursor", "agy")

_ALIASES = {
    "cursor-cli": "cursor",
    "cursor-headless": "cursor",
    "headless": "cursor",
}


class UnknownProviderError(ValueError):
    """Provider name is not in the live set and is not a known alias."""


class ConfigError(ValueError):
    """Invalid config file or setting."""


def normalize_provider(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        raise UnknownProviderError("empty provider name")
    canonical = _ALIASES.get(raw, raw)
    if canonical not in KNOWN_PROVIDERS:
        raise UnknownProviderError(canonical)
    return canonical


def split_role_spec(value: str) -> tuple[str, str]:
    """Parse ``provider`` or ``provider:model``."""
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if ":" in raw:
        provider, model = raw.split(":", 1)
        return provider.strip(), model.strip()
    return raw, ""


_FAMILY_BY_PROVIDER = {
    "agy": "gemini",
    "claude": "claude",
    "gemini": "gemini",
    "codex": "openai",
    "muse": "muse",
}

_CLAUDE_BARE = re.compile(r"(?<![a-z])(opus|sonnet|haiku|fable)(?![a-z])")
_OPENAI_O_SERIES = re.compile(r"(?<![a-z0-9])o\d+(?![a-z0-9])")
_FREE_SUFFIX = re.compile(r"[-:]free$")

def family_of(spec: str) -> str:
    """Model family for a ``provider`` or ``provider:model`` spec.

    Classification follows the model first (an ``opencode``/``agy`` spec can
    serve a model from any family) and falls back to the provider when the
    model is empty or unrecognised (e.g. ``codex`` with its default model).
    """
    provider, model = split_role_spec(spec or "")
    m = model.strip().lower()
    if "claude" in m or _CLAUDE_BARE.search(m):
        return "claude"
    if "gemini" in m:
        return "gemini"
    if "gpt" in m or "openai" in m or _OPENAI_O_SERIES.search(m):
        return "openai"
    if "glm" in m:
        return "glm"
    if "muse" in m or "spark" in m:
        return "muse"
    return _FAMILY_BY_PROVIDER.get(provider.strip().lower(), "other")


def family_key(spec: str) -> str:
    """What "same family" compares: the family, or ``other:<vendor>`` for the rest.

    Free models rotate, so the vendor comes from the model name itself
    (``mimo-v2.6-flash-free`` → ``mimo``), not from a list of known names.
    """
    family = family_of(spec)
    if family != "other":
        return family
    provider, model = split_role_spec(spec or "")
    p = provider.strip().lower()
    name = _FREE_SUFFIX.sub("", model.strip().lower().rsplit("/", 1)[-1])
    if name.startswith(p + "-"):
        name = name[len(p) + 1 :]
    vendor = re.match(r"[a-z]+", name)
    return f"other:{vendor.group(0) if vendor else p}"


@dataclass
class Config:
    """Resolved configuration (spec §6.1)."""

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
    escalate_at_round: int = 0
    escalation: str = "round"  # round | situational
    sonnet_max: int = 2  # лимит Sonnet при situational
    test_command: str = ""
    test_command_scoped: str = ""
    gate_scope: str = ""
    hud: bool = True
    player_timeout_s: int = 3600
    judge_timeout_s: int = 1200
    tester_timeout_s: int = 300
    test_timeout_s: int = 900
    stall_timeout_s: int = 900
    max_rounds: int = 15
    verbose: bool = False
    compact_player_at_tokens: int = 0
    compact_judge_at_tokens: int = 0
    decisions: bool = False
    no_tester: bool = False
    human_timeout_s: int = 900
    agent_timeout_s: int = 1800
    autopilot_grace_s: int = 900
    wake_command: str = ""
    jev_enabled: bool = False
    phone_text_answers: bool = True


from src.config_env import _ENV_MAP, apply_env  # noqa: E402,F401
from src.config_names import short_model_name  # noqa: E402,F401

__all__ = ["_ENV_MAP", "apply_env", "short_model_name"]

# Roles whose empty provider/model/effort means "not set, keep default".
# Escalation is excluded: "" there is a valid value (escalation off).
_BLANK_MEANS_UNSET_PREFIXES = ("player_", "judge_", "tester_")

_ROLE_PROVIDER_FIELDS = (
    "player_provider",
    "judge_provider",
    "tester_provider",
    "escalation_provider",
)

_CLI_SPEC_FIELDS = (
    ("player_spec", "player_provider", "player_model"),
    ("judge_spec", "judge_provider", "judge_model"),
    ("tester_spec", "tester_provider", "tester_model"),
    ("escalation_spec", "escalation_provider", "escalation_model"),
)


def _load_yaml(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" (line {mark.line + 1})" if mark else ""
            raise ConfigError(f"{path}: invalid YAML{where}") from exc
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
        bundled_root / ".tero" / "config.yaml",
        working_path / ".tero" / "config.yaml",
    ]
    if include_global:
        candidates.insert(1, Path("~/.tero/config.yaml").expanduser())

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(candidate)
    return deduped


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


def _blank_means_unset(field: str) -> bool:
    return field.startswith(_BLANK_MEANS_UNSET_PREFIXES)


def _is_unset(key: str, value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        if _blank_means_unset(key):
            return True
        field = Config.__dataclass_fields__.get(key)
        if (
            field is not None
            and isinstance(field.default, int)
            and not isinstance(field.default, bool)
        ):
            return True
    return False


_POSITIVE_FIELDS = {
    "max_rounds",
    "player_timeout_s",
    "judge_timeout_s",
    "tester_timeout_s",
    "test_timeout_s",
}

_NON_NEGATIVE_FIELDS = {
    "stall_timeout_s",
    "compact_player_at_tokens",
    "compact_judge_at_tokens",
}


def _coerce_value(field: str, value: Any, fallback: Any) -> Any:
    """Coerce a yaml/env value to the dataclass field type; "" stays ""."""
    if value is None:
        return fallback
    if isinstance(value, str) and value == "":
        if isinstance(fallback, int) and not isinstance(fallback, bool):
            return fallback
        return value
    if isinstance(fallback, bool) and isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    if isinstance(fallback, int) and not isinstance(fallback, bool):
        if isinstance(value, bool):
            raise ConfigError(f"{field}: expected a number, got {value!r}")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{field}: expected a number, got {value!r}") from None
        if field in _POSITIVE_FIELDS and number < 1:
            raise ConfigError(f"{field}: must be at least 1, got {number}")
        if field in _NON_NEGATIVE_FIELDS and number < 0:
            raise ConfigError(f"{field}: must be at least 0, got {number}")
        return number
    if isinstance(fallback, str):
        return str(value)
    return value


def resolve_config(cli_args: dict) -> Config:
    """Merge: dataclass defaults -> bundled/global/project yaml -> env -> CLI args."""
    valid_fields = {f.name for f in dataclasses.fields(Config)}
    merged: dict = {}

    cli = dict(cli_args or {})
    working_dir = cli.get("working_dir") or "."
    working_dir = str(Path(working_dir).expanduser().resolve())

    bundled_root = Path(__file__).resolve().parent.parent
    bundled_config = bundled_root / ".tero" / "config.yaml"
    global_config = Path("~/.tero/config.yaml").expanduser()
    project_config = Path(working_dir) / ".tero" / "config.yaml"

    # Merge defaults in source order: bundled -> global -> project.
    unknown: set[str] = set()
    seen: set[Path] = set()
    for path in (bundled_config, global_config, project_config):
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        data = _load_yaml(path)
        if isinstance(data, dict):
            extra = [k for k in data.keys() if k not in ("defaults", "providers")]
            if extra:
                labeled = [
                    f"{k} → перенеси в defaults:" if k in valid_fields else str(k)
                    for k in sorted(extra, key=str)
                ]
                warnings.warn(
                    f"{path}: ключи вне defaults: не читаются: [{', '.join(labeled)}]",
                    UserWarning,
                    stacklevel=2,
                )
        defaults_section = data.get("defaults", {}) if isinstance(data, dict) else {}
        if not isinstance(defaults_section, dict):
            defaults_section = {}
        for key, value in defaults_section.items():
            if key not in valid_fields:
                unknown.add(key)
                continue
            if _is_unset(key, value):
                continue
            merged[key] = value

    # Env overrides
    apply_env(merged, normalize_provider)

    # CLI overrides (highest priority, skip None)
    for key, value in cli.items():
        if value is None or key not in valid_fields:
            continue
        if _is_unset(key, value):
            continue
        if key != "working_dir":
            merged[key] = value
    merged["working_dir"] = working_dir

    for key in _ROLE_PROVIDER_FIELDS:
        if merged.get(key):
            merged[key] = normalize_provider(str(merged[key]))
    if merged.get("escalation_provider") == "":
        merged["escalation_model"] = ""
        merged["escalation_effort"] = ""

    # Unknown keys: warn once with the list and ignore (legacy yaml tolerated).
    if unknown:
        warnings.warn(
            f"Unknown config keys (possible typos): {sorted(unknown)}",
            UserWarning,
            stacklevel=2,
        )

    base = Config()
    coerced = {k: _coerce_value(k, v, getattr(base, k)) for k, v in merged.items() if k in valid_fields}
    return Config(**{**dataclasses.asdict(base), **coerced})


def apply_cli_args(config: Config, args: Any) -> Config:
    """Apply argparse role/test/plan flags on top of an already-resolved Config."""
    cfg = config
    if getattr(args, "no_hud", False):
        cfg = dataclasses.replace(cfg, hud=False)
    plan = getattr(args, "plan", None)
    if plan:
        cfg = dataclasses.replace(cfg, plan_file=plan)
    verbose = getattr(args, "verbose", None)
    if verbose:
        cfg = dataclasses.replace(cfg, verbose=True)
    test_command = getattr(args, "test_command", None)
    if test_command is not None:
        cfg = dataclasses.replace(cfg, test_command=test_command)
    test_command_scoped = getattr(args, "test_command_scoped", None)
    if test_command_scoped is not None:
        cfg = dataclasses.replace(cfg, test_command_scoped=test_command_scoped)
    decisions = getattr(args, "decisions", False)
    if decisions or getattr(args, "detach", False) or getattr(args, "owner", False):
        cfg = dataclasses.replace(cfg, decisions=True)
    max_rounds = getattr(args, "max_rounds", None)
    if max_rounds is not None:
        cfg = dataclasses.replace(cfg, max_rounds=_coerce_value("max_rounds", max_rounds, 0))

    if getattr(args, "escalate_at", None) is not None:
        cfg = dataclasses.replace(cfg, escalate_at_round=int(args.escalate_at))
    if getattr(args, "no_tester", False):
        cfg = dataclasses.replace(cfg, no_tester=True)

    if getattr(args, "no_escalation", False):
        cfg = dataclasses.replace(cfg, escalation_provider="", escalation_model="", escalation_effort="")

    for spec_attr, provider_field, model_field in _CLI_SPEC_FIELDS:
        if spec_attr == "escalation_spec" and getattr(args, "no_escalation", False):
            continue
        provider, model = split_role_spec(getattr(args, spec_attr, None) or "")
        if provider:
            effort_field = provider_field.replace("_provider", "_effort")
            new_provider = normalize_provider(provider)
            updates = {provider_field: new_provider, effort_field: ""}
            if new_provider != getattr(cfg, provider_field):
                # Same as the menu: a new provider does not inherit the old model.
                updates[model_field] = ""
            cfg = dataclasses.replace(cfg, **updates)
        # Model without provider keeps whatever resolve_config/menu already set.
        if model:
            cfg = dataclasses.replace(cfg, **{model_field: model})
        elif provider and not getattr(cfg, model_field):
            # The role's own provider gets its default model; another provider
            # keeps "" and runs on that CLI's default.
            if getattr(cfg, provider_field) == getattr(Config(), provider_field):
                cfg = dataclasses.replace(cfg, **{model_field: getattr(Config(), model_field)})
    return cfg
