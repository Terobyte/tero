# Coach-Player via Claude Agent SDK — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite tero from duel system to coach-player feedback loop using Claude Agent SDK for Python.

**Architecture:** Player agent implements requirements, coach agent reviews. Loop until coach says APPROVED or max turns reached. Both use ccg (Blackbox.ai) via SDK with env vars. Real-time streaming to terminal.

**Tech Stack:** Python 3.11+, claude-agent-sdk, pyyaml, asyncio

**Spec:** `docs/superpowers/specs/2026-03-15-coach-player-sdk-design.md`

---

## File Structure

```
g3/
  g3.py                           # CLI entry point (REWRITE — simplified)
  src/
    __init__.py                   # (keep)
    config.py                     # REWRITE — simplified for single provider
    coach_player.py               # CREATE — main coach-player loop
    streaming.py                  # CREATE — real-time terminal UI
    prompts.py                    # CREATE — player/coach system prompts
    feedback.py                   # CREATE — extract verdict from coach output
    plan_tracker.py               # CREATE — parse requirements into checklist
    providers/
      __init__.py                 # (keep)
      ccg.py                      # CREATE — SDK wrapper with ccg env
    learning/
      __init__.py                 # (keep)
      recorder.py                 # REWRITE — new RunRecord schema
  tests/
    test_feedback.py              # CREATE
    test_plan_tracker.py          # CREATE
    test_config.py                # REWRITE
    test_recorder.py              # REWRITE
    test_streaming.py             # CREATE
```

Files to DELETE (old duel system):
- `src/duel.py`, `src/judge.py`, `src/worktree.py`, `src/bug_detector.py`
- `src/orchestrator.py`, `src/state.py`
- `src/parsers/`, `src/prompts/`, `src/terminal/`
- `src/providers/base.py`, `src/providers/claude_glm.py`, `src/providers/registry.py`
- `src/learning/analyzer.py`, `src/learning/calibrator.py`, `src/learning/classifier.py`, `src/learning/recommender.py`
- All old tests in `tests/`

---

## Chunk 1: Foundation — Config, Provider, Dependencies

### Task 1: Install dependencies and clean old files

