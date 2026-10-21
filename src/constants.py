"""Shared constants for providers."""

from __future__ import annotations

DEFAULT_PROVIDER_TIMEOUT_S = 900  # codex / opencode default
DEFAULT_COMPACT_THRESHOLD = 0.85

LARGE_PROMPT_THRESHOLD_BYTES = 64_000  # temp-file env threshold
MAX_TOOL_OUTPUT_CHARS = 8_000  # tool result truncation
STDOUT_READ_CHUNK_SIZE = 65_536
STREAM_READER_LIMIT = 16 * 1024 * 1024  # 16 MB
