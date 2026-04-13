"""Debugger bug parser and report writer."""

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from src.errors import DebuggerError


class BugStatus(str, Enum):
    """Valid lifecycle states for a BugEntry."""

    OPEN = "open"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    INVALID_TEST = "invalid_test"
    FIXED = "fixed"


_VALID_TRANSITIONS: dict[str, set[str]] = {
    BugStatus.OPEN: {BugStatus.CONFIRMED, BugStatus.FALSE_POSITIVE, BugStatus.INVALID_TEST},
    BugStatus.CONFIRMED: {BugStatus.INVALID_TEST, BugStatus.FIXED},
    BugStatus.FALSE_POSITIVE: set(),
    BugStatus.INVALID_TEST: set(),
    BugStatus.FIXED: set(),
}


def transition_bug(bug: "BugEntry", new_status: str) -> None:
    """Transition a bug to a new status, raising DebuggerError on invalid moves."""
    current = bug.status
    allowed = _VALID_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise DebuggerError(
            f"Invalid bug transition: {current!r} → {new_status!r} "
            f"(bug #{bug.id}). Allowed: {sorted(allowed)}"
        )
    bug.status = new_status


@dataclass
class BugEntry:
    """A single bug found by the player."""

    id: int
    file: str
    line: int
    description: str
    severity: str
    status: str = BugStatus.OPEN  # use BugStatus values
    test_file: str | None = None


# ── JSON extraction ───────────────────────────────────────────────────────────

def _strip_trailing_commas(json_text: str) -> str:
    """Remove trailing commas before ] or } to fix common LLM JSON quirks."""
    return re.sub(r",\s*([}\]])", r"\1", json_text)


def _extract_json_from_text(text: str) -> list[str]:
    """Extract potential JSON arrays from LLM output using multiple strategies."""
    candidates: list[str] = []

    # Strategy 1: ```json blocks
    for match in re.findall(r"```json\s*\n(.*?)\s*```", text, re.DOTALL):
        candidates.append(match)

    # Strategy 2: ``` blocks starting with [
    for match in re.findall(r"```\s*\n(.*?)\s*```", text, re.DOTALL):
        if match.strip().startswith("["):
            candidates.append(match)

    # Strategy 3: bracket-matched [...] arrays
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "[" and depth == 0:
            start = i
            depth += 1
        elif ch == "[":
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : i + 1])
                start = None

    # Strategy 4: entire text
    candidates.append(text.strip())

    # Strategy 5: standalone {...} objects with "file" and "line"
    obj_depth = 0
    obj_start: int | None = None
    for i, ch in enumerate(text):
        if ch == "{" and obj_depth == 0:
            obj_start = i
            obj_depth += 1
        elif ch == "{":
            obj_depth += 1
        elif ch == "}" and obj_depth > 0:
            obj_depth -= 1
            if obj_depth == 0 and obj_start is not None:
                obj_text = text[obj_start : i + 1]
                if '"file"' in obj_text and '"line"' in obj_text:
                    candidates.append("[" + obj_text + "]")
                obj_start = None

    return candidates


def _normalize_description(desc: str) -> str:
    """Normalize a bug description for dedup comparison."""
    s = re.sub(r"^[\-\d.]+\s*", "", desc.strip())
    s = re.sub(r"^(High|Medium|Low|Critical):\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(?:line|L|#)\s*\d+", "", s)
    s = re.sub(r":\d+", "", s)
    return s.lower().strip()[:60]


def renumber_bugs(bugs: list[BugEntry]) -> None:
    """Reassign contiguous IDs starting from 1."""
    for i, bug in enumerate(bugs):
        bug.id = i + 1


_PROSE_FILE_LINE_RE = re.compile(r"\b(\w+\.py)[#:](?:L)?(\d+)\b")


def _extract_prose_fallback(text: str) -> list[dict]:
    """Extract file:line references from prose when no JSON is found."""
    if not text or not text.strip():
        return []
    bugs: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _PROSE_FILE_LINE_RE.finditer(text):
        fname = m.group(1)
        line_num = int(m.group(2))
        key = (fname, line_num)
        if key in seen:
            continue
        seen.add(key)
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        context = text[line_start:line_end].strip()
        desc = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", context).strip()[:200]
        bugs.append({
            "file": fname,
            "line": line_num,
            "description": desc or f"Issue at {fname}:{line_num}",
            "severity": "high",
        })
    return bugs


