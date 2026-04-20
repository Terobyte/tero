"""Parse requirements into checklist, track progress."""

import os
import re
import tempfile
from dataclasses import dataclass, field, replace


# Constants
MAX_STEPS = 100
DEFAULT_TITLE = "Plan Progress"
CHECKBOX_DONE = "x"
CHECKBOX_PENDING = " "

@dataclass(frozen=True)
class PlanItem:
    """A single plan item."""
    text: str
    done: bool = False
    roles: tuple[str, ...] = field(default_factory=tuple)
    skipped: bool = False


# --- Batch execution types ---

STEP_TYPE_MAP: dict[str, list[str]] = {
    "create": ["create", "add", "write", "generate", "implement",
               "создать", "добавить", "написать", "реализовать", "сгенерировать"],
    "update": ["update", "modify", "change", "extend", "refactor",
               "обновить", "изменить", "переписать", "заменить", "интегрировать"],
    "test": ["test", "tests", "verify", "validate", "check",
             "тест", "тесты", "проверить", "валидировать"],
    "review": ["review", "analyze", "audit", "fix issue",
               "ревью", "анализировать", "аудит"],
}

PHASE_SIZE: dict[str, int] = {
    "create": 6,
    "update": 4,
    "test": 3,
    "review": 1,
}

DEFAULT_STEP_TYPE = "update"


@dataclass
class Phase:
    """A batch of PlanItems grouped by step type."""

    name: str
    type: str
    steps: list["PlanItem"]
    status: str = "pending"
    attempts: int = 0
    display_name: str = ""


@dataclass(frozen=True)
class PlanLineMatch:
    """A top-level checklist/list item found in the plan source."""

    line_index: int
    text: str
    done: bool
    indent: str
    skipped: bool = False


def _parse_fence_marker(line: str) -> tuple[str, int] | None:
    """Return fence marker character/length for Markdown fences."""
    stripped = line.lstrip(" \t")
    if stripped.startswith("```"):
        return "`", len(stripped) - len(stripped.lstrip("`"))
    if stripped.startswith("~~~"):
        return "~", len(stripped) - len(stripped.lstrip("~"))
    return None


def _iter_plan_line_matches(lines: list[str]) -> list[PlanLineMatch]:
    """Return top-level plan items, skipping fenced code blocks and nested lists."""
    matches: list[PlanLineMatch] = []
    fence_char: str | None = None
    fence_len = 0

    for line_index, line in enumerate(lines):
        fence = _parse_fence_marker(line)
        if fence:
            char, count = fence
            if fence_char is None:
                fence_char = char
                fence_len = count
            elif char == fence_char and count >= fence_len:
                fence_char = None
                fence_len = 0
            continue

        if fence_char is not None:
            continue

        stripped = line.strip()
        if not stripped:
            continue

        left_trimmed = line.lstrip(" \t")
        indent = line[: len(line) - len(left_trimmed)]

        # Only treat top-level list items as executable plan steps.
        if indent:
            continue

        if stripped.startswith("#"):
            continue

        checkbox = re.match(r"^-\s+\[([ xX~])\]\s+(.+)$", stripped)
        if checkbox:
            marker = checkbox.group(1)
            matches.append(
                PlanLineMatch(
                    line_index=line_index,
                    text=checkbox.group(2),
                    done=marker.lower() == "x",
                    skipped=marker == "~",
                    indent=indent,
                )
            )
            continue

        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered:
            matches.append(
                PlanLineMatch(
                    line_index=line_index,
                    text=numbered.group(2),
                    done=False,
                    indent=indent,
                )
            )
            continue

        dash = re.match(r"^-\s+(.+)$", stripped)
        if dash and not stripped.startswith("- ["):
            text = dash.group(1)
            # Skip description bullets like "- **Term**: explanation" — not actionable steps.
            if re.match(r"^\*\*[^*]+\*\*:", text):
                continue
            matches.append(
                PlanLineMatch(
                    line_index=line_index,
                    text=text,
                    done=False,
                    indent=indent,
                )
            )
            continue

        star = re.match(r"^\*\s+(.+)$", stripped)
        if star and not stripped.startswith("* ["):
            matches.append(
                PlanLineMatch(
                    line_index=line_index,
                    text=star.group(1),
                    done=False,
                    indent=indent,
                )
            )
            continue

        plus = re.match(r"^\+\s+(.+)$", stripped)
        if plus and not stripped.startswith("+ ["):
            matches.append(
                PlanLineMatch(
                    line_index=line_index,
                    text=plus.group(1),
                    done=False,
                    indent=indent,
                )
            )
            continue

    return matches


