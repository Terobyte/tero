"""Native Claude CLI provider for Pro subscription."""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from src.constants import DEFAULT_COMPACT_THRESHOLD
from src.errors import ProviderError
from .subprocess_runner import SubprocessExit, run_subprocess_jsonl


# Variables that MUST NOT be passed through to native Claude.
_BLOCKED_ENV_VARS = [
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ZAI_API_KEY",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
]

# Mapping of short aliases to full IDs (so versions aren't hardcoded)
_MODEL_ALIASES = {
    "sonnet": "sonnet",
    "opus": "opus",
    "haiku": "haiku",
}


@dataclass
class ClaudeNativeConfig:
    """Configuration for native Claude CLI provider."""

    claude_home: str = "~/.claude"
    command: str = "claude"
    default_model: str = "sonnet"


class ClaudeNativeProvider:
    """Native Claude CLI provider (Pro subscription)."""

    def __init__(self, config: ClaudeNativeConfig | None = None):
        self.config = config or ClaudeNativeConfig()
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
        compact_threshold: float = DEFAULT_COMPACT_THRESHOLD,
    ):
        """Run the native Claude CLI and yield JSON events."""
        self._last_input_tokens = 0
        self._last_output_tokens = 0
        cmd = self._build_command(
            model, max_turns, system_prompt, context_limit, compact_threshold
        )
        env = self._clean_env()

        _gen = run_subprocess_jsonl(cmd, working_dir, env=env, stdin_data=prompt.encode())
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    if event.returncode is None or event.returncode != 0:
                        raise ProviderError(
                            f"claude CLI exited with code {event.returncode}: "
                            f"{(event.stderr or b'').decode()}"
                        )
                else:
                    self._capture_usage(event)
                    yield event
        finally:
            await _gen.aclose()

    def check_ready(self) -> tuple[bool, str]:
        """Check if native Claude CLI is available and authenticated."""
        if not shutil.which(self.config.command):
            return False, f"'{self.config.command}' not found in PATH"

        result = subprocess.run(
            [self.config.command, "auth", "status"],
            capture_output=True,
            text=True,
            env=self._clean_env(),
            timeout=10,
        )
        if result.returncode != 0:
            return (
                False,
                f"claude auth not configured. Run: {self.config.command} auth login",
            )

        return True, ""

    @property
    def display_name(self) -> str:
        return f"Claude Pro ({self.config.default_model})"

    def _clean_env(self) -> dict:
        """Copy of environment with provider override variables removed."""
        env = os.environ.copy()
        for var in _BLOCKED_ENV_VARS:
            env.pop(var, None)
        # Do NOT set CLAUDE_CONFIG_DIR — it breaks auth resolution on macOS
        # (Claude Code uses its own default path logic when the var is absent)
        env.pop("CLAUDE_CONFIG_DIR", None)
        return env

    def _capture_usage(self, event) -> None:
        """Best-effort token accounting from Claude stream-json result events."""
        if not isinstance(event, dict):
            return
        usage = event.get("usage") or event.get("stats") or {}
        if not isinstance(usage, dict):
            return
        self._last_input_tokens = int(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or self._last_input_tokens
            or 0
        )
        self._last_output_tokens = int(
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or self._last_output_tokens
            or 0
        )

    def _build_command(
        self,
        model: str = "",
        max_turns: int = 30,
        system_prompt: str = "",
        context_limit: int = 0,
        compact_threshold: float = DEFAULT_COMPACT_THRESHOLD,
    ) -> list:
        """Build the CLI command with arguments."""
        resolved_model = model or self.config.default_model
        resolved_model = _MODEL_ALIASES.get(resolved_model, resolved_model)

        cmd = [
            self.config.command,
            "-p",
            "--verbose",
            "--model",
            resolved_model,
            "--max-turns",
            str(max_turns),
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
        ]

        # Disable CLI auto-compact within a single player turn.
        # The CLI applies autoCompactThreshold against its own internal window
        # (~60K for Pro), NOT against the model's full context window, so passing
        # compact_threshold (0.85) caused premature compaction at ~50K tokens —
        # dropping tool results and forcing the model to write an incomplete response.
        # Tero's own continuation mechanism handles context overflow at turn boundaries.
        cmd.extend(
            ["--settings", json.dumps({"autoCompactThreshold": 0.99})]
        )

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        return cmd
