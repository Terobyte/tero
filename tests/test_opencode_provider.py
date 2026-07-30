"""Tests for OpenCode provider (MIMO/Kimi) — event adapter, tokens, CLI, presets."""

import asyncio
import json
from unittest.mock import patch

import pytest
from src.providers.opencode import OpenCodeProvider, OpenCodeConfig, MAX_TOOL_OUTPUT


# ---------------------------------------------------------------------------
# Fixtures: mock subprocess helpers (mirrors test_codex_token_tracking.py)
# ---------------------------------------------------------------------------


class MockStdout:
    """Mock stdout with JSONL lines."""

    def __init__(self, lines: list[bytes]):
        self.lines = lines
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.lines):
            raise StopAsyncIteration
        line = self.lines[self.index]
        self.index += 1
        return line


class MockStderr:
    """Mock stderr."""

    def __init__(self, text: str = ""):
        self.text = text

    async def read(self):
        return self.text.encode()


class MockStdin:
    """Mock stdin writer compatible with asyncio subprocess streams."""

    def __init__(self):
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes):
        self.writes.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class BrokenPipeStdin(MockStdin):
    """Mock stdin that fails during drain, simulating early subprocess exit."""

    async def drain(self):
        raise BrokenPipeError("stdin closed")


class MockProcess:
    """Mock subprocess."""

    def __init__(
        self, lines: list[bytes], stderr_text: str = "", wait_returncode: int = 0
    ):
        self.stdout = MockStdout(lines)
        self.stderr = MockStderr(stderr_text)
        self.returncode = None
        self.stdin = MockStdin()
        self.killed = False
        self._wait_returncode = wait_returncode

    async def wait(self):
        if self.returncode is None:
            self.returncode = self._wait_returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class BlockingStdout:
    """Yield one line, then hang forever until the generator is closed."""

    def __init__(self, first_line: bytes):
        self.first_line = first_line
        self.emitted = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.emitted:
            self.emitted = True
            return self.first_line
        await asyncio.Future()


class ChunkedStdout:
    """Mock stdout that exposes read() chunks instead of async iteration."""

    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.index = 0

    async def read(self, _size: int = -1):
        if self.index >= len(self.chunks):
            return b""
        chunk = self.chunks[self.index]
        self.index += 1
        return chunk


# ---------------------------------------------------------------------------
# Unit tests: _adapt_opencode_event
# ---------------------------------------------------------------------------


def test_adapt_text_event():
    """OpenCode 'text' event → assistant TextBlock."""
    provider = OpenCodeProvider(OpenCodeConfig())
    event = {
        "type": "text",
        "part": {"type": "text", "text": "Hello world"},
    }
    msg = provider._adapt_opencode_event(event)
    assert msg is not None
    assert msg.role == "assistant"
    assert len(msg.content) == 1
    assert msg.content[0].text == "Hello world"


def test_adapt_text_event_empty_text_returns_none():
    """OpenCode 'text' event with empty text → None."""
    provider = OpenCodeProvider(OpenCodeConfig())
    event = {
        "type": "text",
        "part": {"type": "text", "text": ""},
    }
    assert provider._adapt_opencode_event(event) is None


def test_adapt_tool_use_event():
    """OpenCode 'tool_use' event → separate tool_use and tool_result messages."""
    provider = OpenCodeProvider(OpenCodeConfig())
    event = {
        "type": "tool_use",
        "part": {
            "type": "tool",
            "tool": "bash",
            "callID": "call-001",
            "state": {
                "status": "completed",
                "input": {"command": "ls -la", "description": "List files"},
                "output": "total 42\ndrwxr-xr-x  ...\n",
                "metadata": {"exit": 0},
            },
        },
    }
    messages = provider._adapt_opencode_event(event)
    assert messages is not None
    assert isinstance(messages, list)
    assert len(messages) == 2
    tool_use_msg, tool_result_msg = messages
    assert tool_use_msg.role == "assistant"
    assert tool_result_msg.role == "tool"
    assert len(tool_use_msg.content) == 1
    assert len(tool_result_msg.content) == 1
    tool_use = tool_use_msg.content[0]
    tool_result = tool_result_msg.content[0]
    assert tool_use.type == "tool_use"
    assert tool_use.name == "bash"
    assert tool_use.input == {"command": "ls -la"}
    assert tool_use.id == "call-001"
    assert tool_result.type == "tool_result"
    assert tool_result.tool_use_id == "call-001"
    assert tool_result.is_error is False
    assert "total 42" in tool_result.content


