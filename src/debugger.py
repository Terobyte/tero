"""Debugger — automated bug-find-test-fix loop."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from src.config import Config, get_effective_context_limit
from src.role_router import _provider_model
from src.constants import (
    DEBUG_RETRY_BACKOFF_S,
    DEBUG_STALE_THRESHOLD_S,
    ANIMATION_SLEEP_S,
)
from src.debugger_bugs import (
    BugEntry,
    BugStatus,
    transition_bug,
    parse_bugs,
    merge_bugs,
    renumber_bugs,
    write_bugs_md,
    write_final_report,
)
from src.debugger_context import (
    build_context,
    build_block_context,
    plan_file_chunks,
    discover_public_functions,
    ContextCache,
)
from src.debugger_prompts import (
    INTENSITY_PROMPTS,
    TESTER_PROMPT,
    FIXER_PROMPT,
    INPUT_SYNTHESIZER_PROMPT,
)
from src.errors import ProviderError
from src.providers import create_provider
from src.providers.message_adapter import normalize_message


# Retry schedule: 60s → 120s → 240s (7 min total before giving up)
_RETRY_BACKOFF_S = DEBUG_RETRY_BACKOFF_S
_STALE_THRESHOLD_S = DEBUG_STALE_THRESHOLD_S


class _Pulse:
    """Animated status dot showing API call health.

    Green spinner: actively receiving data from the API.
    Yellow spinner: no data for 15+ seconds — might be slow or stuck.
    Red spinner with countdown: API error, retrying.
    """

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, prefix: str, status_fn=None):
        self._prefix = prefix
        self._status_fn = status_fn
        self._state = "active"  # active | waiting | retrying
        self._last_activity = time.time()
        self._task: asyncio.Task | None = None
        self._retry_label = ""

    async def start(self):
        self._last_activity = time.time()
        self._state = "active"
        self._task = asyncio.create_task(self._animate())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Clear the animation line so caller can print the result
        print(f"\r\033[K", end="", flush=True)

    def heartbeat(self):
        """Signal that data was received — keeps dot green."""
        self._last_activity = time.time()
        self._state = "active"
        self._retry_label = ""

    def set_retrying(self, attempt: int, remaining_s: int):
        """Signal retry wait — shows red dot with countdown."""
        self._state = "retrying"
        self._retry_label = f"retry {attempt} ({remaining_s}s)"

    async def _animate(self):
        idx = 0
        try:
            while True:
                # Auto-transition to yellow if no heartbeat for a while
                if (
                    self._state == "active"
                    and time.time() - self._last_activity > _STALE_THRESHOLD_S
                ):
                    self._state = "waiting"

                color = {
                    "active": "\033[32m",  # green
                    "waiting": "\033[33m",  # yellow
                    "retrying": "\033[31m",  # red
                }[self._state]

                frame = self._FRAMES[idx % len(self._FRAMES)]
                status = f" {self._status_fn()}" if self._status_fn else ""
                suffix = f" {self._retry_label}" if self._retry_label else ""
                print(
                    f"\r{self._prefix}{color}{frame}\033[0m{status}{suffix}\033[K",
                    end="",
                    flush=True,
                )

                idx += 1
                await asyncio.sleep(ANIMATION_SLEEP_S)
        except asyncio.CancelledError:
            pass


@dataclass
class DebuggerResult:
    """Result of a debugger run."""

    victory: bool
    iterations: int
    total_bugs: int
    fixed_bugs: int
    confirmed_bugs: int
    duration_s: float


@dataclass
class _CollectedTextResult:
    """Provider output plus whether the run completed successfully."""

    text: str
    completed: bool


class Debugger:
    """Automated bug-find-test-fix loop."""

    def __init__(self, config: Config):
        self.config = config
        self.working_dir = config.working_dir

        player_cfg = {}
        tester_cfg = {}
        fixer_cfg = {}
        synthesizer_cfg = {}

        self._player = create_provider(config.debug_player_provider, player_cfg)
        self._tester = create_provider(config.debug_tester_provider, tester_cfg)
        self._fixer = create_provider(config.debug_fixer_provider, fixer_cfg)
        self._synthesizer = create_provider(
            config.debug_synthesizer_provider, synthesizer_cfg
        )

        self._bugs: list[BugEntry] = []
        self._iteration = 0
        self._clean_passes = 0
        self._start_time = 0.0
        self._inconclusive_reason: str | None = None
        self._current_target: str = ""

        self._cache = ContextCache()

        if config.debug_all:
            self._targets = discover_public_functions(self.working_dir)
            self._chunks = []
            self._chunk_cursor = 0
        elif config.debug_file:
            entry = config.debug_entry
            if not entry:
                raise ValueError(
                    f"--entry is required when --file is specified. "
                    f"Use: debug --file {config.debug_file} --entry <function_name>"
                )
            file_list = [config.debug_file]
            self._chunks = plan_file_chunks(
                self.working_dir,
                cache=self._cache,
                level=config.debug_level,
                file_subset=file_list,
                entry_name=entry,
            )
            self._chunk_cursor = 0
            self._targets = []
        else:
            raise ValueError("Scope required: pass --all or --file [--entry <name>]")

    # ── Public API ────────────────────────────────────────────────────────────

    def run_sync(self) -> DebuggerResult:
        """Synchronous wrapper around run()."""
        return asyncio.run(self.run())

    async def run(self) -> DebuggerResult:
        """Main debugger loop."""
        self._start_time = time.time()
        bugs_md_path = f"{self.working_dir}/bugs.md"

        print(f"\n🔍 Debugger started")
        print(f"   Player:       {self.config.debug_player_provider}")
        print(f"   Tester:       {self.config.debug_tester_provider}")
        print(f"   Fixer:        {self.config.debug_fixer_provider}")
        print(f"   Synthesizer:  {self.config.debug_synthesizer_provider}")
        print(f"   Mode:    {self.config.debug_intensity} intensity")
        print(f"   Level:   {self.config.debug_level}")
        print(f"   Limit:   {self.config.debug_limit_mode}")
        if self.config.debug_all:
            print(f"   Scope:   --all ({len(self._targets)} public functions)")
        elif self.config.debug_file:
            entry_info = (
                f"::{self.config.debug_entry}" if self.config.debug_entry else ""
            )
            print(f"   Scope:   --file {self.config.debug_file}{entry_info}")
        else:
            total_files = sum(len(c) for c in self._chunks)
            print(f"   Files:   {total_files} across {len(self._chunks)} chunk(s)")
        print()

        while True:
            self._iteration += 1

            if self._targets:
                if self._should_stop():
                    break
                if not await self._run_next_target(bugs_md_path):
                    break
                continue

            if self._should_stop():
                break

            chunk = self._next_chunk()
            self._display_iteration_header(chunk)

            new_bugs = await self._run_player(chunk)
            if new_bugs is None:
                break

            if not new_bugs:
                self._clean_passes += 1
                print(
                    f"   No new bugs found. Clean passes: {self._clean_passes}/{self.config.debug_victory_threshold}"
                )
                if self._clean_passes >= self.config.debug_victory_threshold:
                    break
                continue
            else:
                self._clean_passes = 0

            self._bugs = merge_bugs(self._bugs, new_bugs)
            renumber_bugs(self._bugs)

            open_bugs = [b for b in self._bugs if b.status == "open"]

            synth_entries: list[dict[str, str]] = []
            if open_bugs and not self.config.debug_failing_test:
                synth_entries = await self._run_synthesizer(open_bugs)

            if open_bugs:
                tester_ok = await self._run_tester(
                    open_bugs,
                    synth_entries=synth_entries,
                    failing_test=self.config.debug_failing_test or None,
                )
                if not tester_ok:
                    break

            # Fixer: fix confirmed bugs
            confirmed = [b for b in self._bugs if b.status == "confirmed"]
            if confirmed:
                fixed_count = await self._run_fixer(confirmed)
                if fixed_count is None:
                    break
                self._cache.clear()  # files changed by fixer
                if fixed_count > 0:
                    bug_files = sorted({b.file for b in confirmed})
                    self._git_commit(self._iteration, fixed_count, bug_files)

            # Write once per iteration after all phases complete
            write_bugs_md(self._bugs, bugs_md_path, self._iteration)

        duration = time.time() - self._start_time
        victory = (
            self._clean_passes >= self.config.debug_victory_threshold
            and self._inconclusive_reason is None
        )
        fixed = [b for b in self._bugs if b.status == "fixed"]
        confirmed = [b for b in self._bugs if b.status == "confirmed"]

        final_path = str(Path(bugs_md_path).with_name("bugs_final.md"))
        write_final_report(
            self._bugs,
            final_path,
            duration,
            victory,
            victory_threshold=self.config.debug_victory_threshold,
        )
        self._display_final(victory, duration)

        return DebuggerResult(
            victory=victory,
            iterations=self._iteration,
            total_bugs=len(self._bugs),
            fixed_bugs=len(fixed),
            confirmed_bugs=len(confirmed),
            duration_s=duration,
        )

    # ── Whole-project target runner ──────────────────────────────────────────

    async def _run_next_target(self, bugs_md_path: str) -> bool:
        """Pop the next public function target and run the full LDB pipeline."""
        if not self._targets:
            return False

        target = self._targets.pop(0)
        file_name = str(target["file"])
        func_name = str(target["name"])
        lineno_raw = target.get("lineno")
        try:
            func_lineno = int(lineno_raw) if lineno_raw is not None else None
        except (TypeError, ValueError):
            func_lineno = None
        self._current_target = (
            f"{file_name}::{func_name}@{func_lineno}"
            if func_lineno is not None
            else f"{file_name}::{func_name}"
        )

        print(
            f"── Iteration {self._iteration} ── "
            f"{self._current_target} "
            f"({len(self._targets)} remaining) ──"
        )

        context = build_block_context(
            self.working_dir,
            file_name,
            entry_name=func_name,
            cache=self._cache,
            entry_lineno=func_lineno,
        )
        if not context:
            return bool(self._targets)

        prompts = INTENSITY_PROMPTS.get(
            self.config.debug_intensity, INTENSITY_PROMPTS["medium"]
        )

        all_raw: list[BugEntry] = []
        start_id = max((b.id for b in self._bugs), default=0) + 1

        for i, system_prompt in enumerate(prompts):
            prompt_label = (
                ["main", "anchor", "red_team", "boundary", "completeness"][i]
                if i < 5
                else f"prompt_{i}"
            )
            prefix = f"   Player [{self._current_target}:{prompt_label}] "
            pulse = _Pulse(prefix)
            await pulse.start()

            user_prompt = f"Analyze the following code for bugs.\n\n{context}"

            collected = await self._collect_text(
                self._player,
                prompt=user_prompt,
                system_prompt=system_prompt,
                max_turns=1,
                model=self.config.debug_player_model,
                pulse=pulse,
            )

            await pulse.stop()
            if not collected.completed or not collected.text.strip():
                print(
                    f"   Warning: player incomplete for '{self._current_target}:{prompt_label}', skipping"
                )
                return bool(self._targets)

            found = parse_bugs(collected.text, start_id=start_id + len(all_raw))
            print(f"{prefix}{len(found)} bugs")
            all_raw.extend(found)

        if not all_raw:
            self._clean_passes += 1
            print(f"   No new bugs found. Clean passes: {self._clean_passes}")
            write_bugs_md(self._bugs, bugs_md_path, self._iteration)
            return bool(self._targets)

        self._clean_passes = 0
        self._bugs = merge_bugs(self._bugs, all_raw)
        renumber_bugs(self._bugs)

        open_bugs = [b for b in self._bugs if b.status == "open"]
        synth_entries: list[dict[str, str]] = []
        if open_bugs and not self.config.debug_failing_test:
            synth_entries = await self._run_synthesizer(open_bugs)

        if open_bugs:
            tester_ok = await self._run_tester(
                open_bugs,
                synth_entries=synth_entries,
                failing_test=self.config.debug_failing_test or None,
            )
            if not tester_ok:
                print(
                    f"   Warning: tester failed for '{self._current_target}', skipping"
                )
                write_bugs_md(self._bugs, bugs_md_path, self._iteration)
                return bool(self._targets)

        confirmed = [b for b in self._bugs if b.status == "confirmed"]
        if confirmed:
            fixed_count = await self._run_fixer(confirmed)
            if fixed_count is None:
                print(
                    f"   Warning: fixer failed for '{self._current_target}', skipping"
                )
                write_bugs_md(self._bugs, bugs_md_path, self._iteration)
                return bool(self._targets)
            self._cache.clear()
            if fixed_count > 0:
                bug_files = sorted({b.file for b in confirmed})
                self._git_commit(self._iteration, fixed_count, bug_files)

        write_bugs_md(self._bugs, bugs_md_path, self._iteration)
        return bool(self._targets)

    # ── Player ────────────────────────────────────────────────────────────────

    async def _run_player(
        self, chunk: list[str] | None = None
    ) -> list[BugEntry] | None:
        """Run the player (bug-finder) on the given file chunk."""
        if self.config.debug_file:
            context = build_block_context(
                self.working_dir,
                self.config.debug_file,
                entry_name=self.config.debug_entry,
                cache=self._cache,
            )
        else:
            context = build_context(
                self.working_dir, file_subset=chunk, cache=self._cache
            )
        prompts = INTENSITY_PROMPTS.get(
            self.config.debug_intensity, INTENSITY_PROMPTS["medium"]
        )

        all_raw: list[BugEntry] = []
        start_id = max((b.id for b in self._bugs), default=0) + 1

        for i, system_prompt in enumerate(prompts):
            prompt_label = (
                ["main", "anchor", "red_team", "boundary", "completeness"][i]
                if i < 5
                else f"prompt_{i}"
            )
            prefix = f"   Player [{prompt_label}] "
            pulse = _Pulse(prefix)
            await pulse.start()

            user_prompt = f"Analyze the following code for bugs.\n\n{context}"

            collected = await self._collect_text(
                self._player,
                prompt=user_prompt,
                system_prompt=system_prompt,
                max_turns=1,
                model=self.config.debug_player_model,
                pulse=pulse,
            )

            await pulse.stop()
            if not collected.completed or not collected.text.strip():
                self._mark_inconclusive(
                    f"Player output was incomplete for prompt '{prompt_label}'."
                )
                return None

            found = parse_bugs(collected.text, start_id=start_id + len(all_raw))
            print(f"{prefix}{len(found)} bugs")
            all_raw.extend(found)

        return all_raw

    # ── Tester ────────────────────────────────────────────────────────────────

    async def _run_tester(
        self,
        bugs: list[BugEntry],
        synth_entries: list[dict[str, str]] | None = None,
        failing_test: str | None = None,
    ) -> bool:
        """Run the tester agent to confirm or refute bugs."""
        prefix = f"   Tester [{self.config.debug_tester_provider}] "
        pulse = _Pulse(prefix, status_fn=self._compact_status)
        await pulse.start()

        bug_list = "\n".join(
            f"{b.id}. [{b.severity}] {b.file}:{b.line} — {b.description}" for b in bugs
        )
        bug_files = sorted({b.file for b in bugs})
        context = build_context(
            self.working_dir, file_subset=bug_files, cache=self._cache
        )

        synth_block = ""
        if synth_entries:
            lines: list[str] = []
            for se in synth_entries:
                bid = se.get("bug_id", "?")
                ent = se.get("entries", "")
                if ent:
                    lines.append(f"Bug {bid}:\n{ent}")
            if lines:
                synth_block = (
                    "\n\n## Synthesized Test Inputs\n\n"
                    "Use these entry(...) lines as starting points for your tests:\n\n"
                    + "\n\n".join(lines)
                )

        failing_test_block = ""
        if failing_test:
            failing_test_block = (
                "\n\n## Failing Test\n\n"
                "The user provided a failing test that reproduces one or more of "
                "the bugs below. Run this test first, then verify the remaining "
                "bugs:\n\n"
                f"```\n{failing_test}\n```"
            )

        user_prompt = (
            f"## Bug List to Verify\n\n{bug_list}\n\n## Code Context\n\n{context}"
            f"{synth_block}{failing_test_block}"
        )

        collected = await self._collect_text(
            self._tester,
            prompt=user_prompt,
            system_prompt=TESTER_PROMPT,
            max_turns=30,
            model=self.config.debug_tester_model,
            pulse=pulse,
        )

        await pulse.stop()
        if not collected.completed or not collected.text.strip():
            self._mark_inconclusive("Tester output was incomplete.")
            return False

        results = self._parse_tester_results(collected.text)
        if not results:
            self._mark_inconclusive("Tester output could not be parsed.")
            return False

        confirmed = sum(
            1 for bug in bugs if results.get(bug.id, {}).get("status") == "confirmed"
        )
        print(f"{prefix}confirmed={confirmed}/{len(bugs)}")

        for bug in bugs:
            result = results.get(bug.id, {})
            status = result.get("status", "open")
            if status in (
                BugStatus.CONFIRMED,
                BugStatus.FALSE_POSITIVE,
                BugStatus.INVALID_TEST,
            ):
                transition_bug(bug, status)
                bug.test_file = result.get("test_file")
                # A "confirmed" bug with no test file can never be verified
                # by _verify_confirmed_fixes — treat it as invalid_test to
                # prevent it from looping in the fixer indefinitely.
                if bug.status == BugStatus.CONFIRMED and not bug.test_file:
                    transition_bug(bug, BugStatus.INVALID_TEST)
        return True

    # ── Fixer ─────────────────────────────────────────────────────────────────

    async def _run_fixer(self, confirmed: list[BugEntry]) -> int | None:
        """Run the fixer agent to fix confirmed bugs."""
        prefix = f"   Fixer [{self.config.debug_fixer_provider}] "
        pulse = _Pulse(prefix, status_fn=self._compact_status)
        await pulse.start()

        bug_list = "\n".join(
            f"{b.id}. {b.file}:{b.line} — {b.description}"
            + (f"\n   Failing test: {b.test_file}" if b.test_file else "")
            for b in confirmed
        )
        # Targeted context: only files with confirmed bugs
        bug_files = sorted({b.file for b in confirmed})
        context = build_context(
            self.working_dir, file_subset=bug_files, cache=self._cache
        )
        user_prompt = (
            f"## Confirmed Bugs to Fix\n\n{bug_list}\n\n## Code Context\n\n{context}"
        )

        collected = await self._collect_text(
            self._fixer,
            prompt=user_prompt,
            system_prompt=FIXER_PROMPT,
            max_turns=50,
            model=self.config.debug_fixer_model,
            pulse=pulse,
        )

        await pulse.stop()
        if not collected.completed or not collected.text.strip():
            self._mark_inconclusive("Fixer output was incomplete.")
            return None

        fixed_count = self._verify_confirmed_fixes(confirmed)
        print(f"{prefix}done")
        return fixed_count

    # ── Input Synthesizer ─────────────────────────────────────────────────────

    async def _run_synthesizer(self, bugs: list[BugEntry]) -> list[dict[str, str]]:
        """Run the input-synthesizer agent to generate test entries for confirmed bugs.

        For each confirmed bug, the synthesizer receives the function signature,
        type annotations, docstring, and surrounding call-site context, then
        produces 2–3 ``entry(...)`` lines that the tester can use as a starting
        point.

        Returns a list of dicts ``{"bug_id": int, "entries": str}`` where
        *entries* is a newline-separated block of ``entry(...)`` lines.
        """
        results: list[dict[str, str]] = []
        bug_files = sorted({b.file for b in bugs})
        context = build_context(
            self.working_dir, file_subset=bug_files, cache=self._cache
        )

        for bug in bugs:
            prefix = f"   Synthesizer [bug {bug.id}] "
            pulse = _Pulse(prefix)
            await pulse.start()

            bug_context = self._extract_bug_context(bug, context)
            user_prompt = (
                f"## Target function (bug {bug.id})\n\n"
                f"File: {bug.file}:{bug.line}\n"
                f"Description: {bug.description}\n\n"
                f"## Surrounding code\n\n{bug_context}"
            )

            collected = await self._collect_text(
                self._synthesizer,
                prompt=user_prompt,
                system_prompt=INPUT_SYNTHESIZER_PROMPT,
                max_turns=1,
                model=self.config.debug_synthesizer_model,
                pulse=pulse,
            )

            await pulse.stop()
            if collected.completed and collected.text.strip():
                entries = self._parse_entry_lines(collected.text)
                if entries:
                    results.append({"bug_id": str(bug.id), "entries": entries})
                    print(f"{prefix}{len(entries.splitlines())} entries")
                else:
                    print(f"{prefix}no valid entries")
            else:
                print(f"{prefix}no output")

        return results

    def _extract_bug_context(self, bug: BugEntry, full_context: str) -> str:
        """Extract the code context around a specific bug's file and line."""
        file_path = Path(self.working_dir) / bug.file
        if not file_path.exists():
            return full_context[:2000]

        try:
            content = file_path.read_text(errors="replace")
        except OSError:
            return full_context[:2000]

        lines = content.splitlines()
        start = max(0, bug.line - 30)
        end = min(len(lines), bug.line + 30)
        snippet = "\n".join(f"{i + 1:>4}: {lines[i]}" for i in range(start, end))

        import ast as _ast

        func_name: str | None = None
        sig_lines: list[str] = []
        docstring_text = ""
        try:
            tree = _ast.parse(content)
            for node in _ast.walk(tree):
                if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                if not (
                    node.lineno <= bug.line <= getattr(node, "end_lineno", node.lineno)
                ):
                    continue
                func_name = node.name
                sig_lines.append(
                    content.splitlines()[node.lineno - 1].strip().rstrip(":")
                )
                ds = _ast.get_docstring(node, clean=True)
                if ds:
                    docstring_text = ds
                break
        except SyntaxError:
            pass

        call_sites = ""
        if func_name:
            call_sites = self._find_call_sites(func_name, bug.file)

        parts: list[str] = []
        if sig_lines:
            parts.append(f"Signature: `{sig_lines[0]}`")
        if docstring_text:
            parts.append(f"Docstring: {docstring_text}")
        if call_sites:
            parts.append(f"Neighboring call sites:\n{call_sites}")
        parts.append(f"```python\n{snippet}\n```")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_entry_lines(raw: str) -> str:
        """Extract entry(...) lines from synthesizer output."""
        import re as _re

        matches = _re.findall(r"^entry\(.*\)\s*$", raw, _re.MULTILINE)
        return "\n".join(matches) if matches else ""

    def _find_call_sites(
        self, func_name: str, bug_file: str, max_sites: int = 6
    ) -> str:
        """Search the working tree for neighboring call sites of *func_name*.

        Scans Python files under ``self.working_dir`` for lines containing
        ``func_name(`` or ``func_name (``.  Returns up to *max_sites* snippets
        formatted as ``file:line  code``.
        """
        import re as _re

        pattern = _re.compile(rf"(?<!\w){_re.escape(func_name)}\s*\(", _re.UNICODE)
        hits: list[str] = []
        working = Path(self.working_dir)
        for py_file in sorted(working.rglob("*.py")):
            rel = py_file.relative_to(working)
            try:
                file_lines = py_file.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for idx, line_text in enumerate(file_lines):
                stripped = line_text.lstrip()
                if stripped.startswith("def ") or stripped.startswith("async def "):
                    continue
                if pattern.search(line_text):
                    hits.append(f"  {rel}:{idx + 1}  {line_text.strip()}")
                    if len(hits) >= max_sites:
                        return "\n".join(hits)
        return "\n".join(hits)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _collect_text(
        self,
        provider,
        prompt: str,
        system_prompt: str,
        max_turns: int,
        model: str = "",
        pulse: _Pulse | None = None,
    ) -> _CollectedTextResult:
        """Collect text from provider with retry and live pulse status."""
        resolved_model = model or _provider_model(provider) or "unknown"
        effective_limit = get_effective_context_limit(
            resolved_model, self.config.context_limit, provider=provider
        )

        params = inspect.signature(provider.run).parameters
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        has_compaction = "context_limit" in params or accepts_kwargs

        # Providers without compaction (e.g. OpenCode) need a tighter turn cap:
        # without mid-run compaction, the context window fills up and the model
        # hangs. Reserve 50% for initial prompt+context; 25% otherwise.
        _TOKENS_PER_TOOL_TURN = 3_000
        reserve = 0.25 if has_compaction else 0.50
        safe_max_turns = max(
            5, int(effective_limit * (1 - reserve)) // _TOKENS_PER_TOOL_TURN
        )
        actual_max_turns = min(max_turns, safe_max_turns)

        run_kwargs: dict = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "working_dir": self.working_dir,
            "max_turns": actual_max_turns,
            "model": model,
        }
        if "context_limit" in params or accepts_kwargs:
            run_kwargs["context_limit"] = effective_limit
        if "compact_threshold" in params or accepts_kwargs:
            run_kwargs["compact_threshold"] = self.config.compact_threshold

        for attempt in range(len(_RETRY_BACKOFF_S) + 1):
            parts: list[str] = []
            try:
                async for message in provider.run(**run_kwargs):
                    if pulse:
                        pulse.heartbeat()
                    self._extract_text(message, parts)
                return _CollectedTextResult(text="\n".join(parts), completed=True)
            except ProviderError:
                if attempt >= len(_RETRY_BACKOFF_S):
                    return _CollectedTextResult(text="\n".join(parts), completed=False)

                # Retry with countdown visible on the pulse
                wait = _RETRY_BACKOFF_S[attempt]
                if pulse:
                    for remaining in range(wait, 0, -1):
                        pulse.set_retrying(attempt + 1, remaining)
                        await asyncio.sleep(1)
                else:
                    await asyncio.sleep(wait)

    @staticmethod
    def _extract_text(message, parts: list[str]) -> None:
        """Extract text from any provider message format into parts list."""
        adapted = normalize_message(message)
        if adapted:
            text = adapted.get_text_content()
            if text:
                parts.append(text)

    def _should_stop(self) -> bool:
        """Check whether to stop the loop based on limit_mode."""
        mode = self.config.debug_limit_mode
        if mode == "infinite":
            return False
        if mode == "iterations":
            return self._iteration > self.config.debug_limit_value
        if mode == "time":
            elapsed_min = (time.time() - self._start_time) / 60
            return elapsed_min >= self.config.debug_limit_value
        return False

    def _next_chunk(self) -> list[str] | None:
        """Return the next file chunk and advance the cursor."""
        if not self._chunks:
            return None
        chunk = self._chunks[self._chunk_cursor % len(self._chunks)]
        self._chunk_cursor += 1
        return chunk

    def _display_iteration_header(self, chunk: list[str] | None) -> None:
        """Print iteration header with chunk info."""
        if self.config.debug_file:
            entry_info = (
                f"::{self.config.debug_entry}" if self.config.debug_entry else ""
            )
            print(
                f"── Iteration {self._iteration} ── file {self.config.debug_file}{entry_info} ──"
            )
        elif chunk and self._chunks:
            idx = ((self._chunk_cursor - 1) % len(self._chunks)) + 1
            total = len(self._chunks)
            names = [f.rsplit("/", 1)[-1] for f in chunk[:4]]
            summary = ", ".join(names)
            if len(chunk) > 4:
                summary += f" +{len(chunk) - 4}"
            print(
                f"── Iteration {self._iteration} ── chunk {idx}/{total} [{summary}] ──"
            )
        else:
            print(f"── Iteration {self._iteration} ──────────────────────────────────")

    def _git_commit(
        self, iteration: int, count: int, files: list[str] | None = None
    ) -> None:
        """Commit changes after a fix iteration."""
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
            msg = f"fix(debugger): iteration {iteration} — {count} bug(s) fixed"
            subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.working_dir,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # No changes to commit or git not available

    def _parse_tester_results(self, raw: str) -> dict[int, dict[str, str | None]]:
        """Extract tester JSON results keyed by bug id."""
        results: dict[int, dict[str, str | None]] = {}

        # Try to extract JSON array from tester output
        candidates: list[str] = []
        for m in re.findall(r"```json\s*\n(.*?)\s*```", raw, re.DOTALL):
            candidates.append(m)
        for m in re.findall(r"```\s*\n(.*?)\s*```", raw, re.DOTALL):
            if m.strip().startswith("["):
                candidates.append(m)
        # Bracket-matched arrays
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

    def _verify_confirmed_fixes(self, bugs: list[BugEntry]) -> int:
        """Mark bugs fixed only when their tester-generated pytest files now pass."""
        verification_results: dict[str, bool] = {}

        for bug in bugs:
            if not bug.test_file:
                continue

            test_path = Path(bug.test_file)
            if not test_path.is_absolute():
                test_path = Path(self.working_dir) / test_path
            test_key = str(test_path)

            if test_key not in verification_results:
                try:
                    result = subprocess.run(
                        ["python3", "-m", "pytest", str(test_path), "-x", "-q"],
                        cwd=self.working_dir,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    verification_results[test_key] = result.returncode == 0
                except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
                    verification_results[test_key] = False

            if verification_results[test_key]:
                transition_bug(bug, BugStatus.FIXED)

        return sum(1 for bug in bugs if bug.status == BugStatus.FIXED)

    def _mark_inconclusive(self, reason: str) -> None:
        """Record why the debugger could not safely continue."""
        if self._inconclusive_reason is None:
            self._inconclusive_reason = reason

    # ── Color helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _red(n: int) -> str:
        return f"\033[31m{n}\033[0m"

    @staticmethod
    def _yellow(n: int) -> str:
        return f"\033[33m{n}\033[0m"

    @staticmethod
    def _green(n: int) -> str:
        return f"\033[32m{n}\033[0m"

    @staticmethod
    def _grey(n: int) -> str:
        return f"\033[90m{n}\033[0m"

    def _compact_status(self) -> str:
        """Format bug counts as colored numbers for inline display."""
        o = sum(1 for b in self._bugs if b.status == "open")
        c = sum(1 for b in self._bugs if b.status == "confirmed")
        f = sum(1 for b in self._bugs if b.status == "fixed")
        p = sum(1 for b in self._bugs if b.status in ("false_positive", "invalid_test"))
        return f"{self._red(o)} {self._yellow(c)} {self._green(f)} {self._grey(p)}"

    def _display_final(self, victory: bool, duration: float) -> None:
        """Print final summary with colored counters."""
        mins = int(duration // 60)
        secs = int(duration % 60)
        icon = "🏆" if victory else "⏹"
        fixed = sum(1 for b in self._bugs if b.status == "fixed")
        confirmed = sum(1 for b in self._bugs if b.status == "confirmed")
        open_count = sum(1 for b in self._bugs if b.status == "open")
        fp_count = sum(
            1 for b in self._bugs if b.status in ("false_positive", "invalid_test")
        )
        total = len(self._bugs)
        print()
        print(f"{icon} Debugger finished in {mins}m {secs}s")
        print(
            f"   Total: {total} | "
            f"Fixed: {self._green(fixed)} | "
            f"Confirmed: {self._yellow(confirmed)} | "
            f"Open: {self._red(open_count)} | "
            f"FP: {self._grey(fp_count)}"
        )
        if self._inconclusive_reason:
            print(f"   Inconclusive: {self._inconclusive_reason}")
        if victory:
            print(
                f"   \033[32mVictory!\033[0m No bugs found in {self.config.debug_victory_threshold} consecutive passes."
            )
