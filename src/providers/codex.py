"""Codex provider — Native Codex CLI via subprocess."""

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import AsyncIterator

from src.constants import (
    LARGE_PROMPT_THRESHOLD_BYTES,
)
from src.errors import ProviderStalled
from .message_adapter import (
    AdaptedMessage,
)
from .effort import normalize_effort
from .codex_events import adapt_codex_event, adapt_item, stream_error_text
from .subprocess_runner import SubprocessExit, raise_for_exit, run_subprocess_jsonl


TEXT_ONLY_DISABLED_FEATURES = ("shell_tool", "multi_agent", "apps")


@dataclass
class CodexConfig:
    """Configuration for native Codex CLI provider."""

    command: str = "codex"  # Path to codex binary
    default_model: str = ""  # Empty string = default from ~/.codex/config.toml
    stall_timeout_s: float = 0
    sandbox_mode: str = (
        "workspace-write"  # read-only | workspace-write | danger-full-access
    )
    approval_policy: str = "never"  # For exec: never | on-request | untrusted
    ephemeral: bool = True  # --ephemeral (no persistent sessions)
    full_auto: bool = False  # --full-auto (sandbox + on-request)
    bypass_approvals: bool = True  # --dangerously-bypass-approvals-and-sandbox
    config_overrides: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)
    # Reasoning effort override (passed via -c model_reasoning_effort=<value>).
    # Empty string = inherit from ~/.codex/config.toml. Use "medium" to clamp
    # the model down from the user's global setting (e.g. "xhigh") for cheap
    # roles like judge.
    model_reasoning_effort: str = ""
    # Feature flags (passed via --enable/--disable or -c features.<name>=true/false)
    enabled_features: list[str] = field(
        default_factory=list
    )  # e.g. ["shell_tool", "multi_agent", "fast_mode"]
    disabled_features: list[str] = field(
        default_factory=list
    )  # e.g. ["undo", "web_search_request"]
    # MCP servers are managed via `codex mcp` commands and ~/.codex/config.toml
    # No api_url or api_key needed - native CLI uses its own auth


