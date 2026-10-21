"""Split a markdown plan into phases and mark their checkboxes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_H2 = re.compile(r"^##\s+(.+?)\s*$")
_CHECKBOX = re.compile(r"^- \[([ xX])\] (.+)$")


@dataclass(frozen=True)
class Checkbox:
    text: str
    done: bool


@dataclass(frozen=True)
class Phase:
    index: int
    title: str
    body: str
    checkboxes: list[Checkbox]
    start_line: int
    end_line: int

    @property
    def done(self) -> bool:
        return bool(self.checkboxes) and all(box.done for box in self.checkboxes)


def load_preamble(path: str | Path) -> str:
    preamble, _ = _parse(Path(path))
    return preamble


def load_phases(path: str | Path) -> list[Phase]:
    _, phases = _parse(Path(path))
    return phases


def mark_phase_done(path: str | Path, phase: Phase) -> None:
    """Check every checkbox that belongs to ``phase``. Temp-file + rename."""
    target = Path(path)
    # newline="" keeps \r\n: read_text() would translate it and the rewrite
    # would turn a CRLF plan into LF on every ship
    with open(target, newline="") as f:
        lines = f.read().splitlines(keepends=True)
    changed: list[str] = []
    for i, line in enumerate(lines):
        newline = _line_ending(line)
        raw = line.rstrip("\r\n")
        if phase.start_line <= i < phase.end_line and _CHECKBOX.match(raw):
            raw = _CHECKBOX.sub(r"- [x] \2", raw)
        changed.append(raw + newline)
    _atomic_write(target, "".join(changed))


def _parse(path: Path) -> tuple[str, list[Phase]]:
    text = path.read_text()
    lines = text.splitlines()
    headings = [i for i, line in enumerate(lines) if _H2.match(line)]
    if not headings:
        return _checkbox_phases(lines)
    preamble = "\n".join(lines[: headings[0]]).strip()
    phases: list[Phase] = []
    for pos, start in enumerate(headings):
        end = headings[pos + 1] if pos + 1 < len(headings) else len(lines)
        title = _H2.match(lines[start]).group(1)
        body_lines = lines[start + 1 : end]
        checkboxes = _checkboxes(body_lines)
        if not checkboxes:
            continue
        phases.append(
            Phase(
                index=len(phases),
                title=title,
                body="\n".join(body_lines).strip("\n"),
                checkboxes=checkboxes,
                start_line=start,
                end_line=end,
            )
        )
    return preamble, phases


def _checkbox_phases(lines: list[str]) -> tuple[str, list[Phase]]:
    first = next((i for i, line in enumerate(lines) if _CHECKBOX.match(line)), len(lines))
    preamble = "\n".join(lines[:first]).strip()
    phases: list[Phase] = []
    for i, line in enumerate(lines):
        match = _CHECKBOX.match(line)
        if not match:
            continue
        text = match.group(2)
        phases.append(
            Phase(
                index=len(phases),
                title=text,
                body=line,
                checkboxes=[_parse_box(match)],
                start_line=i,
                end_line=i + 1,
            )
        )
    return preamble, phases


def _parse_box(match: re.Match[str]) -> Checkbox:
    return Checkbox(text=match.group(2), done=match.group(1).lower() == "x")


def _checkboxes(lines: list[str]) -> list[Checkbox]:
    found: list[Checkbox] = []
    for line in lines:
        match = _CHECKBOX.match(line)
        if match:
            found.append(_parse_box(match))
    return found


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", newline="") as f:
        f.write(content)
    tmp.replace(path)
