"""Native Claude CLI provider for Pro subscription."""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from src.constants import DEFAULT_COMPACT_THRESHOLD
from src.errors import ProviderStalled
from .effort import normalize_effort
from .message_adapter import adapt_claude_event
from .subprocess_runner import SubprocessExit, raise_for_exit, run_subprocess_jsonl


# Variables that MUST NOT be passed through to native Claude.
_BLOCKED_ENV_VARS = [
    # an API key would silently bill the API instead of the subscription
    "ANTHROPIC_API_KEY",
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
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
    "haiku": "claude-haiku-4-5",
    "fable": "claude-fable-5-1",
}


@dataclass
class ClaudeNativeConfig:
    """Configuration for native Claude CLI provider."""

    command: str = "claude"
    default_model: str = "claude-sonnet-5"
    stall_timeout_s: float = 0


class ClaudeNativeProvider:
    """Native Claude CLI provider (Pro subscription)."""

    streams = True

    def __init__(self, config: ClaudeNativeConfig | None = None):
        self.config = config or ClaudeNativeConfig()
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    @property
    def last_context_tokens(self) -> int:
        return self._last_input_tokens

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
        context_limit: int = 0,
        compact_threshold: float = DEFAULT_COMPACT_THRESHOLD,
        output_schema: dict | None = None,
        read_only: bool = False,
        effort: str = "",
        compact_check=None,
        compact_at_tokens: int = 0,
    ):
        """Run the native Claude CLI and yield JSON events."""
        from src.loop.compact import Compacted

        self._last_input_tokens = 0
        self._last_output_tokens = 0
        cmd = self._build_command(
            model,
            max_turns,
            system_prompt,
            context_limit,
            compact_threshold,
            output_schema=output_schema,
            read_only=read_only,
            effort=effort,
            compact_at_tokens=compact_at_tokens,
        )
        env = self._clean_env()

        _gen = run_subprocess_jsonl(
            cmd,
            working_dir,
            env=env,
            stdin_data=prompt.encode(),
            stall_timeout=self.config.stall_timeout_s,
        )
        try:
            async for event in _gen:
                if isinstance(event, SubprocessExit):
                    if event.stalled:
                        raise ProviderStalled(
                            f"нет вывода {self.config.stall_timeout_s:g} с"
                        )
                    if event.returncode != 0:
                        raise_for_exit("claude CLI", event.returncode, event.stderr)
                else:
                    self._capture_usage(event)
                    if compact_check is not None and _is_token_only(event):
                        # Токеновая граница без сообщения. Событие с текстом
                        # отдаём дальше: решает LiveAgents после обработки.
                        if compact_check(self._last_input_tokens):
                            raise Compacted(self._last_input_tokens)
                    yield event
        finally:
            await _gen.aclose()

    def check_ready(self) -> tuple[bool, str]:
        """Check if native Claude CLI is available and authenticated."""
        if not shutil.which(self.config.command):
            return False, f"'{self.config.command}' not found in PATH"

        try:
            result = subprocess.run(
                [self.config.command, "auth", "status"],
                capture_output=True,
                text=True,
                env=self._clean_env(),
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return False, "claude auth status timed out"
        except OSError as exc:
            return False, f"claude auth status failed: {exc}"
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

    def _autocompact_flag(self, compact_at_tokens: int) -> list:
        """Флаг --autocompact для роли, если её порог задан и в пределах CLI."""
        if not _valid_autocompact(compact_at_tokens):
            if _positive_int(compact_at_tokens) > 0:
                import warnings

                warnings.warn(
                    f"claude --autocompact {compact_at_tokens} вне пределов 100000–1000000, без флага",
                    UserWarning,
                    stacklevel=3,
                )
            return []
        return ["--autocompact", str(int(compact_at_tokens))]

    def _capture_usage(self, event) -> None:
        """Best-effort token accounting from Claude stream-json result events."""
        if not isinstance(event, dict):
            return
        usage = event.get("usage") or event.get("stats") or {}
        if not isinstance(usage, dict):
            return
        if (
            "input_tokens" in usage
            or "prompt_tokens" in usage
            or "cache_read_input_tokens" in usage
        ):
            # Явные нули — тоже счётчик: старое значение не сохраняем.
            inp = usage.get("input_tokens")
            if inp is None:
                inp = usage.get("prompt_tokens")
            if inp is None:
                inp = 0
            cache = usage.get("cache_read_input_tokens")
            if cache is None:
                cache = 0
            self._last_input_tokens = int(inp) + int(cache)
        if "output_tokens" in usage or "completion_tokens" in usage:
            out = usage.get("output_tokens")
            if out is None:
                out = usage.get("completion_tokens")
            if out is None:
                out = 0
            self._last_output_tokens = int(out)

    def _build_command(
        self,
        model: str = "",
        max_turns: int = 30,
        system_prompt: str = "",
        context_limit: int = 0,
        compact_threshold: float = DEFAULT_COMPACT_THRESHOLD,
        output_schema: dict | None = None,
        read_only: bool = False,
        effort: str = "",
        compact_at_tokens: int = 0,
    ) -> list:
        resolved_model = model or self.config.default_model
        resolved_model = _MODEL_ALIASES.get(resolved_model, resolved_model)
        resolved_effort = normalize_effort(effort)

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
        if resolved_effort:
            cmd.extend(["--effort", resolved_effort])

        # Disable CLI auto-compact within a single player turn.
        # The CLI applies autoCompactThreshold against its own internal window
        # (~60K for Pro), NOT against the model's full context window, so passing
        # compact_threshold (0.85) caused premature compaction at ~50K tokens —
        # dropping tool results and forcing the model to write an incomplete response.
        # Tero's own continuation mechanism handles context overflow at turn boundaries.
        cmd.extend(
            ["--settings", json.dumps({"autoCompactThreshold": 0.99})]
        )
        cmd.extend(self._autocompact_flag(compact_at_tokens))

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        if output_schema:
            cmd.extend(["--json-schema", json.dumps(output_schema)])

        if read_only:
            # Bash can write too (sed -i, rm); the judge reads with Read/Grep/Glob.
            cmd.extend(["--disallowedTools", "Edit,Write,NotebookEdit,Bash"])

        return cmd


_TOKEN_USAGE_KEYS = (
    "input_tokens",
    "prompt_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "completion_tokens",
)


def _positive_int(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _valid_autocompact(compact_at_tokens: object) -> bool:
    """Порог годится для --autocompact CLI: 100000–1000000."""
    n = _positive_int(compact_at_tokens)
    return 100000 <= n <= 1000000


def _is_token_only(event: dict) -> bool:
    """True на распознанной токеновой границе без сообщения.

    Только события с usage/stats (там, где `_capture_usage` берёт счётчик),
    которые не отдают сообщение наружу. Метаданные без счётчика (init,
    system и т.п.) — не граница: их решает LiveAgents после реальных
    сообщений, иначе запрошенный компакт останавливает поток до первого
    сообщения.
    """
    if not isinstance(event, dict):
        return False
    if adapt_claude_event(event) is not None:
        return False
    usage = event.get("usage") or event.get("stats")
    if not isinstance(usage, dict):
        return False
    return any(k in usage for k in _TOKEN_USAGE_KEYS)
