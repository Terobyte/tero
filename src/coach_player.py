"""Main coach-player loop: step-by-step plan execution with per-step review."""

import asyncio
import datetime
import inspect
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, replace
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
from src.personas import PersonaRegistry
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
    TEST_WRITER_SYSTEM_PROMPT,
    CODE_REVIEWER_SYSTEM_PROMPT,
    build_coach_step_prompt,
    build_player_step_prompt,
    build_test_writer_prompt,
    build_code_review_prompt,
    build_player_fix_prompt,
)
from src.providers import create_provider, adapt_claude_event, adapt_sdk_message
from src.providers.message_adapter import AdaptedMessage
from src.providers.codex import CodexProvider
from src.providers.ccg import run_agent
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

    BATCH_REVIEW_MAX_TURNS = 4
    REQUIRED_PLAYER_REPORT_HEADERS = ("what changed:", "evidence:", "verification:")

    def __init__(self, config: Config, requirements: str, plan_file_path: str = ""):
        self.config = config
        self.requirements = requirements
        self.plan_file_path = plan_file_path
        self.recorder = RunRecorder(f"{config.working_dir}/.g3/knowledge")
        self._interrupted = False
        self._persona_registry: PersonaRegistry | None = None

        # Load provider configs and create providers (supports multi-account)
        self.provider_configs = self._load_provider_configs()
        self._provider_cache: dict[str, object] = {}
        self.player_provider = self._get_or_create_provider(config.player_provider)
        self.coach_provider = self._get_or_create_provider(config.coach_provider)

        self._runtime = RuntimeControls()
        self._last_turn_result: TurnResult | None = None

        # Resolve role labels using the actual runtime model/provider config.
        self.player_model = self._build_role_display("player")
        self.coach_model = self._build_role_display("coach")

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
        providers_to_check = [
            ("player", self.config.player_provider, self.player_provider),
            ("coach", self.config.coach_provider, self.coach_provider),
        ]

        if self.config.tdd_mode:
            providers_to_check.append(
                (
                    "test_writer",
                    self.config.test_writer_provider or self.config.coach_provider,
                    self._provider_for_role("test_writer"),
                )
            )

        if self.config.preplan_mode:
            providers_to_check.append(
                (
                    "preplanner",
                    self.config.preplan_provider,
                    self._provider_for_role("preplanner"),
                )
            )

        if self.config.code_review and self.review_provider is not None:
            providers_to_check.append(
                (
                    "review",
                    self._resolve_review_provider_name(),
                    self._resolve_review_provider(),
                )
            )

        for role, provider_name, prov in providers_to_check:
            ok, reason = prov.check_ready()
            if not ok:
                raise RuntimeError(
                    f"{role} provider ({provider_name}) not ready: {reason}"
                )

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
            None,
            self.provider_configs.get(provider_name),
        )

    def _check_provider_ready_without_cache(self, provider_name: str) -> bool:
        """Probe provider readiness without caching a possibly unusable instance."""
        try:
            provider = self._create_provider_uncached(provider_name)
            ok, _ = provider.check_ready()
            return ok
        except Exception:
            return False

    def _get_or_create_provider(self, provider_name: str):
        """Return a cached provider instance by configured name."""
        if provider_name not in self._provider_cache:
            self._provider_cache[provider_name] = self._create_provider_uncached(
                provider_name
            )
        return self._provider_cache[provider_name]

    def _provider_name_for_role(self, role: str) -> str:
        """Return the configured provider name backing a runtime role."""
        if role == "player":
            return self.config.player_provider
        if role == "coach":
            return self.config.coach_provider
        if role == "test_writer":
            return self.config.test_writer_provider or self.config.coach_provider
        if role == "preplanner":
            return self.config.preplan_provider
        if role == "reviewer":
            return self._resolve_review_provider_name()
        if role == "coach_fallback":
            return self.config.coach_fallback_provider or self.config.coach_provider
        raise ValueError(f"Unknown role: {role}")

    def _provider_for_role(self, role: str):
        """Return the provider instance backing a role."""
        if role == "player":
            return self.player_provider
        if role == "coach":
            return self.coach_provider
        if role == "test_writer":
            return self._get_or_create_provider(
                self.config.test_writer_provider or self.config.coach_provider
            )
        if role == "preplanner":
            return self._get_or_create_provider(self.config.preplan_provider)
        if role == "reviewer":
            return self._resolve_review_provider()
        if role == "coach_fallback":
            return self._get_or_create_provider(
                self.config.coach_fallback_provider or self.config.coach_provider
            )
        raise ValueError(f"Unknown role: {role}")

    def _resolve_review_provider_name(self) -> str:
        """Return the current provider name for code review.

        When `review_provider` is not explicitly configured, review follows the
        live coach provider so runtime coach switches stay consistent.
        """
        explicit_review = (self.config.review_provider or "").strip()
        if explicit_review:
            return explicit_review
        return self.config.coach_provider

    def _resolve_review_provider(self):
        """Return the current provider instance for code review."""
        review_provider_name = self._resolve_review_provider_name()
        if review_provider_name == self.config.coach_provider:
            return self.coach_provider
        return self._get_or_create_provider(review_provider_name)

    def _resolve_review_model(self) -> str:
        """Return the model override for code review.

        Explicit review models win. Otherwise, when review follows the live
        coach provider, reuse the current coach model so runtime switches stay
        consistent across implementation and review.
        """
        if self.config.review_model:
            return self.config.review_model
        if self._resolve_review_provider_name() == self.config.coach_provider:
            return self.config.coach_model
        return self.review_model

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
        return self._format_provider_display(
            provider_name, provider, getattr(self.config, f"{role}_model", "")
        )

    def _format_provider_display(
        self, provider_name: str, provider, model_override: str = ""
    ) -> str:
        """Build a display label for any provider/model combination."""
        resolved_model = model_override or self._provider_model(provider) or "default"
        account = self._provider_account(provider)

        parts = [provider_name, f"model={resolved_model}"]
        if account:
            parts.append(f"account={account}")
        return " | ".join(parts)

    def build_provider_display(
        self, provider_name: str, model_override: str = ""
    ) -> str:
        """Public helper for UI labels outside the main role pair."""
        provider = self._get_or_create_provider(provider_name)
        return self._format_provider_display(provider_name, provider, model_override)

    def switch_runtime_role(self, role: str, provider_name: str, model: str) -> str:
        """Apply a live provider/model switch safely and return the new label."""
        if role not in {"coach", "player"}:
            raise ValueError(f"Unsupported runtime role switch: {role}")

        provider = self._get_or_create_provider(provider_name)
        ok, reason = provider.check_ready()
        if not ok:
            raise RuntimeError(f"{role} provider ({provider_name}) not ready: {reason}")

        snapshot = {
            "coach_provider": self.config.coach_provider,
            "coach_model": self.config.coach_model,
            "player_provider": self.config.player_provider,
            "player_model": self.config.player_model,
            "batch_pre_provider": self.config.batch_pre_provider,
            "batch_pre_model": self.config.batch_pre_model,
            "batch_post_provider": self.config.batch_post_provider,
            "batch_post_model": self.config.batch_post_model,
            "coach_fallback_provider": getattr(self.config, "coach_fallback_provider", ""),
            "coach_fallback_model": getattr(self.config, "coach_fallback_model", ""),
            "review_provider": getattr(self.config, "review_provider", ""),
            "review_model": getattr(self.config, "review_model", ""),
            "test_writer_provider": getattr(self.config, "test_writer_provider", ""),
            "test_writer_model": getattr(self.config, "test_writer_model", ""),
            "coach_provider_obj": self.coach_provider,
            "player_provider_obj": self.player_provider,
            "coach_display": self.coach_model,
            "player_display": self.player_model,
        }

        try:
            if role == "coach":
                sync_batch_pre = getattr(
                    self.config, "batch_pre_provider", ""
                ) == snapshot["coach_provider"] and getattr(
                    self.config, "batch_pre_model", ""
                ) == snapshot["coach_model"]
                sync_batch_post = getattr(
                    self.config, "batch_post_provider", ""
                ) == snapshot["coach_provider"] and getattr(
                    self.config, "batch_post_model", ""
                ) == snapshot["coach_model"]

                self.config.coach_provider = provider_name
                self.config.coach_model = model
                if sync_batch_pre:
                    self.config.batch_pre_provider = provider_name
                    self.config.batch_pre_model = model
                if sync_batch_post:
                    self.config.batch_post_provider = provider_name
                    self.config.batch_post_model = model
                self.coach_provider = provider
                self.coach_model = self._build_role_display("coach")
                return self.coach_model

            self.config.player_provider = provider_name
            self.config.player_model = model
            self.player_provider = provider
            self.player_model = self._build_role_display("player")
            return self.player_model
        except Exception:
            self.config.coach_provider = snapshot["coach_provider"]
            self.config.coach_model = snapshot["coach_model"]
            self.config.player_provider = snapshot["player_provider"]
            self.config.player_model = snapshot["player_model"]
            self.config.batch_pre_provider = snapshot["batch_pre_provider"]
            self.config.batch_pre_model = snapshot["batch_pre_model"]
            self.config.batch_post_provider = snapshot["batch_post_provider"]
            self.config.batch_post_model = snapshot["batch_post_model"]
            if hasattr(self.config, "coach_fallback_provider"):
                self.config.coach_fallback_provider = snapshot["coach_fallback_provider"]
            if hasattr(self.config, "coach_fallback_model"):
                self.config.coach_fallback_model = snapshot["coach_fallback_model"]
            if hasattr(self.config, "review_provider"):
                self.config.review_provider = snapshot["review_provider"]
            if hasattr(self.config, "review_model"):
                self.config.review_model = snapshot["review_model"]
            if hasattr(self.config, "test_writer_provider"):
                self.config.test_writer_provider = snapshot["test_writer_provider"]
            if hasattr(self.config, "test_writer_model"):
                self.config.test_writer_model = snapshot["test_writer_model"]
            self.coach_provider = snapshot["coach_provider_obj"]
            self.player_provider = snapshot["player_provider_obj"]
            self.coach_model = snapshot["coach_display"]
            self.player_model = snapshot["player_display"]
            raise

    def _setup_interrupt_handler(self):
        def handler(signum, frame):
            self._interrupted = True
            print("\n\n--- Прервано ---")

        signal.signal(signal.SIGINT, handler)

    def _snapshot_pids(self) -> set[int]:
        """Snapshot current PIDs with cwd inside the working directory."""
        working_dir = os.path.abspath(self.config.working_dir)
        pids: set[int] = set()
        try:
            # Use /proc or lsof to find processes whose cwd is in working_dir.
            # Fall back to pgrep only for processes that have working_dir as
            # a command argument — but filter to direct child processes to
            # avoid matching unrelated processes that merely reference the path.
            result = subprocess.run(
                ["lsof", "+c", "0", "-Fn", working_dir],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    # lsof -Fn outputs "p<pid>" lines
                    if line.startswith("p"):
                        pid_str = line[1:].strip()
                        if pid_str.isdigit():
                            pids.add(int(pid_str))
                return pids
        except (subprocess.TimeoutExpired, ValueError, OSError, FileNotFoundError):
            pass

        # Fallback: pgrep with stricter matching (child processes only)
        try:
            result = subprocess.run(
                ["pgrep", "-P", str(os.getpid()), "-f", working_dir],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return {
                    int(pid)
                    for pid in result.stdout.strip().split("\n")
                    if pid.strip().isdigit()
                }
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        return pids

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

    async def _build_continuation_retry_prompt(
        self,
        role: str,
        base_prompt: str,
        last_result: TurnResult,
        provider,
    ) -> str:
        """Build a continuation prompt, compacting aggressively when context is near the limit."""
        from src.context_manager import (
            _build_compact_summary,
            _build_continuation_prompt,
            _compact_codex_context,
        )

        prompt_tokens = int(getattr(provider, "_last_input_tokens", 0) or 0)
        compact_limit = int(self.config.context_limit * self.config.compact_threshold)

        if prompt_tokens > compact_limit:
            compact_summary = await _compact_codex_context(
                provider, last_result.messages, self.config
            )
            if compact_summary:
                streaming_ui.print_compact_triggered(
                    prompt_tokens, self.config.context_limit
                )
                return (
                    f"Context compacted. Summary of previous work:\n{compact_summary}\n\n"
                    f"Original task:\n{base_prompt}"
                )

        summary = (
            _build_compact_summary(last_result.messages)
            or last_result.text
            or base_prompt
        )
        continuation = _build_continuation_prompt(
            summary,
            role=role,
            require_phase_complete=self._needs_phase_complete(base_prompt),
        )
        return f"{continuation}\n\nOriginal task:\n{base_prompt}"

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
        result = await self._run_turn(
            role=role,
            prompt=prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout_s=timeout_s,
            model_override=model_override,
            provider_override=provider_override,
        )

        if role != "player":
            return result

        max_attempts = max(
            0, int(getattr(self.config, "max_continuation_attempts", 0) or 0)
        )
        current_prompt = prompt

        for attempt in range(1, max_attempts + 1):
            if self._player_output_complete(result.text, current_prompt):
                return result

            provider = provider_override
            if provider is None:
                try:
                    provider = self._provider_for_role(role)
                except Exception:
                    provider = None

            streaming_ui.print_continuation_started(role, attempt, max_attempts)
            current_prompt = await self._build_continuation_retry_prompt(
                role=role,
                base_prompt=current_prompt,
                last_result=result,
                provider=provider,
            )
            result = await self._run_turn(
                role=role,
                prompt=current_prompt,
                system_prompt=system_prompt,
                max_turns=max_turns,
                timeout_s=timeout_s,
                model_override=model_override,
                provider_override=provider_override,
            )

        return result

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

    async def _run_phase_zero(self, raw_plan: str) -> tuple[list, list]:
        """Run the pre-planner once and return enriched items/phases.

        On any failure, returns ``([], [])`` so callers can fall back to the
        original parsed checklist.
        """
        from src.plan_tracker import (
            auto_group_phases,
            parse_enriched_plan,
            write_enriched_plan,
        )
        from src.prompts import PREPLANNER_SYSTEM_PROMPT, build_preplan_prompt

        personas_dir = Path(self.config.working_dir) / "src" / "personas" / "prompts"
        if not personas_dir.is_dir():
            personas_dir = Path(__file__).resolve().parent / "personas" / "prompts"

        registry = PersonaRegistry(personas_dir)
        registry.load_all()
        self._persona_registry = registry

        available_roles = registry.available_roles()
        if not any(role["name"] == "general" for role in available_roles):
            available_roles = available_roles + [
                {
                    "name": "general",
                    "description": "General implementation work without a specialist overlay",
                }
            ]

        preplanner_provider = self._provider_for_role("preplanner")
        preplanner_label = self._format_provider_display(
            self.config.preplan_provider,
            preplanner_provider,
            self.config.preplan_model,
        )
        streaming_ui.print_preplanner_header(preplanner_label)

        def _finalize_enriched_plan(items: list, phases: list, raw_text: str) -> tuple[list, list]:
            if not phases:
                phases = auto_group_phases(items)

            enriched_path = Path(self.config.working_dir) / ".g3" / "enriched-plan.md"
            write_enriched_plan(enriched_path, raw_text)

            roles_assigned = sum(len(item.roles) for item in items)
            streaming_ui.print_preplan_result(
                len(phases),
                roles_assigned,
                str(enriched_path),
            )
            return items, phases

        # Fast path: if the input is already an enriched plan, trust it and move on.
        existing_items, existing_phases = parse_enriched_plan(raw_plan)
        if existing_items and all(item.roles for item in existing_items):
            return _finalize_enriched_plan(existing_items, existing_phases, raw_plan)

        original_items = parse_requirements(raw_plan)
        if not original_items:
            return [], []

        normalized_plan = "\n".join(
            f"- [{'x' if item.done else ' '}] {item.text}" for item in original_items
        )
        preplan_prompt = build_preplan_prompt(normalized_plan, available_roles)

        try:
            result = await self._run_turn(
                role="preplanner",
                prompt=preplan_prompt,
                system_prompt=PREPLANNER_SYSTEM_PROMPT,
                max_turns=1,
                timeout_s=self.config.preplan_timeout_s,
                model_override=self.config.preplan_model,
                disable_tools=True,
            )
        except Exception as exc:
            print(
                f"  [Preplanner] Warning: failed ({exc}), using original plan",
                file=sys.stderr,
            )
            return [], []

        items, phases = parse_enriched_plan(result.text)
        if len(items) != len(original_items):
            print(
                "  [Preplanner] Warning: step count mismatch "
                f"({len(items)} vs {len(original_items)}), using original plan",
                file=sys.stderr,
            )
            return [], []

        preserved_items = [
            replace(item, done=original.done)
            for item, original in zip(items, original_items)
        ]
        index_by_old_id = {id(item): idx for idx, item in enumerate(items)}
        for phase in phases:
            phase.steps = [
                preserved_items[index_by_old_id[id(step)]]
                for step in phase.steps
                if id(step) in index_by_old_id
            ]
        return _finalize_enriched_plan(preserved_items, phases, result.text)

    async def run(self) -> SessionResult:
        """Run the step-by-step coach-player loop."""
        self._setup_interrupt_handler()

        start_time = time.time()
        turn_details = []
        error = None

        plan_items = parse_requirements(self.requirements)
        if self.config.preplan_mode:
            enriched_items, _phases = await self._run_phase_zero(self.requirements)
            if enriched_items:
                plan_items = enriched_items
        else:
            self._persona_registry = None
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
                tests_written = False  # TDD: tests written once per step
                restart_requested = False

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
                            model_override=self.config.test_writer_model,
                        )
                        tests_written = True
                    except TimeoutError:
                        print(
                            f"\n  {BOLD}{YELLOW}⚠ Test writer timed out, continuing...{RESET}"
                        )

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
                                    streaming_ui.print_compact_triggered(
                                        self._last_turn_result.tokens_used,
                                        self.config.context_limit,
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
                            max_turns=30,
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
                            review_provider = self._resolve_review_provider()
                            review_provider_name = self._resolve_review_provider_name()
                            review_model = self._resolve_review_model()
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
                                    run_fn = getattr(
                                        self, "_run_with_continuation", self._run_turn
                                    )
                                    await run_fn(
                                        role="player",
                                        prompt=fix_prompt,
                                        system_prompt=PLAYER_SYSTEM_PROMPT,
                                        max_turns=self.config.max_turns,
                                        timeout_s=self.config.player_timeout_s,
                                        model_override=self.config.player_model,
                                    )
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
            timestamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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
        start = time.time()
        messages = []
        tools_used = 0
        tokens_used = 0

        provider = provider_override or self._provider_for_role(role)
        model = model_override or ""

        def _update_native_usage() -> None:
            nonlocal tokens_used
            if tokens_used > 0:
                return
            input_tokens = int(getattr(provider, "_last_input_tokens", 0) or 0)
            output_tokens = int(getattr(provider, "_last_output_tokens", 0) or 0)
            native_total = input_tokens + output_tokens
            if native_total > 0:
                tokens_used = native_total

        async def _collect() -> None:
            nonlocal tools_used, tokens_used
            if self._interrupted:
                return

            # For code review with CodexProvider, use native run_review() method
            if role == "reviewer" and isinstance(provider, CodexProvider):
                async for msg in provider.run_review(
                    working_dir=self.config.working_dir,
                    review_prompt=prompt,
                    model=model,
                    uncommitted=True,
                ):
                    if self._interrupted:
                        return

                    # Adapt message for streaming
                    if isinstance(msg, AdaptedMessage):
                        messages.append(msg)
                        tools_used += streaming_ui.stream_messages(
                            msg, verbose=self.config.verbose, role=role
                        )
                    else:
                        messages.append(msg)
                        tools_used += streaming_ui.stream_messages(
                            msg, verbose=self.config.verbose, role=role
                        )
                _update_native_usage()
                return

            run_kwargs = {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "working_dir": self.config.working_dir,
                "max_turns": max_turns,
                "model": model,
            }

            # Pass context limits to providers that support them (CCG)
            params = inspect.signature(provider.run).parameters
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            if "context_limit" in params or accepts_kwargs:
                run_kwargs["context_limit"] = self.config.context_limit
            if "compact_threshold" in params or accepts_kwargs:
                run_kwargs["compact_threshold"] = self.config.compact_threshold
            if "disable_tools" in params or accepts_kwargs:
                run_kwargs["disable_tools"] = disable_tools

            async for msg in provider.run(**run_kwargs):
                if self._interrupted:
                    return

                # Extract token usage from ResultMessage before adapting
                if not isinstance(msg, dict) and type(msg).__name__ == "ResultMessage":
                    usage = getattr(msg, "usage", None) or {}
                    if isinstance(usage, dict):
                        tokens_used = usage.get("input_tokens", 0) + usage.get(
                            "output_tokens", 0
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
            _update_native_usage()

        try:
            await asyncio.wait_for(_collect(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{role} exceeded timeout of {timeout_s}s") from exc

        duration = time.time() - start
        resolved_model = model or self._provider_model(provider)
        from src.config import get_context_window

        context_window = get_context_window(resolved_model)
        streaming_ui.print_turn_timing(
            role, duration, tools_used, tokens_used, context_window
        )
        runtime = getattr(self, "_runtime", None)
        if runtime is not None:
            runtime.update_context(tokens_used, context_window)

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
            CoachPlayerSession.BATCH_REVIEW_MAX_TURNS,
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
            print(f"\n  {BOLD}{YELLOW}⚠ Reviewer silent - rejecting phase for retry{RESET}")
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
            # Exit code 5 = no tests collected. In TDD mode this must fail closed,
            # otherwise the run silently skips the entire testing gate.
            if result.returncode == 5:
                return False, "No tests collected — TDD requires at least one runnable test"
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
            return ["python3", "-m", "pytest", "-q"]
        if pyproject_path.exists():
            try:
                pyproject_text = pyproject_path.read_text()
            except OSError:
                pyproject_text = ""
            if (
                "[tool.pytest" in pyproject_text
                or "[tool.pytest.ini_options]" in pyproject_text
            ):
                return ["python3", "-m", "pytest", "-q"]
        if (working_dir / "package.json").exists():
            return ["npm", "test"]
        if (working_dir / "Cargo.toml").exists():
            return ["cargo", "test"]
        if (working_dir / "Makefile").exists():
            return ["make", "test"]
        return ["python3", "-m", "pytest", "-q"]
