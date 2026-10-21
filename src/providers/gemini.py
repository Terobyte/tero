"""Gemini CLI provider — Google Gemini via `gemini` CLI subprocess (stream-json)."""

import os
import shutil

from dataclasses import dataclass
from typing import AsyncIterator

from src.constants import DEFAULT_PROVIDER_TIMEOUT_S
from src.errors import ProviderError
from .message_adapter import AdaptedMessage, TextBlock, ToolUseBlock, ToolResultBlock, wrap_system_prompt
from .subprocess_runner import SubprocessExit, raise_for_exit, run_subprocess_jsonl


@dataclass
class GeminiConfig:
    """Configuration for Gemini CLI provider."""

    command: str = "gemini"
    default_model: str = "gemini-3.1-pro-preview"
    display_name: str = "Gemini"
    default_timeout: int = DEFAULT_PROVIDER_TIMEOUT_S
    yolo: bool = True


class GeminiProvider:
    """Gemini provider via CLI (`gemini -p ... --output-format stream-json`).

    Launches the Gemini CLI as a subprocess in non-interactive (headless) mode,
    parses JSONL stream-json output and yields AdaptedMessage objects.
    """

    def __init__(self, config: GeminiConfig | None = None):
        self.config = config or GeminiConfig()
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    def _reset_usage(self) -> None:
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
        output_schema: dict | None = None,
        read_only: bool = False,
        effort: str = "",
    ) -> AsyncIterator:
        """Run Gemini CLI and yield adapted messages."""
        self._reset_usage()
        env = self._build_env(system_prompt)
        full_prompt = self._combine_prompt(system_prompt, prompt)
        cmd = self._build_command(model, effort=effort, read_only=read_only)

        # The prompt goes through stdin: a judge prompt carries the whole diff
        # and would overflow the per-argument limit (128 KB on Linux) in argv.
        _gen = run_subprocess_jsonl(
            cmd, working_dir, env=env, stdin_data=full_prompt.encode("utf-8")
        )
        # Delta chunks are pieces of one text; yielding them separately makes
        # the caller join them with newlines, which breaks JSON inside strings.
        pending: list[str] = []
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    if pending:
                        yield self._text_message("".join(pending))
                    self._raise_for_returncode(event.returncode, event.stderr)
                    continue
                adapted = self._adapt_gemini_event(event)
                if adapted is None:
                    continue
                if event.get("delta") and adapted.type == "text":
                    pending.append(adapted.get_text_content())
                    continue
                if pending:
                    yield self._text_message("".join(pending))
                    pending = []
                yield adapted
        finally:
            await _gen.aclose()

    @staticmethod
    def _text_message(text: str) -> AdaptedMessage:
        return AdaptedMessage(role="assistant", content=[TextBlock(text=text)], type="text")

    def _build_command(
        self, model: str = "", effort: str = "", read_only: bool = False
    ) -> list[str]:
        # Gemini CLI has no effort/thinking flag; `effort` is accepted and ignored.
        resolved_model = model or self.config.default_model
        cmd = [
            self.config.command,
            # -p text is appended to the prompt read from stdin
            "-p",
            "Answer the input above.",
            "--output-format",
            "stream-json",
            "-m",
            resolved_model,
            "--skip-trust",
        ]
        if read_only:
            cmd.extend(["--approval-mode", "plan"])
        elif self.config.yolo:
            cmd.append("--yolo")
        return cmd

    def _build_env(self, system_prompt: str = "") -> dict:
        env = os.environ.copy()
        if system_prompt:
            env["GEMINI_SYSTEM_PROMPT"] = system_prompt
        return env

    @staticmethod
    def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
        return wrap_system_prompt(system_prompt, user_prompt)

    def _adapt_gemini_event(self, event: dict) -> AdaptedMessage | None:
        """Adapt a single Gemini CLI stream-json event into an AdaptedMessage.

        Real Gemini CLI event format (confirmed via smoke-test, issue #6):

        1. ``{"type":"init","timestamp":...,"session_id":...,"model":"..."}``
           — session metadata; ignored (returns None).

        2. ``{"type":"message","role":"assistant","content":"...","delta":true}``
           — streamed text chunk from the model.  All ``type:"message"``
           events with ``role:"assistant"`` and non-empty ``content``
           are yielded as text.

        3. ``{"type":"result","status":"success","stats":{...}}``
           — final event.  ``stats`` may contain ``input_tokens`` and
           ``output_tokens``; captured into ``_last_input_tokens`` /
           ``_last_output_tokens``.

        4. ``{"type":"tool_use","tool_name":"...","tool_id":"...","parameters":{...}}``
           — model invokes a tool.  ``parameters`` holds the tool arguments.

        5. ``{"type":"tool_result","tool_id":"...","status":"success","output":"..."}``
           — tool execution result.  ``status != "success"`` indicates failure.

        6. ``{"type":"error","message":"..."}``
            — error from the CLI.  Raised as ProviderError so the loop can stop the round.
        """
        if not isinstance(event, dict):
            return None
        t = event.get("type", "")

        if t == "message":
            role = event.get("role", "")
            content = event.get("content", "")

            if role == "assistant" and content:
                return AdaptedMessage(
                    role="assistant",
                    content=[TextBlock(text=content)],
                    type="text",
                )
            return None

        if t == "result":
            stats = event.get("stats", {})
            self._last_input_tokens = stats.get("input_tokens", 0)
            self._last_output_tokens = stats.get("output_tokens", 0)
            return AdaptedMessage(
                role="assistant",
                content=[],
                stop_reason="end_turn",
                type="result",
            )

        if t == "tool_use":
            name = event.get("tool_name", "")
            tool_id = event.get("tool_id", "")
            args = event.get("parameters", {})
            return AdaptedMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id=tool_id,
                        name=name,
                        input=args,
                    )
                ],
                stop_reason="tool_use",
                type="tool_use",
            )

        if t == "tool_result":
            tool_id = event.get("tool_id", "")
            output = event.get("output", "")
            status = event.get("status")
            is_error = status is not None and status != "success"
            return AdaptedMessage(
                role="tool",
                content=[
                    ToolResultBlock(
                        tool_use_id=tool_id,
                        content=str(output),
                        is_error=is_error,
                    )
                ],
                type="tool_result",
            )

        if t == "error":
            msg = event.get("message", event.get("error", "Unknown error"))
            raise ProviderError(f"gemini CLI error: {msg}")

        return None

    def _raise_for_returncode(
        self, returncode: int | None, stderr_data: bytes | str
    ) -> None:
        raise_for_exit("gemini", returncode, stderr_data)

    def check_ready(self) -> tuple[bool, str]:
        if shutil.which(self.config.command) is not None:
            return True, ""
        return (
            False,
            f"'{self.config.command}' not found in PATH. Install: npm i -g @google/gemini-cli",
        )

    @property
    def display_name(self) -> str:
        return f"Gemini ({self.config.default_model})"
