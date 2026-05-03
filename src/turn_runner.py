"""Agent Turn Runner — single agent turn execution with continuation support.

Extracted from CoachPlayerSession so that BatchExecutor can call it directly
without going through the monolithic session object.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time

from src.errors import ProviderError
from src.providers import adapt_claude_event, adapt_sdk_message
from src.providers.message_adapter import AdaptedMessage
from src.providers.codex import CodexProvider
from src.role_router import _provider_model

from src import streaming as streaming_ui


class AgentTurnRunner:
    """Runs a single agent turn against a provider and returns a TurnResult."""

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self._last_effective_context_limit: int = 0

    async def run_turn(
        self,
        role: str,
        prompt: str,
        system_prompt: str,
        max_turns: int,
        timeout_s: int,
        provider,
        router,
        config,
        model_override: str = "",
        provider_override=None,
        disable_tools: bool = False,
        interrupted_fn=None,
    ):
        """Run a single agent turn using the appropriate provider.

        Returns a TurnResult dataclass.
        """
        from src.coach_player import TurnResult

        start = time.time()
        messages = []
        tools_used = 0
        tokens_used = 0

        resolved_provider = provider_override or provider or router.provider_for(role)
        model = model_override or ""

        resolved_model_for_ctx = (
            model or _provider_model(resolved_provider) or "unknown"
        )
        from src.config import get_effective_context_limit

        effective_context_limit = get_effective_context_limit(
            resolved_model_for_ctx, config.context_limit, provider=resolved_provider
        )
        self._last_effective_context_limit = effective_context_limit

        def _update_native_usage() -> None:
            nonlocal tokens_used
            if tokens_used > 0:
                return
            input_tokens = int(getattr(resolved_provider, "_last_input_tokens", 0) or 0)
            output_tokens = int(
                getattr(resolved_provider, "_last_output_tokens", 0) or 0
            )
            native_total = input_tokens + output_tokens
            if native_total > 0:
                tokens_used = native_total

        async def _collect() -> None:
            nonlocal tools_used, tokens_used

            if role == "reviewer" and isinstance(resolved_provider, CodexProvider):
                async for msg in resolved_provider.run_review(
                    working_dir=config.working_dir,
                    review_prompt=prompt,
                    model=model,
                    uncommitted=True,
                ):
                    if isinstance(msg, AdaptedMessage):
                        messages.append(msg)
                        tools_used += streaming_ui.stream_messages(
                            msg, verbose=self.verbose, role=role
                        )
                    else:
                        messages.append(msg)
                        tools_used += streaming_ui.stream_messages(
                            msg, verbose=self.verbose, role=role
                        )
                _update_native_usage()
                return

            run_kwargs = {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "working_dir": config.working_dir,
                "max_turns": max_turns,
                "model": model,
            }

            params = inspect.signature(resolved_provider.run).parameters
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            if "context_limit" in params or accepts_kwargs:
                run_kwargs["context_limit"] = effective_context_limit
            if "compact_threshold" in params or accepts_kwargs:
                run_kwargs["compact_threshold"] = config.compact_threshold
            if "disable_tools" in params or accepts_kwargs:
                run_kwargs["disable_tools"] = disable_tools

            async for msg in resolved_provider.run(**run_kwargs):
                if interrupted_fn and interrupted_fn():
                    break

                if not isinstance(msg, dict) and type(msg).__name__ == "ResultMessage":
                    usage = getattr(msg, "usage", None) or {}
                    if isinstance(usage, dict):
                        tokens_used = usage.get("input_tokens", 0) + usage.get(
                            "output_tokens", 0
                        )

                adapted = None
                if isinstance(msg, dict):
                    adapted = adapt_claude_event(msg)
                else:
                    adapted = adapt_sdk_message(msg)

                if adapted:
                    messages.append(adapted)
                    tools_used += streaming_ui.stream_messages(
                        adapted, verbose=self.verbose, role=role
                    )
                else:
                    messages.append(msg)
                    tools_used += streaming_ui.stream_messages(
                        msg, verbose=self.verbose, role=role
                    )
            _update_native_usage()

        try:
            await asyncio.wait_for(_collect(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{role} exceeded timeout of {timeout_s}s") from exc

        duration = time.time() - start
        resolved_model = model or _provider_model(resolved_provider)
        from src.config import get_context_window

        context_window = get_context_window(resolved_model)
        streaming_ui.print_turn_timing(
            role, duration, tools_used, tokens_used, context_window
        )

        text_parts = [
            msg.get_text_content()
            for msg in messages
            if isinstance(msg, AdaptedMessage) and msg.role == "assistant"
        ]
        result_text = "\n".join(p for p in text_parts if p)

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

    async def run_with_continuation(
        self,
        role: str,
        prompt: str,
        system_prompt: str,
        max_turns: int,
        timeout_s: int,
        provider,
        router,
        config,
        model_override: str = "",
        provider_override=None,
        interrupted_fn=None,
    ):
        """Retry incomplete player outputs with a continuation prompt."""
        result = await self.run_turn(
            role=role,
            prompt=prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout_s=timeout_s,
            provider=provider,
            router=router,
            config=config,
            model_override=model_override,
            provider_override=provider_override,
            interrupted_fn=interrupted_fn,
        )

        if role != "player":
            return result

        max_attempts = max(0, int(getattr(config, "max_continuation_attempts", 0) or 0))
        current_prompt = prompt

        resolved_provider = provider_override or provider
        if resolved_provider is None:
            try:
                resolved_provider = router.provider_for(role)
            except (ProviderError, ValueError):
                pass

        for attempt in range(1, max_attempts + 1):
            if interrupted_fn and interrupted_fn():
                return result
            if self._player_output_complete(result.text, current_prompt):
                return result

            streaming_ui.print_continuation_started(role, attempt, max_attempts)
            current_prompt = await self._build_continuation_retry_prompt(
                role=role,
                base_prompt=current_prompt,
                last_result=result,
                provider=resolved_provider,
                config=config,
            )
            result = await self.run_turn(
                role=role,
                prompt=current_prompt,
                system_prompt=system_prompt,
                max_turns=max_turns,
                timeout_s=timeout_s,
                provider=resolved_provider,
                router=router,
                config=config,
                model_override=model_override,
                provider_override=resolved_provider,
                interrupted_fn=interrupted_fn,
            )

        return result

    @staticmethod
    def _player_output_complete(text: str, prompt: str) -> bool:
        """Return True when player output contains the expected completion markers."""
        from src.coach_player import CoachPlayerSession

        return CoachPlayerSession._player_output_complete(text, prompt)

    async def _build_continuation_retry_prompt(
        self,
        role: str,
        base_prompt: str,
        last_result,
        provider,
        config,
    ) -> str:
        """Build a continuation prompt, compacting aggressively when context is near the limit."""
        from src.context_manager import (
            _build_compact_summary,
            _build_continuation_prompt,
            _compact_codex_context,
        )

        prompt_tokens = int(getattr(provider, "_last_input_tokens", 0) or 0)
        effective_limit = getattr(
            self, "_last_effective_context_limit", config.context_limit
        )
        compact_limit = int(effective_limit * config.compact_threshold)

        if prompt_tokens > compact_limit:
            compact_summary = await _compact_codex_context(
                provider, last_result.messages, config
            )
            if compact_summary:
                streaming_ui.print_compact_triggered(prompt_tokens, effective_limit)
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
            require_phase_complete="PHASE_COMPLETE" in (base_prompt or ""),
        )
        return f"{continuation}\n\nOriginal task:\n{base_prompt}"
