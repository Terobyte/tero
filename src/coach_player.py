"""Main coach-player loop: step-by-step plan execution with per-step review."""

import asyncio
import datetime
import inspect
import os
import signal
import sys
import time
from dataclasses import dataclass

from src.config import Config, load_provider_configs
from src.feedback import (
    parse_coach_output,
    parse_review_output,
    Approved,
    Feedback,
    NoVerdict,
    ReviewPassed,
    ReviewIssues,
    is_invalid_feedback,
)
from src.learning.recorder import RunRecord, RunRecorder, TurnDetail, generate_run_id
from src.plan_tracker import (
    get_current_step_index,
    mark_step_done,
    parse_requirements,
    reset_all_progress,
    write_checklist_back,
    format_checklist,
)
from src.prompts import (
    COACH_STRICT_SYSTEM_PROMPT,
    PLAYER_SYSTEM_PROMPT,
    PLAYER_BATCH_SYSTEM_PROMPT,
    CODE_REVIEWER_SYSTEM_PROMPT,
    build_coach_step_prompt,
    build_player_step_prompt,
    build_code_review_prompt,
    build_player_fix_prompt,
)
from src.providers import create_provider, adapt_claude_event, adapt_sdk_message
from src.providers.message_adapter import AdaptedMessage
from src.providers.codex import CodexProvider
from src.role_router import RoleRouter, format_provider_display, _provider_model
from src.constants import BATCH_REVIEW_MAX_TURNS, PLAYER_MAX_TURNS
from src.process_guard import ProcessGuard
from src.turn_runner import AgentTurnRunner

from src import streaming as streaming_ui
from src.streaming import BOLD, RESET, GREEN, RED, YELLOW
from src.runtime_controls import RuntimeControls


@dataclass
class TurnResult:
    """Result of a single agent turn."""

    role: str
    duration_s: float
    tools_used: int
    messages: list
    text: str = ""  # Concatenated assistant text output
    tokens_used: int = 0  # Total tokens (input + output) from ResultMessage


@dataclass
class SessionResult:
    """Result of a complete session."""

    approved: bool
    turns_used: int
    steps_completed: int
    total_steps: int
    total_duration_s: float
    turn_details: list[TurnDetail]
    status: str
    error: str | None = None


