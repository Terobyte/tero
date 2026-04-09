import os
import pytest
from src.providers.codex import CodexProvider, CodexConfig
from src.providers.message_adapter import TextBlock

SMALL_PROMPT = "You are a helpful assistant."
LARGE_PROMPT = "x" * 100_000  # 100KB — above 64KB threshold


class TestLargeSystemPrompt:
    def test_small_prompt_uses_env_var_directly(self, monkeypatch):
        """Below threshold: CODEX_INSTRUCTIONS = literal prompt."""
        monkeypatch.delenv("CODEX_INSTRUCTIONS", raising=False)
        provider = CodexProvider(CodexConfig())
        env = provider._build_env(SMALL_PROMPT)
        assert env["CODEX_INSTRUCTIONS"] == SMALL_PROMPT
        assert provider._temp_instructions_path is None

    def test_large_prompt_writes_temp_file(self, monkeypatch):
        """Above threshold: prompt written to temp file, env var is a reference."""
        monkeypatch.delenv("CODEX_INSTRUCTIONS", raising=False)
        provider = CodexProvider(CodexConfig())
        env = provider._build_env(LARGE_PROMPT)

        # Env var should NOT contain the full prompt
        assert len(env["CODEX_INSTRUCTIONS"]) < 1000

        # Temp file should exist and contain full prompt
        tmp_path = provider._temp_instructions_path
        assert tmp_path is not None
        assert os.path.exists(tmp_path)
        with open(tmp_path) as f:
            assert f.read() == LARGE_PROMPT

        # Env var should reference the temp file path
        assert tmp_path in env["CODEX_INSTRUCTIONS"]

        # Cleanup
        provider._cleanup_temp_instructions()
        assert not os.path.exists(tmp_path)

    def test_cleanup_is_idempotent(self):
        """Calling cleanup twice doesn't error."""
        provider = CodexProvider(CodexConfig())
        provider._build_env(LARGE_PROMPT)
        provider._cleanup_temp_instructions()
        provider._cleanup_temp_instructions()  # No error

    def test_empty_prompt_no_env_var(self, monkeypatch):
        """Empty prompt: CODEX_INSTRUCTIONS not set."""
        monkeypatch.delenv("CODEX_INSTRUCTIONS", raising=False)
        provider = CodexProvider(CodexConfig())
        env = provider._build_env("")
        assert env.get("CODEX_INSTRUCTIONS", "") == ""


class TestCodexTranscriptGuarantee:
    def test_turn_completed_yields_text(self):
        """turn.completed must produce a TextBlock so transcript is non-empty."""
        provider = CodexProvider(CodexConfig())
        event = {
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        result = provider._adapt_codex_event(event)
        assert result is not None
        assert result.role == "assistant"
        # Must have at least one TextBlock
        text_blocks = [b for b in result.content if isinstance(b, TextBlock)]
        assert len(text_blocks) >= 1
        assert text_blocks[0].text  # Non-empty

    def test_agent_message_yields_text(self):
        """agent_message events must produce text (existing behavior)."""
        provider = CodexProvider(CodexConfig())
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "I'll edit the file now."},
        }
        result = provider._adapt_codex_event(event)
        assert result is not None
        assert "edit the file" in result.content[0].text