def parse_bugs(raw_output: str, start_id: int = 1) -> list[BugEntry]:
    """Parse LLM output into structured BugEntry list.

    Args:
        raw_output: Raw text from the player model.
        start_id: First bug ID to assign (allows merging across iterations).
    """
    candidates = _extract_json_from_text(raw_output)

    raw_bugs: list[dict] = []
    seen_entries: set[tuple[str, int, str]] = set()

    for candidate in candidates:
        cleaned = _strip_trailing_commas(candidate)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if not all(k in entry for k in ("file", "line", "description", "severity")):
                continue
            try:
                line = int(entry["line"])
            except (TypeError, ValueError):
                continue
            key = (str(entry["file"]), line, str(entry["description"]))
            if key in seen_entries:
                continue
            seen_entries.add(key)
            raw_bugs.append({
                "file": str(entry["file"]),
                "line": line,
                "description": str(entry["description"]),
                "severity": str(entry["severity"]),
            })

    # Per-line dedup: keep first entry per (file, line)
    seen_lines: set[tuple[str, int]] = set()
    line_deduped: list[dict] = []
    for bug in raw_bugs:
        line_key = (bug["file"], bug["line"])
        if line_key not in seen_lines:
            seen_lines.add(line_key)
            line_deduped.append(bug)

    # Prose fallback if nothing parsed
    if not line_deduped:
        line_deduped = _extract_prose_fallback(raw_output)

    # Description-based dedup: same file + same normalized description = same bug
    seen_descs: set[tuple[str, str]] = set()
    desc_deduped: list[dict] = []
    for bug in line_deduped:
        desc_key = (bug["file"], _normalize_description(bug["description"]))
        if desc_key not in seen_descs:
            seen_descs.add(desc_key)
            desc_deduped.append(bug)

    return [
        BugEntry(
            id=start_id + i,
            file=b["file"],
            line=b["line"],
            description=b["description"],
            severity=b["severity"],
        )
        for i, b in enumerate(desc_deduped)
    ]


def merge_bugs(existing: list[BugEntry], new_bugs: list[BugEntry]) -> list[BugEntry]:
    """Merge new_bugs into existing, deduplicating by (file, line) and description.

    New bugs that share (file, line) or (file, normalized_description) with
    an existing bug are discarded.
    """
    seen_lines: set[tuple[str, int]] = {(b.file, b.line) for b in existing}
    seen_descs: set[tuple[str, str]] = {
        (b.file, _normalize_description(b.description)) for b in existing
    }
    result = list(existing)
    for bug in new_bugs:
        line_key = (bug.file, bug.line)
        desc_key = (bug.file, _normalize_description(bug.description))
        if line_key not in seen_lines and desc_key not in seen_descs:
            seen_lines.add(line_key)
            seen_descs.add(desc_key)
            result.append(bug)
    return result


# ── Report writing ────────────────────────────────────────────────────────────

def _status_icon(status: str) -> str:
    return {
        "confirmed": "✓",
        "false_positive": "✗",
        "invalid_test": "?",
        "fixed": "✔",
    }.get(status, "·")


def write_bugs_md(bugs: list[BugEntry], path: str, iteration: int) -> None:
    """Write bugs.md to path with current iteration status."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    open_bugs = [b for b in bugs if b.status == "open"]
    confirmed = [b for b in bugs if b.status == "confirmed"]
    fixed = [b for b in bugs if b.status == "fixed"]
    false_positive = [b for b in bugs if b.status in ("false_positive", "invalid_test")]

    lines = [
        f"# Debugger Report — Iteration {iteration}",
        f"",
        f"| Category | Count |",
        f"|----------|-------|",
        f"| Open (unverified) | {len(open_bugs)} |",
        f"| Confirmed | {len(confirmed)} |",
        f"| Fixed | {len(fixed)} |",
        f"| False positive / invalid | {len(false_positive)} |",
        f"| **Total** | **{len(bugs)}** |",
        f"",
    ]

    if bugs:
        lines.append("## Bug List")
        lines.append("")
        lines.append("| ID | File | Line | Status | Description |")
        lines.append("|----|------|------|--------|-------------|")
        for bug in bugs:
            icon = _status_icon(bug.status)
            desc = bug.description[:80].replace("|", "\\|")
            lines.append(f"| {bug.id} | `{bug.file}` | {bug.line} | {icon} {bug.status} | {desc} |")

    p.write_text("\n".join(lines) + "\n")


def write_final_report(
    bugs: list[BugEntry],
    path: str,
    duration_s: float,
    victory: bool,
    *,
    victory_threshold: int = 3,
) -> None:
    """Write the final summary report."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    confirmed = [b for b in bugs if b.status == "confirmed"]
    fixed = [b for b in bugs if b.status == "fixed"]
    false_positive = [b for b in bugs if b.status in ("false_positive", "invalid_test")]
    open_bugs = [b for b in bugs if b.status == "open"]

    outcome = f"VICTORY — no bugs in {victory_threshold} consecutive clean passes" if victory else "STOPPED"
    mins = int(duration_s // 60)
    secs = int(duration_s % 60)

    lines = [
        "# Debugger Final Report",
        "",
        f"**Outcome:** {outcome}",
        f"**Duration:** {mins}m {secs}s",
        f"**Total bugs found:** {len(bugs)}",
        f"**Fixed:** {len(fixed)}",
        f"**Confirmed (unfixed):** {len(confirmed)}",
        f"**False positives:** {len(false_positive)}",
        f"**Open (unverified):** {len(open_bugs)}",
        "",
    ]

    if fixed:
        lines += ["## Fixed Bugs", ""]
        for bug in fixed:
            lines.append(f"- [{bug.id}] `{bug.file}:{bug.line}` — {bug.description}")
        lines.append("")

    if confirmed:
        lines += ["## Confirmed (not yet fixed)", ""]
        for bug in confirmed:
            lines.append(f"- [{bug.id}] `{bug.file}:{bug.line}` — {bug.description}")
        lines.append("")

    if false_positive:
        lines += ["## False Positives / Invalid Tests", ""]
        for bug in false_positive:
            lines.append(f"- [{bug.id}] `{bug.file}:{bug.line}` — {bug.description}")
        lines.append("")

    p.write_text("\n".join(lines) + "\n")
