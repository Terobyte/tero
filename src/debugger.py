"""Debugger — automated bug-find-test-fix loop."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from src.config import Config
from src.debugger_bugs import (
    BugEntry,
    extract_json_from_text,
    renumber_bugs,
    strip_trailing_commas,
    write_bugs_md,
    write_final_report,
)
from src.debugger_graph import build_dependency_graph, DependencyGraph
from src.debugger_contracts import FileContract, extract_contracts
from src.debugger_edges import analyze_edges, run_deep_dives, findings_to_bugs
from src.debugger_intra import analyze_all_files
from src.debugger_llm import collect_text, CollectedTextResult, _STALE_THRESHOLD_S
from src.debugger_prompts import TESTER_PROMPT, FIXER_PROMPT
from src.debugger_render import build_context_from_graph
from src.providers import create_provider


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
                if self._state == "active" and time.time() - self._last_activity > _STALE_THRESHOLD_S:
                    self._state = "waiting"

                color = {
                    "active": "\033[32m",    # green
                    "waiting": "\033[33m",   # yellow
                    "retrying": "\033[31m",  # red
                }[self._state]

                frame = self._FRAMES[idx % len(self._FRAMES)]
                status = f" {self._status_fn()}" if self._status_fn else ""
                suffix = f" {self._retry_label}" if self._retry_label else ""
                print(f"\r{self._prefix}{color}{frame}\033[0m{status}{suffix}\033[K", end="", flush=True)

                idx += 1
                await asyncio.sleep(0.12)
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


class Debugger:
    """Automated bug-find-test-fix loop."""

    def __init__(self, config: Config):
        self.config = config
        self.working_dir = config.working_dir

        player_cfg = {}
        tester_cfg = {}
        fixer_cfg = {}

        self._player = create_provider(config.debug_player_provider, player_cfg)
        self._tester = create_provider(config.debug_tester_provider, tester_cfg)
        self._fixer = create_provider(config.debug_fixer_provider, fixer_cfg)

        self._bugs: list[BugEntry] = []
        self._iteration = 0
        self._clean_passes = 0
        self._start_time = 0.0
        self._inconclusive_reason: str | None = None

        # Graph-aware state — dependency graph and contract cache
        self._graph: DependencyGraph | None = None
        self._contracts: dict[str, FileContract] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def run_sync(self) -> DebuggerResult:
        """Synchronous wrapper around run()."""
        return asyncio.run(self.run())

    async def run(self) -> DebuggerResult:
        """Main debugger loop — 6-phase graph-aware pipeline."""
        self._start_time = time.time()
        bugs_md_path = f"{self.working_dir}/bugs.md"

        # Initial build for the startup banner stats
        self._graph = build_dependency_graph(self.working_dir)
        total_files = len(self._graph.files)
        total_edges = len(self._graph.edges)
        total_sccs = len(self._graph.sccs)

        print(f"\n🔍 Debugger started")
        print(f"   Player:  {self.config.debug_player_provider}")
        print(f"   Tester:  {self.config.debug_tester_provider}")
        print(f"   Fixer:   {self.config.debug_fixer_provider}")
        print(f"   Mode:    {self.config.debug_intensity} intensity")
        print(f"   Limit:   {self.config.debug_limit_mode}")
        print(f"   Dive:    {self.config.debug_deep_dive_mode}")
        print(f"   Files:   {total_files} files, {total_edges} edges, {total_sccs} SCCs")
        print()

        if total_files == 0:
            print(f"   ✗ No Python files found in {self.working_dir}")
            print(f"   Check --working-dir or run from the target project directory.")
            return DebuggerResult(
                victory=False,
                iterations=0,
                total_bugs=0,
                fixed_bugs=0,
                confirmed_bugs=0,
                duration_s=time.time() - self._start_time,
            )

        while True:
            if self._should_stop():
                break

            self._iteration += 1
            pre_count = len(self._bugs)

            # Phase 0: Rebuild dependency graph so fixer edits are visible
            self._display_phase_header("Phase 0: Dependency Graph")
            self._graph = build_dependency_graph(self.working_dir)
            print(f"   files={len(self._graph.files)} edges={len(self._graph.edges)} SCCs={len(self._graph.sccs)}")

            # Phase 1: Extract contracts (cached between iterations)
            self._display_phase_header("Phase 1: Contracts")
            pre_contract_count = sum(
                1 for rp in self._graph.files
                if rp in self._contracts
                and self._contracts[rp].source_hash == self._graph.files[rp].source_hash
            )
            self._contracts = await extract_contracts(
                self._graph, self._player, self.config, self.working_dir
            )
            total_files = len(self._graph.files)
            print(f"   fresh={total_files - pre_contract_count} cached={pre_contract_count}")

            # Phase 2: Cross-file edge analysis
            self._display_phase_header("Phase 2: Edge Analysis")
            high_findings, medium_findings = await analyze_edges(
                self._graph, self._contracts, self._player, self.config, self.working_dir
            )
            print(f"   high={len(high_findings)} medium={len(medium_findings)}")

            # Phase 3: Deep dives to confirm medium findings
            self._display_phase_header("Phase 3: Deep Dives")
            confirmed_findings = await run_deep_dives(
                medium_findings, self.working_dir, self._player, self.config
            )
            print(f"   confirmed={len(confirmed_findings)}/{len(medium_findings)}")

            # Phase 4: Intra-file analysis (merges edge bugs + per-file analysis)
            self._display_phase_header("Phase 4: Intra-File Analysis")
            edge_bugs = findings_to_bugs(
                high_findings + confirmed_findings,
                start_id=max((b.id for b in self._bugs), default=0) + 1,
            )
            self._bugs = await analyze_all_files(
                self._graph, self._contracts, self._player, self.config,
                self.working_dir, existing_bugs=self._bugs + edge_bugs,
            )
            renumber_bugs(self._bugs)

            new_found = len(self._bugs) > pre_count
            open_or_confirmed = [b for b in self._bugs if b.status in ("open", "confirmed")]

            if new_found:
                self._clean_passes = 0
            elif not open_or_confirmed:
                # No new bugs AND nothing left unresolved → clean pass
                self._clean_passes += 1
                print(f"   No open bugs. Clean passes: {self._clean_passes}/{self.config.debug_victory_threshold}")
                write_bugs_md(self._bugs, bugs_md_path, self._iteration)
                if self._clean_passes >= self.config.debug_victory_threshold:
                    break
                continue
            # else: no new bugs but unresolved bugs remain → fall through to Phase 5

            # Phase 5: Test & Fix
            self._display_phase_header("Phase 5: Test & Fix")
            open_bugs = [b for b in self._bugs if b.status == "open"]
            if open_bugs:
                tester_ok = await self._run_tester(open_bugs)
                if not tester_ok:
                    break

            confirmed = [b for b in self._bugs if b.status == "confirmed"]
            if confirmed:
                fixed_count = await self._run_fixer(confirmed)
                if fixed_count is None:
                    break
                if fixed_count > 0:
                    self._git_commit(self._iteration, fixed_count)

            write_bugs_md(self._bugs, bugs_md_path, self._iteration)

        duration = time.time() - self._start_time
        victory = (
            self._clean_passes >= self.config.debug_victory_threshold
            and self._inconclusive_reason is None
        )
        fixed = [b for b in self._bugs if b.status == "fixed"]
        confirmed = [b for b in self._bugs if b.status == "confirmed"]

        write_final_report(self._bugs, bugs_md_path.replace("bugs.md", "bugs_final.md"), duration, victory)
        self._display_final(victory, duration)

        return DebuggerResult(
            victory=victory,
            iterations=self._iteration,
            total_bugs=len(self._bugs),
            fixed_bugs=len(fixed),
            confirmed_bugs=len(confirmed),
            duration_s=duration,
        )

    def _display_phase_header(self, name: str) -> None:
        print(f"\n── {name} {'─' * max(1, 50 - len(name))}")

    # ── Tester ────────────────────────────────────────────────────────────────

    async def _run_tester(self, bugs: list[BugEntry]) -> bool:
        """Run the tester agent to confirm or refute bugs."""
        prefix = f"   Tester [{self.config.debug_tester_provider}] "
        pulse = _Pulse(prefix, status_fn=self._compact_status)
        await pulse.start()
        try:
            bug_list = "\n".join(
                f"{b.id}. [{b.severity}] {b.file}:{b.line} — {b.description}"
                for b in bugs
            )
            # Targeted context: only files mentioned in bugs
            bug_files = sorted({b.file for b in bugs})
            context = build_context_from_graph(self._graph, self._contracts, self.working_dir, file_subset=bug_files)
            user_prompt = (
                f"## Bug List to Verify\n\n{bug_list}\n\n"
                f"## Code Context\n\n{context}"
            )

            collected = await collect_text(
                self._tester,
                prompt=user_prompt,
                system_prompt=TESTER_PROMPT,
                working_dir=self.working_dir,
                max_turns=30,
                model=self.config.debug_tester_model,
                pulse=pulse,
            )
        finally:
            await pulse.stop()

        if not collected.completed or not collected.text.strip():
            self._mark_inconclusive("Tester output was incomplete.")
            return False

        results = self._parse_tester_results(collected.text)
        if not results:
            self._mark_inconclusive("Tester output could not be parsed.")
            return False

        confirmed = sum(
            1
            for bug in bugs
            if results.get(bug.id, {}).get("status") == "confirmed"
        )
        print(f"{prefix}confirmed={confirmed}/{len(bugs)}")

        for bug in bugs:
            result = results.get(bug.id, {})
            status = result.get("status", "open")
            if status in ("confirmed", "false_positive", "invalid_test"):
                bug.status = status
                bug.test_file = result.get("test_file")
                # A "confirmed" bug with no test file can never be verified
                # by _verify_confirmed_fixes — treat it as invalid_test to
                # prevent it from looping in the fixer indefinitely.
                if bug.status == "confirmed" and not bug.test_file:
                    bug.status = "invalid_test"
        return True

    # ── Fixer ─────────────────────────────────────────────────────────────────

    async def _run_fixer(self, confirmed: list[BugEntry]) -> int | None:
        """Run the fixer agent to fix confirmed bugs."""
        prefix = f"   Fixer [{self.config.debug_fixer_provider}] "
        pulse = _Pulse(prefix, status_fn=self._compact_status)
        await pulse.start()
        try:
            bug_list = "\n".join(
                f"{b.id}. {b.file}:{b.line} — {b.description}"
                + (f"\n   Failing test: {b.test_file}" if b.test_file else "")
                for b in confirmed
            )
            # Targeted context: only files with confirmed bugs
            bug_files = sorted({b.file for b in confirmed})
            context = build_context_from_graph(self._graph, self._contracts, self.working_dir, file_subset=bug_files)
            user_prompt = (
                f"## Confirmed Bugs to Fix\n\n{bug_list}\n\n"
                f"## Code Context\n\n{context}"
            )

            collected = await collect_text(
                self._fixer,
                prompt=user_prompt,
                system_prompt=FIXER_PROMPT,
                working_dir=self.working_dir,
                max_turns=50,
                model=self.config.debug_fixer_model,
                pulse=pulse,
            )
        finally:
            await pulse.stop()

        if not collected.completed or not collected.text.strip():
            self._mark_inconclusive("Fixer output was incomplete.")
            return None

        fixed_count = self._verify_confirmed_fixes(confirmed)
        print(f"{prefix}done")
        return fixed_count

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _should_stop(self) -> bool:
        """Check whether to stop the loop based on limit_mode.

        Uses ``>=`` so that ``debug_limit_value=N`` means exactly N iterations
        (checked before incrementing self._iteration).
        """
        mode = self.config.debug_limit_mode
        if mode == "infinite":
            return False
        if mode == "iterations":
            return self._iteration >= self.config.debug_limit_value
        if mode == "time":
            elapsed_min = (time.time() - self._start_time) / 60
            return elapsed_min >= self.config.debug_limit_value
        return False

    def _git_commit(self, iteration: int, count: int) -> None:
        """Commit debugger-generated changes (source fixes + output files) after a fix iteration."""
        try:
            # Stage only Python source files and known debug output files;
            # never stage unrelated user changes (notes, config, etc.)
            subprocess.run(["git", "add", "--", "*.py"], cwd=self.working_dir, check=False, capture_output=True)
            for fname in ("bugs.md", "bugs_final.md"):
                subprocess.run(["git", "add", fname], cwd=self.working_dir, check=False, capture_output=True)
            msg = f"fix(debugger): iteration {iteration} — {count} bug(s) fixed"
            subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.working_dir,
                check=True,
                capture_output=True,
            )
        except Exception:
            pass  # No changes to commit, git not available, or disk/permission issue

    def _parse_tester_results(self, raw: str) -> dict[int, dict[str, str | None]]:
        """Extract tester JSON results keyed by bug id."""
        results: dict[int, dict[str, str | None]] = {}

        for candidate in extract_json_from_text(raw):
            try:
                data = json.loads(strip_trailing_commas(candidate))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, list):
                continue
            candidate_results: dict[int, dict[str, str | None]] = {}
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                bug_id = entry.get("bug_id")
                status = entry.get("status", "")
                test_file = entry.get("test_file")
                if isinstance(bug_id, int) and status in (
                    "confirmed", "false_positive", "invalid_test"
                ):
                    candidate_results[bug_id] = {
                        "status": status,
                        "test_file": test_file if isinstance(test_file, str) and test_file else None,
                    }
            if candidate_results:
                results = candidate_results

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
                bug.status = "fixed"

        return sum(1 for bug in bugs if bug.status == "fixed")

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
        fp_count = sum(1 for b in self._bugs if b.status in ("false_positive", "invalid_test"))
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
            print(f"   \033[32mVictory!\033[0m No bugs found in {self.config.debug_victory_threshold} consecutive passes.")