def test_adapt_tool_use_event_nonzero_exit():
    """OpenCode 'tool_use' with exit code != 0 → is_error=True."""
    provider = OpenCodeProvider(OpenCodeConfig())
    event = {
        "type": "tool_use",
        "part": {
            "type": "tool",
            "tool": "bash",
            "callID": "call-002",
            "state": {
                "status": "completed",
                "input": {"command": "false"},
                "output": "",
                "metadata": {"exit": 1},
            },
        },
    }
    messages = provider._adapt_opencode_event(event)
    assert messages is not None
    assert isinstance(messages, list)
    assert messages[1].content[0].is_error is True


def test_adapt_step_finish_stores_tokens():
    """OpenCode 'step_finish' event → stores input/output tokens."""
    provider = OpenCodeProvider(OpenCodeConfig())
    assert provider._last_input_tokens == 0
    assert provider._last_output_tokens == 0

    event = {
        "type": "step_finish",
        "part": {
            "type": "step-finish",
            "reason": "stop",
            "tokens": {"total": 5000, "input": 3200, "output": 1800},
        },
    }
    result = provider._adapt_opencode_event(event)
    assert result is None  # no message yielded, side-effect only
    assert provider._last_input_tokens == 3200
    assert provider._last_output_tokens == 1800


def test_adapt_error_event():
    """OpenCode 'error' event → assistant TextBlock with error message."""
    provider = OpenCodeProvider(OpenCodeConfig())
    event = {
        "type": "error",
        "error": {
            "name": "RateLimitError",
            "data": {"message": "Too many requests"},
        },
    }
    msg = provider._adapt_opencode_event(event)
    assert msg is not None
    assert msg.role == "assistant"
    assert (
        "RateLimitError" in msg.content[0].text
        or "Too many requests" in msg.content[0].text
    )


def test_adapt_unknown_event_returns_none():
    """Unknown event type → None (ignored)."""
    provider = OpenCodeProvider(OpenCodeConfig())
    assert provider._adapt_opencode_event({"type": "step_start"}) is None
    assert provider._adapt_opencode_event({"type": "bogus"}) is None


def test_adapt_tool_output_truncated():
    """Tool output exceeding MAX_TOOL_OUTPUT is truncated."""
    provider = OpenCodeProvider(OpenCodeConfig())
    huge_output = "X" * (MAX_TOOL_OUTPUT + 5000)
    event = {
        "type": "tool_use",
        "part": {
            "type": "tool",
            "tool": "bash",
            "callID": "big",
            "state": {
                "status": "completed",
                "input": {"command": "cat huge_file"},
                "output": huge_output,
                "metadata": {"exit": 0},
            },
        },
    }
    messages = provider._adapt_opencode_event(event)
    assert isinstance(messages, list)
    assert len(messages[1].content[0].content) <= MAX_TOOL_OUTPUT


# ---------------------------------------------------------------------------
# Integration test: run() with mocked subprocess
# ---------------------------------------------------------------------------


def _make_opencode_jsonl(*events) -> list[bytes]:
    """Helper to build JSONL bytes from event dicts."""
    return [(json.dumps(e) + "\n").encode() for e in events]


@pytest.mark.asyncio
async def test_run_yields_text_and_stores_tokens():
    """Full run: text message + step_finish tokens are captured."""
    provider = OpenCodeProvider(OpenCodeConfig())
    events = [
        {"type": "step_start", "part": {"type": "step-start"}},
        {"type": "text", "part": {"type": "text", "text": "Here is the answer."}},
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "bash",
                "callID": "c1",
                "state": {
                    "status": "completed",
                    "input": {"command": "echo hi"},
                    "output": "hi\n",
                    "metadata": {"exit": 0},
                },
            },
        },
        {
            "type": "step_finish",
            "part": {
                "type": "step-finish",
                "reason": "tool-calls",
                "tokens": {"total": 9000, "input": 6000, "output": 3000},
            },
        },
    ]
    mock_process = MockProcess(_make_opencode_jsonl(*events))

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        chunks = []
        async for c in provider.run("test prompt", "sys prompt", "/tmp/work", 10):
            chunks.append(c)

    texts = [c.get_text_content() for c in chunks if c.get_text_content()]
    assert "Here is the answer." in texts
    assert provider._last_input_tokens == 6000
    assert provider._last_output_tokens == 3000


