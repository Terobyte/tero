"""Regression tests for plan parsing and checklist persistence."""

from src.plan_tracker import PlanItem, parse_requirements, write_checklist_back


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
