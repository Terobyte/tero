"""Canonical thinking/effort levels shared by providers and the menu."""

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

EFFORT_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra High",
    "max": "Max",
}

PROVIDERS_WITH_EFFORT = frozenset(
    {"claude", "muse", "codex", "cursor", "opencode"}
)

_ALIASES = {
    "extra high": "xhigh",
    "extra-high": "xhigh",
    "x-high": "xhigh",
    "xl": "xhigh",
}


def normalize_effort(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    raw = _ALIASES.get(raw, raw)
    return raw if raw in EFFORT_LEVELS else ""


def provider_supports_effort(provider: str) -> bool:
    return (provider or "") in PROVIDERS_WITH_EFFORT


def effort_field_for(model_field: str) -> str:
    if model_field.endswith("_model"):
        return f"{model_field[:-6]}_effort"
    return ""


def cursor_model_arg(model: str, effort: str) -> str:
    """Cursor encodes effort as `--model id[effort=high]`."""
    resolved = normalize_effort(effort)
    if not model or not resolved or "[" in model:
        return model
    return f"{model}[effort={resolved}]"