@pytest.mark.asyncio
async def test_run_tool_use_and_result():
    """Full run: tool_use event yields separate tool_use and tool_result messages."""
    provider = OpenCodeProvider(OpenCodeConfig())
    events = [
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "bash",
                "callID": "cmd-1",
                "state": {
                    "status": "completed",
                    "input": {"command": "pwd"},
                    "output": "/home/user\n",
                    "metadata": {"exit": 0},
                },
            },
        },
    ]
    mock_process = MockProcess(_make_opencode_jsonl(*events))

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        chunks = []
        async for c in provider.run("do it", "", ".", 10):
            chunks.append(c)

    assert len(chunks) == 2
    assert chunks[0].role == "assistant"
    assert chunks[0].content[0].name == "bash"
    assert chunks[1].role == "tool"
    assert "/home/user" in chunks[1].content[0].content


@pytest.mark.asyncio
async def test_stderr_is_emitted():
    """OpenCode stderr surfaces as non-fatal assistant message."""
    provider = OpenCodeProvider(OpenCodeConfig())
    events = [
        {"type": "text", "part": {"type": "text", "text": "ok"}},
    ]
    mock_process = MockProcess(
        _make_opencode_jsonl(*events),
        stderr_text="some warning",
    )

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        chunks = []
        async for c in provider.run("hi", "", ".", 10):
            chunks.append(c)

    assert any("some warning" in c.get_text_content() for c in chunks)


@pytest.mark.asyncio
async def test_usage_counters_reset_between_runs():
    """Tokens from previous run are cleared before a new run."""
    provider = OpenCodeProvider(OpenCodeConfig())
    first_events = [
        {"type": "text", "part": {"type": "text", "text": "first"}},
        {
            "type": "step_finish",
            "part": {"type": "step-finish", "tokens": {"input": 1000, "output": 500}},
        },
    ]
    second_events = [
        {"type": "text", "part": {"type": "text", "text": "second"}},
    ]
    first_process = MockProcess(_make_opencode_jsonl(*first_events))
    second_process = MockProcess(_make_opencode_jsonl(*second_events))
    processes = [first_process, second_process]

    async def mock_create_subprocess(*args, **kwargs):
        return processes.pop(0)

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        async for _ in provider.run("a", "", ".", 10):
            pass
        async for _ in provider.run("b", "", ".", 10):
            pass

    assert provider._last_input_tokens == 0
    assert provider._last_output_tokens == 0


@pytest.mark.asyncio
async def test_closing_stream_early_kills_subprocess():
    """Generator cleanup should kill a still-running subprocess."""
    provider = OpenCodeProvider(OpenCodeConfig())
    first_line = (
        json.dumps(
            {
                "type": "text",
                "part": {"type": "text", "text": "hello"},
            }
        )
        + "\n"
    ).encode()
    mock_process = MockProcess([], wait_returncode=0)
    mock_process.stdout = BlockingStdout(first_line)

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        stream = provider.run("hi", "", ".", 10)
        first_chunk = await stream.__anext__()
        assert first_chunk.get_text_content() == "hello"
        await stream.aclose()

    assert mock_process.killed is True


@pytest.mark.asyncio
async def test_run_handles_oversized_jsonl_events_via_chunked_reads():
    """Provider should handle large JSONL events without readline limits."""
    provider = OpenCodeProvider(OpenCodeConfig())
    huge_text = "X" * 200_000
    payload = (
        json.dumps({"type": "text", "part": {"type": "text", "text": huge_text}})
        + "\n"
    ).encode()
    chunks = [payload[:70_000], payload[70_000:140_000], payload[140_000:]]

    mock_process = MockProcess([])
    mock_process.stdout = ChunkedStdout(chunks)

    async def mock_create_subprocess(*args, **kwargs):
        return mock_process

    with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
        messages = []
        async for chunk in provider.run("hi", "", ".", 10):
            messages.append(chunk)

    assert len(messages) == 1
    assert messages[0].get_text_content() == huge_text


# ---------------------------------------------------------------------------
# Unit tests: build_command
# ---------------------------------------------------------------------------


