# Context Management + Batch Orchestration v2 — Design Spec
_Date: 2026-03-17_

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
│  85k tokens →          │  with compact context       │
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
g3/src/context_manager.py    — ContextBudget, build_compact_prompt(),
                               compact_summary(), _build_compact_summary(),
                               _build_continuation_prompt()
```

### 1.3 Modified Files

| File | Change |
|------|--------|
| `config.py` | + `context_limit`, `compact_threshold`, `max_continuation_attempts` |
| `providers/ccg.py` | + PreCompact hook registration in ClaudeAgentOptions |
| `providers/codex.py` | + read `usage` from SSE, store `_last_input_tokens` |
| `coach_player.py` | + `_run_with_continuation()` wraps `_run_turn()` |
| `batch_executor.py` | + call `_run_with_continuation()` for player turns |
| `streaming.py` | + `print_compact_triggered()`, `print_continuation_started()` |

### 1.4 Config Fields

```python
@dataclass
class Config:
    # Context Management
    context_limit: int = 110_000              # universal token limit
    compact_threshold: float = 0.85           # compact at 93.5k tokens
    max_continuation_attempts: int = 2        # continuation retries
```

ENV vars: `G3_CONTEXT_LIMIT`, `G3_COMPACT_THRESHOLD`, `G3_MAX_CONTINUATION_ATTEMPTS`
CLI flags: `--context-limit`, `--compact-threshold`, `--max-continuation`

### 1.5 PreCompact Hook (CCG + Claude — mid-turn)

The Claude Agent SDK fires `PreCompact` during a running turn when context approaches the limit. We register a hook that provides a custom summary prompt:

```python
# providers/ccg.py

def _make_compact_hook(context_limit: int, threshold: float) -> dict:
    compact_at = int(context_limit * threshold)  # 93_500

    async def on_pre_compact(hook_input: PreCompactHookInput) -> dict:
        return {
            "continue": True,
            "custom_instructions": (
                "Summarize this conversation compactly. Preserve: "
                "completed steps with proof, file paths changed, "
                "current implementation state, pending work. "
                f"Target: under {compact_at // 1000}k tokens."
            )
        }

    return {"PreCompact": [{"matcher": "*", "hooks": [on_pre_compact]}]}
```

```python
options = ClaudeAgentOptions(
    ...
    hooks=_make_compact_hook(config.context_limit, config.compact_threshold),
)
```

Flow:
```
GLM-5 uses tools → context grows
  → SDK detects ~93.5k tokens
  → PreCompact hook fires
  → GLM-5 summarizes its own history
  → SDK continues with compact context
  → GLM-5 writes PHASE_COMPLETE:
```

### 1.6 Codex Token Tracking (inter-turn compaction)

Codex has no SDK hook. Last SSE chunk contains `usage`:

```json
{"choices": [...], "usage": {"prompt_tokens": 87234, "completion_tokens": 412}}
```

```python
# providers/codex.py — end of run()
if usage := data.get("usage"):
    self._last_input_tokens = usage.get("prompt_tokens", 0)
```

```python
# coach_player.py — after Codex turn
if hasattr(provider, "_last_input_tokens"):
    tokens = provider._last_input_tokens
    if tokens > config.context_limit * config.compact_threshold:
        compact_ctx = await _compact_codex_context(provider, messages, config)
        # compact_ctx prepended to next turn's prompt
```

### 1.7 Continuation Agent (all providers)

When a turn ends without required markers, instead of immediate rejection:

```python
# coach_player.py
async def _run_with_continuation(self, role, prompt, ...):
    result = await self._run_turn(role, prompt, ...)

    for attempt in range(self.config.max_continuation_attempts):
        if _has_completion_markers(result.text, role):
            return result  # done

        streaming_ui.print_continuation_started(role, attempt + 1)
        summary = _build_compact_summary(result.messages)
        # summary uses only assistant text blocks, drops tool results
        continuation_prompt = _build_continuation_prompt(summary, role)
        result = await self._run_turn(role, continuation_prompt, ...)

    return result  # return as-is, normal downstream logic handles it
```

`_build_compact_summary()` — takes `messages`, keeps assistant text blocks only (heaviest parts — tool results — discarded), builds compact narrative of what was done.

`_build_continuation_prompt()` — wraps summary:
```
"You are continuing previous work. Here is what was already done:
{summary}
Now output the required completion report: PHASE_COMPLETE: {phase} ..."
```

Streaming UI messages:
```
⚡ [Player] Context compacted (91k → 11k tokens) — continuing...
🔄 [Player] No completion markers — starting continuation agent (1/2)...
✅ [Player] Continuation succeeded — PHASE_COMPLETE found
✗  [Player] Continuation exhausted (2/2) — rejecting phase
```

---

## Part 2: Batch Orchestration v2

### 2.1 Configurable Schedule + Providers

Current schedule is hardcoded: `3 × GLM-5 / 1 × Claude Sonnet / 1 × GLM-5`.

**New default:** `3 × GLM-5 / 1 × Codex-High / 1 × GLM-5`

Fully configurable — numbers AND provider/model per slot:

```python
@dataclass
class Config:
    # Schedule numbers (existing)
    batch_pre_judge_attempts: int = 3
    batch_judge_attempts: int = 1
    batch_post_judge_attempts: int = 1

    # Provider + model per slot (NEW)
    batch_pre_provider: str = "ccg"
    batch_pre_model: str = ""                    # GLM-5 default
    batch_judge_provider: str = "codex"          # NEW default (was claude)
    batch_judge_model: str = "gpt-5.4-high"      # NEW default (was sonnet)
    batch_post_provider: str = "ccg"
    batch_post_model: str = ""                   # GLM-5 default

    # Test Writer (NEW)
    test_writer_provider: str = "ccg"
    test_writer_model: str = ""
