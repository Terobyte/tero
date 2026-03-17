"""Main coach-player loop: step-by-step plan execution with per-step review."""

import asyncio
import inspect
import os
import signal
import subprocess
import time
from dataclasses import dataclass

from src.config import Config, CcgEnv, short_model_name
from src.feedback import parse_coach_output, Approved, Feedback
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
    build_coach_step_prompt,
    build_player_step_prompt,
)
from src.providers import create_provider, adapt_claude_event, adapt_sdk_message
from src.providers.message_adapter import AdaptedMessage
from src.providers.ccg import run_agent
from src import streaming as streaming_ui
from src.streaming import BOLD, RESET, GREEN, RED


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

    def __init__(self, config: Config, requirements: str, plan_file_path: str = ""):
        self.config = config
        self.requirements = requirements
        self.plan_file_path = plan_file_path
        self.ccg_env = CcgEnv.from_env(config.claude_home)
        self.recorder = RunRecorder(f"{config.working_dir}/.g3/knowledge")
        self._interrupted = False

        # Create providers based on config
        provider_configs = self._load_provider_configs()
        self.player_provider = create_provider(
            config.player_provider,
            self.ccg_env,
            provider_configs.get(config.player_provider),
        )
        self.coach_provider = create_provider(
            config.coach_provider,
            self.ccg_env,
            provider_configs.get(config.coach_provider),
        )

        # Verify providers are ready
        self._verify_providers_ready()

        # Resolve display names
        self.player_model = self.player_provider.display_name
        self.coach_model = self.coach_provider.display_name

    def _load_provider_configs(self) -> dict:
        """Read providers: section from .g3/config.yaml."""
        yaml_path = os.path.join(self.config.working_dir, ".g3", "config.yaml")
        if os.path.exists(yaml_path):
            try:
                import yaml
                with open(yaml_path) as f:
                    data = yaml.safe_load(f) or {}
                return data.get("providers", {})
            except Exception:
                pass
        return {}

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

                for attempt in range(1, self.config.max_turns + 1):
                    if self._interrupted:
                        break

                    # --- Player turn ---
                    streaming_ui.print_player_header(step_num, total_steps, attempt, self.config.max_turns)
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

                    # --- Coach turn ---
                    streaming_ui.print_coach_header(step_num, total_steps, attempt, self.coach_model)
                    pids_before_coach = self._snapshot_pids()

                    coach_prompt = build_coach_step_prompt(
                        current_step=step.text,
                        step_num=step_num,
                        total_steps=total_steps,
                        completed_steps=completed_steps,
                    )

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
                        feedback = Feedback(
                            f"1. {exc}\n"
                            "2. Review the current implementation yourself and fix obvious issues."
                        )
                        self._kill_new_processes(pids_before_coach)
                        continue

                    self._kill_new_processes(pids_before_coach)

                    turn_details.append(TurnDetail(role="coach", duration_s=coach_result.duration_s, tools_used=coach_result.tools_used))

                    verdict = parse_coach_output(coach_result.messages)

                    if isinstance(verdict, Approved):
                        step_approved = True
                        plan_items = mark_step_done(plan_items, step_index)
                        if self.plan_file_path:
                            write_checklist_back(self.plan_file_path, plan_items)
                        streaming_ui.print_step_approved(step_num, step.text)
                        break
                    else:
                        feedback = verdict
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
        self._print_session_report(record, steps_completed, total_steps)

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
    ) -> TurnResult:
        """Run a single agent turn using the appropriate provider."""
        start = time.time()
        messages = []
        tools_used = 0

        provider = (
            self.player_provider if role == "player"
            else self.coach_provider
        )
        model = model_override or ""

        async def _collect() -> None:
            nonlocal tools_used
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
        streaming_ui.print_turn_timing(role, duration, tools_used)

        # Extract text from assistant messages
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

    def _print_session_report(self, record: RunRecord, steps_completed: int, total_steps: int):
        """Print final session report."""
        print(f"\n{BOLD}--- Итог ---{RESET}")
        print(f"  Шагов: {steps_completed}/{total_steps}")
        print(f"  Ходов: {record.turns_used}")
        print(f"  Время: {record.total_duration_s:.0f}s")
        status_color = GREEN if record.status == "approved" else RED
        print(f"  Статус: {BOLD}{status_color}{record.status.upper()}{RESET}")
