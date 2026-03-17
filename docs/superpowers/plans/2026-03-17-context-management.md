# Context Management Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent GLM-5 (and all providers) from stalling mid-turn due to context overflow — via SDK PreCompact hook for CCG/Claude and a universal Continuation Agent fallback.

**Architecture:** CCG/Claude providers register a `PreCompact` hook in `ClaudeAgentOptions` that fires mid-turn and injects a compaction summary instruction via `systemMessage`. Codex tracks `prompt_tokens` from SSE `usage` field and compacts between turns. All providers get a `_run_with_continuation()` wrapper that retries with a compact context summary when a turn ends without required markers.

**Tech Stack:** `claude_agent_sdk` (PreCompact hook API), `httpx` (Codex SSE), Python async, pytest

**Spec:** `docs/superpowers/specs/2026-03-17-context-management-batch-v2-design.md` — Part 1

---

## Chunk 1: Config + context_manager.py

### Task 1: Add context config fields

**Files:**
- Modify: `g3/src/config.py`
- Modify: `g3/g3.py`
- Test: `g3/tests/test_context_config.py`

- [ ] **Step 1: Write failing test**

```python
# g3/tests/test_context_config.py
from src.config import Config, resolve_config

def test_context_defaults():
    cfg = Config()
    assert cfg.context_limit == 110_000
    assert cfg.compact_threshold == 0.85
    assert cfg.max_continuation_attempts == 2

def test_context_env_override(monkeypatch):
    monkeypatch.setenv("G3_CONTEXT_LIMIT", "80000")
    monkeypatch.setenv("G3_COMPACT_THRESHOLD", "0.70")
    monkeypatch.setenv("G3_MAX_CONTINUATION_ATTEMPTS", "3")
    cfg = resolve_config({"working_dir": "."})
    assert cfg.context_limit == 80_000
    assert cfg.compact_threshold == 0.70
    assert cfg.max_continuation_attempts == 3
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd g3 && python -m pytest tests/test_context_config.py -v
```
Expected: `AttributeError: 'Config' object has no attribute 'context_limit'`

- [ ] **Step 3: Add fields to Config dataclass**

In `g3/src/config.py`, add to the `Config` dataclass after existing fields:

```python
# Context Management
context_limit: int = 110_000
compact_threshold: float = 0.85
max_continuation_attempts: int = 2
```

In `resolve_config()`, add to `env_map`:

```python
"G3_CONTEXT_LIMIT": ("context_limit", int),
"G3_COMPACT_THRESHOLD": ("compact_threshold", float),
"G3_MAX_CONTINUATION_ATTEMPTS": ("max_continuation_attempts", int),
```

- [ ] **Step 4: Run test — expect PASS**

```bash
cd g3 && python -m pytest tests/test_context_config.py -v
```

- [ ] **Step 5: Add CLI flags to g3.py**

Find the argparse section in `g3/g3.py` where `--player-provider` etc. are defined. Add:

```python
parser.add_argument("--context-limit", type=int, default=None,
                    help="Max context tokens (default: 110000)")
parser.add_argument("--compact-threshold", type=float, default=None,
                    help="Compact at this fraction of limit (default: 0.85)")
parser.add_argument("--max-continuation", type=int, default=None,
                    dest="max_continuation_attempts",
                    help="Continuation agent retries (default: 2)")
```

- [ ] **Step 6: Commit**

```bash
git add g3/src/config.py g3/g3.py g3/tests/test_context_config.py
git commit -m "feat: add context management config fields (context_limit, compact_threshold, max_continuation_attempts)"
```

---

### Task 2: Create context_manager.py

