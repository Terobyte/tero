# Context Management + Batch Orchestration v2 — Design Spec
_Date: 2026-03-17 (rev 2)_

---

## Overview

Two interconnected feature sets:

1. **Context Management** — prevent GLM-5 (and all providers) from stalling mid-turn due to context overflow. Uses SDK's built-in `PreCompact` hook for CCG/Claude providers, SSE usage tracking for Codex, and a universal Continuation Agent fallback.

2. **Batch Orchestration v2** — fully configurable batch review schedule (provider + model per slot), iterative Code Review loop (coach-player until zero bugs), configurable Test Writer, and bug logging.

Root cause confirmed: GLM-5 hits its ~100k context limit mid-turn after reading large files (requirements.md = 36KB, coach_player.py, config.py, etc.), and exits without outputting `PHASE_COMPLETE:` markers — so the coach (Codex) is never called.

---

## Part 1: Context Management

### 1.1 Architecture

Two independent subsystems, one unified config:

```
┌─────────────────────────────────────────────────────┐
│              Context Management System               │
├────────────────────────┬────────────────────────────┤
│   MID-TURN COMPACTION  │   CONTINUATION AGENT        │
│   (CCG + Claude)       │   (all providers)           │
│                        │                             │
│  PreCompact hook fires │  Turn ended WITHOUT         │
│  inside the turn at    │  markers → new agent        │
│  ~93.5k tokens →       │  with compact context       │
│  summary → continue    │  up to 2 retries            │
└────────────────────────┴────────────────────────────┘
         ↕                          ↕
┌─────────────────────────────────────────────────────┐
│               ContextBudget (110k limit)            │
│   Config.context_limit    = 110_000                 │
│   Config.compact_threshold = 0.85  → 93.5k tokens  │
│   Config.max_continuation_attempts = 2              │
└─────────────────────────────────────────────────────┘
```

### 1.2 New Files

```
g3/src/context_manager.py    — _build_compact_summary(messages) -> str
                               _build_continuation_prompt(summary, role) -> str
                               _compact_codex_context(provider, messages, config) -> str
                               _log_review_result(step_num, verdict, provider, working_dir)
```

### 1.3 Modified Files

| File | Change |
|------|--------|
| `config.py` | + `context_limit`, `compact_threshold`, `max_continuation_attempts`, `max_review_iterations` |
| `providers/ccg.py` | + PreCompact hook registration in ClaudeAgentOptions |
| `providers/codex.py` | + read `usage` from SSE, store `_last_input_tokens` |
| `coach_player.py` | + `_run_with_continuation()`, iterative code review loop, `_log_review_result()` call |
| `batch_executor.py` | + `_review_strategy()` reads config, remove `JUDGE_PROVIDER`/`JUDGE_MODEL` constants, update `_judge_label()` |
| `menu.py` | + new "batch роли" section |
| `streaming.py` | + `print_compact_triggered()`, `print_continuation_started()`, update `print_code_review_header()` signature |
| `g3.py` | + CLI flags for all new Config fields |

### 1.4 Config Fields

```python
@dataclass
class Config:
    # Context Management
    context_limit: int = 110_000              # universal token limit
    compact_threshold: float = 0.85           # compact at 93.5k tokens
    max_continuation_attempts: int = 2        # continuation agent retries
    max_review_iterations: int = 3            # code review loop max iterations
```

ENV vars: `G3_CONTEXT_LIMIT`, `G3_COMPACT_THRESHOLD`, `G3_MAX_CONTINUATION_ATTEMPTS`, `G3_MAX_REVIEW_ITERATIONS`
CLI flags: `--context-limit`, `--compact-threshold`, `--max-continuation`, `--max-review-iterations`

### 1.5 PreCompact Hook (CCG + Claude — mid-turn)

The Claude Agent SDK fires `PreCompact` during a running turn when context approaches the limit.

**Verified SDK API:**
- Hook callable: `async def fn(hook_input, tool_name: str | None, context: HookContext) -> dict`
- Return keys (`SyncHookJSONOutput`, all optional): `continue_`, `suppressOutput`, `systemMessage`, `decision`, `reason`, `stopReason`, `hookSpecificOutput`
- `systemMessage` injects text into the model's context — used to guide the compaction summary
- HookMatcher dict keys: `matcher` (str | None), `hooks` (list of callables), `timeout` (float | None)

