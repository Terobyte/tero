"""Tests for the Muse Code (Spark) provider."""

from unittest.mock import patch

import pytest

from src.errors import ProviderError
from src.providers import create_provider
from src.providers.muse import MuseConfig, MuseProvider
from src.providers.registry import ProviderRegistry


def test_create_provider_returns_muse():
    provider = create_provider("muse")
    assert isinstance(provider, MuseProvider)
    assert provider.config.default_model == "muse-spark-1.3"
    assert provider.config.yolo is True
    assert provider.config.trust_workspace is True


def test_registry_creates_muse():
    provider = ProviderRegistry().get("muse")
    assert isinstance(provider, MuseProvider)
    assert provider.config.default_model == "muse-spark-1.3"


def test_build_command_uses_yolo_workspace_and_prompt_file():
    provider = MuseProvider(MuseConfig())
    cmd = provider._build_command(
        model="muse-spark-1.3",
        working_dir="/tmp/work",
        max_turns=12,
        prompt_path="/tmp/prompt.md",
    )
    assert cmd[:3] == ["muse", "exec", "--json"]
    assert "--yolo" in cmd
    assert cmd[cmd.index("--workspace") + 1] == "/tmp/work"
    assert cmd[cmd.index("--model") + 1] == "muse-spark-1.3"
    assert cmd[cmd.index("--max-model-steps") + 1] == "12"
    assert cmd[cmd.index("--prompt-file") + 1] == "/tmp/prompt.md"


def test_build_command_trust_workspace_without_yolo():
    provider = MuseProvider(MuseConfig(yolo=False, trust_workspace=True))
    cmd = provider._build_command(working_dir="/tmp/work")
    assert "--yolo" not in cmd
    assert "--trust-workspace" in cmd


def test_unwrap_payload_event():
    provider = MuseProvider()
    inner = {"kind": "assistant", "text": "hello"}
    adapted = provider._adapt_muse_event({"payload": {"event": inner}})
    assert adapted is not None
    assert adapted.role == "assistant"
    assert adapted.content[0].text == "hello"


def test_adapt_tool_and_result_events():
    provider = MuseProvider()
    tool = provider._adapt_muse_event(
        {"kind": "tool", "name": "Read", "id": "t1", "input": {"path": "a.py"}}
    )
    assert tool is not None
    assert tool.type == "tool_use"
    assert tool.content[0].name == "Read"

    result = provider._adapt_muse_event(
        {"kind": "tool_result", "tool_id": "t1", "output": "ok", "status": "success"}
    )
    assert result is not None
    assert result.type == "tool_result"
    assert result.content[0].is_error is False


def test_error_event_raises():
    provider = MuseProvider()
    with pytest.raises(ProviderError, match="muse CLI error"):
        provider._adapt_muse_event({"kind": "error", "message": "boom"})


def test_check_ready_requires_binary():
    provider = MuseProvider()
    with patch("src.providers.muse.shutil.which", return_value=None):
        ok, reason = provider.check_ready()
    assert ok is False
    assert "muse" in reason


def test_unknown_provider_type_is_rejected():
    with pytest.raises(ProviderError, match="Unknown provider type"):
        create_provider("zai")
    with pytest.raises(ProviderError, match="Unknown provider type"):
        create_provider("kilo")