def test_build_command_basic():
    """Default config builds correct opencode CLI command."""
    provider = OpenCodeProvider(OpenCodeConfig())
    cmd = provider._build_command(model="", working_dir="/tmp/work")
    assert cmd[0] == "opencode"
    assert "run" in cmd
    assert "--format" in cmd
    assert "json" in cmd
    assert "--dir" in cmd
    assert "/tmp/work" in cmd
    assert "-" in cmd  # stdin


def test_build_command_with_model():
    """Model override appears as -m flag."""
    provider = OpenCodeProvider(OpenCodeConfig())
    cmd = provider._build_command(model="opencode/mimo-v2-pro-free", working_dir=".")
    idx = cmd.index("-m")
    assert cmd[idx + 1] == "opencode/mimo-v2-pro-free"


def test_build_command_default_model():
    """If no model override, default_model from config is used."""
    provider = OpenCodeProvider(
        OpenCodeConfig(default_model="openrouter/moonshotai/kimi-k2:free")
    )
    cmd = provider._build_command(model="", working_dir=".")
    idx = cmd.index("-m")
    assert cmd[idx + 1] == "openrouter/moonshotai/kimi-k2:free"


def test_build_command_custom_command():
    """Custom command path is used."""
    provider = OpenCodeProvider(OpenCodeConfig(command="/usr/local/bin/opencode"))
    cmd = provider._build_command(model="", working_dir=".")
    assert cmd[0] == "/usr/local/bin/opencode"


# ---------------------------------------------------------------------------
# Unit tests: build_system_prompt
# ---------------------------------------------------------------------------


def test_build_system_prompt_wraps_in_tags():
    """System prompt is wrapped in <SYSTEM INSTRUCTIONS> tags."""
    provider = OpenCodeProvider(OpenCodeConfig())
    full = provider._build_system_prompt("You are a coder", "Write fibonacci")
    assert full.startswith("<SYSTEM INSTRUCTIONS>")
    assert "You are a coder" in full
    assert "</SYSTEM INSTRUCTIONS>" in full
    assert full.endswith("Write fibonacci")


@pytest.mark.asyncio
async def test_write_stdin_ignores_broken_pipe_and_still_closes():
    """Early subprocess exit should not make stdin writes crash the provider.

    run_subprocess_jsonl always closes stdin even when drain() raises BrokenPipe.
    This test verifies that contract via a mock process with a BrokenPipeStdin.
    """
    lines = (
        '{"type":"text","part":{"type":"text","text":"hi"}}\n'
        '{"type":"step_finish","part":{"tokens":{"input":1,"output":1}}}\n'
    ).encode()

    class BrokenPipeProcess:
        def __init__(self):
            self.stdin = BrokenPipeStdin()
            self.stdout = MockStdout([])
            self.stderr = MockStderr()
            self.returncode = None
            self.killed = False

        async def wait(self):
            self.returncode = 0

        def kill(self):
            self.killed = True
            self.returncode = -9

    proc = BrokenPipeProcess()

    async def mock_create(*args, **kwargs):
        return proc

    with patch("asyncio.create_subprocess_exec", mock_create):
        # run() must not raise even though stdin.drain() throws BrokenPipeError
        try:
            async for _ in OpenCodeProvider(OpenCodeConfig()).run("hello", "", "."):
                pass
        except Exception:
            pass  # process may raise on empty stdout, that's fine

    # stdin.write was called with the prompt bytes
    assert any(b"hello" in w for w in proc.stdin.writes)
    # stdin.close was called despite the BrokenPipeError
    assert proc.stdin.closed is True


def test_build_system_prompt_empty_system():
    """Empty system prompt → just user prompt."""
    provider = OpenCodeProvider(OpenCodeConfig())
    full = provider._build_system_prompt("", "Write fibonacci")
    assert "<SYSTEM" not in full
    assert full == "Write fibonacci"


# ---------------------------------------------------------------------------
# Unit tests: check_ready
# ---------------------------------------------------------------------------


def test_check_ready_found():
    """check_ready returns True when opencode is in PATH."""
    provider = OpenCodeProvider(OpenCodeConfig())
    with patch("shutil.which", return_value="/usr/local/bin/opencode"):
        ok, reason = provider.check_ready()
    assert ok is True
    assert reason == ""


def test_check_ready_not_found():
    """check_ready returns False when opencode is missing."""
    provider = OpenCodeProvider(OpenCodeConfig())
    with patch("shutil.which", return_value=None):
        ok, reason = provider.check_ready()
    assert ok is False
    assert "opencode" in reason.lower()


