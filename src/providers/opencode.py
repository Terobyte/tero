"""OpenCode provider — MIMO, Kimi, and other free models via opencode CLI."""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from .message_adapter import (
    AdaptedMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)
from src.errors import ProviderError
from .subprocess_runner import SubprocessExit, run_subprocess_jsonl

from src.constants import (
    MAX_TOOL_OUTPUT_CHARS,
    DEFAULT_PROVIDER_TIMEOUT_S,
)

MAX_TOOL_OUTPUT = MAX_TOOL_OUTPUT_CHARS


@dataclass
class OpenCodeConfig:
    """Configuration for OpenCode CLI provider."""

    command: str = "opencode"
    default_model: str = "opencode/mimo-v2-pro-free"
    default_timeout: int = DEFAULT_PROVIDER_TIMEOUT_S
    display_name: str = "OpenCode"


class OpenCodeProvider:
    """OpenCode provider via CLI (opencode run --format json).

    Launches opencode as a subprocess and parses JSONL output.
    Supports MIMO, Kimi, MiniMax, Nemotron and other free models
    available through the opencode platform.
    """

    def __init__(self, config: OpenCodeConfig | None = None):
        self.config = config or OpenCodeConfig()
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
        context_limit: int = 0,
    ):
        """Run OpenCode agent via CLI and yield adapted messages.

        Args:
            prompt: User prompt
            system_prompt: System instructions (wrapped in <SYSTEM INSTRUCTIONS> tags)
            working_dir: Working directory for subprocess
            max_turns: Maximum turns (metadata)
            model: Optional model override

        Yields:
            AdaptedMessage objects for each relevant event
        """
        self._reset_usage()
        cmd = self._build_command(model, working_dir)
        full_prompt = self._build_system_prompt(system_prompt, prompt)

        _gen = run_subprocess_jsonl(cmd, working_dir, stdin_data=full_prompt.encode("utf-8"), stall_timeout=120.0)
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    stderr_message = await self._stderr_message_from_bytes(event.stderr)
                    if stderr_message is not None:
                        yield stderr_message
                    self._raise_for_returncode(event.returncode, event.stderr)
                else:
                    adapted = self._adapt_opencode_event(event)
                    if adapted is None:
                        continue
                    messages = adapted if isinstance(adapted, list) else [adapted]
                    for message in messages:
                        yield message
        finally:
            await _gen.aclose()

    def _build_command(self, model: str = "", working_dir: str = "") -> list[str]:
        """Build opencode run CLI command."""
        resolved_model = model or self.config.default_model

        cmd = [
            self.config.command,
            "run",
            "--format",
            "json",
            "--dangerously-skip-permissions",
        ]

        if working_dir:
            cmd.extend(["--dir", working_dir])

        if resolved_model:
            cmd.extend(["-m", resolved_model])

        cmd.append("-")  # read from stdin

        return cmd

    def _build_system_prompt(self, system_prompt: str, user_prompt: str) -> str:
        """Format prompt with system instructions wrapping.

        OpenCode has no CODEX_INSTRUCTIONS env var — we prepend the system
        prompt inside <SYSTEM INSTRUCTIONS> tags.
        """
        if system_prompt:
            return (
                f"<SYSTEM INSTRUCTIONS>\n{system_prompt}\n</SYSTEM INSTRUCTIONS>"
                f"\n\n{user_prompt}"
            )
        return user_prompt

    async def _stderr_message_from_bytes(
        self, stderr_data: bytes
    ) -> AdaptedMessage | None:
        """Convert stderr bytes into a non-fatal assistant message."""
        if isinstance(stderr_data, bytes):
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
        else:
            stderr_text = (stderr_data or "").strip()
        if not stderr_text:
            return None

        return AdaptedMessage(
            role="assistant",
            content=[TextBlock(text=f"[opencode stderr] {stderr_text}")],
            type="text",
        )

    def _raise_for_returncode(
        self, returncode: int | None, stderr_data: bytes | str
    ) -> None:
        """Raise when the OpenCode subprocess exits unsuccessfully."""
        if returncode in (None, 0):
            return

        if isinstance(stderr_data, bytes):
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
        else:
            stderr_text = (stderr_data or "").strip()

        detail = stderr_text or "subprocess exited without stderr output"
        raise ProviderError(f"opencode exited with code {returncode}: {detail}")

    def _build_command_messages(self, part: dict) -> list[AdaptedMessage]:
        """Build separate tool-use and tool-result messages for one command."""
        state = part.get("state", {})
        call_id = part.get("callID", "")
        inp = state.get("input", {})
        cmd = inp.get("command") or inp.get("description") or str(inp)
        output = state.get("output", "")
        exit_code = state.get("metadata", {}).get("exit")
        result_text = output[:MAX_TOOL_OUTPUT]

        return [
            AdaptedMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id=call_id,
                        name=part.get("tool", "bash"),
                        input={"command": cmd},
                    )
                ],
                stop_reason="tool_use",
                type="tool_use",
            ),
            AdaptedMessage(
                role="tool",
                content=[
                    ToolResultBlock(
                        tool_use_id=call_id,
                        content=result_text,
                        is_error=(exit_code is not None and exit_code != 0),
                    )
                ],
                type="tool_result",
            ),
        ]

    def _adapt_opencode_event(
        self, event: dict
    ) -> AdaptedMessage | list[AdaptedMessage] | None:
        """Convert OpenCode JSONL event to AdaptedMessage.

        Event types:
          - step_start → ignored (metadata)
          - text       → assistant TextBlock
          - tool_use   → separate tool_use and tool_result messages
          - step_finish → store tokens (no message yielded)
          - error      → assistant TextBlock with error
        """
        t = event.get("type")
        part = event.get("part", {})

        if t == "text":
            text = part.get("text", "")
            if text:
                return AdaptedMessage(role="assistant", content=[TextBlock(text=text)])

        elif t == "tool_use":
            return self._build_command_messages(part)

        elif t == "step_finish":
            tokens = part.get("tokens", {})
            self._last_input_tokens = tokens.get("input", 0)
            self._last_output_tokens = tokens.get("output", 0)

        elif t == "error":
            err = event.get("error", {})
            msg = err.get("data", {}).get("message") or err.get("name", "unknown error")
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"[OpenCode error: {msg}]")],
            )

        return None

    def check_ready(self) -> tuple[bool, str]:
        """Check if opencode CLI is installed and available."""
        if not shutil.which(self.config.command):
            return False, f"'{self.config.command}' not found in PATH."
        return True, ""

    @property
    def display_name(self) -> str:
        """Human-readable name for UI."""
        model = self.config.default_model or "default"
        return f"{self.config.display_name} ({model})"
