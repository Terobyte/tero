"""Main coach-player loop: step-by-step plan execution with per-step review."""

import asyncio
import inspect
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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
    write_checklist_back,
    format_checklist,
)
from src.prompts import (
    COACH_STRICT_SYSTEM_PROMPT,
    PLAYER_SYSTEM_PROMPT,
    PLAYER_BATCH_SYSTEM_PROMPT,
    TEST_WRITER_SYSTEM_PROMPT,
    CODE_REVIEWER_SYSTEM_PROMPT,
    build_coach_step_prompt,
    build_player_step_prompt,
    build_test_writer_prompt,
    build_code_review_prompt,
)
from src.providers import create_provider, adapt_claude_event, adapt_sdk_message
from src.providers.message_adapter import AdaptedMessage
from src.providers.ccg import run_agent
from src import streaming as streaming_ui
from src.streaming import BOLD, RESET, GREEN, RED, YELLOW


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

    BATCH_REVIEW_MAX_TURNS = 4
    REQUIRED_PLAYER_REPORT_HEADERS = ("what changed:", "evidence:", "verification:")

    def __init__(self, config: Config, requirements: str, plan_file_path: str = ""):
        self.config = config
        self.requirements = requirements
        self.plan_file_path = plan_file_path
        self.recorder = RunRecorder(f"{config.working_dir}/.g3/knowledge")
        self._interrupted = False

        # Load provider configs and create providers (supports multi-account)
        self.provider_configs = self._load_provider_configs()
        self._provider_cache: dict[str, object] = {}
        self.player_provider = self._get_or_create_provider(config.player_provider)
        self.coach_provider = self._get_or_create_provider(config.coach_provider)

        # Verify providers are ready
        self._verify_providers_ready()

        # Resolve role labels using the actual runtime model/provider config.
        self.player_model = self._build_role_display("player")
        self.coach_model = self._build_role_display("coach")

        # Initialize review provider if code_review is enabled
        self.review_provider = None
        self.review_provider_name = ""
        self.review_model = ""
        if self.config.code_review:
            self._init_review_provider()

    def _load_provider_configs(self) -> dict:
        """Load provider configs from global, bundled, and working-dir config."""
        return load_provider_configs(self.config.working_dir)

    def _verify_providers_ready(self) -> None:
        """Verify all providers are ready, raise if not."""
        for name, prov in [
            ("player", self.player_provider),
            ("coach", self.coach_provider),
        ]:
            ok, reason = prov.check_ready()
            if not ok:
                provider_key = f"{name}_provider"
                provider_name = getattr(self.config, provider_key)
                raise RuntimeError(
                    f"{name} provider ({provider_name}) not ready: {reason}"
                )

    def _init_review_provider(self) -> None:
        """Initialize review provider for code review phase."""
        review_provider_name = self.config.review_provider
        if not review_provider_name:
            # Auto-detect: try codex first, then coach
            try:
                codex_prov = self._get_or_create_provider("codex")
                ok, _ = codex_prov.check_ready()
            except Exception:
                ok = False

            review_provider_name = "codex" if ok else self.config.coach_provider

        self.review_provider = self._get_or_create_provider(review_provider_name)
        self.review_provider_name = review_provider_name
        self.review_model = self.config.review_model

    def _get_or_create_provider(self, provider_name: str):
        """Return a cached provider instance by configured name."""
        if provider_name not in self._provider_cache:
            self._provider_cache[provider_name] = create_provider(
                provider_name,
                None,
                self.provider_configs.get(provider_name),
            )
        return self._provider_cache[provider_name]

    def _provider_name_for_role(self, role: str) -> str:
        """Return the configured provider name backing a runtime role."""
        if role == "player":
            return self.config.player_provider
        if role in ("coach", "test_writer"):
            return self.config.coach_provider
        if role == "reviewer":
            return self.review_provider_name or self.config.review_provider or self.config.coach_provider
        if role == "coach_fallback":
            return self.config.coach_fallback_provider or self.config.coach_provider
        raise ValueError(f"Unknown role: {role}")

    def _provider_for_role(self, role: str):
        """Return the provider instance backing a role."""
        if role == "player":
            return self.player_provider
        if role in ("coach", "test_writer"):
            return self.coach_provider
        if role == "reviewer":
            return self.review_provider or self.coach_provider
        if role == "coach_fallback":
            return self._get_or_create_provider(self.config.coach_fallback_provider)
        raise ValueError(f"Unknown role: {role}")

    @staticmethod
    def _provider_model(provider) -> str:
        """Best-effort lookup of the model that provider will use by default."""
        env = getattr(provider, "env", None)
        if env is not None:
            model = getattr(env, "model", "")
            if model:
                return model

        provider_config = getattr(provider, "config", None)
        if provider_config is not None:
            for attr in ("default_model", "model"):
                value = getattr(provider_config, attr, "")
                if value:
                    return value

        return ""

    @staticmethod
    def _provider_account(provider) -> str:
        """Best-effort lookup of an account label for display."""
        env = getattr(provider, "env", None)
        if env is not None:
            return getattr(env, "account_label", "") or ""
        return ""

    def _build_role_display(self, role: str) -> str:
        """Build a stable label showing provider, model, and account."""
        provider_name = self._provider_name_for_role(role)
        provider = self._provider_for_role(role)
        return self._format_provider_display(provider_name, provider, getattr(self.config, f"{role}_model", ""))

    def _format_provider_display(self, provider_name: str, provider, model_override: str = "") -> str:
        """Build a display label for any provider/model combination."""
        resolved_model = (
            model_override
            or self._provider_model(provider)
            or "default"
        )
        account = self._provider_account(provider)

        parts = [provider_name, f"model={resolved_model}"]
        if account:
            parts.append(f"account={account}")
        return " | ".join(parts)

    def build_provider_display(self, provider_name: str, model_override: str = "") -> str:
        """Public helper for UI labels outside the main role pair."""
        provider = self._get_or_create_provider(provider_name)
        return self._format_provider_display(provider_name, provider, model_override)

    def _setup_interrupt_handler(self):
        def handler(signum, frame):
            self._interrupted = True
            print("\n\n--- Прервано ---")
        signal.signal(signal.SIGINT, handler)

    def _snapshot_pids(self) -> set[int]:
        """Snapshot current PIDs in the working directory."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", os.path.abspath(self.config.working_dir)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return {
                    int(pid) for pid in result.stdout.strip().split("\n")
                    if pid.strip().isdigit()
                }
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        return set()

    def _kill_new_processes(self, before: set[int]) -> None:
        """Kill processes that appeared since the snapshot."""
        after = self._snapshot_pids()
        new_pids = after - before - {os.getpid()}
        for pid in new_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        if new_pids and self.config.verbose:
            print(f"  [cleanup] убито процессов: {len(new_pids)}")

    @staticmethod
    def _build_step_fallback_feedback(step_text: str, prefix_issue: str | None = None) -> Feedback:
        """Fallback feedback when the coach fails to produce a valid review."""
        lines: list[str] = []
        if prefix_issue:
            lines.append(f"1. {prefix_issue}")
            start_num = 2
        else:
            start_num = 1

        lines.append(f"{start_num}. Complete the current step exactly: {step_text}")
        lines.append(f"{start_num + 1}. Verify the change with the most relevant test, command, or file inspection.")
        lines.append(f"{start_num + 2}. If the code already exists, prove it by making the missing fix instead of only stating that it is done.")
        return Feedback("\n".join(lines))

    @staticmethod
    def _build_phase_fallback_feedback(
        phase,
        completed_steps: list[str] | None,
        prefix_issue: str | None = None,
    ) -> Feedback:
        """Fallback batch feedback when the reviewer fails to produce usable output."""
        completed = set(completed_steps or [])
        missing_steps = [step.text for step in phase.steps if step.text not in completed]

        lines: list[str] = []
        next_num = 1
        if prefix_issue:
            lines.append(f"{next_num}. {prefix_issue}")
            next_num += 1

        if missing_steps:
            for missing_step in missing_steps:
                lines.append(f"{next_num}. Complete the missing planned step: {missing_step}")
                next_num += 1
        else:
            lines.append(f"{next_num}. Re-check the whole phase `{phase.name}` and fix any missing implementation details.")
            next_num += 1

        lines.append(f"{next_num}. Run the most relevant verification before finishing this phase.")
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
    def _build_missing_player_report_feedback(step_text: str) -> Feedback:
        """Structured feedback when Player omitted the final completion report."""
        return Feedback(
            "1. Your final response is missing the required completion report.\n"
            "2. Re-send the completion response with `What changed`, `Evidence`, and `Verification`.\n"
            f"3. If no code changes were needed for `{step_text}`, say that explicitly and cite the files or checks proving it was already implemented."
        )

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
        print(f"  Шагов: {total_steps}  |  Макс. попыток на шаг: {self.config.max_turns}")
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
            print(f"  Продолжаем с шага {start_index + 1} ({start_index} шагов уже сделано)\n")

        try:
            for step_index in range(start_index, total_steps):
                if self._interrupted:
                    break

                step = plan_items[step_index]
                if step.done:
                    continue

                step_num = step_index + 1
                completed_steps = [p.text for p in plan_items[:step_index] if p.done]

                streaming_ui.print_step_header(step_num, total_steps, step.text)

                feedback = None
                step_approved = False
                tests_written = False  # TDD: tests written once per step

                # --- TDD Mode: Test Writer phase (once per step) ---
                if self.config.tdd_mode and not tests_written:
                    streaming_ui.print_test_writer_header(step_num, total_steps)
                    test_prompt = build_test_writer_prompt(
                        current_step=step.text,
                        step_num=step_num,
                        total_steps=total_steps,
                        completed_steps=completed_steps,
                    )
                    try:
                        await self._run_turn(
                            role="test_writer",
                            prompt=test_prompt,
                            system_prompt=TEST_WRITER_SYSTEM_PROMPT,
                            max_turns=15,
                            timeout_s=self.config.coach_timeout_s,
                            model_override=self.config.coach_model,
                        )
                        tests_written = True
                    except TimeoutError:
                        print(f"\n  {BOLD}{YELLOW}⚠ Test writer timed out, continuing...{RESET}")

                for attempt in range(1, self.config.max_turns + 1):
                    if self._interrupted:
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

                    player_prompt = build_player_step_prompt(
                        current_step=step.text,
                        step_num=step_num,
                        total_steps=total_steps,
                        completed_steps=completed_steps,
                        feedback=feedback.text if feedback else None,
                    )

                    try:
                        player_result = await self._run_turn(
                            role="player",
                            prompt=player_prompt,
                            system_prompt=PLAYER_SYSTEM_PROMPT,
                            max_turns=30,
                            timeout_s=self.config.player_timeout_s,
                            model_override=self.config.player_model,
                        )
                    except TimeoutError as exc:
                        turn_details.append(TurnDetail(role="player", duration_s=float(self.config.player_timeout_s), tools_used=0))
                        feedback = Feedback(
                            f"1. {exc}\n"
                            "2. Continue from the current state; do not start over."
                        )
                        self._kill_new_processes(pids_before)
                        continue

                    self._kill_new_processes(pids_before)

                    turn_details.append(TurnDetail(role="player", duration_s=player_result.duration_s, tools_used=player_result.tools_used))

                    if self._interrupted:
                        break

                    if not CoachPlayerSession._has_required_player_report(player_result.text):
                        feedback = CoachPlayerSession._build_missing_player_report_feedback(step.text)
                        streaming_ui.print_step_rejected(feedback.text)
                        continue

                    # --- TDD Mode: Run tests after player attempt ---
                    if self.config.tdd_mode:
                        test_passed, test_output = await self._run_tests()
                        streaming_ui.print_tdd_status(test_passed, test_output)
                        if not test_passed:
                            feedback = Feedback(
                                f"1. Tests are still failing:\n{test_output[:500]}\n"
                                "2. Fix the implementation until tests pass."
                            )
                            continue  # Skip coach, retry player

                    # --- Coach turn with retry on NoVerdict ---
                    streaming_ui.print_coach_header(step_num, total_steps, attempt, self.coach_model)
                    pids_before_coach = self._snapshot_pids()

                    coach_prompt = build_coach_step_prompt(
                        current_step=step.text,
                        step_num=step_num,
                        total_steps=total_steps,
                        completed_steps=completed_steps,
                    )

                    # Coach retry loop for NoVerdict
                    verdict = None
                    coach_retry_max = self.config.coach_retry_max
                    for coach_attempt in range(1, coach_retry_max + 1):
                        try:
                            coach_result = await self._run_turn(
                                role="coach",
                                prompt=coach_prompt,
                                system_prompt=COACH_STRICT_SYSTEM_PROMPT,
                                max_turns=8,
                                timeout_s=self.config.coach_timeout_s,
                                model_override=self.config.coach_model,
                            )
                        except TimeoutError as exc:
                            turn_details.append(TurnDetail(role="coach", duration_s=float(self.config.coach_timeout_s), tools_used=0))
                            verdict = Feedback(f"1. {exc}\n2. Review the current implementation yourself.")
                            break

                        turn_details.append(TurnDetail(role="coach", duration_s=coach_result.duration_s, tools_used=coach_result.tools_used))
                        verdict = parse_coach_output(coach_result.messages)

                        if not isinstance(verdict, NoVerdict):
                            break  # Got a real verdict

                        if coach_attempt < coach_retry_max:
                            streaming_ui.print_coach_no_verdict_retry(coach_attempt, coach_retry_max)
                    else:
                        # Exhausted retries - try fallback
                        if self.config.coach_fallback_provider:
                            fallback_provider = self._get_or_create_provider(self.config.coach_fallback_provider)
                            streaming_ui.print_coach_fallback_escalation(fallback_provider.display_name)
                            try:
                                fallback_result = await self._run_turn(
                                    role="coach_fallback",
                                    prompt=coach_prompt,
                                    system_prompt=COACH_STRICT_SYSTEM_PROMPT,
                                    max_turns=8,
                                    timeout_s=self.config.coach_timeout_s,
                                    model_override=self.config.coach_fallback_model,
                                    provider_override=fallback_provider,
                                )
                                verdict = parse_coach_output(fallback_result.messages)
                            except TimeoutError:
                                verdict = Feedback("1. Fallback coach timed out.\n2. Proceed with current state.")

                        if isinstance(verdict, NoVerdict):
                            # Even fallback failed - skip step
                            print(f"\n  {BOLD}{YELLOW}⚠ Coach silent - approving step{RESET}")
                            verdict = Approved()

                    self._kill_new_processes(pids_before_coach)

                    if isinstance(verdict, Approved):
                        # --- Code Review phase (after coach approval) ---
                        if self.config.code_review and self.review_provider:
                            streaming_ui.print_code_review_header(
                                step_num, total_steps, self.review_provider.display_name
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
                                    model_override=self.review_model,
                                    provider_override=self.review_provider,
                                )
                                review_verdict = parse_review_output(review_result.messages)
                                if isinstance(review_verdict, ReviewPassed):
                                    streaming_ui.print_review_passed(step_num)
                                else:
                                    streaming_ui.print_review_issues(review_verdict.text)
                                    feedback = Feedback(review_verdict.text)
                                    continue  # Retry player
                            except TimeoutError:
                                print(f"\n  {BOLD}{YELLOW}⚠ Code review timed out, approving...{RESET}")

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
                        feedback = CoachPlayerSession._build_step_fallback_feedback(step.text)
                        streaming_ui.print_step_rejected(feedback.text)

                if not step_approved and not self._interrupted:
                    print(f"\n  {BOLD}{RED}⚠ Шаг {step_num} не принят за {self.config.max_turns} попыток{RESET}")
                    break

        except Exception as exc:
            error = str(exc)
            print(f"\n  {RED}[ошибка] сессия упала: {error}{RESET}")

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
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
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
    ) -> TurnResult:
        """Run a single agent turn using the appropriate provider."""
        start = time.time()
        messages = []
        tools_used = 0
        tokens_used = 0

        provider = provider_override or self._provider_for_role(role)
        model = model_override or ""

        async def _collect() -> None:
            nonlocal tools_used, tokens_used
            if self._interrupted:
                return

            async for msg in provider.run(
                prompt=prompt,
                system_prompt=system_prompt,
                working_dir=self.config.working_dir,
                max_turns=max_turns,
                model=model,
            ):
                if self._interrupted:
                    return

                # Extract token usage from ResultMessage before adapting
                if not isinstance(msg, dict) and type(msg).__name__ == "ResultMessage":
                    usage = getattr(msg, "usage", None) or {}
                    if isinstance(usage, dict):
                        tokens_used = (
                            usage.get("input_tokens", 0)
                            + usage.get("output_tokens", 0)
                        )

                # Adapt message if needed (for native CLI JSON)
                adapted = None
                if isinstance(msg, dict):
                    adapted = adapt_claude_event(msg)
                else:
                    adapted = adapt_sdk_message(msg)

                if adapted:
                    messages.append(adapted)
                    tools_used += streaming_ui.stream_messages(
                        adapted, verbose=self.config.verbose, role=role
                    )
                else:
                    messages.append(msg)
                    tools_used += streaming_ui.stream_messages(
                        msg, verbose=self.config.verbose, role=role
                    )

        try:
            await asyncio.wait_for(_collect(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{role} exceeded timeout of {timeout_s}s") from exc

        duration = time.time() - start
        resolved_model = model or self._provider_model(provider)
        from src.config import get_context_window
        context_window = get_context_window(resolved_model)
        streaming_ui.print_turn_timing(role, duration, tools_used, tokens_used, context_window)

        text_parts = [
            msg.get_text_content()
            for msg in messages
            if isinstance(msg, AdaptedMessage) and msg.role == "assistant"
        ]
        result_text = "\n".join(p for p in text_parts if p)

        # Fallback: if no text from AssistantMessages, use ResultMessage.result
        if not result_text:
            for msg in messages:
                if type(msg).__name__ == "ResultMessage":
                    result_text = getattr(msg, "result", "") or ""
                    break

        return TurnResult(
            role=role,
            duration_s=duration,
            tools_used=tools_used,
            messages=messages,
            text=result_text,
            tokens_used=tokens_used,
        )

    def _has_completion_markers(self, text: str, role: str) -> bool:
        """Return True when the turn output contains the required completion markers."""
        from src.batch_executor import _PHASE_COMPLETE_RE, _REQUIRED_REPORT_HEADERS
        from src.feedback import _APPROVED_MARKER_RE, _NUMBERED_ISSUE_RE

        if role == "player":
            if not _PHASE_COMPLETE_RE.search(text):
                return False
            lowered = text.lower()
            return all(h in lowered for h in _REQUIRED_REPORT_HEADERS)
        # coach/reviewer: needs IMPLEMENTATION_APPROVED or numbered issues
        if _APPROVED_MARKER_RE.search(text):
            return True
        return bool(_NUMBERED_ISSUE_RE.search(text))

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
        """Run a turn, retrying with compact context if no completion markers found."""
        from src.context_manager import _build_compact_summary, _build_continuation_prompt
        from src import streaming as streaming_ui

        result = await self._run_turn(
            role=role,
            prompt=prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout_s=timeout_s,
            model_override=model_override,
            provider_override=provider_override,
        )

        for attempt in range(self.config.max_continuation_attempts):
            if self._has_completion_markers(result.text, role):
                return result

            streaming_ui.print_continuation_started(role, attempt + 1, self.config.max_continuation_attempts)
            summary = _build_compact_summary(result.messages)
            continuation_prompt = _build_continuation_prompt(summary, role)

            result = await self._run_turn(
                role=role,
                prompt=continuation_prompt,
                system_prompt=system_prompt,
                max_turns=max_turns,
                timeout_s=timeout_s,
                model_override=model_override,
                provider_override=provider_override,
            )

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

        pids_before = self._snapshot_pids()
        review_turn_budget = min(
            self.config.max_turns,
            CoachPlayerSession.BATCH_REVIEW_MAX_TURNS,
        )

        try:
            result = await self._run_turn(
                role=review_role,
                prompt=prompt,
                system_prompt=COACH_STRICT_SYSTEM_PROMPT,
                max_turns=review_turn_budget,
                timeout_s=self.config.coach_timeout_s,
                model_override=model_override or self.config.coach_model,
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
        if isinstance(verdict, Feedback) and is_invalid_feedback(verdict):
            return CoachPlayerSession._build_phase_fallback_feedback(phase, completed_steps)
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

    async def _run_tests(self) -> tuple[bool, str]:
        """Run tests for TDD mode. Returns (passed, output)."""
        # Determine test command
        if self.config.test_command:
            test_cmd = shlex.split(self.config.test_command)
        else:
            test_cmd = self._detect_test_command()

        try:
            result = subprocess.run(
                test_cmd,
                cwd=self.config.working_dir,
                capture_output=True,
                text=True,
                timeout=self.config.test_timeout_s,
            )
            output = result.stdout + result.stderr
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, f"Test command timed out after {self.config.test_timeout_s}s"
        except Exception as e:
            return False, f"Test command failed: {e}"

    def _detect_test_command(self) -> list[str]:
        """Auto-detect the project test command."""
        working_dir = Path(self.config.working_dir)
        pyproject_path = working_dir / "pyproject.toml"

        if (working_dir / "pytest.ini").exists():
            return ["pytest", "-q"]
        if pyproject_path.exists():
            try:
                pyproject_text = pyproject_path.read_text()
            except OSError:
                pyproject_text = ""
            if "[tool.pytest" in pyproject_text or "[tool.pytest.ini_options]" in pyproject_text:
                return ["pytest", "-q"]
        if (working_dir / "package.json").exists():
            return ["npm", "test"]
        if (working_dir / "Cargo.toml").exists():
            return ["cargo", "test"]
        if (working_dir / "Makefile").exists():
            return ["make", "test"]
        return ["pytest", "-q"]