class CodexProvider:
    """Codex provider via native CLI (codex exec --json)."""

    streams = True

    def __init__(self, config: CodexConfig | None = None):
        self.config = config or CodexConfig()
        self._last_input_tokens = 0
        self._last_output_tokens = 0
        self._temp_instructions_path: str | None = None
        self._temp_schema_path: str | None = None
        self._codex_started_ids: set[str] = set()

    @property
    def last_context_tokens(self) -> int:
        return self._last_input_tokens

    def _reset_usage(self) -> None:
        """Clear usage counters before starting a new Codex subprocess."""
        self._last_input_tokens = 0
        self._last_output_tokens = 0
        self._codex_started_ids = set()

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
        disable_tools: bool = False,
        output_schema: dict | None = None,
        read_only: bool = False,
        effort: str = "",
        compact_check=None,
    ) -> AsyncIterator:
        """Run Codex agent via CLI and yield adapted messages.

        Launches `codex exec --json` as subprocess, reads JSONL from stdout,
        and yields AdaptedMessage objects.
        """
        from src.loop.compact import Compacted

        self._reset_usage()
        cmd = self._build_command(
            model=model,
            working_dir=working_dir,
            disable_tools=disable_tools,
            output_schema=output_schema,
            effort=effort,
            read_only=read_only,
        )
        env = self._build_env(system_prompt)

        _gen = run_subprocess_jsonl(
            cmd,
            working_dir,
            env=env,
            stdin_data=prompt.encode("utf-8"),
            stall_timeout=self.config.stall_timeout_s,
        )
        self._stream_errors: list[str] = []
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    if event.stalled:
                        raise ProviderStalled(
                            f"нет вывода {self.config.stall_timeout_s:g} с"
                        )
                    self._raise_for_returncode(
                        event.returncode, event.stderr, self._stream_errors[-10:]
                    )
                else:
                    err = stream_error_text(event)
                    if err:
                        self._stream_errors.append(err[:2000])
                    tokens_before = self._last_input_tokens
                    adapted = self._adapt_codex_event(event)
                    if adapted is None:
                        if (
                            compact_check is not None
                            and self._last_input_tokens != tokens_before
                        ):
                            if compact_check(self._last_input_tokens):
                                raise Compacted(self._last_input_tokens)
                        continue
                    yield adapted
        finally:
            await _gen.aclose()
            self._cleanup_temp_instructions()
            self._cleanup_temp_schema()

    def _build_command(
        self,
        model: str = "",
        working_dir: str = "",
        disable_tools: bool = False,
        output_schema: dict | None = None,
        effort: str = "",
        read_only: bool = False,
    ) -> list[str]:
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

        # Sandbox mode. Text-only runs should never need command execution;
        # read-only runs (the judge) keep the shell so they can read the code.
        # Codex 0.147 dropped `-a <policy>` on `exec`; use `-s` / `-c` instead.
        # The judge runs the suite, and pytest needs a writable tmp dir, which
        # `read-only` forbids; it gets workspace-write and is told not to edit.
        if disable_tools:
            cmd.extend(["-s", "read-only"])
        elif read_only:
            cmd.extend(["-s", "workspace-write"])
        elif self.config.bypass_approvals:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.config.full_auto:
            cmd.append("--full-auto")
        else:
            cmd.extend(["-s", self.config.sandbox_mode])
            if self.config.approval_policy:
                cmd.extend(["-c", f"approval_policy={self.config.approval_policy}"])

        if self.config.ephemeral:
            cmd.append("--ephemeral")

        # Feature flags (--enable / --disable)
        for feature in self.config.enabled_features:
            cmd.extend(["--enable", feature])
        disabled_features = list(self.config.disabled_features)
        if disable_tools:
            for feature in TEXT_ONLY_DISABLED_FEATURES:
                if feature not in disabled_features:
                    disabled_features.append(feature)
        for feature in disabled_features:
            cmd.extend(["--disable", feature])

        # Config overrides (-c key=value)
        for key, value in self.config.config_overrides.items():
            cmd.extend(["-c", f"{key}={value}"])

        resolved_effort = normalize_effort(effort) or self.config.model_reasoning_effort
        if resolved_effort:
            cmd.extend(["-c", f"model_reasoning_effort={resolved_effort}"])

        # Extra args
        cmd.extend(self.config.extra_args)

        if output_schema:
            self._cleanup_temp_schema()  # a previous build's file, if any
            schema_file = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                prefix="tero-codex-schema-",
                delete=False,
            )
            json.dump(output_schema, schema_file)
            schema_file.close()
            self._temp_schema_path = schema_file.name
            cmd.extend(["--output-schema", schema_file.name])

        # Prompt from stdin
        cmd.append("-")

        return cmd

    def _build_env(self, system_prompt: str = "") -> dict:
        """Build environment for subprocess.

        For large system prompts (>64KB), writes to a temp file and sets
        CODEX_INSTRUCTIONS to a short reference. This avoids env var size
        limits that can cause silent truncation or subprocess start failures.
        """
        self._cleanup_temp_instructions()  # Clean up any previous temp file
        env = os.environ.copy()
        if not system_prompt:
            return env

        encoded = system_prompt.encode("utf-8")
        if len(encoded) <= LARGE_PROMPT_THRESHOLD_BYTES:
            env["CODEX_INSTRUCTIONS"] = system_prompt
            return env

        # Write to temp file
        fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="codex_instructions_")
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)
        self._temp_instructions_path = tmp_path

        env["CODEX_INSTRUCTIONS"] = (
            "IMPORTANT: Your complete system instructions are in the file "
            f"{tmp_path} — you MUST read that file in full before doing "
            "anything else. Follow every instruction in it."
        )
        return env

    def _cleanup_temp_instructions(self) -> None:
        """Remove temp instructions file if it exists."""
        tmp = self._temp_instructions_path
        if tmp is not None:
            self._temp_instructions_path = None
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _cleanup_temp_schema(self) -> None:
        """Remove the --output-schema temp file if it exists.

        Kept separate from _cleanup_temp_instructions: _build_env() calls that
        one right after _build_command() has written the schema file.
        """
        schema = self._temp_schema_path
        if schema is not None:
            self._temp_schema_path = None
            try:
                os.unlink(schema)
            except OSError:
                pass

    def _raise_for_returncode(
        self, returncode: int | None, stderr_data: bytes | str,
        errors: list[str] | None = None,
    ) -> None:
        """Raise when the Codex subprocess exits unsuccessfully."""
        raise_for_exit("codex", returncode, stderr_data, errors)

    def _adapt_codex_event(self, event: dict) -> AdaptedMessage | None:
        """Convert Codex JSONL event to AdaptedMessage."""
        if isinstance(event, dict) and event.get("type", "") in (
            "item.completed",
            "item.started",
        ):
            return self._adapt_item(event.get("item") or {}, event["type"])
        adapted = adapt_codex_event(event, self._codex_started_ids)
        if adapted is not None and adapted.type == "result" and not adapted.content:
            usage = event.get("usage") or {}
            inputs = usage.get("input_tokens", 0)
            outputs = usage.get("output_tokens", 0)
            self._last_input_tokens = inputs
            self._last_output_tokens = outputs
        return adapted

    def _adapt_item(self, item: dict, event_type: str) -> AdaptedMessage | None:
        """Convert a single Codex item to AdaptedMessage."""
        return adapt_item(item, event_type, self._codex_started_ids)

    def check_ready(self) -> tuple[bool, str]:
        """Check if Codex CLI is installed and available."""
        if not shutil.which(self.config.command):
            return (
                False,
                f"'{self.config.command}' not found in PATH. Install: npm i -g @openai/codex",
            )

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
        model = self.config.default_model or "default"
        return f"Codex ({model})"