**Files:**
- Create: `g3/src/context_manager.py`
- Test: `g3/tests/test_context_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# g3/tests/test_context_manager.py
from src.context_manager import _build_compact_summary, _build_continuation_prompt
from src.providers.message_adapter import AdaptedMessage, TextBlock, ToolUseBlock, ToolResultBlock

def _make_assistant_msg(text: str) -> AdaptedMessage:
    return AdaptedMessage(role="assistant", content=[TextBlock(text=text)])

def _make_tool_result_msg() -> AdaptedMessage:
    return AdaptedMessage(role="tool", content=[ToolResultBlock(
        tool_use_id="x", content="big file content " * 500
    )])

def test_compact_summary_keeps_assistant_text():
    msgs = [
        _make_assistant_msg("I read config.py and found CcgEnv."),
        _make_tool_result_msg(),
        _make_assistant_msg("Step 1 done: added from_env_b()"),
    ]
    summary = _build_compact_summary(msgs)
    assert "CcgEnv" in summary
    assert "Step 1 done" in summary
    assert "big file content" not in summary  # tool results dropped

def test_compact_summary_empty_messages():
    assert _build_compact_summary([]) == ""

def test_continuation_prompt_player():
    prompt = _build_continuation_prompt("Did step 1 and 2.", role="player")
    assert "PHASE_COMPLETE" in prompt
    assert "Did step 1 and 2." in prompt

def test_continuation_prompt_coach():
    prompt = _build_continuation_prompt("Found null issue.", role="coach")
    assert "IMPLEMENTATION_APPROVED" in prompt or "verdict" in prompt.lower()
    assert "Found null issue." in prompt
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd g3 && python -m pytest tests/test_context_manager.py -v
```
Expected: `ModuleNotFoundError: No module named 'src.context_manager'`

- [ ] **Step 3: Implement context_manager.py**

```python
# g3/src/context_manager.py
"""Context management utilities: compaction, continuation, review logging."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.providers.message_adapter import AdaptedMessage


def _build_compact_summary(messages: list) -> str:
    """Extract assistant text blocks only — drops tool results (heaviest parts)."""
    parts = []
    for msg in messages:
        if not (hasattr(msg, "role") and msg.role == "assistant"):
            continue
        content = getattr(msg, "content", None) or []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text.strip())
    return "\n".join(p for p in parts if p)


def _build_continuation_prompt(summary: str, role: str) -> str:
    """Build continuation prompt for a given role."""
    if role == "player":
        return (
            f"You are continuing previous work. Here is what was already done:\n\n"
            f"{summary}\n\n"
            "Now output the required completion report:\n"
            "  PHASE_COMPLETE: <phase name>\n"
            "  What changed:\n  - ...\n"
            "  Evidence:\n  - ...\n"
            "  Verification:\n  - ..."
        )
    # coach / reviewer
    return (
        f"You are continuing a review. Here is what was assessed so far:\n\n"
        f"{summary}\n\n"
        "Now output your final verdict: either IMPLEMENTATION_APPROVED "
        "or a numbered list of concrete issues to fix."
    )


async def _compact_codex_context(provider, messages: list, config) -> str:
    """Generate a compact summary of previous Codex turn for use in next prompt."""
    summary = _build_compact_summary(messages)
    if not summary:
        return ""
    compact_prompt = (
        "Summarize the following work log compactly. Preserve: completed steps, "
        "files changed, current state, pending work. Be brief.\n\n"
        f"{summary}"
    )
    result_parts = []
    async for chunk in provider.run(
        prompt=compact_prompt,
        system_prompt="You are a concise summarizer.",
        working_dir=".",
        max_turns=3,
        model=config.coach_model or "",
    ):
        text = getattr(chunk, "text", None) or ""
        if text:
            result_parts.append(text)
    return "\n".join(result_parts)


def _log_review_result(
    step_num: int,
    iteration: int,
    verdict,
    provider_display: str,
    working_dir: str,
) -> None:
    """Append a code review iteration result to .g3/bugs/step-N-DATE.md."""
    from src.feedback import ReviewPassed, ReviewIssues  # local import to avoid cycles

    bugs_dir = Path(working_dir) / ".g3" / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.date.today().isoformat()
    path = bugs_dir / f"step-{step_num}-{date_str}.md"

    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    if not path.exists():
        path.write_text(f"# Code Review — Step {step_num} — {ts}\n\n"
                        f"- Provider: {provider_display}\n\n")

    with path.open("a") as f:
        if isinstance(verdict, ReviewPassed):
            f.write(f"## Iteration {iteration} — PASSED\nNo critical issues found.\n\n")
        else:
            issues = getattr(verdict, "text", str(verdict))
            f.write(f"## Iteration {iteration} — Issues Found\n{issues}\n\n")
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd g3 && python -m pytest tests/test_context_manager.py -v
```

