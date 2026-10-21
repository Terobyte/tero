"""Plan parsing: sections, preamble, checkbox-only, atomic mark."""

from pathlib import Path

import pytest

from src.loop.plan import load_phases, load_preamble, mark_phase_done


SECTIONS = """# Title

Preamble stays with every phase.

## First phase

Do the parser.

- [ ] write parse()
- [ ] empty string returns []

## Notes

Just commentary, no checkboxes.

## Second phase

- [x] already done
- [ ] still open
"""


CHECKBOX_ONLY = """Intro without headings.

- [ ] first item
- [x] second item
- [ ] third item
"""


def test_load_phases_uses_h2_sections_with_checkboxes(tmp_path: Path):
    path = tmp_path / "plan.md"
    path.write_text(SECTIONS)
    phases = load_phases(path)
    assert [p.title for p in phases] == ["First phase", "Second phase"]
    assert phases[0].index == 0
    assert phases[1].index == 1
    assert [c.text for c in phases[0].checkboxes] == [
        "write parse()",
        "empty string returns []",
    ]
    assert phases[0].done is False
    assert phases[1].done is False
    assert "Do the parser." in phases[0].body
    assert "- [ ] write parse()" in phases[0].body


def test_load_preamble_is_text_before_first_h2(tmp_path: Path):
    path = tmp_path / "plan.md"
    path.write_text(SECTIONS)
    assert "Preamble stays with every phase." in load_preamble(path)
    assert "## First" not in load_preamble(path)


def test_section_without_checkboxes_is_not_a_phase(tmp_path: Path):
    path = tmp_path / "plan.md"
    path.write_text(SECTIONS)
    titles = [p.title for p in load_phases(path)]
    assert "Notes" not in titles


def test_no_headings_each_checkbox_is_a_phase(tmp_path: Path):
    path = tmp_path / "plan.md"
    path.write_text(CHECKBOX_ONLY)
    phases = load_phases(path)
    assert [p.title for p in phases] == ["first item", "second item", "third item"]
    assert phases[1].done is True
    assert phases[0].done is False
    assert load_preamble(path).startswith("Intro without headings.")


def test_phase_done_when_all_checkboxes_checked(tmp_path: Path):
    path = tmp_path / "plan.md"
    path.write_text("## Done\n\n- [x] a\n- [x] b\n")
    phases = load_phases(path)
    assert len(phases) == 1
    assert phases[0].done is True


def test_mark_phase_done_checks_only_that_section(tmp_path: Path):
    path = tmp_path / "plan.md"
    path.write_text(SECTIONS)
    first, second = load_phases(path)
    mark_phase_done(path, first)
    text = path.read_text()
    assert "- [x] write parse()" in text
    assert "- [x] empty string returns []" in text
    assert "- [x] already done" in text
    assert "- [ ] still open" in text
    reloaded = load_phases(path)
    assert reloaded[0].done is True
    assert reloaded[1].done is False


def test_mark_phase_done_is_atomic(tmp_path: Path, monkeypatch):
    path = tmp_path / "plan.md"
    path.write_text("## Only\n\n- [ ] one\n")
    phase = load_phases(path)[0]

    real_replace = Path.replace

    def boom(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        mark_phase_done(path, phase)
    assert "- [ ] one" in path.read_text()
    monkeypatch.setattr(Path, "replace", real_replace)
    mark_phase_done(path, load_phases(path)[0])
    assert "- [x] one" in path.read_text()
