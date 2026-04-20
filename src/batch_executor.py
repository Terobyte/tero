"""Batch execution: groups PlanItems into phases, executes one Player turn per phase."""

import inspect
import re
from dataclasses import dataclass, replace

from src.errors import ProviderError
from src.feedback import Approved, Feedback
from src.plan_tracker import PlanItem, Phase


# --- Prompt / parser ---

def build_batch_prompt(
    phase: Phase,
    completed_steps: list[str],
    coach_feedback: str = "",
) -> str:
    """Build Player prompt for executing a phase.

    completed_steps: PlanItem.text values done in a previous attempt.
    coach_feedback:  Coach rejection message (empty on first try).
    Player must output 'Step X done: [desc]' per step and
    'PHASE_COMPLETE: <phase.name>' at the end.
    """
    remaining = [
        (i + 1, step)
        for i, step in enumerate(phase.steps)
        if step.text not in completed_steps
    ]
    sections: list[str] = []

    if coach_feedback:
        sections.append(
            f"STRUCTURED REVIEW FEEDBACK (authoritative):\n{coach_feedback}\n\n"
            "Fix ONLY these numbered issues. Ignore reviewer chatter or meta commentary from earlier attempts."
        )

    if completed_steps:
        done_list = "\n".join(f"  ✅ {s}" for s in completed_steps)
        sections.append(f"Already completed in this attempt:\n{done_list}")

    if remaining:
        steps_list = "\n".join(f"  {step_num}. {step.text}" for step_num, step in remaining)
        sections.append(
            "Before editing files, verify whether each planned step is already satisfied in the current workspace.\n"
            "If a step is already implemented, do not rewrite it; treat it as complete, cite proof, and continue with only the missing work.\n\n"
            "Use the available tools for file inspection, commands, and edits.\n"
            "This environment does provide filesystem inspection, command execution, and edit tools.\n"
            "A claim that tools are unavailable in this session is incorrect and will be rejected.\n"
            "Do not print raw shell or patch commands like `bash -lc`, `python ...`, or `apply_patch` as if they were already executed.\n\n"
            f"Execute ALL of the following steps in sequence.\n"
            f"Complete ALL steps before returning.\n\n"
            f"Phase: {phase.name}\n"
            f"Steps:\n{steps_list}"
        )
        confirmations = "\n".join(
            f"  Step {step_num} done: [one-line description]"
            for step_num, _step in remaining
        )
        sections.append(
            f"After completing each step, output exactly:\n{confirmations}\n\n"
            f"When ALL steps are complete, include:\n"
            f"  PHASE_COMPLETE: {phase.name}\n"
            f"  What changed:\n"
            f"  - [short summary]\n"
            f"  Evidence:\n"
            f"  - [file reference or proof]\n"
            f"  Verification:\n"
            f"  - [command or check]"
        )
    else:
        sections.append(
            f"All planned steps for phase `{phase.name}` are already complete.\n"
            f"Do NOT redo the implementation unless retry feedback asks for a fix.\n"
            f"Only send the required completion report with proof."
        )
        sections.append(
            f"Send exactly this completion report structure:\n"
            f"  PHASE_COMPLETE: {phase.name}\n"
            f"  What changed:\n"
            f"  - [short summary or 'No code changes were needed']\n"
            f"  Evidence:\n"
            f"  - [file reference or proof]\n"
            f"  Verification:\n"
            f"  - [command or check]"
        )

    return "\n\n".join(sections)


