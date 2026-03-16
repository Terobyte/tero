# G3 Learning System — Design Spec

> Date: 2026-03-14
> Status: approved
> Author: architect session
> Core idea: bugs = ground truth metric; system learns from every run

---

## 1. Problem Statement

G3 Coach-Player executes dual-agent tasks, but currently has no memory.
Each run starts from zero. The system doesn't know:
- Which agent pair works best for which task type
- Which judge produces more accurate verdicts
- What timeout is optimal for different complexities
- Whether 1 round or 5 rounds produces better results

**Without learning, the system is a dead conveyor — it does work but never gets smarter.**

---

## 2. Core Metric: Bug Score

### Definition

Bug Score = total number of defects found by automated verification after an agent completes work.

### Bug Detection Pipeline

```
Agent finishes work
        │
        ▼
┌───────────────────────────────────────────┐
│ Stage 1: Compilation / Import Check       │
│   Does it even run?                       │
│   crash = +10 bugs                        │
│                                           │
│ Stage 2: Test Suite (pytest / jest / etc)  │
│   Each test failure = +1 bug              │
│                                           │
│ Stage 3: Type Checker (mypy / tsc)        │
│   Each type error = +1 bug                │
│                                           │
│ Stage 4: Linter (ruff / eslint)           │
│   Each error = +1 bug                     │
│   Warnings NOT counted                    │
│                                           │
│ Stage 5: Review Agent (optional)          │
│   Another agent reviews the code          │
│   Each logical issue found = +1 bug       │
└───────────────────────────────────────────┘
        │
        ▼
   bug_score = sum of all bugs found
```

### Bug Score Scale

| Score | Meaning | System Reaction |
|---|---|---|
| 0 | Perfect | Strong positive signal |
| 1-2 | Minor issues | Acceptable, record as "good" |
| 3-5 | Significant problems | Record as "mediocre" |
| 6-10 | Major defects | Record as "poor" |
| 10+ / compile fail | Catastrophic | Record as "failed" |

### Human Feedback Layer

After each run, the system asks for optional human feedback:

```
Session complete. Bug score: 2

Rate this result:
  [A] Approve — result is good, I'll use it
  [R] Reject  — result is bad, needs redo
  [P] Partial — some parts good, some bad
  [S] Skip    — no opinion right now
  [N] Notes   — add free-text feedback
```

Human feedback = ground truth for calibrating weights.

---

## 3. Run Record Schema

Every run produces a structured record appended to `.g3/knowledge/runs.jsonl`:

```json
{
  "run_id": "run_042",
  "session_id": "sess_20260314_153000",
  "timestamp": "2026-03-14T15:30:00Z",

  "task": {
    "file": "./requirements.md",
    "type": "feature",
    "complexity": "medium",
    "word_count": 340,
    "keywords": ["auth", "middleware", "jwt"]
  },

  "config": {
    "agent_a": "ccg",
    "agent_b": "ccg2",
    "judge": "codex",
    "judge_mode": "single",
    "selection": "best",
    "timeout_s": 600,
    "autonomous": true,
    "worktree_mode": "git",
    "max_rounds": 3
  },

  "results": {
    "agent_a": {
      "success": true,
      "bug_score": 2,
      "bugs_by_stage": {
        "compile": 0,
        "tests": 1,
        "types": 1,
        "lint": 0,
        "review": 0
      },
      "duration_s": 180,
      "files_changed": 4,
      "diff_lines": 120
    },
    "agent_b": {
      "success": true,
      "bug_score": 0,
      "bugs_by_stage": {
        "compile": 0,
        "tests": 0,
        "types": 0,
        "lint": 0,
        "review": 0
      },
      "duration_s": 240,
      "files_changed": 3,
      "diff_lines": 85
    }
  },

  "judge_verdict": {
    "winner": "agent_b",
    "action": "winner_b",
    "confidence": "high",
    "scores": {
      "agent_a": { "total": 42 },
      "agent_b": { "total": 50 }
    }
  },

  "outcome": {
    "rounds_used": 1,
    "total_duration_s": 520,
    "final_winner": "agent_b",
    "promoted": true
  },

  "human_feedback": {
    "rating": "approve",
    "notes": "clean implementation",
    "timestamp": "2026-03-14T15:40:00Z"
  },

  "quality_score": 0.87
}
```

