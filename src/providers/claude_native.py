"""Native Claude CLI provider for Pro subscription."""

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import dataclass


# Variables that MUST NOT be passed to native claude
_BLACKBOX_VARS = [
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "BLACKBOX_ACCOUNT_A_TOKEN",
    "BLACKBOX_ACCOUNT_B_TOKEN",
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

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
    ):
        """Run the native Claude CLI and yield JSON events."""
        cmd = self._build_command(model, max_turns, system_prompt)
        env = self._clean_env()

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
            # Default StreamReader limit is 64 KB — large researcher prompts
            # produce JSON lines that exceed this. Raise to 16 MB.
            proc.stdout._limit = 16 * 1024 * 1024

            proc.stdin.write(prompt.encode())

            await proc.stdin.drain()
            proc.stdin.close()

            async for line in proc.stdout:
                line = line.decode().strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    yield event
                except json.JSONDecodeError:
                    yield {"type": "text", "text": line}

            await proc.wait()

            if proc.returncode and proc.returncode != 0:
                stderr = await proc.stderr.read()
                raise RuntimeError(
                    f"claude CLI exited with code {proc.returncode}: "
                    f"{stderr.decode()}"
                )
        finally:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

    def check_ready(self) -> tuple[bool, str]:
        """Check if native Claude CLI is available and authenticated."""
        if not shutil.which(self.config.command):
            return False, f"'{self.config.command}' not found in PATH"

        result = subprocess.run(
            [self.config.command, "auth", "status"],
            capture_output=True,
            text=True,
            env=self._clean_env(),
        )
        if result.returncode != 0:
            return False, f"claude auth not configured. Run: {self.config.command} auth login"

        return True, ""

    @property
    def display_name(self) -> str:
        return f"Claude Pro ({self.config.default_model})"

    def _clean_env(self) -> dict:
        """Copy of environment WITHOUT Blackbox variables."""
        env = os.environ.copy()
        for var in _BLACKBOX_VARS:
            env.pop(var, None)
        claude_home = os.path.expanduser(self.config.claude_home)
        env["CLAUDE_HOME"] = claude_home
        return env

    def _build_command(self, model: str = "", max_turns: int = 30, system_prompt: str = "") -> list:
        """Build the CLI command with arguments."""
        resolved_model = model or self.config.default_model
        resolved_model = _MODEL_ALIASES.get(resolved_model, resolved_model)

        cmd = [
            self.config.command,
            "-p",
            "--verbose",
            "--model", resolved_model,
            "--max-turns", str(max_turns),
            "--permission-mode", "bypassPermissions",
            "--output-format", "stream-json",
        ]

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        return cmd
