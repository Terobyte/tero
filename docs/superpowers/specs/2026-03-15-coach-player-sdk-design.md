# Coach-Player Loop via Claude Code SDK

## Overview

Rewrite tero from a dual-agent duel system to a **coach-player feedback loop** (like the original g3 Rust system), powered by **Claude Agent SDK for Python** (`claude-agent-sdk`). One player implements, one coach reviews, loop until approved.

## Architecture

```
tero go
  |
  +-- Read requirements.md
  +-- Parse plan checklist from requirements
  +-- Configure ccg env (Blackbox.ai)
  |
  +-- LOOP (max_turns):
       |
       +-- PLAYER TURN (fresh context each time)
       |     Claude Agent SDK: query()
       |     - system_prompt: player role
       |     - prompt: requirements + coach_feedback (if any)
       |     - cwd: working directory (.)
       |     - env: ccg env vars
       |     - permission_mode: "bypassPermissions"
       |     - Stream to terminal in real-time
       |
       +-- COACH TURN (always fresh context)
       |     Claude Agent SDK: query()
       |     - system_prompt: coach/review role
       |     - prompt: requirements + "review implementation"
       |     - cwd: same working directory
       |     - env: ccg env vars
       |     - max_turns: 5 (coach shouldn't run too long)
       |     - Stream to terminal in real-time
       |
       +-- PARSE COACH OUTPUT:
       |     Collect all TextBlock.text from the FINAL AssistantMessage
       |     (the last one before ResultMessage).
       |     If it contains "IMPLEMENTATION_APPROVED" -> break, success.
       |     Otherwise -> full text = feedback -> next player turn.
       |
       +-- UPDATE PLAN CHECKLIST
       |     If APPROVED -> mark all items [x]
       |     If NOT -> display coach's numbered issues as the checklist
       |
       +-- Player gets clean context + feedback next round
```

## Key Decisions

1. **Both player and coach use ccg** (same Blackbox.ai backend, different system prompts)
2. **Player gets fresh context each turn** — receives feedback as part of prompt, not --resume
3. **Coach gets fresh context each turn** — unbiased review (same as g3 Rust)
4. **Single working directory** — no worktrees, no copies, agents work in place
5. **Claude Agent SDK** — not subprocess, native Python async with typed messages
6. **Plan checklist** — parsed from requirements.md, updated after each turn with check/x marks
7. **MVP scope** — no `tero resume`, `tero status`, `tero insights` subcommands. Only `tero go` and `tero history`.

## CCG Integration via SDK

ccg/ccg2 are shell wrappers around `claude` CLI that set env vars for Blackbox.ai.
The SDK spawns the `claude` CLI as a subprocess and passes `env`, so it works identically
to the ccg shell wrapper. We replicate the env vars programmatically:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

CCG_ENV = {
    "ANTHROPIC_BASE_URL": "https://api.blackbox.ai",
    "ANTHROPIC_AUTH_TOKEN": "<from env: ANTHROPIC_AUTH_TOKEN or BLACKBOX_ACCOUNT_A_TOKEN>",
    "ANTHROPIC_MODEL": "blackboxai/z-ai/glm-5",
    "ANTHROPIC_SMALL_FAST_MODEL": "minimax-2.5",
    "CLAUDE_HOME": os.path.expanduser("~/.claude-glm"),
}

async for msg in query(
    prompt=prompt,
    options=ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=working_dir,
        env=CCG_ENV,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
    )
):
    handle_message(msg)
```

**Notes:**
- `permission_mode="bypassPermissions"` auto-approves all tools, so `allowed_tools` is not needed.
- `ANTHROPIC_MODEL` is passed via env to the CLI subprocess. If the CLI doesn't respect it,
  fall back to `model="blackboxai/z-ai/glm-5"` in `ClaudeAgentOptions`.
- `CLAUDE_HOME` isolates settings/sessions per provider account.

## File Structure

```
tero/
  g3/
    g3.py                      # CLI entry point (simplified)
    src/
      config.py                # Config: ccg env, max_turns
      coach_player.py          # Main loop: player turn -> coach turn -> repeat
      streaming.py             # Real-time terminal UI from SDK messages
      prompts.py               # Player/Coach system prompts + prompt builders
      feedback.py              # Extract feedback/approval from coach output
      plan_tracker.py          # Parse requirements into checklist, track progress
      providers/
        ccg.py                 # Claude Agent SDK wrapper with ccg env
      learning/
        recorder.py            # Record run history (ADAPTED for coach-player model)