### Quality Score Formula

```
quality_score = (
    weights.bug_score     * normalize(max_bug - actual_bug, 0, max_bug)
  + weights.test_pass     * (tests_passed / tests_total)
  + weights.duration      * normalize(max_time - actual_time, 0, max_time)
  + weights.retry_penalty * normalize(max_rounds - rounds_used, 0, max_rounds)
  + weights.human         * human_factor
)
```

Where:
- `normalize(value, min, max)` → 0.0 to 1.0
- `human_factor`: approve=1.0, partial=0.5, reject=0.0, skip=use_auto_score
- `max_bug` = 10 (anything above is capped)

### Default Weights

```yaml
weights:
  bug_score: 0.50       # most important — fewer bugs = better
  test_pass: 0.20       # tests passing matters
  duration: 0.10        # faster is slightly better
  retry_penalty: 0.10   # fewer retries = better
  human: 0.10           # human correction factor
```

Weights are **self-calibrating**: after 20+ runs, the system computes correlation between each factor and human approve rate, and adjusts weights to maximize prediction accuracy.

---

## 4. Knowledge Base Structure

```
.g3/knowledge/
├── runs.jsonl           # append-only log, one JSON per line
├── insights.yaml        # auto-generated rules (rebuilt after each run)
├── overrides.yaml       # human-set rules (never auto-modified)
└── weight_history.jsonl # weight calibration history
```

### Auto-Generated Insights

After each run, the system re-analyzes all runs and generates insights:

```yaml
# .g3/knowledge/insights.yaml
generated_at: "2026-03-14T16:00:00Z"
total_runs: 42

# Agent pair performance
agent_pairs:
  ccg+codex:
    runs: 15
    avg_bug_score: 0.8
    approve_rate: 0.92
    best_for: ["refactor", "bugfix"]
    avg_duration_s: 320

  ccg+ccg2:
    runs: 20
    avg_bug_score: 2.4
    approve_rate: 0.65
    best_for: ["feature"]
    avg_duration_s: 210

# Judge performance
judges:
  codex:
    runs_as_judge: 18
    verdict_accuracy: 0.89  # % where human agreed with judge choice
    avg_judge_time_s: 45

  ccg:
    runs_as_judge: 24
    verdict_accuracy: 0.71
    avg_judge_time_s: 120

# Task type patterns
task_types:
  refactor:
    best_pair: "ccg+codex"
    best_judge: "codex"
    optimal_timeout: 600
    optimal_rounds: 2

  feature:
    best_pair: "ccg+ccg2"
    best_judge: "ccg"
    optimal_timeout: 900
    optimal_rounds: 3

# Timeout patterns
timeout_insights:
  - "timeout<300 on high complexity: 60% failure rate (5 runs)"
  - "timeout>600 on low complexity: no improvement vs 300 (8 runs)"

# Current weight calibration
calibrated_weights:
  bug_score: 0.55
  test_pass: 0.18
  duration: 0.08
  retry_penalty: 0.12
  human: 0.07

  calibration_confidence: "medium"  # <20 runs = low, 20-50 = medium, 50+ = high
  last_calibrated: "2026-03-14T16:00:00Z"
```

### Human Overrides

The human can set explicit rules that always take priority:

```yaml
# .g3/knowledge/overrides.yaml
rules:
  - name: "never use ccg2 as judge"
    condition: { judge: "ccg2" }
    action: "block"
    reason: "ccg2 gives inconsistent verdicts"

  - name: "always use codex for security tasks"
    condition: { task_keywords: ["security", "auth", "crypto"] }
    action: "force_config"
    config: { agent_b: "codex", judge: "codex" }
```

---

## 5. Adaptive Recommendation Engine

### Pre-Run Flow

