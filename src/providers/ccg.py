"""CCG provider — wraps Claude Agent SDK with Blackbox.ai env vars."""

import json

from src.config import CcgEnv

# Claude Code CLI assumes ~200k context for models it doesn't recognize
# (e.g. GLM-5 via Blackbox). We use this to back-calculate the
# autoCompactThreshold so Claude Code compacts at the right point.
_CLAUDE_CODE_ASSUMED_WINDOW = 200_000

try:
    from claude_agent_sdk import query, ClaudeAgentOptions
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    query = None
    ClaudeAgentOptions = None


def _make_compact_hooks(context_limit: int, threshold: float) -> dict:
    """Build SDK hooks dict with PreCompact handler for mid-turn compaction."""
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

    return {
        "PreCompact": [
            {"matcher": None, "hooks": [on_pre_compact], "timeout": None}
        ]
    }


# Keep the original function for backwards compatibility
async def run_agent(
    prompt: str,
    system_prompt: str,
    working_dir: str,
    ccg_env: CcgEnv,
    max_turns: int = 30,
    model: str = "",
    context_limit: int = 110_000,
    compact_threshold: float = 0.85,
):
    """Run a Claude Code agent via SDK with ccg env vars.

    Yields SDK messages as they stream in.
    model: if non-empty, overrides ANTHROPIC_MODEL for this call.
    context_limit: Max context tokens before compaction (default: 110k)
    compact_threshold: Fraction of limit to trigger compaction (default: 0.85)
    """
    if not SDK_AVAILABLE:
        raise ImportError(
            "claude-agent-sdk not installed. Install with: pip install claude-agent-sdk"
        )

    if not ccg_env.auth_token:
        raise ValueError(
            "No auth token. Set ANTHROPIC_AUTH_TOKEN or BLACKBOX_ACCOUNT_A_TOKEN"
        )

    env = ccg_env.as_dict()
    if model:
        env["ANTHROPIC_MODEL"] = model

    # Tell Claude Code to compact at the right point for the actual model.
    # Claude Code assumes ~200k for unrecognized models (GLM-5, Kimi, etc.)
    # but these models have 98k–131k windows. We rescale the threshold so
    # Claude Code compacts when the real window is ~85% full.
    target_compact_tokens = int(context_limit * compact_threshold)
    adjusted_threshold = max(0.1, min(0.9, target_compact_tokens / _CLAUDE_CODE_ASSUMED_WINDOW))
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


class CcgProvider:
    """Blackbox CCG provider (GLM-5, Kimi, etc.).

    Wraps the Claude Agent SDK with Blackbox environment variables.
    """

    def __init__(self, ccg_env: CcgEnv):
        """Initialize with CCG environment configuration.

        Args:
            ccg_env: Environment with auth token, base URL, model, etc.
        """
        self.env = ccg_env

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
        """Run a turn using the CCG API.

        Args:
            prompt: User prompt
            system_prompt: System instructions
            working_dir: Working directory
            max_turns: Maximum turns
            model: Optional model override
            context_limit: Max context tokens (default: 110k)
            compact_threshold: Fraction to trigger compaction (default: 0.85)

        Yields:
            AdaptedMessage objects adapted from SDK messages
        """
        async for msg in run_agent(
            prompt=prompt,
            system_prompt=system_prompt,
            working_dir=working_dir,
            ccg_env=self.env,
            max_turns=max_turns,
            model=model,
            context_limit=context_limit,
            compact_threshold=compact_threshold,
        ):
            yield msg

    def check_ready(self) -> tuple[bool, str]:
        """Check if provider is ready to use.

        Returns:
            (True, "") if ready, (False, reason) if not
        """
        if not SDK_AVAILABLE:
            return False, "claude-agent-sdk not installed. Run: pip install claude-agent-sdk"

        if not self.env.auth_token:
            # Account-aware error message
            account = self.env.account_label or "blackbox"
            if account == "blackbox-b" or account.endswith("-b"):
                return False, f"No auth token for {account}. Set BLACKBOX_ACCOUNT_B_TOKEN"
            return False, f"No auth token for {account}. Set ANTHROPIC_AUTH_TOKEN or BLACKBOX_ACCOUNT_A_TOKEN"

        return True, ""

    @property
    def display_name(self) -> str:
        """Human-readable name for UI.

        Shows model and account for multi-account differentiation.
        Examples: "CCG (GLM-5/A)", "CCG (KIMI/B)"
        """
        model = self.env.model or "default"
        account = self.env.account_label or "blackbox"
        # Shorten model name
        if "glm" in model.lower():
            model_name = "GLM-5"
        elif "kimi" in model.lower():
            model_name = "KIMI"
        elif "sonnet" in model.lower():
            model_name = "SONNET"
        else:
            model_name = model.split('/')[-1][:8]
        # Shorten account label: blackbox-a -> A, blackbox-b -> B
        if account.startswith("blackbox-"):
            account_short = account.split("-")[-1].upper()
        elif account.endswith("-a"):
            account_short = "A"
        elif account.endswith("-b"):
            account_short = "B"
        else:
            account_short = account[:3].upper()
        return f"CCG ({model_name}/{account_short})"