- [ ] **Step 5: Commit**

```bash
git add g3/src/context_manager.py g3/tests/test_context_manager.py
git commit -m "feat: add context_manager.py with compact summary, continuation prompt, review logging"
```

---

## Chunk 2: PreCompact Hook (CCG) + Codex token tracking

### Task 3: PreCompact hook in ccg.py

**Files:**
- Modify: `g3/src/providers/ccg.py`
- Test: `g3/tests/test_ccg_compact_hook.py`

- [ ] **Step 1: Write failing test**

```python
# g3/tests/test_ccg_compact_hook.py
from src.providers.ccg import _make_compact_hooks

def test_hook_structure():
    hooks = _make_compact_hooks(110_000, 0.85)
    assert "PreCompact" in hooks
    matchers = hooks["PreCompact"]
    assert len(matchers) == 1
    matcher = matchers[0]
    assert "hooks" in matcher
    assert len(matcher["hooks"]) == 1
    assert callable(matcher["hooks"][0])

import asyncio

def test_hook_returns_correct_keys():
    hooks = _make_compact_hooks(110_000, 0.85)
    fn = hooks["PreCompact"][0]["hooks"][0]

    # hook takes 3 args: hook_input, tool_name, context
    result = asyncio.get_event_loop().run_until_complete(fn({}, None, {}))
    assert "continue_" in result
    assert result["continue_"] is True
    assert "systemMessage" in result
    assert "93" in result["systemMessage"]  # 93k threshold mentioned
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd g3 && python -m pytest tests/test_ccg_compact_hook.py -v
```

- [ ] **Step 3: Add _make_compact_hooks() to ccg.py**

In `g3/src/providers/ccg.py`, add before `CcgProvider` class:

```python
def _make_compact_hooks(context_limit: int, threshold: float) -> dict:
    """Build SDK hooks dict with PreCompact handler for mid-turn compaction."""
    compact_at = int(context_limit * threshold)

    async def on_pre_compact(hook_input, tool_name, context) -> dict:
        return {
            "continue_": True,
            "systemMessage": (
                "Summarize the conversation compactly. Preserve: "
                "completed steps with proof, file paths changed, "
                "current implementation state, pending work. "
                f"Target: under {compact_at // 1000}k tokens."
            ),
        }

    return {
        "PreCompact": [
            {"matcher": None, "hooks": [on_pre_compact], "timeout": None}
        ]
    }
```

In `CcgProvider.run()`, find the `ClaudeAgentOptions(...)` call and add `hooks=` parameter:

```python
# Find where config is accessible (passed via run() or stored in self)
# Add to ClaudeAgentOptions:
hooks=_make_compact_hooks(
    getattr(config, "context_limit", 110_000),
    getattr(config, "compact_threshold", 0.85),
) if config else None,
```

**Note:** `CcgProvider.run()` currently doesn't receive `config`. The cleanest approach: pass context params in constructor or accept optional kwargs. Check `ccg.py` to see how config is currently passed and follow that pattern.

- [ ] **Step 4: Run test — expect PASS**

```bash
cd g3 && python -m pytest tests/test_ccg_compact_hook.py -v
```

- [ ] **Step 5: Commit**

```bash
git add g3/src/providers/ccg.py g3/tests/test_ccg_compact_hook.py
git commit -m "feat: add PreCompact hook to CcgProvider for mid-turn context compaction"
```

---

### Task 4: Codex SSE token tracking

**Files:**
- Modify: `g3/src/providers/codex.py`
- Test: `g3/tests/test_codex_token_tracking.py`

- [ ] **Step 1: Write failing test**