class CoachPlayerSession:
    """Runs step-by-step coach-player feedback loop."""

    REQUIRED_PLAYER_REPORT_HEADERS = ("what changed:", "evidence:", "verification:")

    def __init__(self, config: Config, requirements: str, plan_file_path: str = ""):
        self.config = config
        self.requirements = requirements
        self.plan_file_path = plan_file_path
        self.recorder = RunRecorder(f"{config.working_dir}/.g3/knowledge")
        self._interrupted = False
        self._persona_registry = None

        # Load provider configs and create providers (supports multi-account)
        self.provider_configs = self._load_provider_configs()
        self._provider_cache: dict[str, object] = {}
        self.player_provider = self._get_or_create_provider(config.player_provider)
        self.coach_provider = self._get_or_create_provider(config.coach_provider)

        self.router = RoleRouter(
            config=self.config,
            get_or_create_provider=self._get_or_create_provider,
            player_provider=self.player_provider,
            coach_provider=self.coach_provider,
        )

        self._runtime = RuntimeControls()
        self._last_turn_result: TurnResult | None = None
        self._process_guard = ProcessGuard(verbose=config.verbose)
        self._turn_runner = AgentTurnRunner(verbose=config.verbose)

        self.player_model = self.router.display_label_for("player")
        self.coach_model = self.router.display_label_for("coach")

        # Initialize review provider if code_review is enabled
        self.review_provider = None
        self.review_provider_name = ""
        self.review_model = ""
        if self.config.code_review:
            self._init_review_provider()

        # Verify all providers required by the current runtime mode are ready.
        self._verify_providers_ready()

    def _load_provider_configs(self) -> dict:
        """Load provider configs from global, bundled, and working-dir config."""
        return load_provider_configs(self.config.working_dir)

    def _verify_providers_ready(self) -> None:
        """Verify all providers are ready, raise if not."""
        roles = ["player", "coach"]

        if self.config.code_review and self.review_provider is not None:
            roles.append("reviewer")

        self.router.check_roles_ready(roles)

    def _init_review_provider(self) -> None:
        """Initialize review provider for code review phase."""
        review_provider_name = (self.config.review_provider or "").strip()
        if not review_provider_name:
            review_provider_name = self.config.coach_provider

        self.review_provider = self._get_or_create_provider(review_provider_name)
        self.review_provider_name = review_provider_name
        self.review_model = self.config.review_model

    def _create_provider_uncached(self, provider_name: str):
        """Create a provider instance without mutating the session cache."""
        return create_provider(
            provider_name,
            self.provider_configs.get(provider_name),
        )

    def _check_provider_ready_without_cache(self, provider_name: str) -> bool:
        """Probe provider readiness without caching a possibly unusable instance."""
        from src.errors import ProviderError

        try:
            provider = self._create_provider_uncached(provider_name)
            ok, _ = provider.check_ready()
            return ok
        except ProviderError:
            return False

    def _get_or_create_provider(self, provider_name: str):
        """Return a cached provider instance by configured name."""
        if provider_name not in self._provider_cache:
            self._provider_cache[provider_name] = self._create_provider_uncached(
                provider_name
            )
        return self._provider_cache[provider_name]

    def build_provider_display(
        self, provider_name: str, model_override: str = ""
    ) -> str:
        """Public helper for UI labels outside the main role pair."""
        provider = self._get_or_create_provider(provider_name)
        return format_provider_display(provider_name, provider, model_override)

    def switch_runtime_role(self, role: str, provider_name: str, model: str) -> str:
        """Apply a live provider/model switch safely and return the new label."""
        display = self.router.switch_role(role, provider_name, model)
        if role == "coach":
            self.coach_provider = self.router.provider_for("coach")
            self.coach_model = display
        else:
            self.player_provider = self.router.provider_for("player")
            self.player_model = display
        return display

    def _provider_for_runtime_role(self, role: str):
        """Return the current provider object for a runtime role."""
        legacy_provider_for_role = getattr(self, "_provider_for_role", None)
        if legacy_provider_for_role is not None and not hasattr(self, "router"):
            return legacy_provider_for_role(role)
        if role == "player":
            return self.player_provider
        if role == "coach":
            return self.coach_provider
        if role == "reviewer" and self.review_provider is not None:
            return self.review_provider
        return self.router.provider_for(role)

    def _setup_interrupt_handler(self):
        def handler(signum, frame):
            self._interrupted = True
            print("\n\n--- Прервано ---")

        signal.signal(signal.SIGINT, handler)

    def _snapshot_pids(self) -> set[int]:
        return self._process_guard.snapshot_pids()

    def _kill_new_processes(self, before: set[int]) -> None:
        self._process_guard.kill_new_processes(before)

    @staticmethod
    def _build_step_fallback_feedback(
        step_text: str, prefix_issue: str | None = None
    ) -> Feedback:
        """Fallback feedback when the coach fails to produce a valid review."""
        lines: list[str] = []
        if prefix_issue:
            lines.append(f"1. {prefix_issue}")
            start_num = 2
        else:
            start_num = 1

        lines.append(f"{start_num}. Complete the current step exactly: {step_text}")
        lines.append(
            f"{start_num + 1}. Verify the change with the most relevant test, command, or file inspection."
        )
        lines.append(
            f"{start_num + 2}. If the code already exists, prove it by making the missing fix instead of only stating that it is done."
        )
        return Feedback("\n".join(lines))

    @staticmethod
    def _build_phase_fallback_feedback(
        phase,
        completed_steps: list[str] | None,
        prefix_issue: str | None = None,
    ) -> Feedback:
        """Fallback batch feedback when the reviewer fails to produce usable output."""
        completed = set(completed_steps or [])
        missing_steps = [
            step.text for step in phase.steps if step.text not in completed
        ]

        lines: list[str] = []
        next_num = 1
        if prefix_issue:
            lines.append(f"{next_num}. {prefix_issue}")
            next_num += 1

        if missing_steps:
            for missing_step in missing_steps:
                lines.append(
                    f"{next_num}. Complete the missing planned step: {missing_step}"
                )
                next_num += 1
        else:
            lines.append(
                f"{next_num}. Re-check the whole phase `{phase.name}` and fix any missing implementation details."
            )
            next_num += 1

        lines.append(
            f"{next_num}. Run the most relevant verification before finishing this phase."
        )
        next_num += 1
        lines.append(
            f"{next_num}. End with plain-text completion markers: `Step N done: ...` and `PHASE_COMPLETE: {phase.name}`, plus `What changed`, `Evidence`, and `Verification`."
        )
        return Feedback("\n".join(lines))

    @classmethod
    def _has_required_player_report(cls, text: str) -> bool:
        """Return True when Player included the mandatory completion report."""
        lowered = text.lower()
        return all(header in lowered for header in cls.REQUIRED_PLAYER_REPORT_HEADERS)

    @staticmethod
    def _needs_phase_complete(prompt: str) -> bool:
        """Heuristic: batch prompts explicitly require PHASE_COMPLETE markers."""
        return "PHASE_COMPLETE" in (prompt or "")

    @classmethod
    def _player_output_complete(cls, text: str, prompt: str) -> bool:
        """Return True when player output contains the expected completion markers."""
        if not cls._has_required_player_report(text):
            return False
        if cls._needs_phase_complete(prompt):
            return "PHASE_COMPLETE" in (text or "").upper()
        return True

    async def _run_with_continuation(
        self,
        role: str,
        prompt: str,
        system_prompt: str,
        max_turns: int,
        timeout_s: int,
        model_override: str = "",
        provider_override=None,
    ) -> TurnResult:
        """Retry incomplete player outputs with a continuation prompt."""
        provider = provider_override or self._provider_for_runtime_role(role)
        return await self._turn_runner.run_with_continuation(
            role=role,
            prompt=prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout_s=timeout_s,
            provider=provider,
            router=self.router,
            config=self.config,
            model_override=model_override,
            provider_override=provider,
            interrupted_fn=lambda: self._interrupted,
        )

    @staticmethod
    def _build_missing_player_report_feedback(step_text: str) -> Feedback:
        """Structured feedback when Player omitted the final completion report."""
        return Feedback(
            "1. Your final response is missing the required completion report.\n"
            "2. Re-send the completion response with `What changed`, `Evidence`, and `Verification`.\n"
            f"3. If no code changes were needed for `{step_text}`, say that explicitly and cite the files or checks proving it was already implemented."
        )

    def _reset_plan_progress(self, plan_items: list) -> list:
        """Reset plan execution progress for the current run and persisted checklist."""
        reset_items = reset_all_progress(plan_items)
        self._last_turn_result = None
        if self.plan_file_path:
            write_checklist_back(self.plan_file_path, reset_items)
        print(
            f"\n  {BOLD}{YELLOW}↺ Прогресс плана сброшен — начинаем заново с шага 1{RESET}"
        )
        return reset_items

    @staticmethod
    def _ordered_unique_roles(roles: list[str] | None) -> list[str]:
        """Deduplicate role names while preserving order."""
        result: list[str] = []
        for role in roles or []:
            if role and role not in result:
                result.append(role)
        return result

    def _system_prompt_with_overlay(
        self,
        base_prompt: str,
        roles: list[str] | None,
        *,
        review_focus: bool = False,
    ) -> str:
        """Append persona overlay text when a matching specialist role exists."""
        registry = self._persona_registry
        ordered_roles = self._ordered_unique_roles(roles)
        if registry is None or not ordered_roles:
            return base_prompt

        overlay = registry.build_overlay(ordered_roles)
        if not overlay:
            return base_prompt

        if review_focus:
            return f"{base_prompt}\n\n## Review Focus\n{overlay}"
        return f"{base_prompt}\n\n{overlay}"

    async def run(self) -> SessionResult:
        """Run the step-by-step coach-player loop."""
        self._setup_interrupt_handler()

        start_time = time.time()
        turn_details = []
        error = None

        plan_items = parse_requirements(self.requirements)
        total_steps = len(plan_items)

        print(f"\n{BOLD}--- tero coach-player ---{RESET}")
        print(f"  Файл плана: {self.config.plan_file}")
        print(
            f"  Шагов: {total_steps}  |  Макс. попыток на шаг: {self.config.max_turns}"
        )
        print(f"  Player: {self.player_model}  |  Coach: {self.coach_model}")
        print()

        # Resume from first undone step
        start_index = get_current_step_index(plan_items)
        if start_index is None:
            print(f"{BOLD}{GREEN}  Все шаги уже выполнены!{RESET}")
            return SessionResult(
                approved=True,
                turns_used=0,
                steps_completed=total_steps,
                total_steps=total_steps,
                total_duration_s=0,
                turn_details=[],
                status="approved",
            )

        if start_index > 0:
            print(
                f"  Продолжаем с шага {start_index + 1} ({start_index} шагов уже сделано)\n"
            )

        try:
            self._runtime.start(
                player_name=self.player_model, coach_name=self.coach_model
            )
            step_index = start_index
            while step_index < total_steps:
                if self._interrupted:
                    break

                self._runtime.apply_pending(self)
                if self._runtime.reset_requested:
                    self._runtime.clear_reset()
                    plan_items = self._reset_plan_progress(plan_items)
                    step_index = 0
                    continue

                step = plan_items[step_index]
                if step.done:
                    step_index += 1
                    continue

                step_num = step_index + 1
                completed_steps = [p.text for p in plan_items[:step_index] if p.done]
                self._last_turn_result = None

                streaming_ui.print_step_header(
                    step_num,
                    total_steps,
                    step.text,
                    step.roles,
                )

                feedback = None
                step_approved = False
                restart_requested = False

                for attempt in range(1, self.config.max_turns + 1):
                    if self._interrupted:
                        break

                    self._runtime.apply_pending(self)
                    if self._runtime.reset_requested:
                        self._runtime.clear_reset()
                        plan_items = self._reset_plan_progress(plan_items)
                        step_index = 0
                        restart_requested = True
                        break

                    # --- Player turn ---
                    streaming_ui.print_player_header(
                        step_num,
                        total_steps,
                        attempt,
                        self.config.max_turns,
                        self.player_model,
                    )
                    pids_before = self._snapshot_pids()

                    compact_prompt_override = None
                    if self._runtime.compact_requested:
                        self._runtime.clear_compact()
                        if self._last_turn_result is not None:
                            from src.context_manager import _build_compact_summary

                            summary = _build_compact_summary(
                                self._last_turn_result.messages
                            )
                            if summary:
                                if self._last_turn_result.tokens_used > 0:
                                    _eff = getattr(
                                        self._turn_runner,
                                        "_last_effective_context_limit",
                                        self.config.context_limit,
                                    )
                                    streaming_ui.print_compact_triggered(
                                        self._last_turn_result.tokens_used,
                                        _eff,
                                    )
                                compact_prompt_override = (
                                    f"Context compacted. Summary of previous work:\n{summary}\n\n"
                                    f"Continue implementing the current step: {step.text}\n"
                                    "When done, include:\n"
                                    "What changed:\n- ...\n"
                                    "Evidence:\n- ...\n"
                                    "Verification:\n- ..."
                                )

                    player_prompt = compact_prompt_override or build_player_step_prompt(
                        current_step=step.text,
                        step_num=step_num,
                        total_steps=total_steps,
                        completed_steps=completed_steps,
                        feedback=feedback.text if feedback else None,
                    )
                    player_system = self._system_prompt_with_overlay(
                        PLAYER_SYSTEM_PROMPT,
                        step.roles,
                    )

                    try:
                        player_result = await self._run_with_continuation(
                            role="player",
                            prompt=player_prompt,
                            system_prompt=player_system,
                            max_turns=PLAYER_MAX_TURNS,
                            timeout_s=self.config.player_timeout_s,
                            model_override=self.config.player_model,
                        )
                    except TimeoutError as exc:
                        turn_details.append(
                            TurnDetail(
                                role="player",
                                duration_s=float(self.config.player_timeout_s),
                                tools_used=0,
                            )
                        )
                        feedback = Feedback(
                            f"1. {exc}\n"
                            "2. Continue from the current state; do not start over."
                        )
                        self._kill_new_processes(pids_before)
                        continue

                    self._kill_new_processes(pids_before)
                    self._last_turn_result = player_result

                    turn_details.append(
                        TurnDetail(
                            role="player",
                            duration_s=player_result.duration_s,
                            tools_used=player_result.tools_used,
                        )
                    )

                    if self._interrupted:
                        break

                    if not CoachPlayerSession._has_required_player_report(
                        player_result.text
                    ):
                        feedback = (
                            CoachPlayerSession._build_missing_player_report_feedback(
                                step.text
                            )
                        )
                        streaming_ui.print_step_rejected(feedback.text)
                        continue

                    # --- Coach turn with retry on NoVerdict ---
                    streaming_ui.print_coach_header(
                        step_num, total_steps, attempt, self.coach_model
                    )
                    pids_before_coach = self._snapshot_pids()

                    coach_prompt = build_coach_step_prompt(
                        current_step=step.text,
                        step_num=step_num,
                        total_steps=total_steps,
                        completed_steps=completed_steps,
                    )
                    coach_system = self._system_prompt_with_overlay(
                        COACH_STRICT_SYSTEM_PROMPT,
                        step.roles,
                        review_focus=True,
                    )

                    # Coach retry loop for NoVerdict
                    verdict = None
                    coach_retry_max = self.config.coach_retry_max
                    for coach_attempt in range(1, coach_retry_max + 1):
                        try:
                            coach_result = await self._run_turn(
                                role="coach",
                                prompt=coach_prompt,
                                system_prompt=coach_system,
                                max_turns=8,
                                timeout_s=self.config.coach_timeout_s,
                                model_override=self.config.coach_model,
                            )
                        except TimeoutError as exc:
                            turn_details.append(
                                TurnDetail(
                                    role="coach",
                                    duration_s=float(self.config.coach_timeout_s),
                                    tools_used=0,
                                )
                            )
                            verdict = Feedback(
                                f"1. {exc}\n2. Review the current implementation yourself."
                            )
                            break

                        turn_details.append(
                            TurnDetail(
                                role="coach",
                                duration_s=coach_result.duration_s,
                                tools_used=coach_result.tools_used,
                            )
                        )
                        verdict = parse_coach_output(coach_result.messages)

                        if not isinstance(verdict, NoVerdict):
                            break  # Got a real verdict

                        if coach_attempt < coach_retry_max:
                            streaming_ui.print_coach_no_verdict_retry(
                                coach_attempt, coach_retry_max
                            )
                    else:
                        # Exhausted retries - try fallback
                        if self.config.coach_fallback_provider:
                            fallback_provider = self._get_or_create_provider(
                                self.config.coach_fallback_provider
                            )
                            streaming_ui.print_coach_fallback_escalation(
                                fallback_provider.display_name
                            )
                            try:
                                fallback_result = await self._run_turn(
                                    role="coach_fallback",
                                    prompt=coach_prompt,
                                    system_prompt=coach_system,
                                    max_turns=8,
                                    timeout_s=self.config.coach_timeout_s,
                                    model_override=self.config.coach_fallback_model,
                                    provider_override=fallback_provider,
                                )
                                verdict = parse_coach_output(fallback_result.messages)
                            except TimeoutError:
                                verdict = Feedback(
                                    "1. Fallback coach timed out.\n2. Proceed with current state."
                                )

                        if isinstance(verdict, NoVerdict):
                            print(
                                f"\n  {BOLD}{YELLOW}⚠ Coach silent - rejecting step for retry{RESET}"
                            )
                            verdict = CoachPlayerSession._build_step_fallback_feedback(
                                step.text,
                                prefix_issue="Coach produced no verdict after retries and fallback review.",
                            )

                    self._kill_new_processes(pids_before_coach)

                    if isinstance(verdict, Approved):
                        # --- Code Review phase (iterative until zero bugs) ---
                        if self.config.code_review:
                            review_provider = self._provider_for_runtime_role("reviewer")
                            review_provider_name = self.router.provider_name_for(
                                "reviewer"
                            )
                            review_model = self.router._resolve_review_model()
                            max_iter = self.config.max_review_iterations
                            review_cleared = False
                            review_feedback = Feedback(
                                "1. Code review did not confirm the implementation.\n"
                                "2. Fix the reported issues and re-run the step."
                            )
                            for review_iter in range(max_iter):
                                streaming_ui.print_code_review_header(
                                    step_num,
                                    total_steps,
                                    review_provider.display_name,
                                    iteration=review_iter + 1,
                                    max_iterations=max_iter,
                                )
                                review_prompt = build_code_review_prompt(
                                    current_step=step.text,
                                    step_num=step_num,
                                    total_steps=total_steps,
                                )
                                try:
                                    review_result = await self._run_turn(
                                        role="reviewer",
                                        prompt=review_prompt,
                                        system_prompt=CODE_REVIEWER_SYSTEM_PROMPT,
                                        max_turns=8,
                                        timeout_s=self.config.coach_timeout_s,
                                        model_override=review_model,
                                        provider_override=review_provider,
                                    )
                                except TimeoutError:
                                    review_feedback = Feedback(
                                        "1. Code review timed out.\n"
                                        "2. Retry the step and verify the implementation before asking for approval again."
                                    )
                                    print(
                                        f"\n  {BOLD}{YELLOW}⚠ Code review timed out{RESET}"
                                    )
                                    break

                                review_verdict = parse_review_output(
                                    review_result.messages
                                )

                                if isinstance(review_verdict, ReviewPassed):
                                    review_cleared = True
                                    streaming_ui.print_review_passed(step_num)
                                    break

                                # Bugs found — player fixes, then re-review
                                review_feedback = Feedback(review_verdict.text)
                                streaming_ui.print_review_issues(review_verdict.text)
                                if review_iter < max_iter - 1:
                                    fix_prompt = build_player_fix_prompt(
                                        review_verdict.text
                                    )
                                    try:
                                        fix_result = await self._run_with_continuation(
                                            role="player",
                                            prompt=fix_prompt,
                                            system_prompt=PLAYER_SYSTEM_PROMPT,
                                            max_turns=PLAYER_MAX_TURNS,
                                            timeout_s=self.config.player_timeout_s,
                                            model_override=self.config.player_model,
                                        )
                                        self._last_turn_result = fix_result
                                    except TimeoutError:
                                        break
                            if not review_cleared:
                                feedback = review_feedback
                                streaming_ui.print_step_rejected(feedback.text)
                                continue

                        step_approved = True
                        plan_items = mark_step_done(plan_items, step_index)
                        if self.plan_file_path:
                            write_checklist_back(self.plan_file_path, plan_items)
                        streaming_ui.print_step_approved(step_num, step.text)
                        break
                    elif isinstance(verdict, Feedback):
                        feedback = (
                            CoachPlayerSession._build_step_fallback_feedback(step.text)
                            if is_invalid_feedback(verdict)
                            else verdict
                        )
                        streaming_ui.print_step_rejected(feedback.text)
                    else:
                        # NoVerdict after retries - shouldn't happen but handle it
                        feedback = CoachPlayerSession._build_step_fallback_feedback(
                            step.text
                        )
                        streaming_ui.print_step_rejected(feedback.text)

                if restart_requested:
                    continue

                if not step_approved and not self._interrupted:
                    print(
                        f"\n  {BOLD}{RED}⚠ Шаг {step_num} не принят за {self.config.max_turns} попыток{RESET}"
                    )
                    break

                if step_approved:
                    step_index += 1

        except Exception as exc:
            error = str(exc)
            print(f"\n  {RED}[ошибка] сессия упала: {error}{RESET}")
        finally:
            self._runtime.stop()

        total_duration = time.time() - start_time
        steps_completed = sum(1 for item in plan_items if item.done)
        all_done = steps_completed == total_steps

        if all_done:
            status = "approved"
            streaming_ui.print_all_done(total_steps)
        elif self._interrupted:
            status = "interrupted"
        elif error:
            status = "failed"
        else:
            status = "max_turns_reached"

        turns_used = len([t for t in turn_details if t.role == "player"])

        record = RunRecord(
            run_id=generate_run_id(),
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            requirements_file=self.config.plan_file,
            turns_used=turns_used,
            max_turns=self.config.max_turns,
            status=status,
            total_duration_s=total_duration,
            turn_details=turn_details,
        )
        self.recorder.record(record)
        self._print_session_report(record, steps_completed, total_steps, plan_items)

        return SessionResult(
            approved=all_done,
            turns_used=turns_used,
            steps_completed=steps_completed,
            total_steps=total_steps,
            total_duration_s=total_duration,
            turn_details=turn_details,
            status=status,
            error=error,
        )

    async def _run_turn(
        self,
        role: str,
        prompt: str,
        system_prompt: str,
        max_turns: int,
        timeout_s: int,
        model_override: str = "",
        provider_override=None,
        disable_tools: bool = False,
    ) -> TurnResult:
        """Run a single agent turn using the appropriate provider."""
        if not hasattr(self, "_turn_runner"):
            self._turn_runner = AgentTurnRunner(verbose=getattr(self.config, "verbose", False))
        result = await self._turn_runner.run_turn(
            role=role,
            prompt=prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout_s=timeout_s,
            provider=provider_override or self._provider_for_runtime_role(role),
            router=getattr(self, "router", None),
            config=self.config,
            model_override=model_override,
            provider_override=provider_override,
            disable_tools=disable_tools,
            interrupted_fn=lambda: self._interrupted,
        )
        runtime = getattr(self, "_runtime", None)
        if runtime is not None:
            context_window = self._turn_runner._last_effective_context_limit
            runtime.update_context(result.tokens_used, context_window)
        return result

    async def _run_coach_turn_for_phase(
        self,
        phase,
        last_player_result,
        completed_steps: list[str] | None = None,
        provider_name_override: str | None = None,
        model_override: str = "",
        review_role: str = "coach",
    ) -> "Approved | Feedback":
        """Run Coach review for a phase attempt."""
        from src.prompts import build_phase_coach_prompt

        prompt = build_phase_coach_prompt(phase, last_player_result, completed_steps)
        provider_override = None
        if provider_name_override:
            provider_override = self._get_or_create_provider(provider_name_override)
            ok, reason = provider_override.check_ready()
            if not ok:
                return Feedback(
                    f"1. {review_role} provider ({provider_name_override}) not ready: {reason}\n"
                    "2. Continue from the current state and keep iterating with the regular reviewer."
                )

        review_turn_budget = min(
            self.config.max_turns,
            BATCH_REVIEW_MAX_TURNS,
        )

        verdict = NoVerdict()
        coach_retry_max = max(1, int(getattr(self.config, "coach_retry_max", 1) or 1))
        current_model_override = model_override or (
            self.config.coach_model if not provider_name_override else ""
        )

        for coach_attempt in range(1, coach_retry_max + 1):
            pids_before = self._snapshot_pids()
            try:
                result = await self._run_turn(
                    role=review_role,
                    prompt=prompt,
                    system_prompt=COACH_STRICT_SYSTEM_PROMPT,
                    max_turns=review_turn_budget,
                    timeout_s=self.config.coach_timeout_s,
                    model_override=current_model_override,
                    provider_override=provider_override,
                )
            except TimeoutError as exc:
                self._kill_new_processes(pids_before)
                return CoachPlayerSession._build_phase_fallback_feedback(
                    phase,
                    completed_steps,
                    prefix_issue=str(exc),
                )

            self._kill_new_processes(pids_before)
            verdict = parse_coach_output(result.messages)
            if not isinstance(verdict, NoVerdict):
                break

            if coach_attempt < coach_retry_max:
                streaming_ui.print_coach_no_verdict_retry(
                    coach_attempt, coach_retry_max
                )

        if isinstance(verdict, NoVerdict):
            fallback_name = getattr(self.config, "coach_fallback_provider", "") or ""
            current_provider_name = provider_name_override or self.config.coach_provider
            if fallback_name and fallback_name != current_provider_name:
                fallback_provider = self._get_or_create_provider(fallback_name)
                ok, reason = fallback_provider.check_ready()
                if not ok:
                    return CoachPlayerSession._build_phase_fallback_feedback(
                        phase,
                        completed_steps,
                        prefix_issue=f"{review_role} produced no verdict and fallback provider ({fallback_name}) not ready: {reason}",
                    )

                streaming_ui.print_coach_fallback_escalation(
                    fallback_provider.display_name
                )
                pids_before = self._snapshot_pids()
                try:
                    fallback_result = await self._run_turn(
                        role="coach_fallback",
                        prompt=prompt,
                        system_prompt=COACH_STRICT_SYSTEM_PROMPT,
                        max_turns=review_turn_budget,
                        timeout_s=self.config.coach_timeout_s,
                        model_override=self.config.coach_fallback_model,
                        provider_override=fallback_provider,
                    )
                except TimeoutError as exc:
                    self._kill_new_processes(pids_before)
                    return CoachPlayerSession._build_phase_fallback_feedback(
                        phase,
                        completed_steps,
                        prefix_issue=f"Fallback reviewer timed out: {exc}",
                    )

                self._kill_new_processes(pids_before)
                verdict = parse_coach_output(fallback_result.messages)

        if isinstance(verdict, NoVerdict):
            print(
                f"\n  {BOLD}{YELLOW}⚠ Reviewer silent - rejecting phase for retry{RESET}"
            )
            return CoachPlayerSession._build_phase_fallback_feedback(
                phase,
                completed_steps,
                prefix_issue="Reviewer produced no verdict after retries and fallback review.",
            )

        if isinstance(verdict, Feedback) and is_invalid_feedback(verdict):
            return CoachPlayerSession._build_phase_fallback_feedback(
                phase, completed_steps
            )
        return verdict

    def _print_session_report(
        self,
        record: RunRecord,
        steps_completed: int,
        total_steps: int,
        plan_items: list | None = None,
    ):
        """Print final session report."""
        print(f"\n{BOLD}--- Итог ---{RESET}")
        print(f"  Шагов: {steps_completed}/{total_steps}")
        print(f"  Ходов: {record.turns_used}")
        print(f"  Время: {record.total_duration_s:.0f}s")
        status_color = GREEN if record.status == "approved" else RED
        print(f"  Статус: {BOLD}{status_color}{record.status.upper()}{RESET}")

        if plan_items:
            streaming_ui.print_step_list(plan_items)