```python
# providers/ccg.py

def _make_compact_hooks(context_limit: int, threshold: float) -> dict:
    compact_at = int(context_limit * threshold)  # 93_500

    async def on_pre_compact(
        hook_input: "PreCompactHookInput",
        tool_name: "str | None",
        context: "HookContext",
    ) -> dict:
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

```python
# In CcgProvider.run() → ClaudeAgentOptions:
options = ClaudeAgentOptions(
    ...
    hooks=_make_compact_hooks(config.context_limit, config.compact_threshold),
)
```

Flow:
```
GLM-5 uses tools → context grows
  → SDK detects ~93.5k tokens
  → PreCompact hook fires (3-arg callable)
  → systemMessage injects summary instructions
  → GLM-5 compacts its own history
  → SDK continues the turn with compact context
  → GLM-5 writes PHASE_COMPLETE:
```

### 1.6 Codex Token Tracking (inter-turn compaction)

Codex has no SDK hook. Last SSE chunk contains `usage`:

```json
{"choices": [...], "usage": {"prompt_tokens": 87234, "completion_tokens": 412}}
```

```python
# providers/codex.py — in run(), track usage from final SSE chunk
if usage := data.get("usage"):
    self._last_input_tokens = usage.get("prompt_tokens", 0)
```

```python
# coach_player.py — after a Codex turn completes
if hasattr(provider, "_last_input_tokens"):
    tokens = provider._last_input_tokens
    if tokens > config.context_limit * config.compact_threshold:
        compact_prefix = await _compact_codex_context(provider, messages, config)
        # compact_prefix prepended to next turn's prompt
        streaming_ui.print_compact_triggered(tokens, config.context_limit)
```

`_compact_codex_context()` in `context_manager.py`:
- Takes previous `messages`, extracts assistant text blocks only (drops tool results)
- Calls `provider.run()` with a summarization prompt
- Returns compact string to prefix the next turn

### 1.7 Continuation Agent (all providers)

`_run_with_continuation()` is a method on `CoachPlayerSession`. `batch_executor.py` calls it via `self.session._run_with_continuation(...)`.

When a turn ends without required markers — instead of immediate rejection:

```python
# coach_player.py — method on CoachPlayerSession
async def _run_with_continuation(self, role: str, prompt: str, **kwargs) -> TurnResult:
    result = await self._run_turn(role, prompt, **kwargs)

    for attempt in range(self.config.max_continuation_attempts):
        if _has_completion_markers(result.text, role):
            return result  # done

        streaming_ui.print_continuation_started(role, attempt + 1,
                                                 self.config.max_continuation_attempts)
        summary = _build_compact_summary(result.messages)
        # _build_compact_summary: keeps assistant text blocks only,
        # discards tool results (heaviest parts)
        continuation_prompt = _build_continuation_prompt(summary, role)
        result = await self._run_turn(role, continuation_prompt, **kwargs)

    return result  # return as-is, normal downstream logic handles it
```

`_build_compact_summary(messages)` in `context_manager.py`:
- Iterates `messages`, keeps only `AdaptedMessage(role="assistant")` text content
- Discards all tool use and tool result blocks
- Returns a compact narrative string

`_build_continuation_prompt(summary, role)` in `context_manager.py`:
- Wraps summary with role-appropriate continuation instructions
- For `"player"`: "Here is what was done. Output PHASE_COMPLETE: with What changed / Evidence / Verification."
- For `"coach"`: "Here is the review done so far. Output your final verdict: IMPLEMENTATION_APPROVED or numbered issues."

Streaming UI:
```
⚡ [Player] Context compacted (91k → 11k tokens) — continuing...
🔄 [Player] No completion markers — continuation agent 1/2...
✅ [Player] Continuation succeeded — PHASE_COMPLETE found
✗  [Player] Continuation exhausted (2/2) — rejecting phase
```

---

## Part 2: Batch Orchestration v2

### 2.1 Configurable Schedule + Providers

Current schedule is hardcoded via class constants `JUDGE_PROVIDER = "claude"`, `JUDGE_MODEL = "sonnet"`.

**New default:** `3 × GLM-5 / 1 × Codex-High / 1 × GLM-5`

New Config fields — numbers AND provider/model per slot:

```python
@dataclass
class Config:
    # Schedule numbers (existing fields, unchanged)
    batch_pre_judge_attempts: int = 3
    batch_judge_attempts: int = 1
    batch_post_judge_attempts: int = 1

    # Provider + model per slot (NEW)
    batch_pre_provider: str = "ccg"
    batch_pre_model: str = ""                    # GLM-5 default
    batch_judge_provider: str = "codex"          # NEW default (was "claude")
    batch_judge_model: str = "gpt-5.4-high"      # NEW default (was "sonnet")
    batch_post_provider: str = "ccg"
    batch_post_model: str = ""                   # GLM-5 default

    # Test Writer (NEW)
    test_writer_provider: str = "ccg"
    test_writer_model: str = ""