_STEP_DONE_RE = re.compile(r"step\s+(\d+)\s+done\s*:", re.IGNORECASE)
_PHASE_COMPLETE_LINE_RE = re.compile(
    r"^\s*PHASE_COMPLETE\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)
_DISCUSSION_CONTINUATION_RE = re.compile(
    r"\b(?:let\s+me|when\s+i(?:'m|\s+am)|but\s+first|first\s+let\s+me|"
    r"explor\w*|think\s+about|i\s+should|i\s+will|still\s+need|need\s+to|"
    r"before\s+i|going\s+to)\b",
    re.IGNORECASE,
)
_REQUIRED_REPORT_HEADERS = ("what changed:", "evidence:", "verification:")
_TOOLS_UNAVAILABLE_PATTERNS = (
    "tools are not available",
    "tools unavailable",
    "tools are unavailable",
    "filesystem tools are not available",
    "command tools are not available",
    "editing tools are not available",
    "inspection/edit/execute",
    "не доступны инструменты",
    "недоступны инструменты",
    "нет инструментов",
    "инструменты доступа к файловой системе",
    "инструменты запуска команд",
    "инструменты редактирования файлов",
)


def _phase_complete_matches(raw_value: str, phase_name: str) -> bool:
    """Return True when a PHASE_COMPLETE line names the current phase."""
    candidate = raw_value.strip()
    if not candidate:
        return False

    expected = phase_name.strip()
    variants = [expected]
    if "·" in expected:
        variants.append(expected.split("·", 1)[0].strip())
    if "(" in expected:
        variants.append(expected.split("(", 1)[0].strip())

    def _matches_variant(left: str, right: str) -> bool:
        if left.casefold() == right.casefold():
            return True
        if left.casefold().startswith(right.casefold()):
            suffix = left[len(right):]
            return not suffix or suffix[0].isspace() or suffix[0] in ".:;,-()[]{}"
        return False

    return any(
        _matches_variant(candidate, variant)
        for variant in variants
        if variant
    )


def _find_phase_complete_index(lines: list[str], phase_name: str) -> int | None:
    """Return the line index of a valid PHASE_COMPLETE marker for this phase."""
    for index, raw_line in enumerate(lines):
        match = _PHASE_COMPLETE_LINE_RE.match(raw_line)
        if not match:
            continue
        value = match.group(1)
        if _DISCUSSION_CONTINUATION_RE.search(value):
            continue
        if _phase_complete_matches(value, phase_name):
            return index
    return None


def _extract_completion_report_sections(text: str, phase_name: str) -> dict[str, list[str]] | None:
    """Parse the mandatory completion report after a valid PHASE_COMPLETE line."""
    lines = text.splitlines()
    phase_line_index = _find_phase_complete_index(lines, phase_name)
    if phase_line_index is None:
        return None

    headers = {
        "what changed:": "what changed",
        "evidence:": "evidence",
        "verification:": "verification",
    }
    sections: dict[str, list[str]] = {value: [] for value in headers.values()}
    current_section: str | None = None

    for raw_line in lines[phase_line_index + 1 :]:
        lowered = raw_line.strip().lower()
        if lowered in headers:
            current_section = headers[lowered]
            continue
        if current_section is None:
            if raw_line.strip():
                continue
            continue
        sections[current_section].append(raw_line)

    if any(not any(line.strip() for line in section_lines) for section_lines in sections.values()):
        return None

    return sections


def parse_completed_steps(result, phase: Phase) -> list[str]:
    """Extract confirmed-done step texts from Player output (result.text).

    PHASE_COMPLETE on its own line -> return all step texts.
    Rejects PHASE_COMPLETE mentions that are still prompt-echo discussion rather
    than a real completion marker.
    Otherwise scan for 'Step X done:' (1-based, case-insensitive).
    Out-of-range indices are ignored.
    """
    text = result.text

    if _find_phase_complete_index(text.splitlines(), phase.name) is not None:
        return [s.text for s in phase.steps]

    confirmed: set[int] = set()
    for match in _STEP_DONE_RE.finditer(text):
        idx = int(match.group(1)) - 1  # 1-based → 0-based
        if 0 <= idx < len(phase.steps):
            confirmed.add(idx)

    return [phase.steps[i].text for i in sorted(confirmed)]


def has_required_completion_report(text: str, phase: Phase) -> bool:
    """Return True when the Player included the mandatory completion report."""
    sections = _extract_completion_report_sections(text, phase.name)
    return sections is not None


def player_claimed_tools_unavailable(result) -> bool:
    """Return True when Player claimed tools are unavailable despite using none."""
    tools_used = int(getattr(result, "tools_used", 0) or 0)
    if tools_used > 0:
        return False

    text = str(getattr(result, "text", "") or "").lower()
    return any(pattern in text for pattern in _TOOLS_UNAVAILABLE_PATTERNS)


def build_incomplete_phase_feedback(phase: Phase, completed_steps: list[str]) -> str:
    """Structured retry feedback when the phase is not complete yet."""
    completed = set(completed_steps)
    missing_steps = [step.text for step in phase.steps if step.text not in completed]

    lines: list[str] = []
    for index, missing_step in enumerate(missing_steps, start=1):
        lines.append(f"{index}. Complete the missing planned step: {missing_step}")

    next_num = len(lines) + 1
    lines.append(f"{next_num}. Do not stop after exploration; actually implement the remaining work.")
    next_num += 1
    lines.append(
        f"{next_num}. When finished, end with `PHASE_COMPLETE: {phase.name}` plus `What changed`, `Evidence`, and `Verification`."
    )
    return "\n".join(lines)


def build_missing_report_feedback(phase: Phase) -> str:
    """Structured retry feedback when completion markers exist but the report is missing."""
    return (
        "1. The completion report is missing required sections.\n"
        f"2. Re-send the phase completion response with `PHASE_COMPLETE: {phase.name}`, `What changed`, `Evidence`, and `Verification`.\n"
        "3. If no code changes were needed, say that explicitly and cite the files or checks proving the phase was already implemented."
    )


def build_tool_access_feedback(phase: Phase) -> str:
    """Structured retry feedback when Player incorrectly claims tools are unavailable."""
    return (
        "1. Tool access is available in this environment; the previous reply incorrectly claimed otherwise.\n"
        "2. Use the actual filesystem, command, and edit tools to inspect the workspace, run verification, and make any missing changes.\n"
        "3. Do not say tools are unavailable unless a real tool call failed, and if it failed, cite the actual failure briefly.\n"
        f"4. When finished, end with `PHASE_COMPLETE: {phase.name}` plus `What changed`, `Evidence`, and `Verification`."
    )


def build_player_timeout_feedback(phase: Phase, exc: Exception) -> str:
    """Structured retry feedback when the Player exceeds its timeout."""
    return (
        f"1. {exc}\n"
        "2. Continue from the current state; do not start over.\n"
        f"3. When finished, end with `PHASE_COMPLETE: {phase.name}` plus `What changed`, `Evidence`, and `Verification`."
    )


# --- Error ---

@dataclass
class PhaseFailedError(Exception):
    """Raised when a phase exhausts all retry attempts."""
    phase: Phase | None
    attempts: int

    def __post_init__(self):
        # @dataclass does not call Exception.__init__ — do it manually
        # so that e.args is populated and str(e) works correctly.
        super().__init__(str(self))

    def __str__(self) -> str:
        if self.phase is None:
            return f"Phase failed after {self.attempts} attempts"
        completed = [s.text for s in self.phase.steps if s.done]
        return (
            f"Phase '{self.phase.name}' failed after {self.attempts} attempts. "
            f"Completed steps: {completed}"
        )


class PlanResetRequested(Exception):
    """Raised when the operator requests a full plan-progress reset."""


# --- Executor ---

class BatchExecutor:
    """Executes a plan in phases: one Player turn per phase, Coach review once per phase."""

    DEFAULT_PRE_JUDGE_ATTEMPTS = 3
    DEFAULT_JUDGE_ATTEMPTS = 1
    DEFAULT_POST_JUDGE_ATTEMPTS = 1

    def __init__(self, session, tracker):
        self.session = session
        self.tracker = tracker

    def _config_str(self, attr_name: str, default: str = "") -> str:
        """Read a string config value without letting MagicMock placeholders leak through."""
        value = getattr(self.session.config, attr_name, default)
        return value if isinstance(value, str) else default

    async def _run_player_turn(self, **kwargs):
        """Use continuation support when available, otherwise fall back gracefully."""
        run_with_continuation = getattr(self.session, "_run_with_continuation", None)
        if inspect.iscoroutinefunction(run_with_continuation):
            return await run_with_continuation(**kwargs)

        run_turn = getattr(self.session, "_run_turn", None)
        if inspect.iscoroutinefunction(run_turn):
            return await run_turn(**kwargs)

        raise AttributeError(
            "Session must provide `_run_with_continuation()` or `_run_turn()` for batch execution."
        )

    def _role_label(self, role: str) -> str:
        """Best-effort role label for batch UI."""
        label = getattr(self.session, f"{role}_model", "")
        if isinstance(label, str) and label.strip():
            return label

        provider = getattr(self.session.config, f"{role}_provider", "")
        model = getattr(self.session.config, f"{role}_model", "")

        if isinstance(provider, str) and provider:
            if isinstance(model, str) and model:
                return f"{provider} | model={model}"
            return provider

        return role

    def _judge_label(self) -> str:
        """Display label for the batch judge slot."""
        return self._provider_label(
            self._judge_provider(),
            self._config_str("batch_judge_model", ""),
        )

    def _judge_provider(self) -> str:
        """Return the configured judge provider, falling back to the default."""
        return self._config_str("batch_judge_provider", "").strip() or "codex"

    def _review_slot_label(self, provider_attr: str, model_attr: str) -> str:
        """Display label for a batch pre/post coach slot, including coach fallbacks."""
        provider = self._config_str(provider_attr, "")
        model = self._config_str(model_attr, "")
        if provider:
            return self._provider_label(provider, model)
        if model:
            return self._provider_label(
                self._config_str("coach_provider", "zai"),
                model,
            )
        return self._role_label("coach")

    def _provider_label(self, provider: str, model: str) -> str:
        """Display label for an arbitrary provider/model slot."""
        builder = getattr(self.session, "build_provider_display", None)
        if callable(builder):
            return builder(provider, model)
        if model:
            return f"{provider} | model={model}"
        return provider

    def _reset_tracker_progress_for_batch_run(self) -> None:
        """Start each batch run from the plan itself, not stale checklist state."""
        items_list = getattr(self.tracker, "items", [])
        items_list[:] = [replace(item, done=False) for item in items_list]

    def _reset_plan_progress(self, phases: list[Phase]) -> None:
        """Reset batch progress both in memory and in the persisted plan file."""
        items_list = getattr(self.tracker, "items", [])
        items_list[:] = [replace(item, done=False) for item in items_list]
        for phase in phases:
            phase.status = "pending"
            phase.attempts = 0
            phase.steps = [replace(step, done=False) for step in phase.steps]
        if getattr(self.session, "plan_file_path", ""):
            from src.plan_tracker import write_checklist_back

            write_checklist_back(self.session.plan_file_path, self.tracker.items)
        print("\n  ↺ Прогресс batch-плана сброшен — начинаем заново с первой фазы")

    def _schedule_counts(self) -> tuple[int, int, int]:
        """Return validated batch retry counts (pre, judge, post)."""
        defaults = (
            self.DEFAULT_PRE_JUDGE_ATTEMPTS,
            self.DEFAULT_JUDGE_ATTEMPTS,
            self.DEFAULT_POST_JUDGE_ATTEMPTS,
        )
        attr_names = (
            "batch_pre_judge_attempts",
            "batch_judge_attempts",
            "batch_post_judge_attempts",
        )

        values: list[int] = []
        for attr_name, default in zip(attr_names, defaults):
            value = getattr(self.session.config, attr_name, default)
            if not isinstance(value, int) or value < 0:
                value = default
            values.append(value)

        if sum(values) <= 0:
            return defaults

        return tuple(values)

    def _max_phase_attempts(self) -> int:
        """Total attempts for one batch phase."""
        return sum(self._schedule_counts())

    def _review_strategy(self, attempt_num: int) -> dict[str, str]:
        """Return review strategy for a 1-based batch attempt number."""
        pre_attempts, judge_attempts, post_attempts = self._schedule_counts()
        judge_start = pre_attempts + 1
        judge_end = pre_attempts + judge_attempts

        if judge_attempts > 0 and judge_start <= attempt_num <= judge_end:
            return {
                "header_role": "judge",
                "label": self._judge_label(),
                "provider_name_override": self._judge_provider(),
                "model_override": self._config_str("batch_judge_model", ""),
                "review_role": "judge",
            }

        if post_attempts > 0 and attempt_num > judge_end:
            post_provider = self._config_str(
                "batch_post_provider",
                self._config_str("coach_provider", "zai"),
            )
            post_model = self._config_str("batch_post_model", "")
            return {
                "header_role": "coach",
                "label": self._provider_label(post_provider, post_model),
                "provider_name_override": post_provider,
                "model_override": post_model,
                "review_role": "coach",
            }

        pre_provider = self._config_str(
            "batch_pre_provider",
            self._config_str("coach_provider", "zai"),
        )
        pre_model = self._config_str("batch_pre_model", "")
        return {
            "header_role": "coach",
            "label": self._provider_label(pre_provider, pre_model),
            "provider_name_override": pre_provider,
            "model_override": pre_model,
            "review_role": "coach",
        }

    def _player_strategy(self, attempt_num: int) -> dict[str, str]:
        """Return player provider/model escalation for a 1-based attempt."""
        from src.constants import PLAYER_ESCALATION_SONNET_MODEL, PLAYER_ESCALATION_OPUS_MODEL
        pre, judge, _post = self._schedule_counts()
        sonnet_start = pre + 1
        sonnet_end = pre + judge

        if attempt_num <= pre:
            return {
                "provider_name": self.session.config.player_provider,
                "model": self.session.config.player_model,
            }
        if judge > 0 and sonnet_start <= attempt_num <= sonnet_end:
            return {
                "provider_name": "claude_native",
                "model": PLAYER_ESCALATION_SONNET_MODEL,
            }
        return {
            "provider_name": "claude_native",
            "model": PLAYER_ESCALATION_OPUS_MODEL,
        }

    async def run(self) -> None:
        """Execute all phases. Raises PhaseFailedError on unrecoverable failure."""
        import logging
        from src.plan_tracker import auto_group_phases, write_checklist_back
        from src.streaming import BOLD, RESET

        existing_phases = vars(self.tracker).get("phases")
        phases = existing_phases if existing_phases else auto_group_phases(self.tracker.items)
        if not phases:
            logging.warning("BatchExecutor.run(): no phases generated, nothing to do")
            return

        self.tracker.phases = phases
        pre_attempts, judge_attempts, post_attempts = self._schedule_counts()
        max_phase_attempts = self._max_phase_attempts()
        done_count = sum(1 for p in phases if all(s.done for s in p.steps))
        print(f"\n{BOLD}--- tero batch ---{RESET}")
        print(f"  Фаз: {len(phases)}  |  Выполнено: {done_count}  |  Макс. попыток на фазу: {max_phase_attempts}")
        print(f"  Player: {self._role_label('player')}")
        print(
            f"  Player escalation: sonnet x{judge_attempts} → opus x{post_attempts}"
        )
        print(f"  Coach: {self._role_label('coach')}")
        print(
            f"  Pre-Coach: {self._review_slot_label('batch_pre_provider', 'batch_pre_model')}"
        )
        print(f"  Judge: {self._judge_label()}")
        print(
            f"  Post-Coach: {self._review_slot_label('batch_post_provider', 'batch_post_model')}"
        )
        print(
            f"  Batch review: {pre_attempts} / {judge_attempts} / {post_attempts} "
            "(coach / judge / coach)"
        )
        print()
        runtime = vars(self.session).get("_runtime")
        if runtime is not None:
            runtime.start(
                player_name=self._role_label("player"),
                coach_name=self._role_label("coach"),
            )
            runtime.pause_render()

        self.tracker.start_dashboard()

        try:
            phase_index = 0
            while phase_index < len(phases):
                phase = phases[phase_index]
                # Resume: skip phases already fully completed or skipped in the plan file
                if all(step.done or step.skipped for step in phase.steps):
                    phase.status = "done"
                    self.tracker.render_dashboard()
                    phase_index += 1
                    continue

                phase.status = "in_progress"
                self.tracker.render_dashboard()
                runtime = vars(self.session).get("_runtime")
                if runtime is not None:
                    runtime.apply_pending(self.session)
                    if runtime.reset_requested:
                        raise PlanResetRequested()

                try:
                    success = await self._run_phase(phase)
                except PlanResetRequested:
                    if runtime is not None:
                        runtime.clear_reset()
                    self._reset_plan_progress(phases)
                    self.tracker.render_dashboard()
                    phase_index = 0
                    continue

                if success:
                    phase.status = "done"
                    self.tracker.phase_done(phase)
                    if getattr(self.session, "plan_file_path", ""):
                        write_checklist_back(self.session.plan_file_path, self.tracker.items)
                    phase_index += 1
                else:
                    import logging
                    from src import streaming as streaming_ui
                    phase.status = "skipped"
                    phase.steps = [replace(step, skipped=True) for step in phase.steps]
                    self.tracker.render_dashboard()
                    if getattr(self.session, "plan_file_path", ""):
                        write_checklist_back(self.session.plan_file_path, self.tracker.items)
                    logging.warning(
                        "Phase %r exhausted %d attempts — skipping and continuing",
                        phase.name, phase.attempts,
                    )
                    streaming_ui.print_phase_skipped(phase.name, phase.attempts)
                    phase_index += 1
        finally:
            from src.streaming import BOLD, YELLOW, RESET
            skipped = [p for p in phases if p.status == "skipped"]
            if skipped:
                print(f"\n{BOLD}{YELLOW}--- Skipped phases ({len(skipped)}) ---{RESET}")
                for p in skipped:
                    print(f"  ⏭ {p.display_name or p.name} — {p.attempts} attempts")
                print()
            self.tracker.stop_dashboard()
            if runtime is not None:
                runtime.resume_render()
                runtime.stop()

    async def _run_phase(self, phase: Phase) -> bool:
        """Execute all steps in one Player turn. Retry on incomplete or Coach rejection."""
        from src.prompts import PLAYER_BATCH_SYSTEM_PROMPT
        from src import streaming as streaming_ui

        def _raise_if_interrupted() -> None:
            interrupted = getattr(self.session, "_interrupted", False)
            if isinstance(interrupted, bool) and interrupted:
                raise KeyboardInterrupt()

        completed_steps: list[str] = []
        coach_feedback: str = ""
        max_phase_attempts = self._max_phase_attempts()
        if max_phase_attempts == 0:
            raise ValueError(
                f"max_phase_attempts is 0 for phase '{phase.name}' — check schedule configuration"
            )
        runtime = vars(self.session).get("_runtime")

        for attempt in range(max_phase_attempts):
            _raise_if_interrupted()
            attempt_num = attempt + 1
            phase.attempts = attempt_num
            self.tracker.render_dashboard()
            if runtime is not None:
                runtime.apply_pending(self.session)
                if runtime.reset_requested:
                    raise PlanResetRequested()
            _raise_if_interrupted()

            prompt = build_batch_prompt(phase, completed_steps, coach_feedback)
            snapshot_pids = getattr(self.session, "_snapshot_pids", None)
            cleanup_processes = getattr(self.session, "_kill_new_processes", None)
            pids_before = snapshot_pids() if callable(snapshot_pids) else set()
            phase_roles: list[str] = []
            for step in phase.steps:
                for role in step.roles:
                    if role and role not in phase_roles:
                        phase_roles.append(role)

            streaming_ui.print_batch_turn_header(
                role="player",
                phase_name=phase.name,
                attempt=attempt_num,
                max_attempts=max_phase_attempts,
                model_name=self._role_label("player"),
                active_roles=phase_roles,
            )
            player_system = PLAYER_BATCH_SYSTEM_PROMPT
            persona_registry = vars(self.session).get("_persona_registry")
            if persona_registry is not None:
                if phase_roles:
                    overlay = persona_registry.build_overlay(phase_roles)
                    if overlay:
                        player_system = f"{PLAYER_BATCH_SYSTEM_PROMPT}\n\n{overlay}"
            player_strategy = self._player_strategy(attempt_num)
            player_provider_inst = None
            player_model_for_turn = player_strategy["model"]
            if player_strategy["provider_name"] != self.session.config.player_provider:
                get_or_create = getattr(self.session, "_get_or_create_provider", None)
                if callable(get_or_create):
                    cand = get_or_create(player_strategy["provider_name"])
                    try:
                        ok, reason = cand.check_ready()
                    except (TypeError, ValueError):
                        ok, reason = False, "provider check unavailable"
                    if ok:
                        player_provider_inst = cand
                    else:
                        import logging
                        logging.warning(
                            "Player escalation to %s unavailable: %s — using configured player",
                            player_strategy["provider_name"], reason,
                        )
                        player_model_for_turn = self.session.config.player_model

            try:
                result = await self._run_player_turn(
                    role="player",
                    prompt=prompt,
                    system_prompt=player_system,
                    max_turns=self.session.config.max_turns,
                    timeout_s=self.session.config.player_timeout_s,
                    model_override=player_model_for_turn,
                    provider_override=player_provider_inst,
                )
            except TimeoutError as exc:
                if callable(cleanup_processes):
                    cleanup_processes(pids_before)
                coach_feedback = build_player_timeout_feedback(phase, exc)
                streaming_ui.print_step_rejected(coach_feedback)
                continue

            if callable(cleanup_processes):
                cleanup_processes(pids_before)
            _raise_if_interrupted()

            if player_claimed_tools_unavailable(result):
                coach_feedback = build_tool_access_feedback(phase)
                streaming_ui.print_step_rejected(coach_feedback)
                continue

            for step in parse_completed_steps(result, phase):
                if step not in completed_steps:
                    completed_steps.append(step)
            if len(completed_steps) < len(phase.steps):
                coach_feedback = build_incomplete_phase_feedback(phase, completed_steps)
                streaming_ui.print_step_rejected(coach_feedback)
                continue

            if not has_required_completion_report(result.text, phase):
                coach_feedback = build_missing_report_feedback(phase)
                streaming_ui.print_step_rejected(coach_feedback)
                continue

            strategy = self._review_strategy(attempt_num)

            streaming_ui.print_batch_turn_header(
                role=strategy["header_role"],
                phase_name=phase.name,
                attempt=attempt_num,
                max_attempts=max_phase_attempts,
                model_name=strategy["label"],
            )
            try:
                verdict = await self.session._run_coach_turn_for_phase(
                    phase,
                    result,
                    completed_steps,
                    provider_name_override=strategy["provider_name_override"] or None,
                    model_override=strategy["model_override"],
                    review_role=strategy["review_role"],
                )
            except PlanResetRequested:
                raise
            except (TimeoutError, ProviderError) as exc:
                # Expected provider failures — log the attempt and retry.
                coach_feedback = f"Review provider failed: {exc}. Continuing with next attempt."
                streaming_ui.print_step_rejected(coach_feedback)
                continue
            # ValueError / AttributeError intentionally NOT caught — those indicate bugs (wrong role, missing config)

            if isinstance(verdict, Approved):
                return True
            if getattr(type(verdict), "__name__", "") == "NoVerdict":
                coach_feedback = build_incomplete_phase_feedback(phase, completed_steps)
                streaming_ui.print_step_rejected(coach_feedback)
                continue
            coach_feedback = verdict.text if isinstance(verdict, Feedback) else str(verdict)
            streaming_ui.print_step_rejected(coach_feedback)

        return False