def parse_requirements(content: str) -> list[PlanItem]:
    """Parse numbered items, checkbox items, or headers from requirements.

    Supports:
    - Numbered lists: 1. Item text
    - Checkbox lists: - [ ] Item text or - [x] Item text
    - Dash lists: - Item text
    - Headers: ## Section (treated as separators, not items)
    """
    lines = content.split("\n")
    return [
        PlanItem(text=match.text, done=match.done, skipped=match.skipped)
        for match in _iter_plan_line_matches(lines)
    ]


def format_checklist(items: list[PlanItem], title: str = "Plan Progress") -> str:
    """Format items as a checkbox display."""
    lines = [f"  {title}:"]
    for i, item in enumerate(items, 1):
        mark = "x" if item.done else " "
        lines.append(f"    [{mark}] {i}. {item.text}")
    return "\n".join(lines)


def format_issues(issues_text: str) -> str:
    """Format coach's numbered issues as the active checklist.

    Extracts numbered items from coach feedback.
    """
    items = []
    lines = issues_text.split("\n")

    for line in lines:
        stripped = line.strip()
        # Match numbered issues: 1. Issue text
        match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if match:
            items.append(f"    [ ] {match.group(1)}. {match.group(2)}")

    if not items:
        # No numbered items found, show as plain text
        return f"  Issues:\n    {issues_text.strip()}"

    return "  Issues to address:\n" + "\n".join(items)


def mark_all_done(items: list[PlanItem]) -> list[PlanItem]:
    """Mark all items as done (for approved implementation)."""
    return [replace(item, done=True) for item in items]


def reset_all_progress(items: list[PlanItem]) -> list[PlanItem]:
    """Return a fresh copy of items with all steps marked pending."""
    return [replace(item, done=False) for item in items]


def get_current_step_index(items: list[PlanItem]) -> int | None:
    """Return index of first undone item, or None if all done."""
    for i, item in enumerate(items):
        if not item.done:
            return i
    return None


def mark_step_done(items: list[PlanItem], index: int) -> list[PlanItem]:
    """Return new list with item at index marked done."""
    result = list(items)
    result[index] = replace(result[index], done=True)
    return result


def detect_step_type(item: "PlanItem | str") -> str:
    """Return step type by keyword match.

    Review/test intents take precedence over generic implementation verbs such as
    "write" so phrases like "write tests" are classified as test work.
    """
    text = item.text if isinstance(item, PlanItem) else item
    text = text.lower()

    def has_keyword(keyword: str) -> bool:
        pattern = r"\b" + re.escape(keyword) + r"\b"
        return bool(re.search(pattern, text))

    priority = ("review", "test", "create", "update")
    for step_type in priority:
        keywords = STEP_TYPE_MAP[step_type]
        if any(has_keyword(keyword) for keyword in keywords):
            return step_type
    return DEFAULT_STEP_TYPE


def _make_phase(ptype: str, items: list["PlanItem"]) -> "Phase":
    """Create a named phase for a batch of plan items."""
    snippet = items[0].text if items else ""
    if len(snippet) > 45:
        snippet = snippet[:45].rstrip() + "…"
    count = len(items)
    prefix = f"{ptype.capitalize()} ({count})"
    name = f"{prefix} · {snippet}" if snippet else prefix
    return Phase(name=name, type=ptype, steps=items)


