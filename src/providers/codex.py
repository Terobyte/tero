"""Codex provider — Native Codex CLI via subprocess."""

import asyncio
import inspect
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import AsyncIterator

from .message_adapter import (
    AdaptedMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)


# Maximum output length for tool results to prevent memory issues
MAX_TOOL_OUTPUT = 8000


@dataclass
class CodexConfig:
    """Configuration for native Codex CLI provider."""

    command: str = "codex"  # Path to codex binary
    default_model: str = ""  # Empty string = default from ~/.codex/config.toml
    default_timeout: int = 900  # Subprocess timeout in seconds
    sandbox_mode: str = "workspace-write"  # read-only | workspace-write | danger-full-access
    approval_policy: str = "never"  # For exec: never | on-request | untrusted
    ephemeral: bool = True  # --ephemeral (no persistent sessions)
    full_auto: bool = False  # --full-auto (sandbox + on-request)
    bypass_approvals: bool = True  # --dangerously-bypass-approvals-and-sandbox
    config_overrides: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)
    # Feature flags (passed via --enable/--disable or -c features.<name>=true/false)
    enabled_features: list[str] = field(default_factory=list)  # e.g. ["shell_tool", "multi_agent", "fast_mode"]
    disabled_features: list[str] = field(default_factory=list)  # e.g. ["undo", "web_search_request"]
    # MCP servers are managed via `codex mcp` commands and ~/.codex/config.toml
    # No api_url or api_key needed - native CLI uses its own auth


