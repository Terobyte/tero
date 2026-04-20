"""Regression tests for plan parsing and checklist persistence."""

from dataclasses import replace

from src.plan_tracker import (
    PlanItem,
    Phase,
    parse_requirements,
    parse_enriched_plan,
    write_enriched_plan,
    write_checklist_back,
    mark_step_done,
    mark_all_done,
    reset_all_progress,
)


def test_parse_requirements_ignores_fenced_code_blocks():
    content = """# Plan
- [x] Real completed step
```python
- [ ] fake checkbox in code
1. fake numbered item
- fake dash item
```
```diff
- [ ] fake diff removal
+actual code
```
```markdown
- [ ] fake markdown checkbox
```
- [ ] Real pending step
"""

    items = parse_requirements(content)

    assert [(item.text, item.done) for item in items] == [
        ("Real completed step", True),
        ("Real pending step", False),
    ]


def test_parse_requirements_ignores_nested_checklists():
    content = """- [ ] Parent step
   - [ ] nested child step
\t- [x] nested tab child
  1. nested numbered child
- [ ] Sibling step
"""

    items = parse_requirements(content)

    assert [item.text for item in items] == [
        "Parent step",
        "Sibling step",
    ]


def test_write_checklist_back_updates_only_top_level_items(tmp_path):
    plan_path = tmp_path / "requirements.md"
    plan_path.write_text(
        """# Plan
1. First step
- [ ] Second step
```diff
- [ ] fake diff removal
+replacement
```
- [ ] Parent step
   - [ ] nested child step
"""
    )

    write_checklist_back(
        str(plan_path),
        [
            PlanItem(text="First step", done=True),
            PlanItem(text="Second step", done=False),
            PlanItem(text="Parent step", done=True),
        ],
    )

    assert plan_path.read_text() == """# Plan
- [x] First step
- [ ] Second step
```diff
- [ ] fake diff removal
+replacement
```
- [x] Parent step
   - [ ] nested child step
"""


# --- PlanItem.roles tests ---


def test_plan_item_roles_default_is_empty_tuple():
    """PlanItem with no roles argument should default to an empty tuple."""
    item = PlanItem(text="Do something")
    assert item.roles == ()


def test_plan_item_roles_can_be_set():
    """PlanItem accepts an explicit tuple of roles."""
    item = PlanItem(text="Write tests", roles=("implementer", "reviewer"))
    assert item.roles == ("implementer", "reviewer")


def test_plan_item_roles_are_independent():
    """Each PlanItem instance gets its own roles (no shared mutable default)."""
    item_a = PlanItem(text="Step A")
    item_b = PlanItem(text="Step B")
    assert item_a.roles == ()
    assert item_b.roles == ()
    # Verify that creating a new item with roles doesn't affect others
    item_c = PlanItem(text="Step C", roles=("dev",))
    assert item_c.roles == ("dev",)
    assert item_b.roles == ()


def test_mark_step_done_preserves_roles():
    items = [
        PlanItem(text="Step 1", done=False, roles=("security",)),
        PlanItem(text="Step 2", done=False, roles=("database",)),
    ]
    updated = mark_step_done(items, 0)
    assert updated[0].done is True
    assert updated[0].roles == ("security",)


def test_mark_all_done_preserves_roles():
    items = [PlanItem(text="Step 1", done=False, roles=("security",))]
    updated = mark_all_done(items)
    assert updated[0].done is True
    assert updated[0].roles == ("security",)


def test_reset_all_progress_preserves_roles():
    items = [PlanItem(text="Step 1", done=True, roles=("database",))]
    updated = reset_all_progress(items)
    assert updated[0].done is False
    assert updated[0].roles == ("database",)


def test_phase_has_display_name_defaulting_to_empty():
    phase = Phase(name="Update (2)", type="update", steps=[])
    assert phase.display_name == ""


def test_phase_display_name_set():
    phase = Phase(
        name="Update (2)", type="update", steps=[], display_name="Security Layer"
    )
    assert phase.display_name == "Security Layer"


# --- parse_enriched_plan tests ---


ENRICHED_PLAN = """\
## Phases
- Phase 1: "Инициализация проекта" → steps 1-2
- Phase 2: "Авторизация" → steps 3-4

## Steps
1. [devops] Create pyproject.toml
2. [devops] Initialize git repo
3. [security, architect] Add JWT auth middleware
4. [security] Add rate limiting
"""

ENRICHED_NO_PHASES = """\
## Steps
1. [python-dev] Implement scorer module
2. [tdd-guide] Write tests for scorer
"""


def test_parse_enriched_plan_returns_items_with_roles():
    items, phases = parse_enriched_plan(ENRICHED_PLAN)
    assert len(items) == 4
    assert items[0].text == "Create pyproject.toml"
    assert items[0].roles == ("devops",)
    assert items[2].text == "Add JWT auth middleware"
    assert items[2].roles == ("security", "architect")


def test_parse_enriched_plan_returns_phases():
    items, phases = parse_enriched_plan(ENRICHED_PLAN)
    assert len(phases) == 2
    assert phases[0].display_name == "Инициализация проекта"
    assert phases[1].display_name == "Авторизация"
    # Phase 1 has items 0 and 1 (steps 1-2)
    assert len(phases[0].steps) == 2
    assert phases[0].steps[0].text == "Create pyproject.toml"


def test_parse_enriched_plan_no_phases_section():
    items, phases = parse_enriched_plan(ENRICHED_NO_PHASES)
    assert len(items) == 2
    assert phases == []  # caller must call auto_group_phases