```

### What's Removed (from current tero)

- `duel.py` — replaced by coach_player.py
- `judge.py` — coach IS the judge
- `worktree.py` — not needed, single workspace
- `bug_detector.py` — coach handles this
- `parsers/` — SDK gives typed messages
- `providers/base.py` — no longer needed (SDK has its own message types)
- `providers/claude_glm.py` — replaced by ccg.py (SDK-based)
- `providers/registry.py` — simplified, single provider
- `state.py` — no session state machine for MVP
- `learning/analyzer.py` — removed for MVP (was duel-specific)
- `learning/calibrator.py` — removed for MVP
- `learning/classifier.py` — removed for MVP
- `learning/recommender.py` — removed for MVP

## Real-Time Terminal UI

### Turn Display

```
$ tero go

--- tero coach-player ---
  Requirements: requirements.md (3.8 KB)
  Provider: ccg (blackboxai/z-ai/glm-5)
  Max turns: 10

=== Turn 1/10 -- PLAYER ===

  [tool] Reading requirements.md...
  [tool] Reading src/main.py...
  [tool] Writing src/auth.py...
  [text] Creating authentication module with JWT tokens...
  [tool] Editing src/main.py (lines 15-30)...
  [tool] Running: python -m pytest tests/
  [text] All tests pass. Implementation complete.

  Player: 45s | Tools: 12

=== Turn 1/10 -- COACH ===

  [tool] Reading requirements.md...
  [tool] Reading src/auth.py...
  [tool] Running: python -m pytest tests/ -v
  [text] Reviewing implementation against requirements...

  Coach Verdict: NOT APPROVED

  Issues found:
  1. Missing password hashing - using plaintext
  2. No rate limiting on login endpoint
  3. Test for token expiry missing

  Coach: 30s | Tools: 8

  Plan Progress:
    [x] 1. Set up authentication module
    [x] 2. Add JWT token generation
    [ ] 3. Password hashing with bcrypt
    [ ] 4. Rate limiting on login
    [ ] 5. Token expiry tests

=== Turn 2/10 -- PLAYER ===
  Feedback: 3 issues to address
  ...

=== Turn 2/10 -- COACH ===
  ...
  Coach Verdict: APPROVED

--- Session Report ---
  Turns: 2/10
  Total time: 2m 10s
  Status: APPROVED
```

### Message Handling

| SDK Message Type | UI Output |
|---|---|
| AssistantMessage + TextBlock | `[text] <content>` |
| AssistantMessage + ToolUseBlock | `[tool] <tool_name>: <args summary>` |
| ToolResultMessage | (hidden unless error, or --verbose) |
| ResultMessage | Turn summary (time, cost, tools count) |

## Coach Output Parsing (feedback.py)

Extracting the coach's verdict from SDK messages:

1. Collect ALL messages from the coach's `query()` stream
2. Find the **last** `AssistantMessage` (the one before `ResultMessage`)
3. Concatenate all `TextBlock.text` from that message
4. Check if the concatenated text contains the literal string `IMPLEMENTATION_APPROVED`
5. If yes → return `Approved`
6. If no → return `Feedback(text)` where text is the full concatenated content
7. Ignore `IMPLEMENTATION_APPROVED` appearing inside `ToolResultMessage` (grep output, etc.)

Edge case: if coach produces no `AssistantMessage` with `TextBlock`, use default feedback:
"Coach review produced no output. Please try implementing again."

## Coach-Player Prompts

### Player System Prompt

```
You are an implementation agent (Player). Your job is to implement the
requirements in the current working directory.

Rules:
- Read requirements.md first
- Implement all requirements
- Run tests if they exist
- If you receive feedback from a previous coach review, address ALL issues

When done, simply finish your work.
```

### Player Prompt (first turn)

```
## Requirements
<contents of requirements.md>

Implement all requirements in the current working directory.
```

### Player Prompt (with feedback)

```
## Requirements
<contents of requirements.md>

## Coach Feedback (from previous review)
<feedback text>

Implement the requirements. Address ALL feedback issues listed above.
```

### Coach System Prompt

```
You are a code review agent (Coach). Your job is to review the implementation
in the current working directory against the requirements.

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