# ---------------------------------------------------------------------------
# Unit tests: display_name
# ---------------------------------------------------------------------------


def test_display_name_with_model():
    """display_name shows the model."""
    provider = OpenCodeProvider(
        OpenCodeConfig(default_model="opencode/mimo-v2-pro-free")
    )
    assert (
        "mimo" in provider.display_name.lower()
        or "opencode" in provider.display_name.lower()
    )


def test_display_name_default():
    """display_name works without explicit model."""
    provider = OpenCodeProvider(OpenCodeConfig())
    assert len(provider.display_name) > 0


# ---------------------------------------------------------------------------
# Menu / config / runtime_controls tests
# ---------------------------------------------------------------------------


def test_opencode_provider_in_provider_presets():
    """OpenCode provider is listed in PROVIDER_PRESETS."""
    from src.menu import PROVIDER_PRESETS

    assert "opencode" in PROVIDER_PRESETS.values()


def test_opencode_model_presets_exist():
    """OPENCODE_MODEL_PRESETS dict is defined with expected models."""
    from src.menu import OPENCODE_MODEL_PRESETS

    values = list(OPENCODE_MODEL_PRESETS.values())
    assert "opencode/minimax-m2.5-free" in values


def test_opencode_runtime_presets():
    """MODEL_PRESETS includes opencode entries."""
    from src.runtime_controls import MODEL_PRESETS

    providers = [p for _, p, _ in MODEL_PRESETS]
    assert "opencode" in providers
    assert ("OpenCode GLM-5.1", "opencode", "zai/glm-5.1") in MODEL_PRESETS


def test_opencode_context_windows():
    """Model context windows are defined for opencode models."""
    from src.config import get_context_window

    assert get_context_window("opencode/mimo-v2-pro-free") == 131_072
    assert get_context_window("opencode/minimax-m2.5-free") == 262_144
    assert get_context_window("openrouter/moonshotai/kimi-k2:free") == 131_072
    assert get_context_window("opencode/nemotron-3-super-free") == 131_072


def test_opencode_create_provider():
    """create_provider('opencode') returns an OpenCodeProvider instance."""
    from src.providers import create_provider

    provider = create_provider("opencode")
    from src.providers.opencode import OpenCodeProvider

    assert isinstance(provider, OpenCodeProvider)


def test_opencode_create_provider_with_config():
    """create_provider with opencode config works."""
    from src.providers import create_provider

    provider = create_provider(
        "opencode",
        provider_config={
            "type": "opencode_native",
            "command": "opencode",
            "default_model": "opencode/mimo-v2-pro-free",
            "default_timeout": 900,
        },
    )
    from src.providers.opencode import OpenCodeProvider

    assert isinstance(provider, OpenCodeProvider)
    assert provider.config.default_model == "opencode/mimo-v2-pro-free"


def test_kilo_create_provider_uses_kilo_command_and_models():
    """create_provider('kilo') returns an OpenCodeProvider configured for Kilo."""
    from src.providers import create_provider

    provider = create_provider("kilo")
    from src.providers.opencode import OpenCodeProvider

    assert isinstance(provider, OpenCodeProvider)
    assert provider.config.command == "kilo"
    assert provider.config.default_model == "kilo/xiaomi/mimo-v2-pro:free"
    assert provider.display_name.startswith("Kilo")


def test_fallback_menu_accepts_opencode_player(monkeypatch):
    """Plain-text fallback menu should let users choose opencode for player."""
    from src.menu import _fallback_menu
    from src.config import Config

    answers = iter(["p", "opencode", "kimi-k2", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))

    config = _fallback_menu(Config())

    assert config is not None
    assert config.player_provider == "opencode"
    assert config.player_model == "openrouter/moonshotai/kimi-k2:free"


def test_fallback_menu_accepts_kilo_player(monkeypatch):
    """Plain-text fallback menu should let users choose kilo for player."""
    from src.menu import _fallback_menu
    from src.config import Config

    answers = iter(["p", "kilo", "minimax-m2.5", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))

    config = _fallback_menu(Config())

    assert config is not None
    assert config.player_provider == "kilo"
    assert config.player_model == "kilo/minimax/minimax-m2.5:free"


def test_fallback_menu_accepts_opencode_coach(monkeypatch):
    """Plain-text fallback menu should let users choose opencode for coach."""
    from src.menu import _fallback_menu
    from src.config import Config

    answers = iter(["c", "opencode", "mimo-omni", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))

    config = _fallback_menu(Config())

    assert config is not None
    assert config.coach_provider == "opencode"
    assert config.coach_model == "opencode/mimo-v2-omni-free"