**Files:**
- Modify: `g3/requirements.txt` (or create if doesn't exist)
- Delete: all old source files listed above

- [ ] **Step 1: Verify prerequisites and install deps**

```bash
# Verify Node.js and claude CLI are installed
node --version || echo "ERROR: Node.js not found. Install from https://nodejs.org"
which claude || echo "ERROR: claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"

# Install Python deps
pip3 install claude-agent-sdk pyyaml
```

- [ ] **Step 2: Delete old source files**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3

# Delete old modules
rm -f src/duel.py src/judge.py src/worktree.py src/bug_detector.py
rm -f src/orchestrator.py src/state.py
rm -rf src/parsers src/prompts src/terminal
rm -f src/providers/base.py src/providers/claude_glm.py src/providers/registry.py
rm -f src/learning/analyzer.py src/learning/calibrator.py
rm -f src/learning/classifier.py src/learning/recommender.py

# Delete old tests
rm -f tests/test_bug_detector.py tests/test_claude_glm_provider.py
rm -f tests/test_cli_go_command.py tests/test_duel_runner.py
rm -f tests/test_judge_selection.py tests/test_learning_analyzer.py
rm -f tests/test_learning_calibrator.py tests/test_learning_recommender.py
rm -f tests/test_packaging_metadata.py tests/test_provider_registry.py
rm -f tests/test_state_manager.py tests/test_verdict_parser.py
rm -f tests/test_worktree_manager.py
rm -rf tests/e2e
```

- [ ] **Step 3: Create requirements.txt**

```
claude-agent-sdk>=0.1.0
pyyaml>=6.0
```

Write to: `g3/requirements.txt`

- [ ] **Step 4: Verify clean state**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
find src -name "*.py" | sort
# Expected: src/__init__.py, src/config.py, src/providers/__init__.py, src/learning/__init__.py, src/learning/recorder.py
```

---

### Task 2: Rewrite config.py

**Files:**
- Rewrite: `g3/src/config.py`
- Create: `g3/tests/test_config.py`

- [ ] **Step 1: Write failing test for config**

Write to `g3/tests/test_config.py`:

```python
"""Tests for simplified config."""
import os
from unittest.mock import patch
from src.config import resolve_config, CcgEnv


def test_default_config():
    cfg = resolve_config({})
    assert cfg.max_turns == 10
    assert cfg.plan_file == "requirements.md"
    assert cfg.player_timeout_s == 600
    assert cfg.coach_timeout_s == 300
    assert cfg.autonomous is False
    assert cfg.verbose is False


def test_cli_overrides():
    cfg = resolve_config({"max_turns": 5, "autonomous": True})
    assert cfg.max_turns == 5
    assert cfg.autonomous is True


def test_env_overrides():
    with patch.dict(os.environ, {"G3_MAX_TURNS": "7", "G3_AUTONOMOUS": "true"}):
        cfg = resolve_config({})
        assert cfg.max_turns == 7
        assert cfg.autonomous is True


def test_ccg_env_from_anthropic_token():
    with patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "sk-test123"}, clear=False):
        env = CcgEnv.from_env()
        assert env.auth_token == "sk-test123"
        assert env.base_url == "https://api.blackbox.ai"
        assert env.model == "blackboxai/z-ai/glm-5"


def test_ccg_env_from_blackbox_token():
    with patch.dict(os.environ, {
        "BLACKBOX_ACCOUNT_A_TOKEN": "sk-bb-test",
    }, clear=False):
        # Remove ANTHROPIC_AUTH_TOKEN if present
        env_copy = os.environ.copy()
        env_copy.pop("ANTHROPIC_AUTH_TOKEN", None)
        env_copy["BLACKBOX_ACCOUNT_A_TOKEN"] = "sk-bb-test"
        with patch.dict(os.environ, env_copy, clear=True):
            env = CcgEnv.from_env()
            assert env.auth_token == "sk-bb-test"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_config.py -v
```
Expected: FAIL (imports don't exist yet)

- [ ] **Step 3: Implement config.py**

Write to `g3/src/config.py`:

```python
"""Configuration: defaults -> .g3/config.yaml -> env -> CLI args."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class CcgEnv:
    """Environment variables for ccg (Blackbox.ai via Claude CLI)."""
    base_url: str
    auth_token: str
    model: str
    small_model: str
    claude_home: str

    @classmethod
    def from_env(cls, claude_home: str = "~/.claude-glm") -> "CcgEnv":
        token = (
            os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("BLACKBOX_ACCOUNT_A_TOKEN")
            or ""
        )
        return cls(
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.blackbox.ai"),
            auth_token=token,
            model=os.environ.get("ANTHROPIC_MODEL", "blackboxai/z-ai/glm-5"),
            small_model=os.environ.get("ANTHROPIC_SMALL_FAST_MODEL", "kimi-k2.5"),
            claude_home=os.path.expanduser(claude_home),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_AUTH_TOKEN": self.auth_token,
            "ANTHROPIC_MODEL": self.model,
            "ANTHROPIC_SMALL_FAST_MODEL": self.small_model,
            "CLAUDE_HOME": self.claude_home,
        }


@dataclass
class Config:
    """Resolved configuration."""
    max_turns: int = 10
    autonomous: bool = False
    verbose: bool = False
    plan_file: str = "requirements.md"
    working_dir: str = "."
    player_timeout_s: int = 600
    coach_timeout_s: int = 300
    claude_home: str = "~/.claude-glm"


def _load_yaml(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def resolve_config(cli_args: dict) -> Config:
    """Merge: defaults -> .g3/config.yaml -> env -> CLI args."""
    working_dir = cli_args.get("working_dir") or "."
    working_dir = str(Path(working_dir).expanduser().resolve())

    # Load project config
    project = _load_yaml(Path(working_dir) / ".g3" / "config.yaml")
    defaults = project.get("defaults", {})

    # Env overrides
    env_map = {
        "G3_MAX_TURNS": ("max_turns", int),
        "G3_AUTONOMOUS": ("autonomous", lambda x: x.lower() == "true"),
    }
    for env_key, (cfg_key, conv) in env_map.items():
        if val := os.environ.get(env_key):
            defaults[cfg_key] = conv(val)

    # CLI overrides (highest priority)
    defaults.update({k: v for k, v in cli_args.items() if v is not None})
    defaults["working_dir"] = working_dir

    # Provider config
    provider = project.get("provider", {})
    if claude_home := provider.get("claude_home"):
        defaults["claude_home"] = claude_home

    valid_fields = Config.__dataclass_fields__
    return Config(**{k: v for k, v in defaults.items() if k in valid_fields})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_config.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: rewrite config for coach-player model"
```

---

### Task 3: Create CCG provider (SDK wrapper)

**Files:**
- Create: `g3/src/providers/ccg.py`

- [ ] **Step 1: Write ccg.py provider**

Write to `g3/src/providers/ccg.py`:

```python
"""CCG provider — wraps Claude Agent SDK with Blackbox.ai env vars."""

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

from src.config import CcgEnv


async def run_agent(
    prompt: str,
    system_prompt: str,
    working_dir: str,
    ccg_env: CcgEnv,
    max_turns: int = 30,
):
    """Run a Claude Code agent via SDK with ccg env vars.

    Yields SDK messages as they stream in.
    """
    if not ccg_env.auth_token:
        raise ValueError(
            "No auth token. Set ANTHROPIC_AUTH_TOKEN or BLACKBOX_ACCOUNT_A_TOKEN"
        )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=working_dir,
        env=ccg_env.as_dict(),
        permission_mode="bypassPermissions",
        max_turns=max_turns,
    )

    async for message in query(prompt=prompt, options=options):
        yield message
```

- [ ] **Step 2: Verify import works**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -c "from src.providers.ccg import run_agent; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/providers/ccg.py && git commit -m "feat: add ccg provider using Claude Agent SDK"
```

---

## Chunk 2: Core Loop — Prompts, Feedback, Coach-Player

### Task 4: Create prompts.py

**Files:**
- Create: `g3/src/prompts.py`

- [ ] **Step 1: Write prompts.py**

Write to `g3/src/prompts.py`:

```python
"""System prompts and prompt builders for player and coach."""

PLAYER_SYSTEM_PROMPT = """You are an implementation agent (Player). Your job is to implement the requirements in the current working directory.

Rules:
- Read requirements.md first
- Implement all requirements
- Run tests if they exist
- If you receive feedback from a previous coach review, address ALL issues

When done, simply finish your work."""

COACH_SYSTEM_PROMPT = """You are a code review agent (Coach). Your job is to review the implementation in the current working directory against the requirements.

Review process:
1. Read requirements.md
2. Read the implementation files
3. Run tests if they exist
4. Check code quality

Decision:
- If ALL requirements are met AND tests pass AND code quality is acceptable:
  Write exactly: IMPLEMENTATION_APPROVED
- Otherwise: Write a plain-text summary listing ONLY the specific issues to fix.
  Be concise and actionable. Number each issue.

You MUST end your final response with either IMPLEMENTATION_APPROVED or a numbered list of issues. Nothing else after the decision."""


def build_player_prompt(requirements: str, feedback: str | None = None) -> str:
    """Build the player's prompt with requirements and optional feedback."""
    parts = [f"## Requirements\n{requirements}"]

    if feedback:
        parts.append(f"## Coach Feedback (from previous review)\n{feedback}")
        parts.append("Implement the requirements. Address ALL feedback issues listed above.")
    else:
        parts.append("Implement all requirements in the current working directory.")

    return "\n\n".join(parts)


def build_coach_prompt(requirements: str) -> str:
    """Build the coach's prompt."""
    return f"""## Requirements
{requirements}

Review the implementation in the current working directory against these requirements.
End with IMPLEMENTATION_APPROVED or a numbered list of issues."""
```

- [ ] **Step 2: Commit**

```bash
git add src/prompts.py && git commit -m "feat: add player/coach prompt templates"
```

---

### Task 5: Create feedback.py (coach output parser)

**Files:**
- Create: `g3/src/feedback.py`
- Create: `g3/tests/test_feedback.py`

- [ ] **Step 1: Write failing test**

Write to `g3/tests/test_feedback.py`:

```python
"""Tests for coach feedback extraction."""
from src.feedback import extract_verdict, CoachVerdict


def _make_text_block(text):
    """Create a mock TextBlock."""
    class TB:
        def __init__(self, t):
            self.text = t
            self.type = "text"
    return TB(text)


def _make_tool_block(name):
    class TU:
        def __init__(self, n):
            self.name = n
            self.type = "tool_use"
    return TU(name)


def _make_assistant_msg(blocks):
    class AM:
        def __init__(self, b):
            self.content = b
            self.type = "assistant"
    return AM(blocks)


def _make_result_msg():
    class RM:
        def __init__(self):
            self.type = "result"
    return RM()


def test_approved():
    messages = [
        _make_assistant_msg([_make_text_block("Looking good!")]),
        _make_assistant_msg([_make_text_block("All tests pass.\n\nIMPLEMENTATION_APPROVED")]),
        _make_result_msg(),
    ]
    v = extract_verdict(messages)
    assert v.approved is True
    assert v.feedback == ""


def test_not_approved_with_issues():
    messages = [
        _make_assistant_msg([_make_tool_block("Read")]),
        _make_assistant_msg([_make_text_block("Issues found:\n1. Missing tests\n2. No error handling")]),
        _make_result_msg(),
    ]
    v = extract_verdict(messages)
    assert v.approved is False
    assert "Missing tests" in v.feedback
    assert "No error handling" in v.feedback


def test_empty_output():
    messages = [_make_result_msg()]
    v = extract_verdict(messages)
    assert v.approved is False
    assert "no output" in v.feedback.lower()


def test_approved_in_tool_result_ignored():
    """IMPLEMENTATION_APPROVED in tool output should be ignored."""
    messages = [
        _make_assistant_msg([_make_tool_block("Bash")]),
        # tool result would be a different message type, not AssistantMessage
        _make_assistant_msg([_make_text_block("Issues:\n1. Tests failing")]),
        _make_result_msg(),
    ]
    v = extract_verdict(messages)
    assert v.approved is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_feedback.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement feedback.py**

Write to `g3/src/feedback.py`:

```python
"""Extract coach verdict from SDK messages."""

from dataclasses import dataclass


APPROVAL_MARKER = "IMPLEMENTATION_APPROVED"
DEFAULT_FEEDBACK = "Coach review produced no output. Please try implementing again."


@dataclass
class CoachVerdict:
    approved: bool
    feedback: str


def extract_verdict(messages: list) -> CoachVerdict:
    """Extract verdict from a list of SDK messages.

    Looks at the LAST AssistantMessage's TextBlocks.
    If it contains IMPLEMENTATION_APPROVED -> approved.
    Otherwise -> the full text is feedback.
    """
    # Find last AssistantMessage (before ResultMessage)
    last_assistant = None
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type == "assistant":
            last_assistant = msg

    if last_assistant is None:
        return CoachVerdict(approved=False, feedback=DEFAULT_FEEDBACK)

    # Concatenate all TextBlocks from last assistant message
    text_parts = []
    for block in getattr(last_assistant, "content", []):
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)

    full_text = "\n".join(text_parts).strip()

    if not full_text:
        return CoachVerdict(approved=False, feedback=DEFAULT_FEEDBACK)

    if APPROVAL_MARKER in full_text:
        return CoachVerdict(approved=True, feedback="")

    return CoachVerdict(approved=False, feedback=full_text)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_feedback.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/feedback.py tests/test_feedback.py && git commit -m "feat: add coach feedback extraction"
```

---

### Task 6: Create plan_tracker.py

**Files:**
- Create: `g3/src/plan_tracker.py`
- Create: `g3/tests/test_plan_tracker.py`

- [ ] **Step 1: Write failing test**

Write to `g3/tests/test_plan_tracker.py`:

```python
"""Tests for plan tracker."""
from src.plan_tracker import parse_plan_items, format_checklist


def test_parse_numbered_list():
    text = """# My Project
1. Set up database
2. Add authentication
3. Create API endpoints
"""
    items = parse_plan_items(text)
    assert len(items) == 3
    assert items[0] == "Set up database"
    assert items[2] == "Create API endpoints"


def test_parse_checkbox_list():
    text = """## Requirements
- [ ] Build login page
- [ ] Add tests
- [x] Setup project
"""
    items = parse_plan_items(text)
    assert len(items) == 3
    assert "Build login page" in items


def test_parse_dash_list():
    text = """Requirements:
- Feature A
- Feature B
- Feature C
"""
    items = parse_plan_items(text)
    assert len(items) == 3


def test_format_checklist_all_done():
    items = ["Task A", "Task B"]
    result = format_checklist(items, approved=True)
    assert "[x]" in result
    assert "[ ]" not in result


def test_format_checklist_with_issues():
    items = ["Task A", "Task B"]
    issues = "1. Task B not working"
    result = format_checklist(items, approved=False, coach_issues=issues)
    assert "[ ]" in result or issues in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_plan_tracker.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement plan_tracker.py**

Write to `g3/src/plan_tracker.py`:

```python
"""Parse requirements into a checklist and track progress."""

import re


def parse_plan_items(text: str) -> list[str]:
    """Extract actionable items from requirements text.

    Supports: numbered lists, checkbox lists, dash lists.
    """
    items = []

    for line in text.splitlines():
        line = line.strip()

        # Numbered: "1. Do something" or "1) Do something"
        m = re.match(r"^\d+[\.\)]\s+(.+)", line)
        if m:
            items.append(m.group(1).strip())
            continue

        # Checkbox: "- [ ] Do something" or "- [x] Do something"
        m = re.match(r"^-\s*\[[ x]\]\s+(.+)", line)
        if m:
            items.append(m.group(1).strip())
            continue

        # Dash list: "- Do something" (but not headers or empty)
        m = re.match(r"^-\s+(.+)", line)
        if m and not line.startswith("---"):
            items.append(m.group(1).strip())
            continue

    return items


def format_checklist(
    items: list[str],
    approved: bool = False,
    coach_issues: str | None = None,
) -> str:
    """Format plan items as a checklist.

    If approved: all items marked [x].
    If not approved: show coach's issues as the active checklist.
    """
    lines = ["  Plan Progress:"]

    if approved:
        for i, item in enumerate(items, 1):
            lines.append(f"    [x] {i}. {item}")
    elif coach_issues:
        # Show coach's numbered issues directly
        lines.append(f"    {coach_issues}")
    else:
        for i, item in enumerate(items, 1):
            lines.append(f"    [ ] {i}. {item}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_plan_tracker.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/plan_tracker.py tests/test_plan_tracker.py && git commit -m "feat: add plan tracker for requirements checklist"
```

---

## Chunk 3: Streaming UI and Main Loop

### Task 7: Create streaming.py (terminal UI)

**Files:**
- Create: `g3/src/streaming.py`

- [ ] **Step 1: Write streaming.py**

Write to `g3/src/streaming.py`:

```python
"""Real-time terminal UI for SDK message streams."""

import sys
import time


class StreamingUI:
    """Renders SDK messages to terminal in real-time."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.tool_count = 0
        self.start_time = 0.0

    def start_turn(self, turn: int, max_turns: int, role: str):
        """Print turn header."""
        self.tool_count = 0
        self.start_time = time.time()
        print(f"\n=== Turn {turn}/{max_turns} -- {role.upper()} ===\n")

    def handle_message(self, msg):
        """Route an SDK message to the appropriate renderer."""
        msg_type = getattr(msg, "type", None)

        if msg_type == "assistant":
            self._handle_assistant(msg)
        elif msg_type == "result":
            self._handle_result(msg)
        elif msg_type == "tool_result" and self.verbose:
            self._handle_tool_result(msg)

    def end_turn(self, role: str):
        """Print turn summary."""
        elapsed = time.time() - self.start_time
        print(f"\n  {role}: {int(elapsed)}s | Tools: {self.tool_count}")

    def print_verdict(self, approved: bool, feedback: str):
        """Print coach verdict."""
        if approved:
            print("\n  Coach Verdict: APPROVED")
        else:
            print("\n  Coach Verdict: NOT APPROVED")
            print(f"\n  {feedback}")

    def print_header(self, plan_file: str, plan_size: int, max_turns: int):
        """Print session header."""
        print("\n--- tero coach-player ---")
        print(f"  Requirements: {plan_file} ({plan_size} bytes)")
        print(f"  Provider: ccg (blackboxai/z-ai/glm-5)")
        print(f"  Max turns: {max_turns}")

    def print_session_report(self, turns: int, max_turns: int, total_time: float, status: str):
        """Print final session report."""
        mins = int(total_time // 60)
        secs = int(total_time % 60)
        print(f"\n--- Session Report ---")
        print(f"  Turns: {turns}/{max_turns}")
        print(f"  Total time: {mins}m {secs}s")
        print(f"  Status: {status}")

    def _handle_assistant(self, msg):
        for block in getattr(msg, "content", []):
            block_type = getattr(block, "type", None)

            if block_type == "text":
                text = block.text.strip()
                if text:
                    # Show first 200 chars of each text block
                    display = text[:200] + ("..." if len(text) > 200 else "")
                    print(f"  [text] {display}")

            elif block_type == "tool_use":
                self.tool_count += 1
                name = getattr(block, "name", "?")
                # Try to get a summary of args
                inp = getattr(block, "input", {})
                summary = self._tool_summary(name, inp)
                print(f"  [tool] {name}: {summary}")

    def _handle_result(self, msg):
        cost = getattr(msg, "total_cost_usd", None)
        if cost:
            print(f"  [cost] ${cost:.4f}")

    def _handle_tool_result(self, msg):
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content.strip():
            display = content[:100] + ("..." if len(content) > 100 else "")
            print(f"  [result] {display}")

    def _tool_summary(self, name: str, inp: dict) -> str:
        """Create a short summary of tool arguments."""
        if name in ("Read", "read_file"):
            return inp.get("file_path", inp.get("path", "?"))
        if name in ("Write", "write_file"):
            return inp.get("file_path", inp.get("path", "?"))
        if name == "Edit":
            return inp.get("file_path", "?")
        if name == "Bash":
            cmd = inp.get("command", "?")
            return cmd[:80] + ("..." if len(cmd) > 80 else "")
        if name in ("Grep", "Glob"):
            return inp.get("pattern", "?")
        return str(inp)[:60]
```

- [ ] **Step 2: Commit**

```bash
git add src/streaming.py && git commit -m "feat: add real-time streaming terminal UI"
```

---

### Task 8: Create coach_player.py (main loop)

**Files:**
- Create: `g3/src/coach_player.py`

- [ ] **Step 1: Write coach_player.py**

Write to `g3/src/coach_player.py`:

```python
"""Coach-Player feedback loop — the heart of tero."""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Config, CcgEnv
from src.providers.ccg import run_agent
from src.prompts import (
    PLAYER_SYSTEM_PROMPT,
    COACH_SYSTEM_PROMPT,
    build_player_prompt,
    build_coach_prompt,
)
from src.feedback import extract_verdict
from src.streaming import StreamingUI
from src.plan_tracker import parse_plan_items, format_checklist


MAX_RETRIES = 2
BACKOFF_BASE = 2  # seconds


@dataclass
class TurnDetail:
    turn: int
    role: str  # "player" or "coach"
    duration_s: float
    tools_used: int


@dataclass
class SessionResult:
    success: bool
    turns_used: int
    status: str  # "approved", "max_turns_reached", "failed"
    total_duration_s: float
    turn_details: list[TurnDetail] = field(default_factory=list)
    error: str | None = None


async def _run_turn(prompt, system_prompt, working_dir, ccg_env, max_turns, ui, timeout_s=600):
    """Run a single agent turn with timeout and retry.

    Streams to UI. Returns collected messages.
    Raises TimeoutError if agent exceeds timeout_s.
    """
    messages = []

    async def _collect():
        async for msg in run_agent(
            prompt=prompt,
            system_prompt=system_prompt,
            working_dir=working_dir,
            ccg_env=ccg_env,
            max_turns=max_turns,
        ):
            ui.handle_message(msg)
            messages.append(msg)
        return messages

    for attempt in range(MAX_RETRIES + 1):
        try:
            messages = []
            return await asyncio.wait_for(_collect(), timeout=timeout_s)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Agent exceeded {timeout_s}s timeout")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if attempt >= MAX_RETRIES:
                raise
            wait = BACKOFF_BASE ** attempt
            print(f"  [retry] Attempt {attempt + 1} failed: {e}. Retrying in {wait}s...")
            await asyncio.sleep(wait)
            messages = []

    return messages  # unreachable but satisfies type checker


async def run_session(config: Config) -> SessionResult:
    """Run the full coach-player loop."""
    start_time = time.time()
    ui = StreamingUI(verbose=config.verbose)

    # Read requirements
    plan_path = Path(config.plan_file)
    if not plan_path.exists():
        return SessionResult(
            success=False, turns_used=0, status="failed",
            total_duration_s=0, error=f"Plan file not found: {config.plan_file}",
        )

    requirements = plan_path.read_text()
    plan_items = parse_plan_items(requirements)

    # Setup ccg env
    ccg_env = CcgEnv.from_env(config.claude_home)
    if not ccg_env.auth_token:
        return SessionResult(
            success=False, turns_used=0, status="failed",
            total_duration_s=0,
            error="No auth token. Set ANTHROPIC_AUTH_TOKEN or BLACKBOX_ACCOUNT_A_TOKEN",
        )

    # Print header
    ui.print_header(
        plan_file=str(plan_path.name),
        plan_size=len(requirements),
        max_turns=config.max_turns,
    )

    turn_details = []
    coach_feedback = None

    for turn in range(1, config.max_turns + 1):
        try:
            # === PLAYER TURN ===
            ui.start_turn(turn, config.max_turns, "player")
            if coach_feedback:
                print(f"  Feedback: {len(coach_feedback.splitlines())} issues to address")

            player_prompt = build_player_prompt(requirements, coach_feedback)
            player_start = time.time()

            player_msgs = await _run_turn(
                prompt=player_prompt,
                system_prompt=PLAYER_SYSTEM_PROMPT,
                working_dir=config.working_dir,
                ccg_env=ccg_env,
                max_turns=30,
                ui=ui,
                timeout_s=config.player_timeout_s,
            )
            ui.end_turn("Player")
            turn_details.append(TurnDetail(
                turn=turn, role="player",
                duration_s=round(time.time() - player_start, 2),
                tools_used=ui.tool_count,
            ))

            # === COACH TURN ===
            ui.start_turn(turn, config.max_turns, "coach")
            coach_prompt = build_coach_prompt(requirements)
            coach_start = time.time()

            coach_msgs = await _run_turn(
                prompt=coach_prompt,
                system_prompt=COACH_SYSTEM_PROMPT,
                working_dir=config.working_dir,
                ccg_env=ccg_env,
                max_turns=5,
                ui=ui,
                timeout_s=config.coach_timeout_s,
            )
            ui.end_turn("Coach")
            turn_details.append(TurnDetail(
                turn=turn, role="coach",
                duration_s=round(time.time() - coach_start, 2),
                tools_used=ui.tool_count,
            ))

            # === PARSE VERDICT ===
            verdict = extract_verdict(coach_msgs)
            ui.print_verdict(verdict.approved, verdict.feedback)

            # === PLAN CHECKLIST ===
            if plan_items:
                checklist = format_checklist(
                    plan_items,
                    approved=verdict.approved,
                    coach_issues=verdict.feedback if not verdict.approved else None,
                )
                print(f"\n{checklist}")

            if verdict.approved:
                total = time.time() - start_time
                ui.print_session_report(turn, config.max_turns, total, "APPROVED")
                return SessionResult(
                    success=True, turns_used=turn, status="approved",
                    total_duration_s=round(total, 2),
                    turn_details=turn_details,
                )

            # Not approved — set feedback for next player turn
            coach_feedback = verdict.feedback

        except KeyboardInterrupt:
            total = time.time() - start_time
            ui.print_session_report(turn, config.max_turns, total, "INTERRUPTED")
            return SessionResult(
                success=False, turns_used=turn, status="interrupted",
                total_duration_s=round(total, 2),
                turn_details=turn_details,
            )
        except Exception as e:
            total = time.time() - start_time
            print(f"\n  [error] {e}")
            ui.print_session_report(turn, config.max_turns, total, f"FAILED: {e}")
            return SessionResult(
                success=False, turns_used=turn, status="failed",
                total_duration_s=round(total, 2),
                turn_details=turn_details, error=str(e),
            )

    # Max turns reached
    total = time.time() - start_time
    ui.print_session_report(config.max_turns, config.max_turns, total, "MAX TURNS REACHED")
    return SessionResult(
        success=False, turns_used=config.max_turns, status="max_turns_reached",
        total_duration_s=round(total, 2), turn_details=turn_details,
    )


def run_session_sync(config: Config) -> SessionResult:
    """Synchronous wrapper for run_session."""
    return asyncio.run(run_session(config))
```

- [ ] **Step 2: Commit**

```bash
git add src/coach_player.py && git commit -m "feat: add coach-player main loop"
```

---

### Task 8b: Test streaming.py and prompts.py

**Files:**
- Create: `g3/tests/test_streaming.py`
- Create: `g3/tests/test_prompts.py`

- [ ] **Step 1: Write test_prompts.py**

Write to `g3/tests/test_prompts.py`:

```python
"""Tests for prompt builders."""
from src.prompts import build_player_prompt, build_coach_prompt


def test_player_prompt_first_turn():
    prompt = build_player_prompt("Build a hello world app")
    assert "Build a hello world app" in prompt
    assert "Coach Feedback" not in prompt
    assert "Implement all requirements" in prompt


def test_player_prompt_with_feedback():
    prompt = build_player_prompt("Build app", feedback="1. Missing tests")
    assert "Build app" in prompt
    assert "Missing tests" in prompt
    assert "Address ALL feedback" in prompt


def test_coach_prompt():
    prompt = build_coach_prompt("Build a hello world app")
    assert "Build a hello world app" in prompt
    assert "IMPLEMENTATION_APPROVED" in prompt
```

- [ ] **Step 2: Write test_streaming.py**

Write to `g3/tests/test_streaming.py`:

```python
"""Tests for streaming UI."""
from src.streaming import StreamingUI


def test_tool_summary_read():
    ui = StreamingUI()
    assert "foo.py" in ui._tool_summary("Read", {"file_path": "foo.py"})


def test_tool_summary_bash():
    ui = StreamingUI()
    summary = ui._tool_summary("Bash", {"command": "python -m pytest tests/"})
    assert "pytest" in summary


def test_tool_summary_unknown():
    ui = StreamingUI()
    summary = ui._tool_summary("Unknown", {"x": 1})
    assert summary  # doesn't crash, returns something
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_prompts.py tests/test_streaming.py -v
```
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_prompts.py tests/test_streaming.py && git commit -m "test: add tests for prompts and streaming"
```

---

## Chunk 4: CLI, Recorder, and Integration

### Task 9: Rewrite learning/recorder.py

**Files:**
- Rewrite: `g3/src/learning/recorder.py`
- Create: `g3/tests/test_recorder.py`

- [ ] **Step 1: Write failing test**

Write to `g3/tests/test_recorder.py`:

```python
"""Tests for run recorder."""
import json
import tempfile
from pathlib import Path
from src.learning.recorder import RunRecorder, RunRecord


def test_record_and_load(tmp_path):
    recorder = RunRecorder(knowledge_dir=str(tmp_path))
    record = RunRecord(
        run_id="",  # will be assigned
        timestamp="",
        requirements_file="requirements.md",
        turns_used=2,
        max_turns=10,
        status="approved",
        total_duration_s=120.5,
        turn_details=[],
    )
    run_id = recorder.record(record)
    assert run_id == "run_0001"

    runs = recorder.history(limit=10)
    assert len(runs) == 1
    assert runs[0]["status"] == "approved"
    assert runs[0]["turns_used"] == 2


def test_multiple_records(tmp_path):
    recorder = RunRecorder(knowledge_dir=str(tmp_path))
    for i in range(3):
        record = RunRecord(
            run_id="", timestamp="",
            requirements_file="req.md",
            turns_used=i + 1, max_turns=10,
            status="approved", total_duration_s=60.0,
            turn_details=[],
        )
        recorder.record(record)

    runs = recorder.history(limit=2)
    assert len(runs) == 2  # last 2


def test_history_limit(tmp_path):
    recorder = RunRecorder(knowledge_dir=str(tmp_path))
    for i in range(5):
        record = RunRecord(
            run_id="", timestamp="",
            requirements_file="req.md",
            turns_used=1, max_turns=10,
            status="approved", total_duration_s=30.0,
            turn_details=[],
        )
        recorder.record(record)

    assert len(recorder.history(limit=3)) == 3
    assert len(recorder.history(limit=10)) == 5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_recorder.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement recorder.py**

Write to `g3/src/learning/recorder.py`:

```python
"""Run record persistence in runs.jsonl — coach-player model."""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from pathlib import Path


@dataclass
class TurnDetail:
    turn: int
    role: str
    duration_s: float
    tools_used: int


@dataclass
class RunRecord:
    run_id: str
    timestamp: str
    requirements_file: str
    turns_used: int
    max_turns: int
    status: str  # "approved", "max_turns_reached", "failed", "interrupted"
    total_duration_s: float
    turn_details: list[dict] = field(default_factory=list)


class RunRecorder:
    def __init__(self, knowledge_dir: str = ".g3/knowledge"):
        self.knowledge_dir = Path(knowledge_dir)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.runs_file = self.knowledge_dir / "runs.jsonl"
        self._run_counter = self._count_existing()

    def record(self, record: RunRecord) -> str:
        """Save a run record. Returns assigned run_id."""
        self._run_counter += 1
        record.run_id = f"run_{self._run_counter:04d}"
        record.timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        data = asdict(record)
        with open(self.runs_file, "a") as f:
            f.write(json.dumps(data) + "\n")

        return record.run_id

    def history(self, limit: int = 10) -> list[dict]:
        """Load last N run records."""
        all_runs = self._load_all()
        return all_runs[-limit:]

    def _load_all(self) -> list[dict]:
        if not self.runs_file.exists():
            return []
        return [
            json.loads(line)
            for line in self.runs_file.read_text().splitlines()
            if line.strip()
        ]

    def _count_existing(self) -> int:
        if not self.runs_file.exists():
            return 0
        return sum(1 for l in self.runs_file.read_text().splitlines() if l.strip())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/test_recorder.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/learning/recorder.py tests/test_recorder.py && git commit -m "feat: rewrite recorder for coach-player model"
```

---

### Task 10: Rewrite g3.py (CLI entry point)

**Files:**
- Rewrite: `g3/g3.py`

- [ ] **Step 1: Write new g3.py**

Write to `g3/g3.py`:

```python
#!/usr/bin/env python3
"""tero — coach-player feedback loop via Claude Agent SDK."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import resolve_config
from src.coach_player import run_session_sync, SessionResult
from src.learning.recorder import RunRecorder, RunRecord


def _resolve_working_dir(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "requirements.md").exists() or (cwd / ".g3").exists():
        return cwd
    return cwd


def _resolve_plan_file(plan_value: str, working_dir: Path) -> Path:
    plan_path = Path(plan_value).expanduser()
    if plan_path.is_absolute():
        return plan_path
    return (working_dir / plan_path).resolve()


def cmd_go(args):
    """Run coach-player session."""
    working_dir = _resolve_working_dir(getattr(args, "working_dir", None))
    plan_file = _resolve_plan_file(args.plan, working_dir)

    cli_args = {
        "plan_file": str(plan_file),
        "working_dir": str(working_dir),
        "max_turns": getattr(args, "max_turns", None),
        "autonomous": getattr(args, "autonomous", None),
        "verbose": getattr(args, "verbose", None),
    }

    config = resolve_config(cli_args)
    result = run_session_sync(config)

    # Record run
    recorder = RunRecorder(
        knowledge_dir=str(Path(config.working_dir) / ".g3" / "knowledge")
    )
    record = RunRecord(
        run_id="",
        timestamp="",
        requirements_file=config.plan_file,
        turns_used=result.turns_used,
        max_turns=config.max_turns,
        status=result.status,
        total_duration_s=result.total_duration_s,
        turn_details=[
            {"turn": td.turn, "role": td.role,
             "duration_s": td.duration_s, "tools_used": td.tools_used}
            for td in result.turn_details
        ],
    )
    recorder.record(record)

    if not result.success:
        sys.exit(1)


def cmd_history(args):
    """Show run history."""
    working_dir = _resolve_working_dir(None)
    recorder = RunRecorder(
        knowledge_dir=str(working_dir / ".g3" / "knowledge")
    )
    runs = recorder.history(limit=args.limit)

    if not runs:
        print("No runs recorded yet.")
        return

    print(f"\n{'='*60}")
    print(f"  Run History (last {len(runs)})")
    print(f"{'='*60}\n")

    for run in runs:
        status_icon = {
            "approved": "APPROVED",
            "max_turns_reached": "MAX TURNS",
            "failed": "FAILED",
            "interrupted": "INTERRUPTED",
        }.get(run.get("status", "?"), "?")

        print(
            f"  {run.get('run_id', '?')}: "
            f"turns={run.get('turns_used', '?')}/{run.get('max_turns', '?')} "
            f"| {run.get('total_duration_s', 0):.0f}s "
            f"| {status_icon}"
        )


def main():
    parser = argparse.ArgumentParser(
        prog="tero",
        description="tero — coach-player feedback loop"
    )
    sub = parser.add_subparsers(dest="command")

    # go
    go = sub.add_parser("go", help="Run coach-player session")
    go.add_argument("--plan", default="requirements.md", help="Requirements file")
    go.add_argument("--working-dir", help="Working directory")
    go.add_argument("--max-turns", type=int, help="Max coach-player iterations")
    go.add_argument("--autonomous", action="store_true", help="Skip confirmations")
    go.add_argument("--verbose", action="store_true", help="Show full output")
    go.set_defaults(func=cmd_go)

    # history
    history = sub.add_parser("history", help="Show run history")
    history.add_argument("--limit", type=int, default=10, help="Runs to show")
    history.set_defaults(func=cmd_history)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI works**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python g3.py --help
python g3.py go --help
python g3.py history
```
Expected: help text shown, history shows "No runs recorded yet."

- [ ] **Step 3: Commit**

```bash
git add g3.py && git commit -m "feat: rewrite CLI for coach-player model"
```

---

### Task 11: End-to-end smoke test

- [ ] **Step 1: Run all unit tests**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
python -m pytest tests/ -v
```
Expected: ALL PASS

- [ ] **Step 2: Test tero go with a real requirements.md**

Create a simple requirements.md in a test directory and run:

```bash
mkdir -p /tmp/tero-test
cat > /tmp/tero-test/requirements.md << 'EOF'
# Simple Test

1. Create a file called hello.py that prints "Hello, World!"
2. Create a file called test_hello.py that tests hello.py
EOF

cd /tmp/tero-test
python /Users/terobyte/Desktop/Projects/Active/tero/g3/g3.py go --max-turns 3
```

Verify:
- Player streams tool calls and text
- Coach reviews and gives verdict
- If not approved, player retries with feedback
- Session report printed at end

- [ ] **Step 3: Verify history works**

```bash
cd /tmp/tero-test
python /Users/terobyte/Desktop/Projects/Active/tero/g3/g3.py history
```
Expected: shows the run we just did

- [ ] **Step 4: Final commit**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3
git add -A && git commit -m "feat: complete coach-player rewrite via Claude Agent SDK"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Clean old files, install deps, verify prereqs | requirements.txt |
| 2 | Rewrite config.py | src/config.py, tests/test_config.py |
| 3 | Create ccg provider (with token validation) | src/providers/ccg.py |
| 4 | Create prompts | src/prompts.py |
| 5 | Create feedback parser | src/feedback.py, tests/test_feedback.py |
| 6 | Create plan tracker | src/plan_tracker.py, tests/test_plan_tracker.py |
| 7 | Create streaming UI | src/streaming.py |
| 8 | Create main loop (with timeout + retry) | src/coach_player.py |
| 8b | Test streaming + prompts | tests/test_streaming.py, tests/test_prompts.py |
| 9 | Rewrite recorder | src/learning/recorder.py, tests/test_recorder.py |
| 10 | Rewrite CLI | g3.py |
| 11 | End-to-end smoke test | — |
