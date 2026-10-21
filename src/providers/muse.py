"""Muse API provider — Meta Muse Spark with your own key.

Runs the Claude Code agent loop (`claude -p`) against Meta's
Anthropic-compatible Messages API, so the model gets real file and shell
tools. Meta documents this path for Claude Code:
https://dev.meta.ai/docs/protocols

The key comes from MODEL_API_KEY (Meta's official variable) or from a
private secrets file; it is passed only through the subprocess environment,
never through argv.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.errors import ProviderError

from .claude_native import ClaudeNativeProvider

KEY_ENV = "MODEL_API_KEY"
DEFAULT_BASE_URL = "https://api.meta.ai"
DEFAULT_SECRETS_FILE = "~/.g3/secrets.env"

_MODEL_ENV_VARS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
)


@dataclass
class MuseConfig:
    """Configuration for the Muse API provider."""

    command: str = "claude"
    default_model: str = "muse-spark-1.3"
    base_url: str = DEFAULT_BASE_URL
    secrets_file: str = DEFAULT_SECRETS_FILE
    display_name: str = "Muse API"


def muse_from_config(provider_config: dict) -> "MuseProvider":
    """Build a MuseProvider from a `.g3/config.yaml` providers entry."""
    return MuseProvider(
        MuseConfig(
            command=provider_config.get("command", "claude"),
            default_model=provider_config.get("default_model", "muse-spark-1.3"),
            base_url=provider_config.get("base_url", DEFAULT_BASE_URL),
            secrets_file=provider_config.get("secrets_file", DEFAULT_SECRETS_FILE),
            display_name=provider_config.get("display_name", "Muse API"),
        )
    )


def load_api_key(secrets_file: str = DEFAULT_SECRETS_FILE) -> str:
    """MODEL_API_KEY from the environment, else from the secrets file."""
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    try:
        lines = Path(secrets_file).expanduser().read_text().splitlines()
    except OSError:
        return ""
    prefix = f"{KEY_ENV}="
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        return value
    return ""


def _missing_key_message(config: MuseConfig) -> str:
    return (
        f"no {KEY_ENV} for Muse API: export it or put "
        f"`{KEY_ENV}=...` into {config.secrets_file}"
    )


class MuseProvider(ClaudeNativeProvider):
    """Claude Code agent loop on Meta's Messages API with a Muse Spark model."""

    def __init__(self, config: MuseConfig | None = None):
        super().__init__(config or MuseConfig())
        self._model = self.config.default_model
        self._config_dir: str | None = None

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
        **kwargs,
    ):
        if not load_api_key(self.config.secrets_file):
            raise ProviderError(_missing_key_message(self.config))
        self._model = model or self.config.default_model
        # Fresh Claude Code home per run: no claude.ai login, history or
        # settings from ~/.claude leak into Muse runs, and parallel runs
        # do not share session state.
        self._config_dir = tempfile.mkdtemp(prefix="tero-muse-claude-")
        try:
            async for event in super().run(
                prompt,
                system_prompt,
                working_dir,
                max_turns=max_turns,
                model=self._model,
                **kwargs,
            ):
                yield event
        finally:
            shutil.rmtree(self._config_dir, ignore_errors=True)
            self._config_dir = None

    def _clean_env(self) -> dict:
        env = super()._clean_env()
        env.pop("ANTHROPIC_API_KEY", None)
        env["ANTHROPIC_BASE_URL"] = self.config.base_url
        env["ANTHROPIC_AUTH_TOKEN"] = load_api_key(self.config.secrets_file)
        for var in _MODEL_ENV_VARS:
            env[var] = self._model
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        # Clear the nested-session guard when tero itself runs inside Claude Code.
        env["CLAUDECODE"] = ""
        if self._config_dir:
            env["CLAUDE_CONFIG_DIR"] = self._config_dir
        return env

    def check_ready(self) -> tuple[bool, str]:
        if shutil.which(self.config.command) is None:
            return (
                False,
                f"'{self.config.command}' not found in PATH "
                "(Muse API runs through the Claude Code CLI)",
            )
        if not load_api_key(self.config.secrets_file):
            return False, _missing_key_message(self.config)
        return True, ""

    @property
    def display_name(self) -> str:
        return f"{self.config.display_name} ({self.config.default_model})"
