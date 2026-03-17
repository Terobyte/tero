"""CCG provider — wraps Claude Agent SDK with Blackbox.ai env vars."""

from src.config import CcgEnv

try:
    from claude_agent_sdk import query, ClaudeAgentOptions
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    query = None
    ClaudeAgentOptions = None


# Keep the original function for backwards compatibility
async def run_agent(
    prompt: str,
    system_prompt: str,
    working_dir: str,
    ccg_env: CcgEnv,
    max_turns: int = 30,
    model: str = "",
):
    """Run a Claude Code agent via SDK with ccg env vars.

    Yields SDK messages as they stream in.
    model: if non-empty, overrides ANTHROPIC_MODEL for this call.
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

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=working_dir,
        env=env,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
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
    ):
        """Run a turn using the CCG API.

        Args:
            prompt: User prompt
            system_prompt: System instructions
            working_dir: Working directory
            max_turns: Maximum turns
            model: Optional model override

        Yields:
            SDK messages from the agent
        """
        async for msg in run_agent(
            prompt=prompt,
            system_prompt=system_prompt,
            working_dir=working_dir,
            ccg_env=self.env,
            max_turns=max_turns,
            model=model,
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
            return False, "No auth token. Set ANTHROPIC_AUTH_TOKEN or BLACKBOX_ACCOUNT_A_TOKEN"

        return True, ""

    @property
    def display_name(self) -> str:
        """Human-readable name for UI."""
        model = self.env.model or "default"
        # Shorten model name
        if "glm" in model.lower():
            return "CCG (GLM-5)"
        if "kimi" in model.lower():
            return "CCG (KIMI)"
        if "sonnet" in model.lower():
            return "CCG (SONNET)"
        return f"CCG ({model.split('/')[-1][:8]})"
