"""Muse Code provider — Meta Spark via `muse exec --json`."""

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import AsyncIterator

from src.errors import ProviderError
from .message_adapter import AdaptedMessage, TextBlock, ToolUseBlock, ToolResultBlock
from .subprocess_runner import SubprocessExit, run_subprocess_jsonl


@dataclass
class MuseConfig:
    """Configuration for Muse Code CLI provider."""

    command: str = "muse"
    default_model: str = "muse-spark-1.3"
    display_name: str = "Muse"
    yolo: bool = True
    trust_workspace: bool = True


class MuseProvider:
    """Muse Code provider via `muse exec --json`.

    Headless Muse has no approval UI, so unattended runs require `--yolo`
    (disable-approval + disable-sandbox + trust-workspace).
    """

    def __init__(self, config: MuseConfig | None = None):
        self.config = config or MuseConfig()
        self._last_input_tokens = 0
        self._last_output_tokens = 0
        self._temp_prompt_path: str | None = None

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
        """Run Muse Code and yield adapted messages."""
        self._reset_usage()
        full_prompt = self._combine_prompt(system_prompt, prompt)
        prompt_path = self._write_prompt_file(full_prompt)
        env = os.environ.copy()
        cmd = self._build_command(
            model=model,
            working_dir=working_dir,
            max_turns=max_turns,
            prompt_path=prompt_path,
        )

        _gen = run_subprocess_jsonl(cmd, working_dir, env=env)
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    self._raise_for_returncode(event.returncode, event.stderr)
                else:
                    adapted = self._adapt_muse_event(event)
                    if adapted is not None:
                        yield adapted
        finally:
            await _gen.aclose()
            self._cleanup_prompt_file()

    def _build_command(
        self,
        model: str = "",
        working_dir: str = "",
        max_turns: int = 30,
        prompt_path: str = "",
    ) -> list[str]:
        resolved_model = model or self.config.default_model
        cmd = [self.config.command, "exec", "--json"]
        if self.config.yolo:
            cmd.append("--yolo")
        elif self.config.trust_workspace:
            cmd.append("--trust-workspace")
        if working_dir:
            cmd.extend(["--workspace", working_dir])
        if resolved_model:
            cmd.extend(["--model", resolved_model])
        if max_turns > 0:
            cmd.extend(["--max-model-steps", str(max_turns)])
        if prompt_path:
            cmd.extend(["--prompt-file", prompt_path])
        return cmd

    def _write_prompt_file(self, prompt: str) -> str:
        self._cleanup_prompt_file()
        fd, path = tempfile.mkstemp(suffix=".md", prefix="muse_prompt_")
        try:
            os.write(fd, prompt.encode("utf-8"))
        finally:
            os.close(fd)
        self._temp_prompt_path = path
        return path

    def _cleanup_prompt_file(self) -> None:
        if not self._temp_prompt_path:
            return
        try:
            os.unlink(self._temp_prompt_path)
        except OSError:
            pass
        self._temp_prompt_path = None

    @staticmethod
    def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
        if system_prompt:
            return (
                f"<SYSTEM INSTRUCTIONS>\n{system_prompt}\n</SYSTEM INSTRUCTIONS>"
                f"\n\n{user_prompt}"
            )
        return user_prompt

    def _unwrap_event(self, event: dict) -> dict:
        payload = event.get("payload")
        if isinstance(payload, dict):
            inner = payload.get("event")
            if isinstance(inner, dict):
                return inner
        return event

    def _adapt_muse_event(self, event: dict) -> AdaptedMessage | None:
        """Adapt a Muse JSONL event. Unwraps `{payload: {event}}` if present."""
        if not isinstance(event, dict):
            return None
        raw = self._unwrap_event(event)
        kind = str(raw.get("kind") or raw.get("type") or "")

        if kind in {"assistant", "message", "text", "agent_message"}:
            text = raw.get("text") or raw.get("content") or raw.get("message") or ""
            if isinstance(text, list):
                text = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in text
                )
            if text:
                return AdaptedMessage(
                    role="assistant",
                    content=[TextBlock(text=str(text))],
                    type="text",
                )
            return None

        if kind in {"tool", "tool_use", "tool_call", "tool_started"}:
            name = raw.get("name") or raw.get("tool_name") or raw.get("tool") or ""
            tool_id = str(raw.get("id") or raw.get("tool_id") or raw.get("call_id") or "")
            args = raw.get("input") or raw.get("parameters") or raw.get("args") or {}
            if not isinstance(args, dict):
                args = {"value": args}
            return AdaptedMessage(
                role="assistant",
                content=[ToolUseBlock(id=tool_id, name=str(name), input=args)],
                stop_reason="tool_use",
                type="tool_use",
            )

        if kind in {"tool_result", "tool_completed"}:
            tool_id = str(
                raw.get("tool_id") or raw.get("call_id") or raw.get("id") or ""
            )
            output = raw.get("output") or raw.get("content") or raw.get("result") or ""
            status = raw.get("status")
            is_error = status is not None and status not in ("success", "ok", "completed")
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

        if kind in {"result", "session_end", "stop"}:
            usage = raw.get("usage") or raw.get("stats") or {}
            if isinstance(usage, dict):
                self._last_input_tokens = int(
                    usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                )
                self._last_output_tokens = int(
                    usage.get("output_tokens") or usage.get("completion_tokens") or 0
                )
            return AdaptedMessage(
                role="assistant",
                content=[],
                stop_reason="end_turn",
                type="result",
            )

        if kind == "error":
            msg = raw.get("message") or raw.get("error") or "Unknown Muse error"
            raise ProviderError(f"muse CLI error: {msg}")

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
        raise ProviderError(f"muse exited with code {returncode}: {detail}")

    def check_ready(self) -> tuple[bool, str]:
        if shutil.which(self.config.command) is None:
            return (
                False,
                f"'{self.config.command}' not found in PATH. "
                "Install Muse Code from https://dev.meta.ai/docs/muse-code",
            )
        return True, ""

    @property
    def display_name(self) -> str:
        return f"{self.config.display_name} ({self.config.default_model})"