def auto_group_phases(items: list["PlanItem"]) -> list["Phase"]:
    """Group PlanItems into phases by type, splitting at PHASE_SIZE boundaries."""
    if not items:
        return []

    phases: list[Phase] = []
    current_type = detect_step_type(items[0])
    current_batch: list[PlanItem] = []

    for item in items:
        item_type = detect_step_type(item)
        if item_type != current_type or len(current_batch) >= PHASE_SIZE[current_type]:
            if current_batch:
                phases.append(_make_phase(current_type, current_batch))
            current_type = item_type
            current_batch = []
        current_batch.append(item)

    if current_batch:
        phases.append(_make_phase(current_type, current_batch))

    return phases


class PlanTracker:
    """Tracks phase/step progress and owns the Rich live dashboard."""

    def __init__(self, items: list[PlanItem]):
        self.items = items
        self.phases: list[Phase] = []
        self._live = None  # Rich Live instance

    def phase_done(self, phase: Phase) -> None:
        """Mark all PlanItems in phase as done and re-render dashboard."""
        original_steps = list(phase.steps)
        updated_items = list(self.items)
        matched_item_indexes: set[int] = set()
        resolved_steps: list[PlanItem] = []

        for original_step in original_steps:
            updated_step = replace(original_step, done=True)
            matched_index = None

            for item_index, item in enumerate(updated_items):
                if item_index in matched_item_indexes:
                    continue
                if item is original_step:
                    matched_index = item_index
                    break

            if matched_index is None:
                for item_index, item in enumerate(updated_items):
                    if item_index in matched_item_indexes:
                        continue
                    if item.text == original_step.text and item.roles == original_step.roles:
                        matched_index = item_index
                        break

            if matched_index is None:
                updated_items.append(updated_step)
                resolved_steps.append(updated_step)
                continue

            updated_item = replace(updated_items[matched_index], done=True)
            updated_items[matched_index] = updated_item
            matched_item_indexes.add(matched_index)
            resolved_steps.append(updated_item)

        self.items = updated_items
        phase.steps = resolved_steps
        self.render_dashboard()

    def start_dashboard(self) -> None:
        """Start Rich Live display. Call once before execution loop."""
        from rich.live import Live
        self._live = Live(self._build_table(), refresh_per_second=4)
        self._live.__enter__()

    def render_dashboard(self) -> None:
        """Update live display. Idempotent — safe to call extra times."""
        if self._live is not None:
            self._live.update(self._build_table())

    def stop_dashboard(self) -> None:
        """Stop Rich Live display. Always called in finally block."""
        if self._live is not None:
            self._live.__exit__(None, None, None)
            self._live = None

    def _build_table(self):
        """Build Rich Table showing phase progress."""
        from rich.table import Table
        table = Table(title="G3 Execution", show_header=False, box=None)
        for i, phase in enumerate(self.phases):
            done = sum(1 for s in phase.steps if s.done)
            total = len(phase.steps)
            pct = done * 100 // total if total else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            icon = {
                "pending": "⏳", "in_progress": "🔄",
                "done": "✅", "failed": "❌", "skipped": "⏭",
            }.get(phase.status, "❓")
            attempts_str = f" (attempt {phase.attempts})" if phase.attempts > 1 else ""
            label = phase.display_name or phase.name
            table.add_row(
                f"{icon} Phase {i + 1}: {label}{attempts_str}",
                f"{bar} {pct}%",
            )
        total_steps = sum(len(p.steps) for p in self.phases)
        done_steps = sum(s.done for p in self.phases for s in p.steps)
        table.add_row("", f"Steps: {done_steps}/{total_steps}")
        return table


# --- Enriched plan parsing ---

ROLE_TAG_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")