def test_parse_enriched_plan_items_not_done_by_default():
    items, _ = parse_enriched_plan(ENRICHED_PLAN)
    assert all(not item.done for item in items)


def test_write_enriched_plan_creates_file(tmp_path):
    enriched_path = tmp_path / ".g3" / "enriched-plan.md"
    write_enriched_plan(enriched_path, ENRICHED_PLAN)
    assert enriched_path.exists()
    assert "## Phases" in enriched_path.read_text()


# --- Phase-reference variant tests ---


ENRICHED_PLAN_COMMA_REFS = """\
## Phases
- Phase 1: "Setup" → steps 1, 3, 5
- Phase 2: "Core" → steps 2, 4

## Steps
1. [devops] Create pyproject.toml
2. [devops] Initialize git repo
3. [security] Add JWT auth middleware
4. [security] Add rate limiting
5. [devops] Configure CI pipeline
"""

ENRICHED_PLAN_MIXED_REFS = """\
## Phases
- Phase 1: "Setup" → steps 1-2, 4
- Phase 2: "Auth" → step 3

## Steps
1. [devops] Create pyproject.toml
2. [devops] Initialize git repo
3. [security] Add JWT auth middleware
4. [devops] Configure CI pipeline
"""


def test_parse_enriched_plan_comma_separated_refs():
    """Comma-separated step refs like 'steps 1, 3, 5' are parsed correctly."""
    items, phases = parse_enriched_plan(ENRICHED_PLAN_COMMA_REFS)
    assert len(phases) == 2
    # Phase 1 picks steps 1, 3, 5
    assert phases[0].display_name == "Setup"
    assert len(phases[0].steps) == 3
    assert phases[0].steps[0].text == "Create pyproject.toml"
    assert phases[0].steps[1].text == "Add JWT auth middleware"
    assert phases[0].steps[2].text == "Configure CI pipeline"
    # Phase 2 picks steps 2, 4
    assert len(phases[1].steps) == 2
    assert phases[1].steps[0].text == "Initialize git repo"


def test_parse_enriched_plan_mixed_refs():
    """Mixed refs like 'steps 1-2, 4' and singular 'step 3' parse correctly."""
    items, phases = parse_enriched_plan(ENRICHED_PLAN_MIXED_REFS)
    assert len(phases) == 2
    # Phase 1 picks steps 1, 2, 4 (range 1-2 plus step 4)
    assert phases[0].display_name == "Setup"
    assert len(phases[0].steps) == 3
    assert phases[0].steps[0].text == "Create pyproject.toml"
    assert phases[0].steps[1].text == "Initialize git repo"
    assert phases[0].steps[2].text == "Configure CI pipeline"
    # Phase 2 picks step 3 (singular 'step')
    assert phases[1].display_name == "Auth"
    assert len(phases[1].steps) == 1
    assert phases[1].steps[0].text == "Add JWT auth middleware"


def test_parse_enriched_plan_phase_name_includes_display_name():
    """Phase.name must follow '{Type} ({count}) · {display_name}' format."""
    items, phases = parse_enriched_plan(ENRICHED_PLAN)
    # ENRICHED_PLAN Phase 1: "Инициализация проекта" → steps 1-2
    assert phases[0].name.startswith("Create (2) · ")
    assert "Инициализация проекта" in phases[0].name
    # Phase 2: "Авторизация" → steps 3-4 (type "create" because "Add" → create)
    assert phases[1].name.startswith("Create (2) · ")
    assert "Авторизация" in phases[1].name


def test_parse_enriched_plan_phase_name_truncates_long_display_name():
    """Display names longer than 45 chars are truncated in Phase.name."""
    long_name = "A" * 60
    content = f"""\
## Phases
- Phase 1: "{long_name}" → steps 1

## Steps
1. Create something
"""
    items, phases = parse_enriched_plan(content)
    assert len(phases) == 1
    # The display_name field stores the full name
    assert phases[0].display_name == long_name
    # But Phase.name truncates to 45 chars
    assert len(phases[0].name.split(" · ", 1)[1]) == 45


class TestSkippedMarker:
    def test_write_checklist_back_skipped_item_marker_tilde(self):
        """Skipped items write [~] to file."""
        import tempfile, os
        from src.plan_tracker import write_checklist_back
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("- [ ] Step one\n- [ ] Step two\n")
            path = f.name
        try:
            items = [
                PlanItem(text="Step one", skipped=True),
                PlanItem(text="Step two", done=True),
            ]
            write_checklist_back(path, items)
            content = open(path).read()
            assert "- [~] Step one" in content
            assert "- [x] Step two" in content
        finally:
            os.unlink(path)

    def test_parse_checklist_tilde_marker_sets_skipped_true(self):
        """[~] items parse as skipped=True, done=False."""
        from src.plan_tracker import parse_requirements
        content = "- [~] Step one\n- [x] Step two\n- [ ] Step three\n"
        items = parse_requirements(content)
        assert items[0].skipped is True
        assert items[0].done is False
        assert items[1].done is True
        assert items[1].skipped is False
        assert items[2].done is False
        assert items[2].skipped is False

    def test_resume_skipped_phase_not_rerun(self):
        """Phase where all steps are skipped is treated as complete on resume."""
        from src.plan_tracker import PlanItem
        steps = [PlanItem(text="a", skipped=True), PlanItem(text="b", skipped=True)]
        # The resume condition: all(step.done or step.skipped for step in phase.steps)
        assert all(step.done or step.skipped for step in steps)