```
User runs: g3 /go --plan ./req.md
        │
        ▼
┌─────────────────────────────────────┐
│ 1. Classify task                    │
│    - parse plan file                │
│    - detect type (feature/bug/...)  │
│    - estimate complexity            │
│    - extract keywords               │
├─────────────────────────────────────┤
│ 2. Check overrides                  │
│    - human rules take priority      │
├─────────────────────────────────────┤
│ 3. Query knowledge base             │
│    - find similar past runs         │
│    - compute recommended config     │
├─────────────────────────────────────┤
│ 4. Present recommendation           │
│    - show suggested config          │
│    - show confidence level          │
│    - show supporting data           │
├─────────────────────────────────────┤
│ 5. User accepts or overrides        │
└─────────────────────────────────────┘
```

### Recommendation Display

```text
╔══════════════════════════════════════════════════════════════╗
║  G3 Pre-Run Analysis                                        ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Task: ./req.md                                              ║
║  Type: refactor (detected from keywords)                     ║
║  Complexity: medium (87 lines)                               ║
║                                                              ║
║  📊 Based on 15 similar runs:                                ║
║                                                              ║
║  RECOMMENDED:                                                ║
║    Agent A: ccg                                              ║
║    Agent B: codex  ← win rate 78% on refactoring             ║
║    Judge:   ccg2   ← 89% verdict accuracy                    ║
║    Timeout: 600s                                             ║
║    Rounds:  2                                                ║
║                                                              ║
║  ⚠️  AVOID: ccg+ccg2 (avg 3.1 bugs on this type)            ║
║                                                              ║
║  Confidence: MEDIUM (15 similar runs)                        ║
║                                                              ║
║  [Enter] Accept  [O] Override  [D] Details                   ║
╚══════════════════════════════════════════════════════════════╝
```

When `<5 runs`:
```text
║  📊 Not enough data yet (3 runs total)                       ║
║     Using defaults. Every run improves recommendations.      ║
```

### Post-Run Learning

```
Run completes
        │
        ▼
┌─────────────────────────────────────┐
│ 1. Run Bug Detection Pipeline       │
│    → compute bug_score              │
├─────────────────────────────────────┤
│ 2. Compute quality_score            │
│    → apply weighted formula         │
├─────────────────────────────────────┤
│ 3. Append to runs.jsonl             │
├─────────────────────────────────────┤
│ 4. Ask human for feedback           │
│    → approve/reject/partial/skip    │
├─────────────────────────────────────┤
│ 5. Rebuild insights.yaml            │
│    → re-analyze all runs            │
│    → update agent pair stats        │
│    → update judge accuracy          │
│    → recalibrate weights (if 20+)   │
├─────────────────────────────────────┤
│ 6. Show learning summary            │
│    "This run improved ccg+codex     │
│     stats: avg bug 0.8 → 0.7"      │
└─────────────────────────────────────┘
```

---

## 6. Weight Self-Calibration

After 20+ runs with human feedback, the system can calibrate weights.

### Algorithm (simplified)

```python
def calibrate_weights(runs: list[RunRecord]) -> dict[str, float]:
    """Find weights that maximize correlation with human approve."""
    runs_with_feedback = [r for r in runs if r.human_feedback.rating != "skip"]

    if len(runs_with_feedback) < 20:
        return DEFAULT_WEIGHTS  # not enough data

    # For each factor, compute correlation with human_approve
    factors = ["bug_score", "test_pass", "duration", "retry_penalty"]
    correlations = {}

    for factor in factors:
        values = [get_factor_value(r, factor) for r in runs_with_feedback]
        approves = [1.0 if r.human_feedback.rating == "approve" else 0.0
                    for r in runs_with_feedback]
        correlations[factor] = compute_correlation(values, approves)

    # Normalize correlations to weights (sum = 0.90, leaving 0.10 for human)
    total = sum(abs(c) for c in correlations.values())
    weights = {f: abs(c) / total * 0.90 for f, c in correlations.items()}
    weights["human"] = 0.10

    return weights
```

