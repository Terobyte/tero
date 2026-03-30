"""Tests for Persona dataclass and PersonaRegistry.

These tests target the contract described in:
  - docs/superpowers/plans/2026-03-29-persona-preplanner-plan.md  (Chunk 1, Steps 1-2)
  - docs/superpowers/specs/2026-03-29-persona-preplanner-design.md  (Section 1)
"""

import pytest
from pathlib import Path

from src.personas.registry import Persona, PersonaRegistry


# ---------------------------------------------------------------------------
# Persona dataclass tests
# ---------------------------------------------------------------------------

class TestPersona:
    """Tests for the Persona dataclass."""

    def test_create_with_all_fields(self):
        """Persona stores name, description, and overlay."""
        p = Persona(
            name="security",
            description="OWASP Top 10",
            overlay="## Specialist Context: Security\nFocus on vulns.",
        )
        assert p.name == "security"
        assert p.description == "OWASP Top 10"
        assert "Security" in p.overlay

    def test_fields_are_stored(self):
        """All three fields are plain strings with no defaults."""
        p = Persona(name="devops", description="CI/CD specialist", overlay="Docker tips")
        assert p.name == "devops"
        assert p.description == "CI/CD specialist"
        assert p.overlay == "Docker tips"


# ---------------------------------------------------------------------------
# PersonaRegistry init tests
# ---------------------------------------------------------------------------

class TestPersonaRegistryInit:
    """Tests for PersonaRegistry initialisation."""

    def test_init_with_dir(self, tmp_path):
        """Registry stores the personas_dir path."""
        registry = PersonaRegistry(tmp_path)
        assert registry._dir == tmp_path
        assert registry._cache == {}

    def test_init_accepts_path_object(self, tmp_path):
        """Registry works with pathlib.Path objects."""
        p = Path(tmp_path)
        registry = PersonaRegistry(p)
        assert registry._dir == p


# ---------------------------------------------------------------------------
# PersonaRegistry.load_all() tests
# ---------------------------------------------------------------------------

class TestPersonaRegistryLoadAll:
    """Tests for PersonaRegistry.load_all()."""

    def test_load_all_empty_dir(self, tmp_path):
        """load_all returns [] when the directory has no .md files."""
        registry = PersonaRegistry(tmp_path)
        result = registry.load_all()
        assert result == []

    def test_load_all_parses_persona_file(self, tmp_path):
        """load_all parses frontmatter + body from a persona .md file."""
        persona_file = tmp_path / "security.md"
        persona_file.write_text(
            "---\n"
            "name: security\n"
            "description: Security specialist\n"
            "---\n"
            "## Specialist Context: Security\n"
            "Focus on vulns.\n"
        )
        registry = PersonaRegistry(tmp_path)
        loaded = registry.load_all()
        assert len(loaded) == 1
        assert loaded[0].name == "security"
        assert loaded[0].description == "Security specialist"
        assert "Security" in loaded[0].overlay
        assert "vulns" in loaded[0].overlay

    def test_load_all_populates_cache(self, tmp_path):
        """load_all caches personas so get() can look them up."""
        persona_file = tmp_path / "security.md"
        persona_file.write_text(
            "---\n"
            "name: security\n"
            "description: Security specialist\n"
            "---\n"
            "## Specialist Context: Security\n"
            "Focus on vulns.\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        assert registry.get("security") is not None
        assert registry.get("security").name == "security"

    def test_load_all_ignores_non_markdown(self, tmp_path):
        """load_all skips files that are not .md."""
        (tmp_path / "notes.txt").write_text("not a persona")
        (tmp_path / "security.md").write_text(
            "---\nname: security\ndescription: desc\n---\noverlay text\n"
        )
        registry = PersonaRegistry(tmp_path)
        loaded = registry.load_all()
        assert len(loaded) == 1
        assert loaded[0].name == "security"


# ---------------------------------------------------------------------------
# PersonaRegistry.get() tests
# ---------------------------------------------------------------------------

class TestPersonaRegistryGet:
    """Tests for PersonaRegistry.get()."""

    def test_get_unknown_returns_none(self, tmp_path):
        """get returns None for unknown role names (soft fallback)."""
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        assert registry.get("nonexistent") is None

    def test_get_found_after_load(self, tmp_path):
        """get returns the Persona after load_all has cached it."""
        (tmp_path / "devops.md").write_text(
            "---\nname: devops\ndescription: CI/CD\n---\nDocker tips\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        p = registry.get("devops")
        assert p is not None
        assert p.name == "devops"
        assert p.description == "CI/CD"


# ---------------------------------------------------------------------------
# PersonaRegistry.available_roles() tests
# ---------------------------------------------------------------------------

class TestPersonaRegistryAvailableRoles:
    """Tests for PersonaRegistry.available_roles()."""

    def test_available_roles_empty(self, tmp_path):
        """available_roles returns [] when no personas are loaded."""
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        assert registry.available_roles() == []

    def test_available_roles_returns_name_description(self, tmp_path):
        """available_roles returns [{name, description}, ...]."""
        (tmp_path / "security.md").write_text(
            "---\nname: security\ndescription: Security specialist\n---\noverlay\n"
        )
        (tmp_path / "devops.md").write_text(
            "---\nname: devops\ndescription: CI/CD specialist\n---\noverlay\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        roles = registry.available_roles()
        assert len(roles) == 2
        names = [r["name"] for r in roles]
        assert "security" in names
        assert "devops" in names
        for r in roles:
            assert "name" in r
            assert "description" in r


# ---------------------------------------------------------------------------
# PersonaRegistry.build_overlay() tests
# ---------------------------------------------------------------------------

class TestPersonaRegistryBuildOverlay:
    """Tests for PersonaRegistry.build_overlay()."""

    def test_build_overlay_single_role(self, tmp_path):
        """build_overlay returns overlay text for a known role."""
        (tmp_path / "security.md").write_text(
            "---\nname: security\ndescription: Security specialist\n---\n"
            "## Specialist Context: Security\nFocus on vulns.\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        overlay = registry.build_overlay(["security"])
        assert "## Specialist Context: Security" in overlay
        assert "vulns" in overlay

    def test_build_overlay_unknown_role_returns_empty(self, tmp_path):
        """build_overlay returns '' when all roles are unknown."""
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        overlay = registry.build_overlay(["unknown_role"])
        assert overlay == ""

    def test_build_overlay_multiple_roles(self, tmp_path):
        """build_overlay combines overlays for multiple roles."""
        (tmp_path / "security.md").write_text(
            "---\nname: security\ndescription: Security\n---\n"
            "## Specialist Context: Security\nSec content.\n"
        )
        (tmp_path / "architect.md").write_text(
            "---\nname: architect\ndescription: Architect\n---\n"
            "## Specialist Context: Architect\nArch content.\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        overlay = registry.build_overlay(["security", "architect"])
        assert "## Specialist Context: Security" in overlay
        assert "## Specialist Context: Architect" in overlay