```python
# g3/tests/test_codex_token_tracking.py
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from src.providers.codex import CodexProvider, CodexConfig

def _make_sse_lines(content: str, prompt_tokens: int) -> list[str]:
    import json
    return [
        f"data: {json.dumps({'choices': [{'delta': {'content': content}, 'finish_reason': None}]})}",
        f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': 10}})}",
        "data: [DONE]",
    ]

@patch("httpx.AsyncClient")
def test_last_input_tokens_stored(mock_client_cls):
    provider = CodexProvider(CodexConfig(api_url="http://localhost:9999"))
    lines = _make_sse_lines("hello", prompt_tokens=85000)

    mock_resp = MagicMock()
    mock_resp.aiter_lines = AsyncMock(return_value=aiter(lines))
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock(stream=MagicMock(return_value=mock_resp)))

    async def run():
        chunks = []
        async for c in provider.run("hi", "", ".", 10):
            chunks.append(c)
        return chunks

    asyncio.get_event_loop().run_until_complete(run())
    assert provider._last_input_tokens == 85000

async def aiter(items):
    for item in items:
        yield item
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd g3 && python -m pytest tests/test_codex_token_tracking.py -v
```

- [ ] **Step 3: Add token tracking to codex.py**

In `CodexProvider.__init__`, add:
```python
self._last_input_tokens: int = 0
```

In `CodexProvider.run()`, within the SSE parsing loop, find where `data.get("choices")` is processed and add:

```python
# After parsing each chunk, check for usage:
if usage := data.get("usage"):
    self._last_input_tokens = usage.get("prompt_tokens", 0)
```

- [ ] **Step 4: Run test — expect PASS**

```bash
cd g3 && python -m pytest tests/test_codex_token_tracking.py -v
```

- [ ] **Step 5: Commit**

```bash
git add g3/src/providers/codex.py g3/tests/test_codex_token_tracking.py
git commit -m "feat: track prompt_tokens from Codex SSE usage field"
```

---

## Chunk 3: Continuation Agent

### Task 5: _run_with_continuation() in CoachPlayerSession

**Files:**
- Modify: `g3/src/coach_player.py`
- Test: `g3/tests/test_continuation_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# g3/tests/test_continuation_agent.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.config import Config

def make_session(max_continuation=2):
    cfg = Config(max_continuation_attempts=max_continuation)
    # minimal session — only needs config, player_provider, _run_turn
    from src.coach_player import CoachPlayerSession
    session = object.__new__(CoachPlayerSession)
    session.config = cfg
    session._interrupted = False
    return session

def make_turn_result(text: str):
    from src.coach_player import TurnResult
    return TurnResult(role="player", duration_s=1.0, tools_used=0, messages=[], text=text)

def test_continuation_returns_immediately_when_markers_present():
    session = make_session()
    good_result = make_turn_result("PHASE_COMPLETE: Update\nWhat changed:\n- x\nEvidence:\n- y\nVerification:\n- z")

    async def fake_run_turn(*args, **kwargs):
        return good_result

    session._run_turn = fake_run_turn

    async def run():
        return await session._run_with_continuation(
            role="player", prompt="do stuff",
            system_prompt="", max_turns=10, timeout_s=60,
        )

    result = asyncio.get_event_loop().run_until_complete(run())
    assert result.text == good_result.text

def test_continuation_retries_when_no_markers():
    session = make_session(max_continuation=2)
    call_count = 0

    async def fake_run_turn(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return make_turn_result("I explored the code but forgot to write PHASE_COMPLETE")
        return make_turn_result(
            "PHASE_COMPLETE: Update\nWhat changed:\n- added\nEvidence:\n- file\nVerification:\n- ok"
        )

    session._run_turn = fake_run_turn

    async def run():
        return await session._run_with_continuation(
            role="player", prompt="do stuff",
            system_prompt="", max_turns=10, timeout_s=60,
        )

    result = asyncio.get_event_loop().run_until_complete(run())
    assert call_count == 2
    assert "PHASE_COMPLETE" in result.text

def test_continuation_exhausted_returns_last_result():
    session = make_session(max_continuation=2)

    async def fake_run_turn(*args, **kwargs):
        return make_turn_result("no markers here ever")

    session._run_turn = fake_run_turn

    async def run():
        return await session._run_with_continuation(
            role="player", prompt="do stuff",
            system_prompt="", max_turns=10, timeout_s=60,
        )

    result = asyncio.get_event_loop().run_until_complete(run())
    # 1 original + 2 continuation = 3 total calls
    assert result.text == "no markers here ever"
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd g3 && python -m pytest tests/test_continuation_agent.py -v
```