def parse_enriched_plan(content: str) -> tuple[list[PlanItem], list[Phase]]:
    """Parse Pre-Planner enriched plan into (items, phases).

    Handles two sections::

        ## Phases
        - Phase 1: "Setup" → steps 1-3

        ## Steps
        1. [security, architect] Add authentication middleware

    Returns ``(items, phases)``.  When no ``## Phases`` section is present
    *phases* is an empty list — the caller should fall back to
    :func:`auto_group_phases`.
    """
    items: list[PlanItem] = []
    phases_raw: list[tuple[str, list[int]]] = []  # (display_name, step_indices) 0-based

    sections = content.split("## ")
    for section in sections:
        lines = section.split("\n")
        header = lines[0].strip() if lines else ""

        if header == "Steps":
            for line in lines[1:]:
                stripped = line.strip()
                if not stripped:
                    continue
                match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
                if not match:
                    continue
                text = match.group(2)
                roles: tuple[str, ...] = ()
                role_match = ROLE_TAG_RE.match(text)
                if role_match:
                    roles = tuple(r.strip() for r in role_match.group(1).split(","))
                    text = role_match.group(2)
                items.append(PlanItem(text=text, done=False, roles=roles))

        elif header == "Phases":
            for line in lines[1:]:
                stripped = line.strip()
                if not stripped:
                    continue
                match = re.match(
                    r'^-\s+Phase\s+\d+:\s+"([^"]+)"\s+→\s+steps?\s+([\d,\s-]+)$',
                    stripped,
                )
                if match:
                    display_name = match.group(1)
                    # Parse step references: "1-3" or "1, 3, 5" or "1-2, 4"
                    step_indices: list[int] = []
                    for part in match.group(2).split(","):
                        part = part.strip()
                        if "-" in part:
                            s, e = part.split("-", 1)
                            step_indices.extend(range(int(s) - 1, int(e)))
                        else:
                            step_indices.append(int(part) - 1)
                    phases_raw.append((display_name, step_indices))

    # Build Phase objects referencing the same PlanItem instances.
    phases: list[Phase] = []
    for display_name, step_indices in phases_raw:
        phase_steps = [items[i] for i in step_indices if 0 <= i < len(items)]
        if not phase_steps:
            continue
        ptype = detect_step_type(phase_steps[0])
        phases.append(
            Phase(
                name=f"{ptype.capitalize()} ({len(phase_steps)}) · {display_name[:45]}",
                type=ptype,
                steps=phase_steps,
                display_name=display_name,
            )
        )

    return items, phases


def write_enriched_plan(file_path, content: str) -> None:
    """Save enriched plan content to *file_path*, creating parent directories."""
    from pathlib import Path

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_checklist_back(file_path: str, items: list[PlanItem]) -> None:
    """Update requirements file with current done state for each item.

    Replaces numbered items (1. text) and checkbox items (- [ ] text)
    with checkbox format showing current completion status.
    """
    from pathlib import Path
    path = Path(file_path)
    if not path.exists():
        return

    content = path.read_text()
    lines = content.split("\n")
    new_lines = list(lines)
    matches = _iter_plan_line_matches(lines)

    for item_index, match in enumerate(matches):
        if item_index >= len(items):
            break
        item = items[item_index]
        if item.done:
            mark = "x"
        elif item.skipped:
            mark = "~"
        else:
            mark = " "
        new_lines[match.line_index] = f"{match.indent}- [{mark}] {item.text}"

    if len(items) > len(matches):
        extra_lines = [
            f"- [{'x' if item.done else ('~' if item.skipped else ' ')}] {item.text}"
            for item in items[len(matches):]
        ]
        insert_at = len(new_lines) - 1 if new_lines and new_lines[-1] == "" else len(new_lines)
        new_lines[insert_at:insert_at] = extra_lines

    content_to_write = "\n".join(new_lines)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, suffix=".tmp"
    ) as tmp:
        tmp.write(content_to_write)
        tmp_path = tmp.name
    os.replace(tmp_path, path)
