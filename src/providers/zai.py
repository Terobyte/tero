"""Z.AI provider — runs Claude Code agent with Z.AI API (GLM-5.1)."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

_ZAI_BASE_URL = "https://api.z.ai/api/anthropic"
_ZAI_DEFAULT_MODEL = "glm-5.1"

# Claude Code assumes ~200k context for unrecognised models.
# We rescale autoCompactThreshold so compaction fires at the right real-window size.
_CLAUDE_CODE_ASSUMED_WINDOW = 200_000

try:
    from claude_agent_sdk import query, ClaudeAgentOptions
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    query = None
    ClaudeAgentOptions = None


@dataclass
class ZaiConfig:
    """Configuration for the Z.AI provider."""
    claude_home: str = "~/.claude-zai"
    default_model: str = _ZAI_DEFAULT_MODEL


def _load_token(claude_home: str) -> str:
    """Read ZAI_API_KEY from env, then fall back to settings.json in claude_home."""
    token = os.environ.get("ZAI_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""
    if token:
        return token

    settings_path = Path(os.path.expanduser(claude_home)) / "settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            env_vals = data.get("env", {})
            return env_vals.get("ZAI_API_KEY") or env_vals.get("ANTHROPIC_AUTH_TOKEN") or ""
        except (OSError, json.JSONDecodeError):
            pass
    return ""


def _make_compact_hooks(context_limit: int, threshold: float) -> dict:
    compact_at = int(context_limit * threshold)

    async def on_pre_compact(hook_input, tool_name, context) -> dict:
        return {
            "continue_": True,
            "systemMessage": (
                "Summarize the conversation compactly. Preserve: "
                "completed steps with proof, file paths changed, "
                "current implementation state, pending work. "
                f"Target: under {compact_at // 1000}k tokens."
            ),
        }

    return {"PreCompact": [{"matcher": None, "hooks": [on_pre_compact], "timeout": None}]}


class ZaiProvider:
    """Z.AI provider — uses Claude Code agent loop with GLM-5.1 via api.z.ai."""

    def __init__(self, config: ZaiConfig | None = None):
        self.config = config or ZaiConfig()

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
        context_limit: int = 110_000,
        compact_threshold: float = 0.85,
    ):
        """Run a turn using the Z.AI API via Claude Code agent loop.

        Yields SDK messages as they stream in.
        """
        if not SDK_AVAILABLE:
            raise ImportError(
                "claude-agent-sdk not installed. Run: pip install claude-agent-sdk"
            )

        token = _load_token(self.config.claude_home)
        if not token:
            raise ValueError("No Z.AI auth token. Set ZAI_API_KEY env var.")

        resolved_model = model or self.config.default_model

        env = {
            "ANTHROPIC_BASE_URL": _ZAI_BASE_URL,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_MODEL": resolved_model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": resolved_model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": resolved_model,
            "CLAUDE_HOME": os.path.expanduser(self.config.claude_home),
            # Clear the nested-session guard so the subprocess starts cleanly.
            # The SDK merges {**os.environ, **env}, so this overrides the
            # CLAUDECODE var set by the parent tero/Claude Code session.
            "CLAUDECODE": "",
        }

        target_compact_tokens = int(context_limit * compact_threshold)
        adjusted_threshold = max(
            0.1, min(0.9, target_compact_tokens / _CLAUDE_CODE_ASSUMED_WINDOW)
        )
        settings = json.dumps({"autoCompactThreshold": round(adjusted_threshold, 3)})

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=working_dir,
            env=env,
            permission_mode="bypassPermissions",
            max_turns=max_turns,
            hooks=_make_compact_hooks(context_limit, compact_threshold),
            settings=settings,
        )

        async for message in query(prompt=prompt, options=options):
            yield message

    def check_ready(self) -> tuple[bool, str]:
        """Check if Z.AI provider is ready to use."""
        if not SDK_AVAILABLE:
            return False, "claude-agent-sdk not installed. Run: pip install claude-agent-sdk"
        token = _load_token(self.config.claude_home)
        if not token:
            return False, "No Z.AI auth token. Set ZAI_API_KEY env var."
        return True, ""

    @property
    def display_name(self) -> str:
        model = self.config.default_model
        lower = model.lower()
        if "glm-5.1" in lower:
            model_name = "GLM-5.1"
        elif "glm-4.7" in lower:
            model_name = "GLM-4.7"
        elif "glm" in lower:
            model_name = "GLM"
        else:
            model_name = model.split("/")[-1][:10]
        return f"ZAI ({model_name})"