- [ ] **Step 3: Add _run_with_continuation() and _has_completion_markers() to coach_player.py**

Add these two methods to `CoachPlayerSession` class in `g3/src/coach_player.py`:

```python
def _has_completion_markers(self, text: str, role: str) -> bool:
    """Return True when the turn output contains the required completion markers."""
    from src.batch_executor import _PHASE_COMPLETE_RE, _REQUIRED_REPORT_HEADERS
    if role == "player":
        if not _PHASE_COMPLETE_RE.search(text):
            return False
        lowered = text.lower()
        return all(h in lowered for h in _REQUIRED_REPORT_HEADERS)
    # coach/reviewer: needs IMPLEMENTATION_APPROVED or numbered issues
    from src.feedback import _APPROVED_MARKER_RE, _NUMBERED_ISSUE_RE
    if _APPROVED_MARKER_RE.search(text):
        return True
    return bool(_NUMBERED_ISSUE_RE.search(text))

async def _run_with_continuation(
    self,
    role: str,
    prompt: str,
    system_prompt: str,
    max_turns: int,
    timeout_s: int,
    model_override: str = "",
    provider_override=None,
) -> "TurnResult":
    """Run a turn, retrying with compact context if no completion markers found."""
    from src.context_manager import _build_compact_summary, _build_continuation_prompt
    from src import streaming as streaming_ui

    result = await self._run_turn(
        role=role,
        prompt=prompt,
        system_prompt=system_prompt,
        max_turns=max_turns,
        timeout_s=timeout_s,
        model_override=model_override,
        provider_override=provider_override,
    )

    for attempt in range(self.config.max_continuation_attempts):
        if self._has_completion_markers(result.text, role):
            return result

        streaming_ui.print_continuation_started(role, attempt + 1,
                                                 self.config.max_continuation_attempts)
        summary = _build_compact_summary(result.messages)
        continuation_prompt = _build_continuation_prompt(summary, role)

        result = await self._run_turn(
            role=role,
            prompt=continuation_prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout_s=timeout_s,
            model_override=model_override,
            provider_override=provider_override,
        )

    return result
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd g3 && python -m pytest tests/test_continuation_agent.py -v
```

- [ ] **Step 5: Commit**

```bash
git add g3/src/coach_player.py g3/tests/test_continuation_agent.py
git commit -m "feat: add _run_with_continuation() to CoachPlayerSession"
```

---

### Task 6: Wire continuation into batch_executor.py

**Files:**
- Modify: `g3/src/batch_executor.py`
- Test: `g3/tests/test_batch_continuation.py`

- [ ] **Step 1: Write failing test**

```python
# g3/tests/test_batch_continuation.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

def test_batch_player_uses_run_with_continuation(monkeypatch):
    """batch_executor._run_phase() must call session._run_with_continuation for player."""
    from src.batch_executor import BatchExecutor
    from src.plan_tracker import Phase, PlanItem
    from src.coach_player import TurnResult
    from src.config import Config

    session = MagicMock()
    session.config = Config()
    session._interrupted = False

    # _run_with_continuation returns a result with PHASE_COMPLETE markers
    complete_text = (
        "PHASE_COMPLETE: Test\n"
        "What changed:\n- x\nEvidence:\n- y\nVerification:\n- z"
    )
    session._run_with_continuation = AsyncMock(
        return_value=TurnResult("player", 1.0, 0, [], complete_text)
    )
    # _run_coach_turn_for_phase returns Approved
    from src.feedback import Approved
    session._run_coach_turn_for_phase = AsyncMock(return_value=Approved())

    executor = BatchExecutor(session)
    phase = Phase(name="Test", steps=[PlanItem(text="step one")], status="pending")

    asyncio.get_event_loop().run_until_complete(executor._run_phase(phase))

    session._run_with_continuation.assert_called_once()
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd g3 && python -m pytest tests/test_batch_continuation.py -v
```

