"""Gemini CLI provider — Google Gemini via `gemini` CLI subprocess (stream-json)."""

import os
import shutil

from dataclasses import dataclass
from typing import AsyncIterator

from src.constants import DEFAULT_PROVIDER_TIMEOUT_S
from src.errors import ProviderError
from .message_adapter import AdaptedMessage, TextBlock, ToolUseBlock, ToolResultBlock
from .subprocess_runner import SubprocessExit, run_subprocess_jsonl


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
    ) -> AsyncIterator:
        """Run Gemini CLI and yield adapted messages."""
        self._reset_usage()
        env = self._build_env(system_prompt)
        full_prompt = self._combine_prompt(system_prompt, prompt)
        cmd = self._build_command(model, full_prompt)

        _gen = run_subprocess_jsonl(cmd, working_dir, env=env)
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    self._raise_for_returncode(event.returncode, event.stderr)
                else:
                    adapted = self._adapt_gemini_event(event)
                    if adapted is not None:
                        yield adapted
        finally:
            await _gen.aclose()

    def _build_command(self, model: str = "", prompt: str = "") -> list[str]:
        resolved_model = model or self.config.default_model
        cmd = [
            self.config.command,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "-m",
            resolved_model,
        ]
        if self.config.yolo:
            cmd.append("--yolo")
        return cmd

    def _build_env(self, system_prompt: str = "") -> dict:
        env = os.environ.copy()
        if system_prompt:
            env["GEMINI_SYSTEM_PROMPT"] = system_prompt
        return env

    @staticmethod
    def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
        if system_prompt:
            return (
                f"<SYSTEM INSTRUCTIONS>\n{system_prompt}\n</SYSTEM INSTRUCTIONS>"
                f"\n\n{user_prompt}"
            )
        return user_prompt

    def _adapt_gemini_event(self, event: dict) -> AdaptedMessage | None:
        """Adapt a single Gemini CLI stream-json event into an AdaptedMessage.

        Real Gemini CLI event format (confirmed via smoke-test, issue #6):

        1. ``{"type":"init","timestamp":...,"session_id":...,"model":"..."}``
           — session metadata; ignored (returns None).

        2. ``{"type":"message","role":"assistant","content":"...","delta":true}``
           — streamed text chunk from the model.  The ``delta`` field is present
           but not used for routing; all ``type:"message"`` events with
           ``role:"assistant"`` and non-empty ``content`` are yielded as text.

        3. ``{"type":"result","status":"success","stats":{...}}``
           — final event.  ``stats`` may contain ``input_tokens`` and
           ``output_tokens``; captured into ``_last_input_tokens`` /
           ``_last_output_tokens``.

        4. ``{"type":"tool_use","tool_name":"...","tool_id":"...","parameters":{...}}``
           — model invokes a tool.  ``parameters`` holds the tool arguments.

        5. ``{"type":"tool_result","tool_id":"...","status":"success","output":"..."}``
           — tool execution result.  ``status != "success"`` indicates failure.

        6. ``{"type":"error","message":"..."}``
           — error from the CLI.  Raised as ProviderError so orchestration can retry/fail.
        """
        t = event.get("type", "")

        if t == "message":
            role = event.get("role", "")
            content = event.get("content", "")
            is_delta = event.get("delta", False)

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
        if returncode in (None, 0):
            return
        if isinstance(stderr_data, bytes):
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
        else:
            stderr_text = (stderr_data or "").strip()
        detail = stderr_text or "subprocess exited without stderr output"
        raise ProviderError(f"gemini exited with code {returncode}: {detail}")

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
