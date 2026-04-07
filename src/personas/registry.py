"""Persona registry for managing agent personas."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Persona:
    """A single persona loaded from a Markdown file with YAML frontmatter.

    Attributes:
        name: Unique identifier for the persona.
        description: Human-readable description of the persona.
        overlay: Markdown body (the specialist context / system-prompt overlay).
    """

    name: str
    description: str
    overlay: str


# Backward-compatible alias used elsewhere in the package.
@dataclass
class PersonaEntry:
    """A single persona definition.

    Attributes:
        name: Unique identifier for the persona.
        description: Human-readable description of the persona.
        system_prompt: System prompt fragment that shapes persona behaviour.
        traits: Optional dict of arbitrary trait key-values (tone, verbosity, etc.).
    """

    name: str
    description: str = ""
    system_prompt: str = ""
    traits: dict[str, Any] = field(default_factory=dict)


class PersonaRegistry:
    """Registry for loading, caching, and looking up personas from disk.

    Personas are stored as ``.md`` files in *dir_path*.  Each file has a YAML
    frontmatter block with at least ``name`` and ``description`` fields; the
    Markdown body becomes the persona's ``overlay`` text.
    """

    def __init__(self, dir_path: Path) -> None:
        """Initialise the registry pointing at *dir_path*.

        Args:
            dir_path: Directory containing persona ``.md`` files.
        """
        self._dir: Path = Path(dir_path)
        self._cache: dict[str, Persona] = {}

    # -- loading ------------------------------------------------------------

    def load_all(self) -> list[Persona]:
        """Scan ``_dir`` for ``.md`` files and parse each as a persona.

        Returns:
            A list of :class:`Persona` objects (also cached internally).
        """
        self._cache.clear()
        personas: list[Persona] = []
        if not self._dir.is_dir():
            return personas

        for md_file in sorted(self._dir.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            parsed = self._parse_persona_md(text)
            if parsed is not None:
                self._cache[parsed.name] = parsed
                personas.append(parsed)
        return personas

    # -- lookups ------------------------------------------------------------

    def get(self, name: str) -> Persona | None:
        """Return a cached :class:`Persona` by name, or ``None``."""
        return self._cache.get(name)

    def available_roles(self) -> list[dict[str, str]]:
        """Return ``[{name, description}, ...]`` for every cached persona."""
        return [{"name": p.name, "description": p.description} for p in self._cache.values()]

    def build_overlay(self, roles: list[str]) -> str:
        """Combine overlay text for the given *roles* (capped at 2).

        If more than 2 roles are supplied a warning is printed to *stderr*
        and only the first 2 are used.

        Args:
            roles: Persona names to include.

        Returns:
            Joined overlay strings separated by blank lines, or ``""`` if
            none of the roles are found.
        """
        if len(roles) > 2:
            print(
                f"  [PersonaRegistry] Warning: {len(roles)} roles assigned, "
                f"using first 2: {roles[:2]}",
                file=sys.stderr,
            )
        parts: list[str] = []
        for role in roles[:2]:
            persona = self._cache.get(role)
            if persona is not None:
                parts.append(persona.overlay)
        return "\n\n".join(parts)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _parse_persona_md(text: str) -> Persona | None:
        """Parse a Markdown file with YAML frontmatter into a Persona."""
        if not text.startswith("---"):
            return None
        # Split on the closing ``---`` of the frontmatter.
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        fm_text = parts[1].strip()
        body = parts[2].strip()
        try:
            meta = yaml.safe_load(fm_text)
        except yaml.YAMLError:
            return None
        if not isinstance(meta, dict):
            return None
        name = meta.get("name")
        description = meta.get("description", "")
        if not name:
            return None
        return Persona(name=str(name), description=str(description), overlay=body)
