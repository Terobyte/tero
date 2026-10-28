"""Пресеты конфига: файлы `src/presets/<name>.yaml`, секция defaults.

Пресет ложится сразу после значений dataclass — yaml, env и CLI его перекрывают.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PRESET_DIR = Path(__file__).resolve().parent / "presets"
DEFAULT_PRESET = "golden"
NONE = "none"


def preset_names() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.yaml"))


def load_preset(name: str) -> dict:
    """defaults пресета; `none` — пусто; неизвестное имя — KeyError."""
    name = (name or "").strip() or DEFAULT_PRESET
    if name == NONE:
        return {}
    path = PRESET_DIR / f"{name}.yaml"
    if not path.is_file():
        raise KeyError(name)
    data = yaml.safe_load(path.read_text()) or {}
    section = data.get("defaults") if isinstance(data, dict) else None
    return dict(section) if isinstance(section, dict) else {}



def apply_preset(merged: dict, cli_value, paths, valid_fields, error: type) -> dict:
    """Имя: CLI > env TERO_PRESET > последний yaml с preset > golden; значения — под merged."""
    import os

    name = cli_value or os.environ.get("TERO_PRESET") or ""
    for path in reversed(list(paths)):
        if name:
            break
        try:
            data = yaml.safe_load(Path(path).read_text()) if Path(path).is_file() else None
        except (OSError, yaml.YAMLError):
            data = None
        section = data.get("defaults") if isinstance(data, dict) else None
        if isinstance(section, dict) and section.get("preset"):
            name = str(section["preset"])
    name = str(name).strip() or DEFAULT_PRESET
    try:
        values = load_preset(name)
    except KeyError:
        raise error(f"unknown preset: {name}") from None
    return {**{k: v for k, v in values.items() if k in valid_fields}, **merged, "preset": name}
