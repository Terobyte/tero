"""Parse requirements into checklist, track progress."""

import re
from dataclasses import dataclass


# Constants
MAX_STEPS = 100
DEFAULT_TITLE = "Plan Progress"
CHECKBOX_DONE = "x"
CHECKBOX_PENDING = " "

@dataclass
class PlanItem:
    """A single plan item."""
    text: str
    done: bool = False


# --- Batch execution types ---

STEP_TYPE_MAP: dict[str, list[str]] = {
    "create": ["create", "add", "write", "generate", "implement"],
    "update": ["update", "modify", "change", "extend", "refactor"],
    "test": ["test", "tests", "verify", "validate", "check"],
    "review": ["review", "analyze", "audit", "fix issue"],
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


def parse_requirements(content: str) -> list[PlanItem]:
    """Parse numbered items, checkbox items, or headers from requirements.

    Supports:
    - Numbered lists: 1. Item text
    - Checkbox lists: - [ ] Item text or - [x] Item text
    - Dash lists: - Item text
    - Headers: ## Section (treated as separators, not items)
    """
    items = []
    lines = content.split("\n")

    for line in lines:
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            continue

        # Skip headers (## or #)
        if stripped.startswith("#"):
            continue

        # Numbered list: 1. Item text
        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered:
            items.append(PlanItem(text=numbered.group(2), done=False))
            continue

        # Checkbox: - [ ] or - [x]
        checkbox = re.match(r"^-\s+\[([ xX])\]\s+(.+)$", stripped)
        if checkbox:
            is_done = checkbox.group(1).lower() == "x"
            items.append(PlanItem(text=checkbox.group(2), done=is_done))
            continue

        # Dash list: - Item text (not a checkbox)
        dash = re.match(r"^-\s+(.+)$", stripped)
        if dash and not stripped.startswith("- ["):
            items.append(PlanItem(text=dash.group(1), done=False))

    return items


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
    return [PlanItem(text=item.text, done=True) for item in items]


def reset_all_progress(items: list[PlanItem]) -> list[PlanItem]:
    """Return a fresh copy of items with all steps marked pending."""
    return [PlanItem(text=item.text, done=False) for item in items]


def get_current_step_index(items: list[PlanItem]) -> int | None:
    """Return index of first undone item, or None if all done."""
    for i, item in enumerate(items):
        if not item.done:
            return i
    return None


def mark_step_done(items: list[PlanItem], index: int) -> list[PlanItem]:
    """Return new list with item at index marked done."""
    result = list(items)
    result[index] = PlanItem(text=result[index].text, done=True)
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
    name = f"{ptype.capitalize()} · {snippet}" if snippet else ptype.capitalize()
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
        for item in phase.steps:
            item.done = True
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
                "done": "✅", "failed": "❌",
            }.get(phase.status, "❓")
            attempts_str = f" (attempt {phase.attempts})" if phase.attempts > 1 else ""
            table.add_row(
                f"{icon} Phase {i + 1}: {phase.name}{attempts_str}",
                f"{bar} {pct}%",
            )
        total_steps = sum(len(p.steps) for p in self.phases)
        done_steps = sum(s.done for p in self.phases for s in p.steps)
        table.add_row("", f"Steps: {done_steps}/{total_steps}")
        return table


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
    item_index = 0
    new_lines = []

    for line in lines:
        stripped = line.strip()
        indent_len = len(line) - len(line.lstrip())
        indent = " " * indent_len

        if item_index >= len(items):
            new_lines.append(line)
            continue

        # Checkbox: - [ ] or - [x]
        checkbox = re.match(r"^-\s+\[([ xX])\]\s+(.+)$", stripped)
        if checkbox:
            mark = "x" if items[item_index].done else " "
            new_lines.append(indent + f"- [{mark}] {items[item_index].text}")
            item_index += 1
            continue

        # Numbered list: 1. Item text → convert to checkbox
        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered:
            mark = "x" if items[item_index].done else " "
            new_lines.append(indent + f"- [{mark}] {items[item_index].text}")
            item_index += 1
            continue

        # Dash list: - Item text → convert to checkbox
        dash = re.match(r"^-\s+(.+)$", stripped)
        if dash and not stripped.startswith("- ["):
            mark = "x" if items[item_index].done else " "
            new_lines.append(indent + f"- [{mark}] {items[item_index].text}")
            item_index += 1
            continue

        new_lines.append(line)

    path.write_text("\n".join(new_lines))
