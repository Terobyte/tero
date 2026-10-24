"""OpenCode provider — free and other models via opencode CLI."""

import shutil
from dataclasses import dataclass

from .message_adapter import (
    AdaptedMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    wrap_system_prompt,
)
from src.errors import ProviderStalled
from .effort import normalize_effort
from .subprocess_runner import SubprocessExit, raise_for_exit, run_subprocess_jsonl

from src.constants import (
    MAX_TOOL_OUTPUT_CHARS,
)


@dataclass
class OpenCodeConfig:
    """Configuration for OpenCode CLI provider."""

    command: str = "opencode"
    # free models rotate, so none is pinned: "" lets opencode use its own default
    default_model: str = ""
    # Seconds without stdout before the run is killed; 0 = off, the role timeout governs.
    stall_timeout_s: float = 0
    display_name: str = "OpenCode"


class OpenCodeProvider:
    """OpenCode provider via CLI (opencode run --format json).

    Launches opencode as a subprocess and parses JSONL output.
    The user picks the model; the menu lists today's free ones
    from ``opencode models``.
    """

    streams = True

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
        output_schema: dict | None = None,
        read_only: bool = False,
        effort: str = "",
    ):
        """Run OpenCode agent via CLI and yield adapted messages."""
        self._reset_usage()
        cmd = self._build_command(model, working_dir, effort=effort, read_only=read_only)
        full_prompt = self._build_system_prompt(system_prompt, prompt)

        _gen = run_subprocess_jsonl(
            cmd,
            working_dir,
            stdin_data=full_prompt.encode("utf-8"),
            stall_timeout=self.config.stall_timeout_s,
        )
        errors: list[str] = []
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    if event.stalled:
                        raise ProviderStalled(
                            f"нет вывода {self.config.stall_timeout_s:g} с"
                        )
                    self._raise_for_returncode(event.returncode, event.stderr, errors)
                else:
                    if isinstance(event, dict) and event.get("type") == "error":
                        errors.append(self._error_text(event))
                    adapted = self._adapt_opencode_event(event)
                    if adapted is None:
                        continue
                    messages = adapted if isinstance(adapted, list) else [adapted]
                    for message in messages:
                        yield message
        finally:
            await _gen.aclose()

    def _build_command(
        self, model: str = "", working_dir: str = "", effort: str = "", read_only: bool = False
    ) -> list[str]:
        """Build opencode run CLI command."""
        resolved_model = model or self.config.default_model
        resolved_effort = normalize_effort(effort)

        cmd = [
            self.config.command,
            "run",
            "--format",
            "json",
        ]
        # The built-in plan agent cannot edit files or run shell commands.
        cmd.extend(["--agent", "plan"] if read_only else ["--dangerously-skip-permissions"])

        if working_dir:
            cmd.extend(["--dir", working_dir])

        if resolved_model:
            cmd.extend(["-m", resolved_model])

        if resolved_effort:
            cmd.extend(["--variant", resolved_effort])

        cmd.append("-")  # read from stdin

        return cmd

    def _build_system_prompt(self, system_prompt: str, user_prompt: str) -> str:
        return wrap_system_prompt(system_prompt, user_prompt)

    def _raise_for_returncode(
        self, returncode: int | None, stderr_data: bytes | str, errors: list[str] | None = None
    ) -> None:
        """Raise when the OpenCode subprocess exits unsuccessfully."""
        raise_for_exit("opencode", returncode, stderr_data, errors)

    def _tool_messages(self, part: dict) -> list[AdaptedMessage]:
        """Build separate tool-use and tool-result messages for one command.

        Pass the real `state.input` dict through (e.g. `{"filePath": ...}` for
        a read, `{"command": ...}` for bash) so `_activity` can show the real
        argument instead of a stringified dict.
        """
        state = part.get("state") or {}
        call_id = part.get("callID", "")
        tool = part.get("tool", "bash")
        inp = dict(state.get("input") or {})
        if tool == "bash" and "command" not in inp:
            inp["command"] = inp.get("description") or str(inp)
        output = state.get("output") or ""
        exit_code = (state.get("metadata") or {}).get("exit")
        result_text = output[:MAX_TOOL_OUTPUT_CHARS]

        return [
            AdaptedMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id=call_id,
                        name=tool,
                        input=inp,
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
        if not isinstance(event, dict):
            return None
        t = event.get("type")
        part = event.get("part") or {}
        if not isinstance(part, dict):
            part = {}

        if t == "text":
            text = part.get("text", "")
            if text:
                return AdaptedMessage(role="assistant", content=[TextBlock(text=text)])

        elif t == "tool_use":
            return self._tool_messages(part)

        elif t == "step_finish":
            tokens = part.get("tokens") or {}
            self._last_input_tokens = tokens.get("input", 0)
            self._last_output_tokens = tokens.get("output", 0)

        elif t == "error":
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"[OpenCode error: {self._error_text(event)}]")],
            )

        return None

    @staticmethod
    def _error_text(event: dict) -> str:
        err = event.get("error")
        if not isinstance(err, dict):
            return str(err or "unknown error")
        data = err.get("data")
        message = data.get("message") if isinstance(data, dict) else None
        return message or err.get("name") or "unknown error"

    def check_ready(self) -> tuple[bool, str]:
        """Check if opencode CLI is installed and available."""
        if not shutil.which(self.config.command):
            return False, f"'{self.config.command}' not found in PATH."
        return True, ""

    @property
    def display_name(self) -> str:
        model = self.config.default_model or "default"
        return f"{self.config.display_name} ({model})"