class CodexProvider:
    """Codex provider via native CLI (codex exec --json).

    Launches codex as a subprocess and parses JSONL output.
    This approach:
    - Eliminates HTTP proxy dependency (ai-cli-proxy-api Go server)
    - Preserves full tool execution context (command_execution results)
    - Provides clean JSONL streaming without SSE conversion artifacts
    """

    def __init__(self, config: CodexConfig | None = None):
        self.config = config or CodexConfig()
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    def _reset_usage(self) -> None:
        """Clear usage counters before starting a new Codex subprocess."""
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
        """Run Codex agent via CLI and yield adapted messages.

        Launches `codex exec --json` as subprocess, reads JSONL from stdout,
        and yields AdaptedMessage objects compatible with tero's streaming UI.

        Args:
            prompt: User prompt
            system_prompt: System instructions (passed via CODEX_INSTRUCTIONS env)
            working_dir: Working directory for subprocess
            max_turns: Maximum turns (passed as metadata, not directly supported)
            model: Optional model override

        Yields:
            AdaptedMessage objects for each relevant event
        """
        self._reset_usage()
        cmd = self._build_command(model, working_dir)
        env = self._build_env(system_prompt)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=env,
            )

            # Send prompt via stdin ("-" argument means read from stdin)
            await self._write_stdin(proc.stdin, prompt)

            async for line in proc.stdout:
                decoded = line.decode("utf-8").strip()
                if not decoded:
                    continue
                try:
                    event = json.loads(decoded)
                except json.JSONDecodeError:
                    continue

                adapted = self._adapt_codex_event(event)
                if adapted is not None:
                    yield adapted

            await proc.wait()

            stderr_message = await self._stderr_message(proc.stderr)
            if stderr_message is not None:
                yield stderr_message

        finally:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

    def _build_command(self, model: str = "", working_dir: str = "") -> list[str]:
        """Build codex exec CLI command."""
        resolved_model = model or self.config.default_model

        cmd = [
            self.config.command,
            "exec",
            "--json",  # JSONL output to stdout
        ]

        if resolved_model:
            cmd.extend(["-m", resolved_model])

        if working_dir:
            cmd.extend(["-C", working_dir])

        # Sandbox mode
        if self.config.bypass_approvals:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.config.full_auto:
            cmd.append("--full-auto")
        else:
            cmd.extend(["-s", self.config.sandbox_mode])
            cmd.extend(["-a", self.config.approval_policy])

        if self.config.ephemeral:
            cmd.append("--ephemeral")

        # Feature flags (--enable / --disable)
        for feature in self.config.enabled_features:
            cmd.extend(["--enable", feature])
        for feature in self.config.disabled_features:
            cmd.extend(["--disable", feature])

        # Config overrides (-c key=value)
        for key, value in self.config.config_overrides.items():
            cmd.extend(["-c", f"{key}={value}"])

        # Extra args
        cmd.extend(self.config.extra_args)

        # Prompt from stdin
        cmd.append("-")

        return cmd

    def _build_env(self, system_prompt: str = "") -> dict:
        """Build environment for subprocess.

        Codex CLI uses CODEX_INSTRUCTIONS env var for system prompt.
        """
        env = os.environ.copy()
        if system_prompt:
            env["CODEX_INSTRUCTIONS"] = system_prompt
        return env

    async def _write_stdin(self, stdin, text: str) -> None:
        """Write stdin payload, tolerating sync and async test doubles."""
        if stdin is None:
            return

        write_result = stdin.write(text.encode("utf-8"))
        if inspect.isawaitable(write_result):
            await write_result

        await stdin.drain()

        close_result = stdin.close()
        if inspect.isawaitable(close_result):
            await close_result

    async def _stderr_message(self, stderr) -> AdaptedMessage | None:
        """Convert stderr output into a non-fatal assistant message."""
        if stderr is None:
            return None

        stderr_data = await stderr.read()
        if isinstance(stderr_data, bytes):
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
        elif isinstance(stderr_data, str):
            stderr_text = stderr_data.strip()
        else:
            return None
        if not stderr_text:
            return None

        return AdaptedMessage(
            role="assistant",
            content=[TextBlock(text=f"[codex stderr] {stderr_text}")],
            type="text",
        )

    def _adapt_codex_event(self, event: dict) -> AdaptedMessage | None:
        """Convert Codex JSONL event to AdaptedMessage.

        Handles all Codex event types:
        - [x] thread.started → ignored (metadata only)
        - [x] turn.started → empty assistant marker
        - [x] item.completed (agent_message) → TextBlock
        - [x] item.started (command_execution) → ToolUseBlock (in_progress)
        - [x] item.completed (command_execution) → ToolResultBlock
        - [x] turn.completed → result with usage stats
        - [x] error / turn.failed → error text
        """
        etype = event.get("type", "")

        # Thread / Turn lifecycle
        if etype == "thread.started":
            return None  # metadata only, ignore

        if etype == "turn.started":
            return AdaptedMessage(
                role="assistant",
                content=[],
                type="assistant",
            )

        # Item events
        if etype in ("item.completed", "item.started"):
            item = event.get("item", {})
            return self._adapt_item(item, etype)

        # Turn completion (contains usage)
        if etype == "turn.completed":
            usage = event.get("usage", {})
            self._last_input_tokens = usage.get("input_tokens", 0)
            self._last_output_tokens = usage.get("output_tokens", 0)
            return AdaptedMessage(
                role="assistant",
                content=[],
                stop_reason="end_turn",
                type="result",
            )

        # Errors
        if etype == "error":
            msg = event.get("message", "Unknown error")
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"⚠ Codex error: {msg}")],
                type="text",
            )

        if etype == "turn.failed":
            error = event.get("error", {})
            msg = error.get("message", "Turn failed") if isinstance(error, dict) else str(error)
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"⚠ Codex turn failed: {msg}")],
                type="text",
            )

        return None

    def _adapt_item(self, item: dict, event_type: str) -> AdaptedMessage | None:
        """Convert a single Codex item to AdaptedMessage."""
        item_type = item.get("type", "")

        # Agent message (text response)
        if item_type == "agent_message":
            text = item.get("text", "")
            if not text:
                return None
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=text)],
                type="text",
            )

        # Command execution (tool use)
        if item_type == "command_execution":
            command = item.get("command", "")
            status = item.get("status", "")
            exit_code = item.get("exit_code")
            output = item.get("aggregated_output", "")

            if event_type == "item.started":
                # Tool use started
                return AdaptedMessage(
                    role="assistant",
                    content=[ToolUseBlock(
                        name="shell",
                        input={"command": command},
                        id=item.get("id", ""),
                    )],
                    stop_reason="tool_use",
                    type="tool_use",
                )

            if event_type == "item.completed":
                # Tool result completed
                is_error = exit_code is not None and exit_code != 0
                result_text = output or ""
                if exit_code is not None:
                    result_text = f"[exit code: {exit_code}]\n{result_text}"

                # Truncate long output
                if len(result_text) > MAX_TOOL_OUTPUT:
                    result_text = result_text[:MAX_TOOL_OUTPUT] + "\n... (truncated)"

                return AdaptedMessage(
                    role="tool",
                    content=[ToolResultBlock(
                        tool_use_id=item.get("id", ""),
                        content=result_text,
                        is_error=is_error,
                    )],
                    type="tool_result",
                )

        # File edit operations (if Codex emits these)
        if item_type in ("file_edit", "file_write", "file_read"):
            path = item.get("path", item.get("file", ""))
            content = item.get("content", item.get("text", ""))

            if event_type == "item.started":
                return AdaptedMessage(
                    role="assistant",
                    content=[ToolUseBlock(
                        name=item_type,
                        input={"path": path},
                        id=item.get("id", ""),
                    )],
                    stop_reason="tool_use",
                    type="tool_use",
                )

            if event_type == "item.completed":
                return AdaptedMessage(
                    role="tool",
                    content=[ToolResultBlock(
                        tool_use_id=item.get("id", ""),
                        content=content or f"[{item_type}: {path}]",
                        is_error=False,
                    )],
                    type="tool_result",
                )

        # Error item
        if item_type == "error":
            msg = item.get("message", "Unknown item error")
            return AdaptedMessage(
                role="assistant",
                content=[TextBlock(text=f"⚠ {msg}")],
                type="text",
            )

        return None

    async def run_review(
        self,
        working_dir: str,
        review_prompt: str = "",
        model: str = "",
        uncommitted: bool = True,
    ) -> AsyncIterator:
        """Run codex exec review --json for native code review.

        Uses Codex CLI's native code review functionality instead of
        manual prompt-based review.

        Args:
            working_dir: Working directory
            review_prompt: Optional custom review instructions
            model: Optional model override
            uncommitted: Review uncommitted changes (vs last commit)

        Yields:
            AdaptedMessage objects for each relevant event
        """
        self._reset_usage()
        resolved_model = model or self.config.default_model

        cmd = [
            self.config.command,
            "exec", "review",
            "--json",
        ]

        if resolved_model:
            cmd.extend(["-m", resolved_model])

        if uncommitted:
            cmd.append("--uncommitted")

        if self.config.ephemeral:
            cmd.append("--ephemeral")

        if self.config.bypass_approvals:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.config.full_auto:
            cmd.append("--full-auto")

        cmd.append("--skip-git-repo-check")

        if review_prompt:
            cmd.append("-")  # Read from stdin

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if review_prompt else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )

            if review_prompt:
                await self._write_stdin(proc.stdin, review_prompt)

            async for line in proc.stdout:
                decoded = line.decode("utf-8").strip()
                if not decoded:
                    continue
                try:
                    event = json.loads(decoded)
                except json.JSONDecodeError:
                    continue

                adapted = self._adapt_codex_event(event)
                if adapted is not None:
                    yield adapted

            await proc.wait()

            stderr_message = await self._stderr_message(proc.stderr)
            if stderr_message is not None:
                yield stderr_message

        finally:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

    def check_ready(self) -> tuple[bool, str]:
        """Check if Codex CLI is installed and available."""
        if not shutil.which(self.config.command):
            return False, f"'{self.config.command}' not found in PATH. Install: npm i -g @openai/codex"

        try:
            result = subprocess.run(
                [self.config.command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False, f"codex --version failed: {result.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Codex CLI check timed out"
        except OSError as e:
            return False, f"Codex CLI check failed: {e}"

        return True, ""

    @property
    def display_name(self) -> str:
        """Human-readable name for UI."""
        model = self.config.default_model or "default"
        return f"Codex ({model})"