```

### 2.2 batch_executor.py — Remove hardcoded constants

Remove class-level constants and update all references:

```python
# REMOVE these:
# JUDGE_PROVIDER = "claude"
# JUDGE_MODEL = "sonnet"

# UPDATE _judge_label() to read from config:
def _judge_label(self) -> str:
    provider = self.session.config.batch_judge_provider
    model = self.session.config.batch_judge_model
    builder = self._provider_label_builder(provider)
    if builder:
        return builder(provider, model)
    return f"{provider} | model={model}"
```

`_review_strategy()` reads config — and preserves the `"label"` key used by `print_batch_turn_header()`:

```python
def _review_strategy(self, attempt_num: int) -> dict[str, str]:
    pre, judge, _post = self._schedule_counts()
    judge_start = pre + 1
    judge_end = pre + judge

    if judge > 0 and judge_start <= attempt_num <= judge_end:
        return {
            "header_role": "judge",
            "label": self._judge_label(),                          # required by _run_phase
            "provider_name_override": self.session.config.batch_judge_provider,
            "model_override": self.session.config.batch_judge_model,
            "review_role": "judge",
        }

    return {
        "header_role": "coach",
        "label": self._role_label("coach"),                        # required by _run_phase
        "provider_name_override": self.session.config.batch_pre_provider,
        "model_override": self.session.config.batch_pre_model,
        "review_role": "coach",
    }
```

Also update `run()` header print to use dynamic judge label (already calls `self._judge_label()` which now reads config).

### 2.3 Menu — New "batch роли" Section

```
⚙  tero — настройка  (↑↓ выбор, Enter)
  ▶   Запустить
  ─── провайдеры ──────────────────────────
      Player:         ccg (GLM-5)
      Coach:          ccg (GLM-5)
  ─── batch роли ──────────────────────────
      Pre-Coach:      ccg (GLM-5)        [3x]
      Judge:          codex (GPT-5.4H)   [1x]
      Post-Coach:     ccg (GLM-5)        [1x]
      Test Writer:    ccg (GLM-5)
  ─── настройки ───────────────────────────
      ...
```

Clicking any batch role → provider selection → model selection (same flow as existing Player/Coach selection in `_edit_setting_questionary()`).

Example custom `5 × Kimi / 4 × Codex-Ultra / 1 × GLM-5`:
- `batch_pre_provider="ccg"`, `batch_pre_model="kimi-k2.5"`, `batch_pre_judge_attempts=5`
- `batch_judge_provider="codex"`, `batch_judge_model="gpt-5.4-ultra-high"`, `batch_judge_attempts=4`
- `batch_post_provider="ccg"`, `batch_post_model=""`, `batch_post_judge_attempts=1`

### 2.4 Iterative Code Review Loop

Code Review becomes a full coach-player loop until zero bugs found.

```
After Coach approves step:
  Code Reviewer reads git diff →
    found bugs → Player (GLM-5) fixes →
    Code Reviewer checks again →
    ... repeat up to max_review_iterations →
  Log all iterations to .g3/bugs/step-N-YYYY-MM-DD.md
  Mark step DONE
```

```python
# coach_player.py — after Coach Approved, before mark_step_done

if self.config.code_review:
    for review_iter in range(self.config.max_review_iterations):
        review_result = await self._run_turn(
            role="reviewer",
            prompt=build_code_review_prompt(step, step_num, total_steps),
            system_prompt=CODE_REVIEWER_SYSTEM_PROMPT,
            max_turns=8,
            timeout_s=self.config.coach_timeout_s,
            model_override=self.config.review_model,
            provider_override=self.review_provider,
        )

        review_verdict = parse_review_output(review_result.messages)
        _log_review_result(step_num, review_iter + 1, review_verdict,
                           self.review_provider, self.config.working_dir)

        if isinstance(review_verdict, ReviewPassed):
            streaming_ui.print_review_passed(step_num)
            break  # → mark step done

        # bugs found → Player fixes then re-review
        streaming_ui.print_review_issues(review_verdict.text)
        fix_prompt = build_player_fix_prompt(review_verdict.text)
        await self._run_with_continuation(role="player", prompt=fix_prompt, ...)
    # after loop: mark step done regardless (max_review_iterations exhausted)
```

`max_review_iterations` (default 3) controls the review loop — separate from `max_turns` which governs the outer player/coach loop.

### 2.5 Bug Logging

`_log_review_result()` lives in `context_manager.py`. Called from `coach_player.py`.

All bugs logged to `<working_dir>/.g3/bugs/`:

```
.g3/bugs/
  step-1-2026-03-17.md
  step-3-2026-03-17.md
```

File format — iterations appended in one file per step per day:
```markdown
# Code Review — Step 1 — 2026-03-17T14:32:00

