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

    def test_load_all_ignores_file_without_frontmatter(self, tmp_path):
        """load_all skips .md files that lack YAML frontmatter."""
        (tmp_path / "no_frontmatter.md").write_text(
            "This is just a plain markdown file.\nNo frontmatter here.\n"
        )
        (tmp_path / "security.md").write_text(
            "---\nname: security\ndescription: Security\n---\noverlay\n"
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

    def test_build_overlay_caps_at_two(self, tmp_path):
        """build_overlay uses at most 2 roles even when more are requested."""
        for name in ("security", "architect", "devops"):
            (tmp_path / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: {name}\n---\n"
                f"## Specialist Context: {name}\n{name} content.\n"
            )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        overlay = registry.build_overlay(["security", "architect", "devops"])
        assert "## Specialist Context: security" in overlay
        assert "## Specialist Context: architect" in overlay
        # devops is the 3rd role and must be excluded by the cap
        assert "## Specialist Context: devops" not in overlay

    def test_build_overlay_role_cap_warning(self, tmp_path):
        """build_overlay prints a warning to stderr when role cap is exceeded."""
        for name in ("security", "architect", "devops"):
            (tmp_path / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: {name}\n---\n"
                f"## Specialist Context: {name}\n{name} content.\n"
            )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        import io
        import sys

        stderr_capture = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = stderr_capture
        try:
            registry.build_overlay(["security", "architect", "devops"])
        finally:
            sys.stderr = old_stderr
        warning = stderr_capture.getvalue()
        assert "Warning" in warning
        assert "3 roles" in warning

    def test_build_overlay_empty_roles_list(self, tmp_path):
        """build_overlay returns '' when called with an empty list."""
        (tmp_path / "security.md").write_text(
            "---\nname: security\ndescription: Security\n---\nSec content.\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        overlay = registry.build_overlay([])
        assert overlay == ""

    def test_build_overlay_partial_known_roles(self, tmp_path):
        """build_overlay includes only known roles, silently skipping unknowns."""
        (tmp_path / "security.md").write_text(
            "---\nname: security\ndescription: Security\n---\nSec content.\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        overlay = registry.build_overlay(["security", "nonexistent"])
        assert "Sec content." in overlay
        # Only security's overlay is present; nonexistent is silently skipped
        assert "nonexistent" not in overlay

    def test_build_overlay_joins_with_double_newline(self, tmp_path):
        """build_overlay joins multiple overlays with a blank line (\\n\\n)."""
        (tmp_path / "security.md").write_text(
            "---\nname: security\ndescription: Security\n---\nSec overlay\n"
        )
        (tmp_path / "architect.md").write_text(
            "---\nname: architect\ndescription: Architect\n---\nArch overlay\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        overlay = registry.build_overlay(["security", "architect"])
        # The two overlays must be separated by a blank line
        assert "Sec overlay" in overlay
        assert "Arch overlay" in overlay
        assert "Sec overlay\n\nArch overlay" in overlay


# ---------------------------------------------------------------------------
# _parse_persona_md edge-case tests (static method)
# ---------------------------------------------------------------------------

class TestParsePersonaMdEdgeCases:
    """Tests for PersonaRegistry._parse_persona_md edge cases."""

    def test_no_closing_frontmatter_returns_none(self):
        """Returns None when there is no closing --- delimiter."""
        text = "---\nname: security\ndescription: Security\n"
        assert PersonaRegistry._parse_persona_md(text) is None

    def test_invalid_yaml_returns_none(self):
        """Returns None when frontmatter YAML is malformed."""
        text = "---\nname: [broken yaml\n---\noverlay text\n"
        assert PersonaRegistry._parse_persona_md(text) is None

    def test_missing_name_returns_none(self):
        """Returns None when frontmatter has no 'name' field."""
        text = "---\ndescription: No name field\n---\noverlay text\n"
        assert PersonaRegistry._parse_persona_md(text) is None

    def test_non_dict_yaml_returns_none(self):
        """Returns None when YAML frontmatter parses to a non-dict value."""
        text = "---\njust a plain string\n---\noverlay text\n"
        assert PersonaRegistry._parse_persona_md(text) is None

    def test_empty_body_after_frontmatter(self):
        """Returns a Persona with empty overlay when body is blank."""
        text = "---\nname: security\ndescription: Security\n---\n"
        p = PersonaRegistry._parse_persona_md(text)
        assert p is not None
        assert p.name == "security"
        assert p.overlay == ""

    def test_missing_description_defaults_to_empty(self):
        """Returns a Persona with description='' when description is omitted."""
        text = "---\nname: devops\n---\nDocker tips\n"
        p = PersonaRegistry._parse_persona_md(text)
        assert p is not None
        assert p.name == "devops"
        assert p.description == ""

    def test_does_not_start_with_frontmatter_returns_none(self):
        """Returns None when text does not start with ---."""
        text = "name: security\n---\noverlay\n"
        assert PersonaRegistry._parse_persona_md(text) is None


# ---------------------------------------------------------------------------
# PersonaRegistry.load_all() additional edge-case tests
# ---------------------------------------------------------------------------

class TestPersonaRegistryLoadAllEdgeCases:
    """Additional edge-case tests for PersonaRegistry.load_all()."""

    def test_load_all_nonexistent_dir(self, tmp_path):
        """load_all returns [] when the directory does not exist."""
        registry = PersonaRegistry(tmp_path / "nonexistent")
        result = registry.load_all()
        assert result == []

    def test_load_all_multiple_files_sorted(self, tmp_path):
        """load_all returns personas sorted by filename."""
        (tmp_path / "zebra.md").write_text(
            "---\nname: zebra\ndescription: Zebra\n---\nZ overlay\n"
        )
        (tmp_path / "alpha.md").write_text(
            "---\nname: alpha\ndescription: Alpha\n---\nA overlay\n"
        )
        registry = PersonaRegistry(tmp_path)
        loaded = registry.load_all()
        assert len(loaded) == 2
        assert loaded[0].name == "alpha"
        assert loaded[1].name == "zebra"

    def test_load_all_clears_previous_cache(self, tmp_path):
        """Calling load_all a second time clears stale entries."""
        (tmp_path / "old.md").write_text(
            "---\nname: old\ndescription: Old\n---\nOld overlay\n"
        )
        registry = PersonaRegistry(tmp_path)
        registry.load_all()
        assert registry.get("old") is not None

        # Remove the file and reload
        (tmp_path / "old.md").unlink()
        registry.load_all()
        assert registry.get("old") is None


# ---------------------------------------------------------------------------
# Bundled persona smoke test
# ---------------------------------------------------------------------------

import os

BUNDLED_PERSONAS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "src", "personas", "prompts"
)


def test_bundled_personas_load():
    """Smoke test: bundled personas/ dir loads without errors."""
    personas_dir = Path(BUNDLED_PERSONAS_DIR)
    if not personas_dir.exists():
        pytest.skip("personas/prompts dir not yet created")
    registry = PersonaRegistry(personas_dir)
    personas = registry.load_all()
    assert len(personas) >= 10
    names = {p.name for p in personas}
    expected = {
        "python-dev", "frontend-dev", "designer", "security",
        "database", "architect", "devops", "tdd-guide",
        "performance", "refactor",
    }
    assert expected.issubset(names)
    for p in personas:
        assert p.name
        assert p.description
        assert len(p.overlay) > 50  # non-trivial content
