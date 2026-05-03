"""Tests for src/providers/gemini.py — GeminiProvider."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.constants import LARGE_PROMPT_THRESHOLD_BYTES
from src.errors import ProviderError
from src.providers.gemini import GeminiConfig, GeminiProvider
from src.providers.message_adapter import AdaptedMessage
from src.providers.subprocess_runner import SubprocessExit


def _make_provider(**overrides):
    cfg = GeminiConfig(**overrides)
    return GeminiProvider(cfg)


class TestGeminiConfig:
    def test_defaults(self):
        cfg = GeminiConfig()
        assert cfg.command == "gemini"
        assert cfg.default_model == "gemini-2.5-pro"
        assert cfg.display_name == "Gemini"
        assert cfg.yolo is True

    def test_custom_command(self):
        cfg = GeminiConfig(command="/usr/local/bin/gemini")
        assert cfg.command == "/usr/local/bin/gemini"


class TestBuildCommand:
    def test_default_command(self):
        p = _make_provider()
        cmd = p._build_command(prompt="hello")
        assert cmd[0] == "gemini"
        assert "-p" in cmd
        assert "hello" in cmd
        assert "-o" in cmd
        assert "stream-json" in cmd
        assert "-m" in cmd
        assert "gemini-2.5-pro" in cmd
        assert "--yolo" in cmd

    def test_custom_model(self):
        p = _make_provider()
        cmd = p._build_command(model="gemini-2.5-flash", prompt="hi")
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "gemini-2.5-flash"

    def test_yolo_disabled(self):
        p = _make_provider(yolo=False)
        cmd = p._build_command(prompt="hi")
        assert "--yolo" not in cmd

    def test_long_prompt_uses_see_stdin(self):
        p = _make_provider()
        long_prompt = "x" * (LARGE_PROMPT_THRESHOLD_BYTES + 1)
        cmd = p._build_command(prompt=long_prompt)
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "see stdin"

    def test_short_prompt_inlined(self):
        p = _make_provider()
        cmd = p._build_command(prompt="short prompt")
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "short prompt"

    def test_command_order(self):
        p = _make_provider()
        cmd = p._build_command(prompt="test")
        assert cmd == [
            "gemini",
            "-p",
            "test",
            "-o",
            "stream-json",
            "-m",
            "gemini-2.5-pro",
            "--yolo",
        ]


class TestBuildEnv:
    def test_with_system_prompt(self):
        p = _make_provider()
        env = p._build_env("You are helpful")
        assert env["GEMINI_SYSTEM_PROMPT"] == "You are helpful"

    def test_without_system_prompt(self):
        p = _make_provider()
        env = p._build_env("")
        assert "GEMINI_SYSTEM_PROMPT" not in env


class TestCombinePrompt:
    def test_with_system_prompt(self):
        result = GeminiProvider._combine_prompt("sys", "user msg")
        assert "<SYSTEM INSTRUCTIONS>" in result
        assert "sys" in result
        assert "user msg" in result

    def test_without_system_prompt(self):
        result = GeminiProvider._combine_prompt("", "user msg")
        assert result == "user msg"


class TestAdaptGeminiEvent:
    def test_message_event(self):
        p = _make_provider()
        msg = p._adapt_gemini_event(
            {"type": "message", "role": "assistant", "content": "hello"}
        )
        assert msg is not None
        assert msg.role == "assistant"
        assert msg.get_text_content() == "hello"

    def test_result_event(self):
        p = _make_provider()
        msg = p._adapt_gemini_event(
            {
                "type": "result",
                "stats": {"input_tokens": 100, "output_tokens": 50},
            }
        )
        assert msg is not None
        assert msg.stop_reason == "end_turn"
        assert p._last_input_tokens == 100
        assert p._last_output_tokens == 50

    def test_tool_use_event(self):
        p = _make_provider()
        msg = p._adapt_gemini_event(
            {
                "type": "tool_use",
                "name": "read_file",
                "id": "t1",
                "input": {"path": "foo.py"},
            }
        )
        assert msg is not None
        assert msg.stop_reason == "tool_use"
        assert len(msg.content) == 1
        block = msg.content[0]
        assert block.name == "read_file"

    def test_tool_result_event(self):
        p = _make_provider()
        msg = p._adapt_gemini_event(
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "output": "file contents",
            }
        )
        assert msg is not None
        assert msg.role == "tool"

    def test_error_event(self):
        p = _make_provider()
        msg = p._adapt_gemini_event({"type": "error", "message": "something broke"})
        assert msg is not None
        assert "something broke" in msg.get_text_content()

    def test_unknown_event_returns_none(self):
        p = _make_provider()
        msg = p._adapt_gemini_event({"type": "unknown_type"})
        assert msg is None

    def test_message_empty_content_returns_none(self):
        p = _make_provider()
        msg = p._adapt_gemini_event(
            {"type": "message", "role": "assistant", "content": ""}
        )
        assert msg is None

    def test_message_user_role_returns_none(self):
        p = _make_provider()
        msg = p._adapt_gemini_event(
            {"type": "message", "role": "user", "content": "hi"}
        )
        assert msg is None


class TestRaiseForReturncode:
    def test_zero_does_not_raise(self):
        p = _make_provider()
        p._raise_for_returncode(0, b"")
        p._raise_for_returncode(None, b"")

    def test_nonzero_raises_provider_error(self):
        p = _make_provider()
        with pytest.raises(ProviderError, match="exited with code 1"):
            p._raise_for_returncode(1, b"some error")

    def test_nonzero_with_string_stderr(self):
        p = _make_provider()
        with pytest.raises(ProviderError, match="string error"):
            p._raise_for_returncode(2, "string error")

    def test_nonzero_with_empty_stderr(self):
        p = _make_provider()
        with pytest.raises(ProviderError, match="exited without stderr"):
            p._raise_for_returncode(1, b"")


class TestDisplayName:
    def test_default(self):
        p = _make_provider()
        assert "Gemini" in p.display_name
        assert "gemini-2.5-pro" in p.display_name

    def test_custom_model(self):
        p = _make_provider(default_model="gemini-3.0-flash")
        assert "gemini-3.0-flash" in p.display_name


class TestResetUsage:
    def test_resets_counters(self):
        p = _make_provider()
        p._last_input_tokens = 100
        p._last_output_tokens = 50
        p._reset_usage()
        assert p._last_input_tokens == 0
        assert p._last_output_tokens == 0


class TestRunIntegration:
    @pytest.mark.asyncio
    async def test_run_yields_adapted_messages(self):
        p = _make_provider()

        events = [
            {"type": "message", "role": "assistant", "content": "hello"},
            SubprocessExit(returncode=0, stderr=b""),
        ]

        async def fake_generator(*args, **kwargs):
            for e in events:
                yield e

        with patch(
            "src.providers.gemini.run_subprocess_jsonl", side_effect=fake_generator
        ) as mock_run:
            msgs = []
            async for msg in p.run("test prompt", "system", "/tmp"):
                msgs.append(msg)
            assert len(msgs) == 1
            assert msgs[0].get_text_content() == "hello"
            call_kwargs = mock_run.call_args
            assert (
                call_kwargs[1].get("stdin_data") is None
                or call_kwargs.kwargs.get("stdin_data") is None
            )

    @pytest.mark.asyncio
    async def test_run_raises_on_nonzero_exit(self):
        p = _make_provider()

        events = [
            SubprocessExit(returncode=1, stderr=b"fatal error"),
        ]

        async def fake_generator(*args, **kwargs):
            for e in events:
                yield e

        with patch(
            "src.providers.gemini.run_subprocess_jsonl", side_effect=fake_generator
        ):
            with pytest.raises(ProviderError, match="exited with code 1"):
                async for _ in p.run("test", "sys", "/tmp"):
                    pass

    @pytest.mark.asyncio
    async def test_run_long_prompt_passes_stdin(self):
        p = _make_provider()
        long_prompt = "x" * (LARGE_PROMPT_THRESHOLD_BYTES + 1)

        events = [
            {"type": "message", "role": "assistant", "content": "response"},
            SubprocessExit(returncode=0, stderr=b""),
        ]

        async def fake_generator(*args, **kwargs):
            for e in events:
                yield e

        with patch(
            "src.providers.gemini.run_subprocess_jsonl", side_effect=fake_generator
        ) as mock_run:
            msgs = []
            async for msg in p.run(long_prompt, "", "/tmp"):
                msgs.append(msg)
            assert len(msgs) == 1
            call_kwargs = mock_run.call_args
            stdin_data = call_kwargs.kwargs.get("stdin_data") or call_kwargs[1].get(
                "stdin_data"
            )
            assert stdin_data is not None
            cmd = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.args[0]
            assert "see stdin" in cmd


class TestCheckReady:
    def test_not_found_in_path(self):
        p = _make_provider(command="nonexistent_gemini_binary_xyz")
        ok, msg = p.check_ready()
        assert ok is False
        assert "not found" in msg

    def test_ready_success(self):
        p = _make_provider()
        with patch("shutil.which", return_value="/usr/local/bin/gemini"):
            ok, msg = p.check_ready()
        assert ok is True
        assert msg == ""


class TestGeminiVersionIntegration:
    def test_gemini_version_subprocess(self):
        import shutil
        import subprocess

        gemini_path = shutil.which("gemini")
        if gemini_path is None:
            pytest.skip("gemini CLI not installed")

        result = subprocess.run(
            [gemini_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert len(result.stdout.strip()) > 0