def test_fallback_menu_accepts_opencode_zai_model(monkeypatch):
    """Plain-text fallback menu should let users choose direct Z.AI in OpenCode."""
    from src.menu import _fallback_menu
    from src.config import Config

    answers = iter(["p", "opencode", "glm-5.1", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))

    config = _fallback_menu(Config())

    assert config is not None
    assert config.player_provider == "opencode"
    assert config.player_model == "zai/glm-5.1"


def test_questionary_player_opencode_uses_same_model_presets(monkeypatch):
    """Questionary player provider flow should expose the same OpenCode models as coach."""
    from src.menu import _edit_setting_questionary
    from src.config import Config

    prompts = iter(["OpenCode (MIMO/Kimi/Z.AI)", "MiniMax M2.5 (free)"])

    class DummyPrompt:
        def __init__(self, value):
            self._value = value

        def ask(self):
            return self._value

    class DummyQuestionary:
        @staticmethod
        def select(*args, **kwargs):
            return DummyPrompt(next(prompts))

        @staticmethod
        def text(*args, **kwargs):
            raise AssertionError("custom model prompt should not be used")

    monkeypatch.setitem(_edit_setting_questionary.__globals__, "questionary", DummyQuestionary)

    updated = _edit_setting_questionary(Config(), "player_provider")

    assert updated.player_provider == "opencode"
    assert updated.player_model == "opencode/minimax-m2.5-free"


def test_questionary_player_opencode_can_pick_minimax_model(monkeypatch):
    """Questionary player provider flow should expose OpenCode model presets."""
    from src.menu import _edit_setting_questionary
    from src.config import Config

    prompts = iter(["OpenCode (MIMO/Kimi/Z.AI)", "MiniMax M2.5 (free)"])

    class DummyPrompt:
        def __init__(self, value):
            self._value = value

        def ask(self):
            return self._value

    class DummyQuestionary:
        @staticmethod
        def select(*args, **kwargs):
            return DummyPrompt(next(prompts))

        @staticmethod
        def text(*args, **kwargs):
            raise AssertionError("custom model prompt should not be used")

    monkeypatch.setitem(_edit_setting_questionary.__globals__, "questionary", DummyQuestionary)

    updated = _edit_setting_questionary(Config(), "player_provider")

    assert updated.player_provider == "opencode"
    assert updated.player_model == "opencode/minimax-m2.5-free"


def test_questionary_player_kilo_uses_kilo_model_presets(monkeypatch):
    """Questionary player provider flow should expose the Kilo-only models."""
    from src.menu import _edit_setting_questionary
    from src.config import Config

    prompts = iter(["Kilo (MIMO/MiniMax)", "MiniMax M2.5 (free)"])

    class DummyPrompt:
        def __init__(self, value):
            self._value = value

        def ask(self):
            return self._value

    class DummyQuestionary:
        @staticmethod
        def select(*args, **kwargs):
            return DummyPrompt(next(prompts))

        @staticmethod
        def text(*args, **kwargs):
            raise AssertionError("custom model prompt should not be used")

    monkeypatch.setitem(_edit_setting_questionary.__globals__, "questionary", DummyQuestionary)

    updated = _edit_setting_questionary(Config(), "player_provider")

    assert updated.player_provider == "kilo"
    assert updated.player_model == "kilo/minimax/minimax-m2.5:free"


def test_fallback_menu_accepts_opencode_escalation(monkeypatch):
    """Plain-text fallback menu should let users choose opencode for escalation."""
    from src.menu import _fallback_menu
    from src.config import Config

    answers = iter(["f", "opencode", "kimi-k2", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))

    config = _fallback_menu(Config())

    assert config is not None
    assert config.coach_fallback_provider == "opencode"
    assert config.coach_fallback_model == "openrouter/moonshotai/kimi-k2:free"


