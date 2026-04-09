# Debugger Integration — Design Brainstorm

**Date:** 2026-04-09
**Status:** In progress — brainstorming phase

---

## 1. Overview

Integrate the auto-researched bug-finding system from `debugger-research` into TerraGo as an autonomous debugger mode. The debugger runs after the main Coach-Player iteration as a post-processing step.

**Source:** `/Users/terobyte/Desktop/Projects/Active/auto/debugger-research/`
**Target:** `/Users/terobyte/Desktop/Projects/Active/tero/`

**What we take from research:**
- `prompt.md` — main bug-hunting prompt (3 passes + blind-spot checklist)
- `anchor_prompt.md` — "Search Engine" cross-function audit (6 checks)
- `file_strategy.py` — context builder (skeleton + hotspots for large files, budget allocation) — needs adaptation: replace `task_dir/buggy_files/` with `working_dir`
- `FOCUSED_PROMPTS` from `run_experiment.py` — for High mode (3 extra personas)
- `debugger.py:parse_bugs()` — JSON bug parser with prose fallback

**What we DON'T take:** The autoresearch loop, scorer, benchmark infrastructure.

---

## 2. Architecture: Pipeline Cycle

Three sequential agents in a loop:

```
┌─────────────────────────────────────────────────────┐
│                   DEBUGGER CYCLE                     │
│                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │  PLAYER  │──▶│  TESTER  │──▶│  FIXER   │        │
│  │ (finder) │   │(confirmer)│   │ (fixer)  │        │
│  └──────────┘   └──────────┘   └──────────┘        │
│       │                              │               │
│       │         git commit           │               │
│       └──────────────────────────────┘               │
│                  next iteration                      │
└─────────────────────────────────────────────────────┘
```

### Flow per iteration:
1. **Player** reads current code → finds bugs → writes to `bugs.md`
2. **Tester** reads `bugs.md` → writes pytest tests → runs them → confirms or rejects (false positive → grey)
3. **Fixer** reads confirmed bugs + failing tests → plans fix → implements → commits

### Cumulative (snowball):
- Fixer commits changes after each iteration
- Player in next iteration sees the updated code
- Each iteration finds fewer bugs as code gets cleaner

---

## 3. Victory Condition

**3 consecutive Player passes with 0 bugs found = VICTORY**

- Iteration N: Player finds 0 → skip Tester/Fixer, count as "clean pass" #1
- Iteration N+1: Player finds 0 → clean pass #2
- Iteration N+2: Player finds 0 → clean pass #3 → DONE, report victory

**Optimization:** When Player finds 0, skip Tester and Fixer entirely — go straight to next Player pass.

---

## 4. Providers — Per-Role Configuration

Each role has its own configurable provider (chosen in menu):

| Role | Example | Why |
|------|---------|-----|
| Player | Zai | Cheap, good at finding bugs |
| Tester | Claude | Reliable test writing |
| Fixer | Codex or Claude | Best price/quality for fixes |

Available providers: OpenAI Codex, Claude (Opus/Sonnet), Zai, OpenCode (Minimax 2.5)

**Removed providers:**
- ~~Black~~ — no longer supported, remove entirely
- OpenCode cleanup — keep only Minimax 2.5

---

## 5. Intensity Levels (Player LLM Calls)

| Level | Player calls | What runs | Description |
|-------|-------------|-----------|-------------|
| **Low** | 1 | `prompt.md` only | Economy mode |
| **Medium** | 2 | `prompt.md` + `anchor_prompt.md` | Optimum ~90%. This is the auto-researched pair. |
| **High** | 5 | Medium (2) + 3 ensemble personas (e.g. red_team, boundary, completeness) | Deep scan ~100%, diminishing returns |

**The two core prompts (auto-researched, optimized):**
- `prompt.md` — 3-pass analysis: structural → docstring-verification → integration/boundary audit + blind-spot checklist
- `anchor_prompt.md` — "Search Engine" cross-function audit: method selection, side-effect sequencing, resource contracts, data source completeness, max/min on empty, literal business semantics

**High mode** adds 3 focused persona prompts from the ensemble system (already defined in `run_experiment.py:FOCUSED_PROMPTS`).

Tester: always 1 call. Fixer: always 1 call.

Per iteration total: Low=3, Medium=4, High=7

---

## 6. Limit Modes