- Provider: codex / gpt-5.4-high
- Verdict: ISSUES_FOUND → PASSED (2 iterations)

## Iteration 1 — Issues Found
1. Null check missing in config loader (config.py:87)
2. Race condition in async provider init (coach_player.py:234)

## Iteration 2 — PASSED
No critical issues found.
```

```python
# context_manager.py
def _log_review_result(
    step_num: int,
    iteration: int,
    verdict: "ReviewPassed | ReviewIssues",
    provider,
    working_dir: str,
) -> None:
    bugs_dir = Path(working_dir) / ".g3" / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = bugs_dir / f"step-{step_num}-{date_str}.md"
    # open in append mode, write iteration block
```

### 2.6 streaming.py — Updated Signatures

New functions:
```python
def print_compact_triggered(tokens_used: int, context_limit: int) -> None:
    """⚡ [Player] Context compacted (91k → 11k tokens) — continuing..."""

def print_continuation_started(role: str, attempt: int, max_attempts: int) -> None:
    """🔄 [Player] No completion markers — continuation agent 1/2..."""
```

Updated function — add `iteration` param:
```python
def print_code_review_header(step_num: int, total_steps: int, iteration: int) -> None:
    """🔍 [Step 1/5] Code Review iteration 1/3 (Codex/GPT-5.4H)..."""
```

---

## Part 3: Summary of All Changes

### New files
```
g3/src/context_manager.py          — _build_compact_summary, _build_continuation_prompt,
                                     _compact_codex_context, _log_review_result
```

### Modified files
```
g3/src/config.py                   — context_limit, compact_threshold,
                                     max_continuation_attempts, max_review_iterations,
                                     batch_pre/judge/post _provider/_model,
                                     test_writer_provider/model
g3/src/providers/ccg.py            — _make_compact_hooks(), PreCompact hook in options
g3/src/providers/codex.py          — SSE usage tracking (_last_input_tokens)
g3/src/coach_player.py             — _run_with_continuation(), iterative review loop,
                                     _log_review_result() call
g3/src/batch_executor.py           — remove JUDGE_PROVIDER/JUDGE_MODEL constants,
                                     update _judge_label() to read from config,
                                     _review_strategy() reads config + keeps "label" key,
                                     player turns use session._run_with_continuation()
g3/src/menu.py                     — new "batch роли" section
g3/src/streaming.py                — print_compact_triggered(), print_continuation_started(),
                                     print_code_review_header() gets iteration param
g3/g3.py                           — CLI flags for all new Config fields
```

### Unchanged
```
g3/src/feedback.py                 — ReviewPassed, ReviewIssues already planned
g3/src/prompts.py                  — CODE_REVIEWER_SYSTEM_PROMPT already planned
g3/src/providers/__init__.py       — no changes needed
g3/src/providers/message_adapter.py — no changes needed
```

---

## Implementation Order

### Phase A: Context Management (unblocks GLM-5 immediately)
- [ ] A.1 Add `context_limit`, `compact_threshold`, `max_continuation_attempts` to Config + g3.py
- [ ] A.2 Create `context_manager.py` with `_build_compact_summary()`, `_build_continuation_prompt()`, `_compact_codex_context()`
- [ ] A.3 Add `_make_compact_hooks()` and register in `ccg.py` ClaudeAgentOptions
- [ ] A.4 Add `_last_input_tokens` SSE tracking to `codex.py`
- [ ] A.5 Add `_run_with_continuation()` to `CoachPlayerSession` in `coach_player.py`
- [ ] A.6 Update `batch_executor.py` player turn to call `session._run_with_continuation()`
- [ ] A.7 Add `print_compact_triggered()`, `print_continuation_started()` to `streaming.py`

### Phase B: Batch Schedule Config
- [ ] B.1 Add `batch_pre/judge/post _provider/_model` + `test_writer_provider/model` to Config + g3.py
- [ ] B.2 Remove `JUDGE_PROVIDER`/`JUDGE_MODEL` constants from `batch_executor.py`
- [ ] B.3 Update `_judge_label()` to read from config
- [ ] B.4 Update `_review_strategy()` to read from config, preserve `"label"` key
- [ ] B.5 Add "batch роли" section to `menu.py`

### Phase C: Code Review Loop + Bug Logging
- [ ] C.1 Add `max_review_iterations` to Config + g3.py
- [ ] C.2 Make code review iterative loop in `coach_player.py`
- [ ] C.3 Add `_log_review_result()` to `context_manager.py`
- [ ] C.4 Update `print_code_review_header()` signature in `streaming.py`

---

## Non-Goals
- No per-turn token budget UI (too noisy)
- No context limit override per-provider (one limit for all)
- No automatic context limit detection from provider API
