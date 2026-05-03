"""Gemini CLI provider — Google Gemini via `gemini` CLI subprocess (stream-json)."""

import os
import shutil

from dataclasses import dataclass
from typing import AsyncIterator

from src.constants import DEFAULT_PROVIDER_TIMEOUT_S, LARGE_PROMPT_THRESHOLD_BYTES
from src.errors import ProviderError
from .message_adapter import AdaptedMessage, TextBlock, ToolUseBlock, ToolResultBlock
from .subprocess_runner import SubprocessExit, run_subprocess_jsonl


@dataclass
class GeminiConfig:
    """Configuration for Gemini CLI provider."""

    command: str = "gemini"
    default_model: str = "gemini-2.5-pro"
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

        stdin_data = None
        if len(full_prompt) > LARGE_PROMPT_THRESHOLD_BYTES:
            stdin_data = full_prompt.encode("utf-8")

        _gen = run_subprocess_jsonl(cmd, working_dir, env=env, stdin_data=stdin_data)
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

        if len(prompt) > LARGE_PROMPT_THRESHOLD_BYTES:
            p_arg = "see stdin"
        else:
            p_arg = prompt

        cmd = [
            self.config.command,
            "-p",
            p_arg,
            "-o",
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
            name = event.get("name", "")
            tool_id = event.get("id", "")
            args = event.get("input", {})
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
            tool_id = event.get("tool_use_id", event.get("id", ""))
            output = event.get("output", event.get("content", ""))
            is_error = event.get("is_error", False)
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
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"[gemini error: {msg}]")],
                type="text",
            )

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