User chooses before starting:
- **By iterations** (e.g., 5, 10, 20)
- **By time** (e.g., 5 min, 10 min, 30 min, 1 hour)
- **Infinite** — runs until victory (3× clean pass) or manual stop

---

## 7. Tester — Design

Regular code-agent call (Claude Code / Codex / any provider) with a good system prompt.
No special architecture — just a prompt that forces test quality verification.

**Key principle:** LLMs often write bad tests. Tester prompt must force self-verification.
**Future:** Tester prompt will be improved via separate autoresearch session.

---

## 8. Fixer — Design

Regular code-agent call (Claude Code / Codex / any provider) with a good system prompt.
Provider's built-in planning handles think → implement under the hood.
After fix: Fixer commits changes. User pushes manually.

---

## 9. bugs.md Format

During debugging:
```markdown
# Bug Report — Debugger Session

## Found Bugs
- [RED] Bug #1: Off-by-one in calculate_total (line 42) — CONFIRMED by test
- [GREY] Bug #2: Missing null check in parse_input (line 15) — FALSE POSITIVE
- [GREEN] Bug #3: Wrong operator in validate_age (line 88) — FIXED

## Summary
- Found: 5
- False Positive: 1
- Fixed: 4
```

After completion (cleaned up):
```markdown
# Bug Report — Debugger Session (Complete)

- Bug #1: Off-by-one in calculate_total — Fixed
- Bug #2: Missing null check — False Positive
- Bug #3: Wrong operator in validate_age — Fixed

Total: 5 found, 1 false positive, 4 fixed
```

---

## 10. UI/UX — Console Counters

While running, display:
- 🔴 Red number = found bugs (confirmed by Tester)
- ⬜ Grey number = false positives
- 🟢 Green number = fixed bugs
- Spinner showing current phase (Player searching... / Tester verifying... / Fixer fixing...)
- Iteration counter (e.g., "Iteration 3/10" or "Iteration 3/∞")

Victory screen when 3× clean pass achieved.

---

## 11. Entry Point

**Debugger is ALWAYS available in the main menu** — not just after Coach-Player.

From TerraGo main menu:
1. User selects "Debugger" (always available)
2. Menu shows: provider selection (Player/Tester/Fixer), intensity (Low/Medium/High), limit (iterations/time/infinite)
3. User presses Start
4. Debugger runs autonomously on the current working directory
5. User returns later to see results

---

## 12. Cleanup Tasks (Prerequisites)

Before implementing debugger:

**Remove Provider Black (CCG):**
- Delete `src/providers/ccg.py`
- Remove `"black"`, `"turbo"` from `create_provider()` in `src/providers/__init__.py`
- Remove BLACK/TURBO presets from `src/menu.py` (CCG_MODEL_PRESETS)
- Remove `CcgEnv` class from `src/config.py` (or strip Black-specific accounts)
- Clean up env vars: `BLACKBOX_API_KEY`, `BLACKBOX_ACCOUNT_*`

**Cleanup OpenCode models:**
- In `src/menu.py` OPENCODE_MODEL_PRESETS — keep only `MiniMax M2.5 (free)`
- Remove: MIMO Pro, MIMO Omni, Kimi K2, Kimi K2.5, Z.AI, Nemotron 3 Super

**Cleanup Codex models:**
- In `src/menu.py` CODEX_MODEL_PRESETS — keep only Medium (default) and High (gpt-5.4)
- Remove: o3, o4-mini

**Provider list after cleanup:**

| Provider | Models | Use case |
|----------|--------|----------|
| Claude | opus, sonnet, haiku | Quality (Tester, Fixer) |
| Codex | medium (default), high | Price/quality Fixer |
| Zai | glm-5.1 | Cheap Player |
| OpenCode | minimax-2.5 | Free option |

---

## OPEN QUESTIONS

- [ ] Exact prompt for Tester — need to design robust verification prompt (future autoresearch)
- [ ] Exact prompt for Fixer — or delegate to Claude Code/Codex built-in planning?
- [x] ~~Medium mode (2 calls)~~ — RESOLVED: prompt.md + anchor_prompt.md (the auto-researched pair)
- [ ] How to handle Fixer failures (fix introduces new bugs)?
- [ ] Push workflow — manual only (confirmed: user pushes manually)
- [ ] High mode: which 3 personas from ensemble? (red_team + boundary + completeness?)