### Calibration Guard Rails

- Weights are always between 0.05 and 0.60 (no single factor dominates)
- `bug_score` weight never drops below 0.30 (bugs are always important)
- `human` weight never drops below 0.05
- Calibration runs max once per day (not on every run)
- Previous weights are logged in `weight_history.jsonl`

---

## 7. Task Classification

The system auto-classifies tasks to match against knowledge base patterns.

### Classification Method

```python
TASK_KEYWORDS = {
    "feature": ["add", "implement", "create", "new", "build"],
    "bugfix": ["fix", "bug", "error", "crash", "broken", "issue"],
    "refactor": ["refactor", "clean", "reorganize", "simplify", "restructure"],
    "test": ["test", "coverage", "spec", "assertion"],
    "docs": ["document", "readme", "docstring", "comment"],
}

COMPLEXITY_THRESHOLDS = {
    "low": 50,      # < 50 words
    "medium": 200,   # 50-200 words
    "high": 200,     # > 200 words
}

def classify_task(plan_text: str) -> TaskClassification:
    words = plan_text.lower().split()
    word_count = len(words)

    # Type by keyword frequency
    type_scores = {}
    for task_type, keywords in TASK_KEYWORDS.items():
        type_scores[task_type] = sum(1 for w in words if w in keywords)

    task_type = max(type_scores, key=type_scores.get)

    # Complexity by word count
    if word_count < 50:
        complexity = "low"
    elif word_count < 200:
        complexity = "medium"
    else:
        complexity = "high"

    return TaskClassification(
        type=task_type,
        complexity=complexity,
        word_count=word_count,
        keywords=[w for w in words if any(w in kws for kws in TASK_KEYWORDS.values())],
    )
```

---

## 8. Integration Points in G3 Pipeline

Learning system touches these stages:

| Stage | What happens | Learning involvement |
|---|---|---|
| Pre-run | Config resolution | Query KB → recommend config |
| Agent execution | Agents work | (no change) |
| Post-agent | Bug detection | Run pipeline → compute bug_score |
| Judge | Verdict | (no change, but verdict accuracy tracked) |
| Post-promote | Session complete | Record run, ask human feedback |
| Background | Insights rebuild | Re-analyze all runs, update insights |
| Pre-run (next) | Config resolution | Use updated insights |

### Minimal MVP Integration

Even MVP Level 1 must:
1. Run Bug Detection Pipeline after each agent
2. Record run to `runs.jsonl`
3. Ask for human feedback (can be skipped)

This costs ~50 lines of code and provides data from day 1.

---

## 9. CLI Commands for Learning

| Command | Purpose |
|---|---|
| `g3 /insights` | Show current knowledge base summary |
| `g3 /insights --agent-pairs` | Show agent pair performance |
| `g3 /insights --judges` | Show judge accuracy |
| `g3 /insights --weights` | Show current weight calibration |
| `g3 /feedback <session_id>` | Add/change human feedback for a past run |
| `g3 /recommend --plan ./req.md` | Show recommended config without running |
| `g3 /override add "never use X"` | Add human override rule |
| `g3 /override list` | List all override rules |
| `g3 /export-data` | Export runs.jsonl for external analysis |

---

## 10. Data Privacy & Retention

- `runs.jsonl` stores config and metrics only, never source code
- Diffs are stored in session dirs, not in knowledge base
- Retention: keep all runs forever (tiny data, ~1KB per run)
- Export: `g3 /export-data` for external analysis
- Reset: `g3 /knowledge reset` to start fresh

---

## 11. Evolution Timeline

| Runs | System behavior |
|---|---|
| 0-5 | "Not enough data. Using defaults." |
| 5-20 | Basic patterns visible. Shows recommendations with "low confidence" |
| 20-50 | Weight calibration enabled. "Medium confidence" recommendations |
| 50+ | Reliable patterns. "High confidence". System knows optimal configs per task type |
| 100+ | Can predict human approve/reject with 80%+ accuracy |

This is the path from "dead conveyor" to "system that knows itself."
