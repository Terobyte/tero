"""Batch execution: groups PlanItems into phases, executes one Player turn per phase."""

import re
from dataclasses import dataclass

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
_PHASE_COMPLETE_RE = re.compile(r"PHASE_COMPLETE\s*:", re.IGNORECASE)
_REQUIRED_REPORT_HEADERS = ("what changed:", "evidence:", "verification:")


def parse_completed_steps(result, phase: Phase) -> list[str]:
    """Extract confirmed-done step texts from Player output (result.text).

    PHASE_COMPLETE anywhere → return all step texts (unconditional).
    Otherwise scan for 'Step X done:' (1-based, case-insensitive).
    Out-of-range indices are ignored.
    """
    text = result.text

    if _PHASE_COMPLETE_RE.search(text):
        return [s.text for s in phase.steps]

    confirmed: set[int] = set()
    for match in _STEP_DONE_RE.finditer(text):
        idx = int(match.group(1)) - 1  # 1-based → 0-based
        if 0 <= idx < len(phase.steps):
            confirmed.add(idx)

    return [phase.steps[i].text for i in sorted(confirmed)]


def has_required_completion_report(text: str) -> bool:
    """Return True when the Player included the mandatory completion report."""
    lowered = text.lower()
    return all(header in lowered for header in _REQUIRED_REPORT_HEADERS)


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
    phase: Phase
    attempts: int

    def __post_init__(self):
        # @dataclass does not call Exception.__init__ — do it manually
        # so that e.args is populated and str(e) works correctly.
        super().__init__(str(self))

    def __str__(self) -> str:
        completed = [s.text for s in self.phase.steps if s.done]
        return (
            f"Phase '{self.phase.name}' failed after {self.attempts} attempts. "
            f"Completed steps: {completed}"
        )


# --- Executor ---

class BatchExecutor:
    """Executes a plan in phases: one Player turn per phase, Coach review once per phase."""

    DEFAULT_PRE_JUDGE_ATTEMPTS = 3
    DEFAULT_JUDGE_ATTEMPTS = 1
    DEFAULT_POST_JUDGE_ATTEMPTS = 1
    JUDGE_PROVIDER = "claude"
    JUDGE_MODEL = "sonnet"

    def __init__(self, session, tracker):
        self.session = session
        self.tracker = tracker

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
        """Display label for the one-off batch judge attempt."""
        builder = getattr(self.session, "build_provider_display", None)
        if callable(builder):
            return builder(self.JUDGE_PROVIDER, self.JUDGE_MODEL)
        return f"{self.JUDGE_PROVIDER} | model={self.JUDGE_MODEL}"

    def _reset_tracker_progress_for_batch_run(self) -> None:
        """Start each batch run from the plan itself, not stale checklist state."""
        for item in getattr(self.tracker, "items", []):
            item.done = False

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
        pre_attempts, judge_attempts, _post_attempts = self._schedule_counts()
        judge_start = pre_attempts + 1
        judge_end = pre_attempts + judge_attempts

        if judge_attempts > 0 and judge_start <= attempt_num <= judge_end:
            return {
                "header_role": "judge",
                "label": self._judge_label(),
                "provider_name_override": self.JUDGE_PROVIDER,
                "model_override": self.JUDGE_MODEL,
                "review_role": "judge",
            }

        return {
            "header_role": "coach",
            "label": self._role_label("coach"),
            "provider_name_override": "",
            "model_override": self.session.config.coach_model,
            "review_role": "coach",
        }

    async def run(self) -> None:
        """Execute all phases. Raises PhaseFailedError on unrecoverable failure."""
        import logging
        from src.plan_tracker import auto_group_phases, write_checklist_back
        from src.streaming import BOLD, RESET

        self._reset_tracker_progress_for_batch_run()
        phases = auto_group_phases(self.tracker.items)
        if not phases:
            logging.warning("BatchExecutor.run(): no phases generated, nothing to do")
            return

        self.tracker.phases = phases
        pre_attempts, judge_attempts, post_attempts = self._schedule_counts()
        max_phase_attempts = self._max_phase_attempts()
        print(f"\n{BOLD}--- tero batch ---{RESET}")
        print(f"  Фаз: {len(phases)}  |  Макс. попыток на фазу: {max_phase_attempts}")
        print(f"  Player: {self._role_label('player')}")
        print(f"  Coach: {self._role_label('coach')}")
        print(f"  Judge: {self._judge_label()}")
        print(
            f"  Batch review: {pre_attempts} / {judge_attempts} / {post_attempts} "
            "(coach / judge / coach)"
        )
        print()
        self.tracker.start_dashboard()

        try:
            for phase in phases:
                phase.status = "in_progress"
                self.tracker.render_dashboard()

                success = await self._run_phase(phase)

                if success:
                    phase.status = "done"
                    self.tracker.phase_done(phase)
                    if getattr(self.session, "plan_file_path", ""):
                        write_checklist_back(self.session.plan_file_path, self.tracker.items)
                else:
                    phase.status = "failed"
                    self.tracker.render_dashboard()
                    raise PhaseFailedError(phase=phase, attempts=phase.attempts)
        finally:
            self.tracker.stop_dashboard()

    async def _run_phase(self, phase: Phase) -> bool:
        """Execute all steps in one Player turn. Retry on incomplete or Coach rejection."""
        from src.prompts import PLAYER_BATCH_SYSTEM_PROMPT
        from src import streaming as streaming_ui

        completed_steps: list[str] = []
        coach_feedback: str = ""
        max_phase_attempts = self._max_phase_attempts()

        for attempt in range(max_phase_attempts):
            attempt_num = attempt + 1
            phase.attempts = attempt_num
            self.tracker.render_dashboard()

            prompt = build_batch_prompt(phase, completed_steps, coach_feedback)
            snapshot_pids = getattr(self.session, "_snapshot_pids", None)
            cleanup_processes = getattr(self.session, "_kill_new_processes", None)
            pids_before = snapshot_pids() if callable(snapshot_pids) else set()

            streaming_ui.print_batch_turn_header(
                role="player",
                phase_name=phase.name,
                attempt=attempt_num,
                max_attempts=max_phase_attempts,
                model_name=self._role_label("player"),
            )
            try:
                result = await self.session._run_with_continuation(
                    role="player",
                    prompt=prompt,
                    system_prompt=PLAYER_BATCH_SYSTEM_PROMPT,
                    max_turns=self.session.config.max_turns,
                    timeout_s=self.session.config.player_timeout_s,
                    model_override=self.session.config.player_model,
                )
            except TimeoutError as exc:
                if callable(cleanup_processes):
                    cleanup_processes(pids_before)
                coach_feedback = build_player_timeout_feedback(phase, exc)
                streaming_ui.print_step_rejected(coach_feedback)
                continue

            if callable(cleanup_processes):
                cleanup_processes(pids_before)

            completed_steps = parse_completed_steps(result, phase)
            if len(completed_steps) < len(phase.steps):
                coach_feedback = build_incomplete_phase_feedback(phase, completed_steps)
                streaming_ui.print_step_rejected(coach_feedback)
                continue

            if not has_required_completion_report(result.text):
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
            verdict = await self.session._run_coach_turn_for_phase(
                phase,
                result,
                completed_steps,
                provider_name_override=strategy["provider_name_override"] or None,
                model_override=strategy["model_override"],
                review_role=strategy["review_role"],
            )
            from src.feedback import Approved, Feedback

            if isinstance(verdict, Approved):
                return True
            coach_feedback = verdict.text if isinstance(verdict, Feedback) else str(verdict)
            streaming_ui.print_step_rejected(coach_feedback)

        return False
