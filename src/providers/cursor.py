"""Cursor Headless provider — Cursor CLI via `agent -p --output-format stream-json`."""

import os
import shutil
from dataclasses import dataclass
from typing import Any, AsyncIterator

from src.errors import ProviderError
from .message_adapter import AdaptedMessage, TextBlock, ToolUseBlock, ToolResultBlock
from .subprocess_runner import SubprocessExit, run_subprocess_jsonl


@dataclass
class CursorConfig:
    """Configuration for Cursor Headless CLI provider."""

    command: str = "agent"
    default_model: str = "composer-2.5"
    display_name: str = "Cursor"
    force: bool = True
    trust: bool = True


class CursorProvider:
    """Cursor provider via `agent -p --force --trust --output-format stream-json`.

    ``-p`` is a boolean print-mode flag; the prompt is positional. Do not pass
    ``--worktree`` — tero already isolates via WorktreeManager.
    """

    def __init__(self, config: CursorConfig | None = None):
        self.config = config or CursorConfig()
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
        """Run Cursor CLI and yield adapted messages."""
        self._reset_usage()
        full_prompt = self._combine_prompt(system_prompt, prompt)
        env = os.environ.copy()
        cmd = self._build_command(
            model=model,
            working_dir=working_dir,
            prompt=full_prompt,
        )

        _gen = run_subprocess_jsonl(cmd, working_dir, env=env)
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    self._raise_for_returncode(event.returncode, event.stderr)
                else:
                    adapted = self._adapt_cursor_event(event)
                    if adapted is not None:
                        yield adapted
        finally:
            await _gen.aclose()

    def _build_command(
        self,
        model: str = "",
        working_dir: str = "",
        prompt: str = "",
    ) -> list[str]:
        resolved_model = model or self.config.default_model
        cmd = [self.config.command, "-p"]
        if self.config.force:
            cmd.append("--force")
        if self.config.trust:
            cmd.append("--trust")
        cmd.extend(["--output-format", "stream-json"])
        if working_dir:
            cmd.extend(["--workspace", working_dir])
        if resolved_model:
            cmd.extend(["--model", resolved_model])
        if prompt:
            cmd.append(prompt)
        return cmd

    @staticmethod
    def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
        if system_prompt:
            return (
                f"<SYSTEM INSTRUCTIONS>\n{system_prompt}\n</SYSTEM INSTRUCTIONS>"
                f"\n\n{user_prompt}"
            )
        return user_prompt

    def _adapt_cursor_event(self, event: dict) -> AdaptedMessage | None:
        """Adapt a Cursor stream-json event."""
        if not isinstance(event, dict):
            return None
        kind = str(event.get("type") or event.get("kind") or "")
        subtype = str(event.get("subtype") or "")

        if kind == "assistant":
            if not self._is_new_assistant_text(event):
                return None
            text = self._extract_assistant_text(event)
            if not text:
                return None
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=text)],
                type="text",
            )

        if kind == "tool_call" and subtype == "started":
            name, args, call_id = self._extract_tool_call(event)
            return AdaptedMessage(
                role="assistant",
                content=[ToolUseBlock(id=call_id, name=name, input=args)],
                stop_reason="tool_use",
                type="tool_use",
            )

        if kind == "tool_call" and subtype == "completed":
            name, args, call_id = self._extract_tool_call(event)
            output, is_error = self._extract_tool_result(event)
            return AdaptedMessage(
                role="tool",
                content=[
                    ToolResultBlock(
                        tool_use_id=call_id,
                        content=output or name,
                        is_error=is_error,
                    )
                ],
                type="tool_result",
            )

        if kind == "result":
            usage = event.get("usage") or event.get("stats") or {}
            if isinstance(usage, dict):
                self._last_input_tokens = int(
                    usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                )
                self._last_output_tokens = int(
                    usage.get("output_tokens") or usage.get("completion_tokens") or 0
                )
            if event.get("is_error"):
                msg = event.get("result") or event.get("message") or "Cursor run failed"
                raise ProviderError(f"cursor CLI error: {msg}")
            return AdaptedMessage(
                role="assistant",
                content=[],
                stop_reason="end_turn",
                type="result",
            )

        if kind == "error":
            msg = event.get("message") or event.get("error") or "Unknown Cursor error"
            raise ProviderError(f"cursor CLI error: {msg}")

        return None

    @staticmethod
    def _is_new_assistant_text(event: dict) -> bool:
        """Skip duplicate assistant flushes from --stream-partial-output."""
        has_ts = "timestamp_ms" in event
        has_call = "model_call_id" in event
        if has_ts and has_call:
            return False
        if not has_ts and not has_call:
            # Final flush without --stream-partial-output is the real message.
            # With --stream-partial-output the same shape is a duplicate.
            return True
        return True

    @staticmethod
    def _extract_assistant_text(event: dict) -> str:
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("text"):
                        parts.append(str(part["text"]))
                    elif isinstance(part, str):
                        parts.append(part)
                return "".join(parts)
            if isinstance(content, str):
                return content
        text = event.get("text") or event.get("content") or ""
        return str(text) if text else ""

    @staticmethod
    def _extract_tool_call(event: dict) -> tuple[str, dict[str, Any], str]:
        call_id = str(event.get("call_id") or event.get("id") or "")
        payload = event.get("tool_call")
        if not isinstance(payload, dict):
            return str(event.get("name") or "tool"), {}, call_id
        if "function" in payload and isinstance(payload["function"], dict):
            fn = payload["function"]
            name = str(fn.get("name") or "function")
            args = fn.get("arguments") or fn.get("args") or {}
            if isinstance(args, str):
                args = {"value": args}
            if not isinstance(args, dict):
                args = {"value": args}
            return name, args, call_id
        for key, value in payload.items():
            if not key.endswith("ToolCall") or not isinstance(value, dict):
                continue
            name = key[: -len("ToolCall")] or "tool"
            args = value.get("args") or {}
            if not isinstance(args, dict):
                args = {"value": args}
            return name, args, call_id
        return "tool", {}, call_id

    @staticmethod
    def _extract_tool_result(event: dict) -> tuple[str, bool]:
        payload = event.get("tool_call")
        if not isinstance(payload, dict):
            return "", False
        for value in payload.values():
            if not isinstance(value, dict):
                continue
            result = value.get("result")
            if not isinstance(result, dict):
                continue
            if "success" in result:
                success = result["success"]
                if isinstance(success, dict):
                    if "content" in success:
                        return str(success["content"]), False
                    return str(success), False
                return str(success), False
            if "error" in result or "failure" in result:
                err = result.get("error") or result.get("failure")
                return str(err), True
        return "", False

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
        raise ProviderError(f"cursor exited with code {returncode}: {detail}")

    def check_ready(self) -> tuple[bool, str]:
        if shutil.which(self.config.command) is None:
            return (
                False,
                f"'{self.config.command}' not found in PATH. "
                "Install Cursor CLI: curl https://cursor.com/install -fsS | bash",
            )
        return True, ""

    @property
    def display_name(self) -> str:
        return f"{self.config.display_name} ({self.config.default_model})"
