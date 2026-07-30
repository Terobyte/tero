"""LdbRunner — orchestrates Input -> Player -> Tester -> (Fixer if mode=3).

Mode 2: input+find+test (read-only).
Mode 3: +fix +auto-commit (mirrors tero debug behaviour).
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import get_effective_context_limit
from src.ldb.blocks import Block, decompose_function
from src.ldb.inputs import synthesize_inputs_llm
from src.ldb.prompts import (
    FIXER_PROMPT_LDB_ARCH,
    PLAYER_PROMPT_LDB,
    TESTER_PROMPT_LDB,
)
from src.ldb.git_diff import filter_targets_by_diff, get_changed_lines
from src.ldb.scope import LdbTarget, iter_targets
from src.ldb.tracer import trace_function
from src.providers import create_provider
from src.providers.message_adapter import normalize_message


@dataclass
class LdbResult:
    success: bool
    bugs_found: int
    tests_written: int
    bugs_fixed: int


@dataclass
class _LdbBug:
    id: int
    file: str
    line: int
    description: str
    severity: str = "high"
    status: str = "open"
    test_file: str = ""


async def _collect_text(
    provider,
    prompt: str,
    system_prompt: str,
    working_dir: str,
    max_turns: int = 30,
    model: str = "",
    context_limit: int = 0,
    compact_threshold: float = 0.6,
) -> str:
    """Collect full text output from a provider call.

    ToolUseBlock messages are intentionally skipped — only text content is
    collected via ``get_text_content()``.  Tool calls are side-effects that
    should not appear in the synthesised output.
    """
    resolved_model = model or _provider_model(provider) or "unknown"
    effective_limit = context_limit or get_effective_context_limit(
        resolved_model, 0, provider=provider
    )

    run_kwargs: dict = {
        "prompt": prompt,
        "system_prompt": system_prompt,
        "working_dir": working_dir,
        "max_turns": max_turns,
        "model": model,
    }
    params = inspect.signature(provider.run).parameters
    accepts_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if "context_limit" in params or accepts_kwargs:
        run_kwargs["context_limit"] = effective_limit
    if "compact_threshold" in params or accepts_kwargs:
        run_kwargs["compact_threshold"] = compact_threshold

    parts: list[str] = []
    async for message in provider.run(**run_kwargs):
        adapted = normalize_message(message)
        if adapted:
            text = adapted.get_text_content()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _provider_model(provider) -> str:
    """Best-effort model name from provider instance."""
    cfg = getattr(provider, "config", None)
    if cfg:
        return getattr(cfg, "default_model", "") or ""
    return ""


@dataclass
class PlayerBug:
    """First incorrect block found by the Player agent."""
    block_id: int
    explanation: str


def parse_player_response(raw: str, trace_blocks: list | None = None) -> PlayerBug | None:
    """Parse Player's JSONL response and return only the FIRST incorrect block.

    Per PLAYER_PROMPT_LDB rule: "If multiple blocks have the same root cause,
    mark only the FIRST one as the bug."

    Each line is a JSON object:
        {"block": "BLOCK-N", "correct": true/false, "explanation": "..."}

    Returns a PlayerBug for the first ``correct: false`` entry, or ``None``
    when every block is correct (or the response is empty/unparseable).
    """
    if not raw or not raw.strip():
        return None

    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("correct") is False:
            block_name = obj.get("block", "")
            # Extract numeric id from "BLOCK-N"
            match = re.match(r"BLOCK-(\d+)", block_name)
            block_id = int(match.group(1)) if match else -1
            explanation = obj.get("explanation", "")
            return PlayerBug(block_id=block_id, explanation=explanation)

    return None


def _parse_bugs_json(raw: str, start_id: int = 1) -> list[_LdbBug]:
    """Extract bug entries from player JSON output."""
    candidates: list[str] = []
    for m in re.findall(r"```json\s*\n(.*?)\s*```", raw, re.DOTALL):
        candidates.append(m)
    for m in re.findall(r"```\s*\n(.*?)\s*```", raw, re.DOTALL):
        if m.strip().startswith("["):
            candidates.append(m)
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "[" and depth == 0:
            start = i
            depth += 1
        elif ch == "[":
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(raw[start : i + 1])
                start = None
    candidates.append(raw.strip())

    bugs: list[_LdbBug] = []
    for candidate in candidates:
        try:
            data = json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            bugs.append(
                _LdbBug(
                    id=start_id + len(bugs),
                    file=entry.get("file", ""),
                    line=int(entry.get("line", 0)),
                    description=entry.get("description", ""),
                    severity=entry.get("severity", "high"),
                )
            )
        if bugs:
            break
    return bugs


def _parse_tester_results(raw: str) -> dict[int, dict[str, str | None]]:
    """Extract tester JSON results keyed by bug id."""
    results: dict[int, dict[str, str | None]] = {}
    candidates: list[str] = []
    for m in re.findall(r"```json\s*\n(.*?)\s*```", raw, re.DOTALL):
        candidates.append(m)
    for m in re.findall(r"```\s*\n(.*?)\s*```", raw, re.DOTALL):
        if m.strip().startswith("["):
            candidates.append(m)
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "[" and depth == 0:
            start = i
            depth += 1
        elif ch == "[":
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(raw[start : i + 1])
                start = None
    candidates.append(raw.strip())

    for candidate in candidates:
        try:
            data = json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            bug_id = entry.get("bug_id")
            status = entry.get("status", "")
            test_file = entry.get("test_file")
            if isinstance(bug_id, int) and status in (
                "confirmed",
                "false_positive",
                "invalid_test",
            ):
                results[bug_id] = {
                    "status": status,
                    "test_file": test_file
                    if isinstance(test_file, str) and test_file
                    else None,
                }
        if results:
            break
    return results


class LdbRunner:
    def __init__(self, config) -> None:
        self.config = config
        self.working_dir = config.working_dir
        self._bugs: list[_LdbBug] = []
        self._bug_counter = 0

        self._input = create_provider(config.ldb_input_provider, {})
        self._player = create_provider(config.ldb_player_provider, {})
        self._tester = create_provider(config.ldb_tester_provider, {})
        self._fixer = create_provider(config.ldb_fixer_provider, {})

    def run_sync(self) -> LdbResult:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.run()).result()
        return asyncio.run(self.run())

    def _resolve_targets(self) -> list[LdbTarget]:
        """Return target list according to scope config.

        Resolution order:
          1. ``ldb_target_file`` set → that single function only.
          2. ``ldb_scope_all=True`` → every public function under working_dir.
          3. (default) → only functions overlapping ``git diff <ldb_diff_base>``,
             i.e. things actually changed since the base ref.  This is the
             pre-commit / pre-PR gate use case.
        """
        if self.config.ldb_target_file:
            return [self._single_target()]

        all_targets = list(iter_targets(self.working_dir))

        if self.config.ldb_scope_all:
            return all_targets

        base = self.config.ldb_diff_base or "HEAD"
        changed = get_changed_lines(self.working_dir, base=base)
        if not changed:
            print(
                f"ldb: no changes detected vs {base} — nothing to scan "
                f"(use --all to force-scan every public function)",
                flush=True,
            )
            return []
        return filter_targets_by_diff(all_targets, changed)

    def _single_target(self) -> LdbTarget:
        """Build an LdbTarget from the --file/--entry CLI config."""
        import ast as _ast

        file = self.config.ldb_target_file
        name = self.config.ldb_target_entry
        lineno = 0
        end_lineno = 0

        if file:
            p = Path(self.working_dir) / file
            if p.exists():
                try:
                    source = p.read_text(errors="replace")
                    tree = _ast.parse(source)
                    func_name = name.rpartition(".")[2] if "." in name else name
                    for node in _ast.walk(tree):
                        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                            if node.name == func_name:
                                lineno = node.lineno
                                end_lineno = getattr(
                                    node, "end_lineno", node.lineno
                                )
                                break
                except SyntaxError:
                    pass

        return LdbTarget(file=file, name=name, lineno=lineno, end_lineno=end_lineno)

    async def run(self) -> LdbResult:
        targets = self._resolve_targets()
        total_found = 0
        total_tests = 0
        total_fixed = 0

        print(f"ldb: {len(targets)} target(s)", flush=True)

        for t_idx, target in enumerate(targets, 1):
            self._bugs = []
            self._bug_counter = 0
            self.config.ldb_target_file = target.file
            self.config.ldb_target_entry = target.name

            print(f"\n[{t_idx}/{len(targets)}] {target.file}::{target.name}", flush=True)

            for _iteration in range(self.config.ldb_max_iterations):
                print(f"  iteration {_iteration + 1}/{self.config.ldb_max_iterations}", flush=True)
                source = self._read_target_source()

                print("  → input: synthesizing test cases...", flush=True)
                print("  → player: analysing blocks...", flush=True)
                new_bugs = await self._run_player(source, target.name)
                if new_bugs is None:
                    print("  player returned no output — stopping", flush=True)
                    break
                total_found += len(new_bugs)

                if not new_bugs:
                    print("  no bugs found — done", flush=True)
                    break

                for b in new_bugs:
                    print(f"  bug: [{b.severity}] {b.file}:{b.line} — {b.description}", flush=True)

                self._bugs.extend(new_bugs)
                self._append_bugs_md(target.file, target.name, new_bugs)

                open_bugs = [b for b in self._bugs if b.status == "open"]
                if open_bugs:
                    print(f"  → tester: verifying {len(open_bugs)} bug(s)...", flush=True)
                    test_count = await self._run_tester(open_bugs)
                    if test_count is None:
                        print("  tester returned no output — stopping", flush=True)
                        break
                    total_tests += test_count
                    confirmed = [b for b in self._bugs if b.status == "confirmed"]
                    false_pos = [b for b in self._bugs if b.status == "false_positive"]
                    print(f"  tester: {len(confirmed)} confirmed, {len(false_pos)} false-positive", flush=True)

                if self.config.ldb_mode == 3:
                    confirmed = [b for b in self._bugs if b.status == "confirmed"]
                    if confirmed:
                        print(f"  → fixer: fixing {len(confirmed)} bug(s)...", flush=True)
                        fixed_count = await self._run_fixer(confirmed)
                        if fixed_count is None:
                            print("  fixer returned no output — stopping", flush=True)
                            break
                        total_fixed += fixed_count
                        print(f"  fixer: {fixed_count} fixed", flush=True)
                        if fixed_count > 0:
                            summary = (
                                f"{target.file}:{target.name}"
                                f" — {fixed_count} bug(s) fixed"
                            )
                            bug_files = sorted(
                                {b.file for b in confirmed if b.file}
                                | {b.test_file for b in confirmed if b.test_file}
                            )
                            self._git_commit(summary, bug_files or None)

        return LdbResult(
            success=total_fixed > 0
            or total_found == 0
            or (self.config.ldb_mode == 2 and total_tests > 0),
            bugs_found=total_found,
            tests_written=total_tests,
            bugs_fixed=total_fixed,
        )

    def _read_target_source(self) -> str:
        """Read the target file source."""
        target = self.config.ldb_target_file
        if not target:
            return ""
        p = Path(self.working_dir) / target
        if p.exists():
            return p.read_text(errors="replace")
        if Path(target).exists():
            return Path(target).read_text(errors="replace")
        return ""

    async def _run_player(self, source: str, entry: str) -> list[_LdbBug] | None:
        """Run the player (bug-finder) on the target function."""
        inputs = await synthesize_inputs_llm(
            self._input,
            source,
            entry,
            working_dir=self.working_dir,
            model=self.config.ldb_input_model,
        )

        func_name = entry.rpartition(".")[2] if "." in entry else entry

        try:
            blocks = decompose_function(source, func_name)
        except (ValueError, Exception):
            blocks = []

        blocks_text = ""
        if blocks:
            lines = []
            for b in blocks:
                succ = ", ".join(str(s) for s in b.successors) or "none"
                stmts = "; ".join(b.statements[:5])
                lines.append(f"  Block {b.id}: [{stmts}] -> [{succ}]")
            blocks_text = "\n## Block Decomposition\n\n" + "\n".join(lines)

        inputs_text = ""
        if inputs:
            inputs_text = "\n## Synthesized Test Inputs\n\n" + "\n".join(inputs)

        trace_text = ""
        trace_sections: list[str] = []
        for test_input in inputs:
            trace = trace_function(
                source=source,
                test=test_input,
                entry=func_name,
                timeout=self.config.ldb_timeout_s,
            )
            if getattr(trace, "kind", "") != "ok":
                continue
            rendered_blocks = []
            for block in trace.blocks[:10]:
                rendered_blocks.append(
                    f"[BLOCK-{block.block_id}]\n" + "\n".join(block.rendered)
                )
            if rendered_blocks:
                trace_sections.append(
                    f"### Input: `{test_input}`\n" + "\n\n".join(rendered_blocks)
                )
        if trace_sections:
            trace_text = "\n## Runtime Trace\n\n" + "\n\n".join(trace_sections)

        target_file = self.config.ldb_target_file
        source_display = source[:6000] if len(source) > 6000 else source

        user_prompt = (
            f"## Target file: {target_file}\n"
            f"## Target function: `{entry}`\n\n"
            f"```python\n{source_display}\n```"
            f"{blocks_text}"
            f"{inputs_text}"
            f"{trace_text}"
        )

        raw = await _collect_text(
            self._player,
            prompt=user_prompt,
            system_prompt=PLAYER_PROMPT_LDB,
            working_dir=self.working_dir,
            max_turns=1,
            model=self.config.ldb_player_model,
        )

        if not raw.strip():
            return None

        start_id = max((b.id for b in self._bugs), default=0) + 1
        return _parse_bugs_json(raw, start_id=start_id)

    async def _run_tester(self, bugs: list[_LdbBug]) -> int | None:
        """Run the tester to confirm or refute bugs."""
        bug_list = "\n".join(
            f"{b.id}. [{b.severity}] {b.file}:{b.line} — {b.description}" for b in bugs
        )

        source = self._read_target_source()
        source_display = source[:6000] if len(source) > 6000 else source
        context = f"## Code Context\n\n```python\n{source_display}\n```"

        user_prompt = f"## Bug List to Verify\n\n{bug_list}\n\n{context}"

        raw = await _collect_text(
            self._tester,
            prompt=user_prompt,
            system_prompt=TESTER_PROMPT_LDB,
            working_dir=self.working_dir,
            max_turns=30,
            model=self.config.ldb_tester_model,
        )

        if not raw.strip():
            return None

        results = _parse_tester_results(raw)
        if not results:
            return 0

        tests_count = 0
        for bug in bugs:
            result = results.get(bug.id, {})
            status = result.get("status", "open")
            if status in ("confirmed", "false_positive", "invalid_test"):
                bug.status = status
                test_file = result.get("test_file")
                if test_file:
                    bug.test_file = test_file
                if bug.status == "confirmed" and not bug.test_file:
                    bug.status = "invalid_test"
                if status == "confirmed":
                    tests_count += 1

        return tests_count

    async def _run_fixer(self, confirmed: list[_LdbBug]) -> int | None:
        """Run the fixer to fix confirmed bugs."""
        bug_list = "\n".join(
            f"{b.id}. {b.file}:{b.line} — {b.description}"
            + (f"\n   Failing test: {b.test_file}" if b.test_file else "")
            for b in confirmed
        )

        source = self._read_target_source()
        source_display = source[:6000] if len(source) > 6000 else source
        context = f"## Code Context\n\n```python\n{source_display}\n```"

        user_prompt = f"## Confirmed Bugs to Fix\n\n{bug_list}\n\n{context}"

        try:
            await _collect_text(
                self._fixer,
                prompt=user_prompt,
                system_prompt=FIXER_PROMPT_LDB_ARCH,
                working_dir=self.working_dir,
                max_turns=50,
                model=self.config.ldb_fixer_model,
            )
        except Exception:
            return None

        return self._verify_fixes(confirmed)

    def _verify_fixes(self, bugs: list[_LdbBug]) -> int:
        """Mark bugs fixed only when their tester-generated pytest files now pass."""
        verification: dict[str, bool] = {}

        for bug in bugs:
            if not bug.test_file:
                continue

            test_path = Path(bug.test_file)
            if not test_path.is_absolute():
                test_path = Path(self.working_dir) / test_path
            key = str(test_path)

            if key not in verification:
                try:
                    result = subprocess.run(
                        ["python3", "-m", "pytest", str(test_path), "-x", "-q"],
                        cwd=self.working_dir,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    verification[key] = result.returncode == 0
                except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
                    verification[key] = False

            if verification[key]:
                bug.status = "fixed"

        return sum(1 for bug in bugs if bug.status == "fixed")

    def _git_commit(
        self, summary: str, files: list[str] | None = None
    ) -> None:
        """Stage and commit changes; uses selective ``git add -- <files>``
        when file paths are known, falling back to ``git add -A`` only
        when no specific files are available."""
        try:
            if files:
                subprocess.run(
                    ["git", "add", "--"] + files,
                    cwd=self.working_dir,
                    check=True,
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=self.working_dir,
                    check=True,
                    capture_output=True,
                )
            subprocess.run(
                ["git", "commit", "-m", f"ldb fix: {summary}"],
                cwd=self.working_dir,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:  # noqa: BLE001
            warnings.warn(
                f"ldb _git_commit failed: {(exc.stderr or b'').decode().strip() or exc}",
                stacklevel=2,
            )

    def _append_bugs_md(self, file: str, entry: str, bugs: list[_LdbBug]) -> None:
        """Append found bugs to ``{working_dir}/bugs.md`` (feedback memory).

        ALWAYS writes to the root of *working_dir* — same location as
        ``tero debug`` so there is a single source of truth.

        Format: ``## <file>::<entry> — Bug <n>`` headings (compatible with
        the ``## <file> — Bug <n>`` format that ``tero debug`` writes).

        Guards (#R10, #R16): if *bugs* is empty the method returns
        immediately — never writes a heading without content.
        """
        if not bugs:
            return  # Guard: don't write empty headings (#R10, #R16)

        path = Path(self.working_dir) / "bugs.md"
        existing = path.read_text() if path.exists() else "# Bugs\n\n"
        lines = [existing.rstrip()]
        for i, b in enumerate(bugs, 1):
            lines.append(f"\n## {file}::{entry} — Bug {i}")
            lines.append(f"**Line {b.line}:** {b.description}")
            lines.append(f"Severity: {b.severity}")
        path.write_text("\n".join(lines) + "\n")
