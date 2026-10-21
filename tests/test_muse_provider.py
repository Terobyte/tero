"""Tests for the Muse API provider (Meta Muse Spark with your own key).

The provider runs the Claude Code agent loop against Meta's
Anthropic-compatible Messages API; the Muse Code CLI is not used.
"""

import asyncio
import json
import os
from unittest.mock import patch

import pytest

from src.errors import ProviderError
from src.loop.config import UnknownProviderError, normalize_provider
from src.providers import claude_native as claude_native_module
from src.providers import create_provider
from src.providers.muse import MuseConfig, MuseProvider, load_api_key
from src.providers.subprocess_runner import SubprocessExit

KEY = "LLM_test_key_123"


@pytest.fixture
def no_env_key(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)


@pytest.fixture
def secrets(tmp_path):
    path = tmp_path / "secrets.env"
    path.write_text(f"# muse\nMODEL_API_KEY={KEY}\n")
    return str(path)


def test_create_provider_returns_muse_api():
    provider = create_provider("muse")
    assert isinstance(provider, MuseProvider)
    assert provider.config.default_model == "muse-spark-1.3"
    assert provider.config.base_url == "https://api.meta.ai"
    assert provider.config.command == "claude"


def test_muse_code_cli_names_are_gone():
    for name in ("spark", "muse-code", "musecode"):
        with pytest.raises(UnknownProviderError):
            normalize_provider(name)
    with pytest.raises(ProviderError, match="Unknown provider type"):
        create_provider("muse_code")


def test_opencode_keeps_free_muse_preset():
    from src.menu import OPENCODE_MODEL_PRESETS

    assert "opencode/muse-spark-1.3-contributor-free" in OPENCODE_MODEL_PRESETS.values()


def test_key_from_env_wins(monkeypatch, secrets):
    monkeypatch.setenv("MODEL_API_KEY", "from-env")
    assert load_api_key(secrets) == "from-env"


def test_key_from_secrets_file(no_env_key, secrets):
    assert load_api_key(secrets) == KEY


@pytest.mark.parametrize(
    "line",
    [f'MODEL_API_KEY="{KEY}"', f"export MODEL_API_KEY='{KEY}'", f"  MODEL_API_KEY={KEY}  "],
)
def test_key_file_quotes_and_export(no_env_key, tmp_path, line):
    path = tmp_path / "s.env"
    path.write_text(line + "\n")
    assert load_api_key(str(path)) == KEY


def test_key_missing(no_env_key, tmp_path):
    assert load_api_key(str(tmp_path / "nope.env")) == ""


def test_check_ready_without_key(no_env_key, tmp_path):
    provider = MuseProvider(MuseConfig(secrets_file=str(tmp_path / "nope.env")))
    with patch("src.providers.muse.shutil.which", return_value="/bin/claude"):
        ok, reason = provider.check_ready()
    assert ok is False
    assert "MODEL_API_KEY" in reason


def test_check_ready_without_claude_cli(no_env_key, secrets):
    provider = MuseProvider(MuseConfig(secrets_file=secrets))
    with patch("src.providers.muse.shutil.which", return_value=None):
        ok, reason = provider.check_ready()
    assert ok is False
    assert "claude" in reason


def test_check_ready_ok_without_claude_login(no_env_key, secrets):
    provider = MuseProvider(MuseConfig(secrets_file=secrets))
    with patch("src.providers.muse.shutil.which", return_value="/bin/claude"), patch(
        "src.providers.claude_native.subprocess.run",
        side_effect=AssertionError("must not call `claude auth status`"),
    ):
        ok, reason = provider.check_ready()
    assert (ok, reason) == (True, "")


def _run(provider, monkeypatch, **run_kwargs):
    seen = {}

    async def fake_runner(cmd, working_dir, env=None, stdin_data=None, **_):
        seen["cmd"] = cmd
        seen["env"] = dict(env)
        seen["config_dir_exists"] = os.path.isdir(env.get("CLAUDE_CONFIG_DIR", ""))
        yield SubprocessExit(returncode=0, stderr=b"")

    monkeypatch.setattr(claude_native_module, "run_subprocess_jsonl", fake_runner)

    async def consume():
        async for _ in provider.run(
            prompt="do it", system_prompt="", working_dir=".", **run_kwargs
        ):
            pass

    asyncio.run(consume())
    return seen


def test_run_points_claude_code_at_meta(no_env_key, secrets, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    provider = MuseProvider(MuseConfig(secrets_file=secrets))

    seen = _run(provider, monkeypatch, model="muse-spark-1.2")

    env = seen["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://api.meta.ai"
    assert env["ANTHROPIC_AUTH_TOKEN"] == KEY
    assert "ANTHROPIC_API_KEY" not in env
    for var in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
    ):
        assert env[var] == "muse-spark-1.2", var
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "muse-spark-1.2"
    assert seen["config_dir_exists"]


def test_run_default_model(no_env_key, secrets, monkeypatch):
    seen = _run(MuseProvider(MuseConfig(secrets_file=secrets)), monkeypatch)
    assert seen["env"]["ANTHROPIC_MODEL"] == "muse-spark-1.3"


def test_key_never_in_argv(no_env_key, secrets, monkeypatch):
    seen = _run(MuseProvider(MuseConfig(secrets_file=secrets)), monkeypatch)
    assert all(KEY not in part for part in seen["cmd"])


def test_run_passes_schema_and_read_only(no_env_key, secrets, monkeypatch):
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    seen = _run(
        MuseProvider(MuseConfig(secrets_file=secrets)),
        monkeypatch,
        output_schema=schema,
        read_only=True,
    )
    cmd = seen["cmd"]
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == schema
    assert "--disallowedTools" in cmd


def test_config_dir_removed_after_run(no_env_key, secrets, monkeypatch):
    provider = MuseProvider(MuseConfig(secrets_file=secrets))
    seen = _run(provider, monkeypatch)
    assert not os.path.exists(seen["env"]["CLAUDE_CONFIG_DIR"])


def test_run_without_key_raises_before_spawn(no_env_key, tmp_path, monkeypatch):
    provider = MuseProvider(MuseConfig(secrets_file=str(tmp_path / "nope.env")))

    async def fake_runner(*args, **kwargs):
        raise AssertionError("must not spawn without a key")
        yield  # pragma: no cover

    monkeypatch.setattr(claude_native_module, "run_subprocess_jsonl", fake_runner)

    async def consume():
        async for _ in provider.run(prompt="x", system_prompt="", working_dir="."):
            pass

    with pytest.raises(ProviderError, match="MODEL_API_KEY"):
        asyncio.run(consume())


def test_unknown_provider_type_is_rejected():
    with pytest.raises(ProviderError, match="Unknown provider type"):
        create_provider("zai")
    with pytest.raises(ProviderError, match="Unknown provider type"):
        create_provider("kilo")