- [ ] **Step 3: Replace _run_turn with _run_with_continuation in batch_executor.py**

In `g3/src/batch_executor.py`, find `_run_phase()`. The current call is:

```python
result = await self.session._run_turn(
    role="player",
    prompt=prompt,
    system_prompt=PLAYER_BATCH_SYSTEM_PROMPT,
    max_turns=self.session.config.max_turns,
    timeout_s=self.session.config.player_timeout_s,
    model_override=self.session.config.player_model,
)
```

Replace with:

```python
result = await self.session._run_with_continuation(
    role="player",
    prompt=prompt,
    system_prompt=PLAYER_BATCH_SYSTEM_PROMPT,
    max_turns=self.session.config.max_turns,
    timeout_s=self.session.config.player_timeout_s,
    model_override=self.session.config.player_model,
)
```

- [ ] **Step 4: Run test — expect PASS**

```bash
cd g3 && python -m pytest tests/test_batch_continuation.py -v
```

- [ ] **Step 5: Run full test suite**

```bash
cd g3 && python -m pytest -v
```

Fix any regressions before committing.

- [ ] **Step 6: Commit**

```bash
git add g3/src/batch_executor.py g3/tests/test_batch_continuation.py
git commit -m "feat: batch player turns use _run_with_continuation() to handle missing markers"
```

---

## Chunk 4: Streaming UI

### Task 7: New streaming functions

**Files:**
- Modify: `g3/src/streaming.py`
- Test: `g3/tests/test_streaming_context.py`

- [ ] **Step 1: Write failing tests**

```python
# g3/tests/test_streaming_context.py
from io import StringIO
import sys
from src import streaming as s

def capture(fn, *args):
    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    fn(*args)
    sys.stdout = old
    return buf.getvalue()

def test_print_compact_triggered():
    out = capture(s.print_compact_triggered, 91_000, 110_000)
    assert "91" in out or "compact" in out.lower()

def test_print_continuation_started():
    out = capture(s.print_continuation_started, "player", 1, 2)
    assert "1" in out and "2" in out
    assert "player" in out.lower() or "continuation" in out.lower()
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd g3 && python -m pytest tests/test_streaming_context.py -v
```

- [ ] **Step 3: Add functions to streaming.py**

In `g3/src/streaming.py`, add:

```python
def print_compact_triggered(tokens_used: int, context_limit: int) -> None:
    """Print notification when context compaction fires."""
    print(f"\n{BOLD}⚡ Context compacted{RESET} "
          f"({tokens_used // 1000}k/{context_limit // 1000}k tokens) — continuing...")

def print_continuation_started(role: str, attempt: int, max_attempts: int) -> None:
    """Print notification when continuation agent starts."""
    print(f"\n{BOLD}🔄 [{role}]{RESET} No completion markers — "
          f"continuation agent {attempt}/{max_attempts}...")
```

Use the `BOLD`/`RESET` constants already in `streaming.py`.

- [ ] **Step 4: Run test — expect PASS**

```bash
cd g3 && python -m pytest tests/test_streaming_context.py -v
```

- [ ] **Step 5: Final full suite run**

```bash
cd g3 && python -m pytest -v
```

- [ ] **Step 6: Commit**

```bash
git add g3/src/streaming.py g3/tests/test_streaming_context.py
git commit -m "feat: add print_compact_triggered() and print_continuation_started() to streaming"
```

---

## Verification

After all tasks complete:

```bash
# Run full test suite
cd g3 && python -m pytest -v

# Smoke test: run batch with GLM-5 on a small plan and verify
# continuation messages appear when context fills
tero go --batch --context-limit=10000 --compact-threshold=0.5
# Should see "⚡ Context compacted" or "🔄 continuation agent" messages
```