```

### 2.2 Menu — New Section

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

Clicking any role opens provider selection → then model selection (same flow as existing Player/Coach selection).

Example custom schedule `5 × Kimi / 4 × Codex-Ultra / 1 × GLM-5`:
- Pre-Coach: `ccg / kimi-k2.5`, count=5
- Judge: `codex / gpt-5.4-ultra-high`, count=4
- Post-Coach: `ccg / glm-5`, count=1

### 2.3 batch_executor.py changes

`_review_strategy()` reads new config fields instead of hardcoded constants:

```python
def _review_strategy(self, attempt_num: int) -> dict[str, str]:
    pre, judge, _post = self._schedule_counts()
    judge_start = pre + 1
    judge_end = pre + judge

    if judge > 0 and judge_start <= attempt_num <= judge_end:
        return {
            "header_role": "judge",
            "provider_name_override": self.session.config.batch_judge_provider,
            "model_override": self.session.config.batch_judge_model,
            "review_role": "judge",
        }

    # pre or post
    return {
        "header_role": "coach",
        "provider_name_override": self.session.config.batch_pre_provider,
        "model_override": self.session.config.batch_pre_model,
        "review_role": "coach",
    }
```

### 2.4 Iterative Code Review Loop

Code Review is no longer a single pass — it's a full coach-player loop until zero bugs found.

```
After Coach approves step:
  Code Reviewer (review_provider) reads git diff →
    found bugs → Player (GLM-5) fixes →
    Code Reviewer checks again →
    ... repeat until CODE_REVIEW_PASSED →
  Log results to .g3/bugs/step-N-YYYY-MM-DD.md
  Mark step DONE
```

```python
# coach_player.py — after Coach Approved

if self.config.code_review:
    for review_attempt in range(self.config.max_turns):
        streaming_ui.print_code_review_header(step_num, total_steps, review_attempt + 1)

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
        _log_review_result(step_num, review_verdict, self.review_provider)

        if isinstance(review_verdict, ReviewPassed):
            streaming_ui.print_review_passed(step_num)
            break  # → mark step done

        # bugs found → Player fixes
        streaming_ui.print_review_issues(review_verdict.text)
        player_fix_prompt = build_player_fix_prompt(review_verdict.text)
        await self._run_turn(role="player", prompt=player_fix_prompt, ...)
```

### 2.5 Bug Logging

All bugs logged to `<working_dir>/.g3/bugs/`:

```
.g3/bugs/
  step-1-2026-03-17.md
  step-3-2026-03-17.md
```

File format:
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
# context_manager.py or coach_player.py
def _log_review_result(step_num: int, verdict, provider, working_dir: str) -> None:
    bugs_dir = Path(working_dir) / ".g3" / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = bugs_dir / f"step-{step_num}-{date_str}.md"
    # append iteration result
```

---

## Part 3: Summary of All Changes

### New files
```
g3/src/context_manager.py
```

### Modified files
```
g3/src/config.py                — context_limit, compact_threshold,
                                  max_continuation_attempts,
                                  batch_pre/judge/post _provider/_model,
                                  test_writer_provider/model
g3/src/providers/ccg.py         — PreCompact hook registration
g3/src/providers/codex.py       — SSE usage tracking (_last_input_tokens)
g3/src/coach_player.py          — _run_with_continuation(), code review loop,
                                  _log_review_result(), review provider routing
g3/src/batch_executor.py        — _review_strategy() reads config (not hardcoded),
                                  _run_with_continuation() for player turns
g3/src/menu.py                  — new "batch роли" section, provider+model per slot
g3/src/streaming.py             — print_compact_triggered(),
                                  print_continuation_started(),
                                  print_code_review_header() updated
```

### Unchanged
```
g3/src/feedback.py              — ReviewPassed, ReviewIssues already planned
g3/src/prompts.py               — CODE_REVIEWER_SYSTEM_PROMPT already planned
g3/src/providers/__init__.py    — no changes needed
g3/g3.py                        — CLI flags added for new Config fields
```

---

## Implementation Order

### Phase A: Context Management (unblocks GLM-5 immediately)
- [ ] A.1 Add context fields to Config
- [ ] A.2 Create context_manager.py with _build_compact_summary(), _build_continuation_prompt()
- [ ] A.3 Add PreCompact hook to ccg.py
- [ ] A.4 Add _last_input_tokens tracking to codex.py
- [ ] A.5 Add _run_with_continuation() to coach_player.py
- [ ] A.6 Update batch_executor.py to use _run_with_continuation()
- [ ] A.7 Add streaming UI messages

### Phase B: Batch Schedule Config
- [ ] B.1 Add batch_pre/judge/post provider/model fields to Config
- [ ] B.2 Update _review_strategy() to read from config
- [ ] B.3 Add "batch роли" section to menu.py
- [ ] B.4 Update default judge to codex/gpt-5.4-high

### Phase C: Code Review Loop + Bug Logging
- [ ] C.1 Make code review iterative (coach-player loop)
- [ ] C.2 Add _log_review_result() with .g3/bugs/ logging
- [ ] C.3 Add test_writer_provider/model to Config and menu

---

## Non-Goals
- No per-turn token budget UI (too noisy)
- No context limit override per-provider (one limit for all)
- No automatic context limit detection from provider API