You MUST end your final response with either IMPLEMENTATION_APPROVED or a numbered
list of issues. Nothing else after the decision.
```

## Plan Tracker

Simple approach for MVP:

1. Parse numbered items / checkbox items / headers from requirements.md as plan items
2. After each coach turn:
   - If coach says APPROVED → mark all remaining items `[x]`
   - If NOT approved → display the coach's numbered issues as the active checklist
     (don't attempt fuzzy matching between requirements items and coach feedback)
3. Display the plan after each coach turn

## CLI Interface

```
tero go [--max-turns N] [--plan FILE] [--verbose] [--autonomous]
tero history [--limit N]
```

**`tero go`:**
- `--max-turns`: Maximum coach-player iterations (default: 10)
- `--plan`: Requirements file (default: requirements.md)
- `--verbose`: Show full agent output including tool results
- `--autonomous`: Skip all confirmations

**`tero history`:**
- `--limit`: Number of recent runs to show (default: 10)

Removed subcommands: `resume`, `status`, `insights` (MVP scope).

## Config

### .g3/config.yaml (project level)

```yaml
provider:
  type: ccg
  claude_home: ~/.claude-glm
  # Auth token from env: ANTHROPIC_AUTH_TOKEN or BLACKBOX_ACCOUNT_A_TOKEN
  # Base URL defaults to https://api.blackbox.ai for ccg type
  # Model defaults to blackboxai/z-ai/glm-5 for ccg type

defaults:
  max_turns: 10
  autonomous: false
  plan_file: requirements.md
  player_timeout_s: 600
  coach_timeout_s: 300
```

### Environment Variables

- `ANTHROPIC_AUTH_TOKEN` or `BLACKBOX_ACCOUNT_A_TOKEN`: Auth token for ccg
- `G3_MAX_TURNS`: Override max turns
- `G3_AUTONOMOUS`: Skip confirmations ("true"/"false")

Config loading: defaults → .g3/config.yaml → env vars → CLI args (highest priority).

## Error Handling

1. **Agent timeout** — `player_timeout_s` / `coach_timeout_s` per-agent-call. If exceeded, mark turn as failed, continue to next turn.
2. **Agent crash** — log error, retry turn once. If fails again, stop session.
3. **Max turns reached** — stop with "NOT APPROVED after N turns" status.
4. **SDK connection error** — retry with exponential backoff (3 attempts).
5. **Empty coach output** — use default feedback "Review could not complete. Please try implementing again."
6. **User interrupt (Ctrl+C)** — catch KeyboardInterrupt, stop current agent cleanly, print partial session report. Working directory may be in partial state (user's responsibility to review).

## Dependencies

### Python packages
- `claude-agent-sdk` — Claude Agent SDK for Python (the SDK, formerly claude-code-sdk)
- `pyyaml` — config loading
- `asyncio` — async execution (built-in)

### Runtime prerequisites
- `claude` CLI — installed via npm: `npm install -g @anthropic-ai/claude-code`
  (The Python SDK spawns this CLI process under the hood)
- Node.js — required by the claude CLI

## Learning Module Adaptation

The current `recorder.py` has duel-specific types (`AgentResult`, `BugReport`, `JudgeDecision`).
For the new coach-player model, `recorder.py` is **rewritten** with a new signature:

```python
@dataclass
class RunRecord:
    run_id: str
    timestamp: str
    requirements_file: str
    turns_used: int
    max_turns: int
    status: str            # "approved", "max_turns_reached", "failed"
    total_duration_s: float
    turn_details: list[TurnDetail]  # per-turn: role, duration, tools_used

class RunRecorder:
    def record(self, record: RunRecord) -> None: ...
    def history(self, limit: int = 10) -> list[RunRecord]: ...
```

Storage: `.g3/knowledge/runs.jsonl` (same location, new schema).

## Migration Path

This is a **rewrite**, not a refactor. The current duel system is replaced entirely.
Learning module is rewritten with new data model.

## Success Criteria

1. `tero go` launches player, streams output in real-time
2. After player finishes, coach reviews and streams output
3. If not approved — player gets feedback, starts fresh with clean context
4. Loop continues until APPROVED or max_turns
5. Plan checklist displayed and updated after each coach turn
6. Session report at the end with turns, time, status
7. Works with ccg (Blackbox.ai) out of the box
8. Ctrl+C gracefully stops the session
