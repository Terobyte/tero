"""Centralized constants for tero.

Replaces magic numbers scattered across the codebase with named constants.
Import what you need:  from src.constants import DEFAULT_PLAYER_TIMEOUT_S
"""

from __future__ import annotations

# ── Timeouts (seconds) ────────────────────────────────────────────────

DEFAULT_PLAYER_TIMEOUT_S = 600
DEFAULT_COACH_TIMEOUT_S = 300
DEFAULT_DUEL_TIMEOUT_S = 600
DEFAULT_CHAIN_RETRY_WAIT_S = 60.0
DEFAULT_PROVIDER_TIMEOUT_S = 900  # codex / opencode default
CANCEL_DELAY_S = 5.0  # auto-cancel picker after idle

COMPILE_CHECK_TIMEOUT_S = 10
LINT_CHECK_TIMEOUT_S = 30
TYPE_CHECK_TIMEOUT_S = 60
TEST_CHECK_TIMEOUT_S = 60
VERSION_CHECK_TIMEOUT_S = 10
PGREP_TIMEOUT_S = 5

# ── Turn / attempt limits ─────────────────────────────────────────────

DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_ROUNDS = 3
DEFAULT_BATCH_PRE_JUDGE_ATTEMPTS = 5
DEFAULT_BATCH_JUDGE_ATTEMPTS = 2
DEFAULT_BATCH_POST_JUDGE_ATTEMPTS = 3
PLAYER_ESCALATION_SONNET_MODEL = "claude-sonnet-4-6"
PLAYER_ESCALATION_OPUS_MODEL = "claude-opus-4-7"
DEFAULT_COACH_RETRY_MAX = 2
DEFAULT_MAX_CONTINUATION_ATTEMPTS = 2
DEFAULT_MAX_REVIEW_ITERATIONS = 3
DEFAULT_CHAIN_MAX_RETRIES = 2
DEFAULT_LDB_LIMIT_VALUE = 10
DEFAULT_LDB_TIMEOUT_S = 30

PROTOCOL_DEFAULT_MAX_TURNS = 30  # AgentProvider protocol default
BATCH_REVIEW_MAX_TURNS = 4
PLAYER_MAX_TURNS = 100
COACH_MAX_TURNS = 8
CODE_REVIEWER_MAX_TURNS = 8
BUG_FINDER_MAX_TURNS = 1
COMPACT_CODEX_MAX_TURNS = 3

# ── Buffer / size limits ──────────────────────────────────────────────

MAX_BUFFER_MSGS = 10_000  # runaway message detection
MAX_TOOL_OUTPUT_CHARS = 8_000  # tool result truncation
LARGE_PROMPT_THRESHOLD_BYTES = 64_000  # temp-file env threshold
STDOUT_READ_CHUNK_SIZE = 65_536
STREAM_READER_LIMIT = 16 * 1024 * 1024  # 16 MB

MAX_CONTEXT_CHARS = 200_000  # ~50K tokens
BUDGET_CHARS_PER_FILE = 8_000
LARGE_FILE_LINE_THRESHOLD = 200
MAX_SYMBOLS = 200
SHORT_HELPER_LINE_THRESHOLD = 15
SEMANTIC_HELPER_LINE_THRESHOLD = 40
STRUCTURE_OVERVIEW_RATIO = 2.0
MAX_CONTEXT_WINDOWS = 5

MAX_PLAN_STEPS = 100

# ── Display / truncation ──────────────────────────────────────────────

STREAM_TEXT_TRUNCATE = 200
STREAM_RESULT_TRUNCATE = 100
STREAM_TEST_LINES = 10
PROGRESS_BAR_WIDTH = 20
DESCRIPTION_NORM_LENGTH = 60
PLAYER_SUMMARY_TRUNCATE = 2000

# ── Thresholds / fractions ────────────────────────────────────────────

DEFAULT_COMPACT_THRESHOLD = 0.85
COMPACT_THRESHOLD_FLOOR = 0.1
COMPACT_THRESHOLD_CEIL = 0.9
ANIMATION_SLEEP_S = 0.12
KEYBOARD_SELECT_TIMEOUT_S = 0.1
KEYBOARD_ESC_TIMEOUT_S = 0.1
KEYBOARD_BRACKET_TIMEOUT_S = 0.05

# ── Budget defaults (USD) ─────────────────────────────────────────────

DEFAULT_DAILY_BUDGET = 10.0
DEFAULT_MONTHLY_BUDGET = 200.0

# ── Context window sizes (tokens) ────────────────────────────────────

DEFAULT_CONTEXT_LIMIT = 110_000
CLAUDE_CODE_ASSUMED_WINDOW = 200_000

# ── Exit codes ────────────────────────────────────────────────────────

EXIT_AGENT_TIMEOUT = 124
EXIT_NO_TESTS_COLLECTED = 5

# ── Logging ───────────────────────────────────────────────────────────

LOG_ROTATION = "10 MB"
LOG_RETENTION = "7 days"

# ── Misc ──────────────────────────────────────────────────────────────

HISTORY_DEFAULT_LIMIT = 10
NEGATIVE_PREFIX_WINDOW = 40
PLAYER_PROMPT_LABELS = 5