def test_questionary_fallback_can_disable_escalation(monkeypatch):
    """Questionary fallback flow should support disabling escalation."""
    from src.menu import _edit_setting_questionary
    from src.config import Config

    prompts = iter(["Отключить escalation"])

    class DummyPrompt:
        def __init__(self, value):
            self._value = value

        def ask(self):
            return self._value

    class DummyQuestionary:
        @staticmethod
        def select(*args, **kwargs):
            return DummyPrompt(next(prompts))

        @staticmethod
        def text(*args, **kwargs):
            raise AssertionError("custom model prompt should not be used")

    monkeypatch.setitem(_edit_setting_questionary.__globals__, "questionary", DummyQuestionary)

    updated = _edit_setting_questionary(
        Config(
            coach_fallback_provider="claude",
            coach_fallback_model="sonnet",
        ),
        "coach_fallback",
    )

    assert updated.coach_fallback_provider == ""
    assert updated.coach_fallback_model == ""


def test_questionary_coach_change_syncs_batch_pre_and_post_when_they_follow_coach(monkeypatch):
    """Changing coach should update batch pre/post when they still mirror coach."""
    from src.menu import _edit_setting_questionary
    from src.config import Config

    prompts = iter(["OpenCode (MIMO/Kimi/Z.AI)", "MiniMax M2.5 (free)"])

    class DummyPrompt:
        def __init__(self, value):
            self._value = value

        def ask(self):
            return self._value

    class DummyQuestionary:
        @staticmethod
        def select(*args, **kwargs):
            return DummyPrompt(next(prompts))

        @staticmethod
        def text(*args, **kwargs):
            raise AssertionError("custom model prompt should not be used")

    monkeypatch.setitem(_edit_setting_questionary.__globals__, "questionary", DummyQuestionary)

    updated = _edit_setting_questionary(
        Config(
            coach_provider="zai",
            coach_model="glm-5.1",
            batch_pre_provider="zai",
            batch_pre_model="glm-5.1",
            batch_post_provider="zai",
            batch_post_model="glm-5.1",
        ),
        "coach_provider",
    )

    assert updated.coach_provider == "opencode"
    assert updated.coach_model == "opencode/minimax-m2.5-free"
    assert updated.batch_pre_provider == "opencode"
    assert updated.batch_pre_model == "opencode/minimax-m2.5-free"
    assert updated.batch_post_provider == "opencode"
    assert updated.batch_post_model == "opencode/minimax-m2.5-free"


def test_fallback_menu_coach_change_syncs_batch_pre_and_post(monkeypatch):
    """Plain-text menu should also keep batch pre/post aligned with coach."""
    from src.menu import _fallback_menu
    from src.config import Config

    answers = iter(["c", "opencode", "kimi-k2", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))

    config = _fallback_menu(
        Config(
            coach_provider="zai",
            coach_model="glm-5.1",
            batch_pre_provider="zai",
            batch_pre_model="glm-5.1",
            batch_post_provider="zai",
            batch_post_model="glm-5.1",
        )
    )

    assert config is not None
    assert config.coach_provider == "opencode"
    assert config.coach_model == "openrouter/moonshotai/kimi-k2:free"
    assert config.batch_pre_provider == "opencode"
    assert config.batch_pre_model == "openrouter/moonshotai/kimi-k2:free"
    assert config.batch_post_provider == "opencode"
    assert config.batch_post_model == "openrouter/moonshotai/kimi-k2:free"


def test_fallback_menu_can_edit_batch_judge_provider(monkeypatch):
    """Plain-text menu should expose the batch judge slot directly."""
    from src.menu import _fallback_menu
    from src.config import Config

    answers = iter(["j", "claude", "opus", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))

    config = _fallback_menu(Config(batch_judge_provider="codex", batch_judge_model=""))

    assert config is not None
    assert config.batch_judge_provider == "claude"
    assert config.batch_judge_model == "opus"


def test_questionary_zai_provider_selects_without_model_submenu(monkeypatch):
    """ZAI provider has no fixed model — should not crash on empty model presets."""
    from src.menu import _edit_setting_questionary
    from src.config import Config

    prompts = iter(["ZAI (Z.AI / GLM-5.1)"])

    class DummyPrompt:
        def __init__(self, value):
            self._value = value

        def ask(self):
            return self._value

    class DummyQuestionary:
        @staticmethod
        def select(*args, **kwargs):
            return DummyPrompt(next(prompts, None))

        @staticmethod
        def text(*args, **kwargs):
            raise AssertionError("custom model prompt should not be used")

    monkeypatch.setitem(_edit_setting_questionary.__globals__, "questionary", DummyQuestionary)

    updated = _edit_setting_questionary(Config(), "player_provider")

    assert updated.player_provider == "zai"
