# GSD-2 vs Tero

Date: 2026-03-30

## Snapshot

- `gsd-2` is a much broader product surface: standalone TypeScript agent platform, headless mode, MCP mode, VS Code extension, web UI, native engine, and a large extension/skills ecosystem.
- `tero` is a narrower Python execution system centered on a coach-player loop, configurable provider routing, batch/TDD/review phases, and a lightweight local UX.
- Rough scale check:
  - `tero/src`: ~36 code files, ~9.8k LOC
  - `gsd-2/src + packages`: ~1245 code files, ~365k LOC

Conclusion: we should not copy the overall architecture wholesale. The best move is to selectively steal operating-system-level patterns from `gsd-2` while keeping Tero's simpler execution core.

## Where GSD-2 Is Stronger

### 1. Durable state machine and resumability

`gsd-2` treats `.gsd/` as the source of truth, persists orchestration state on disk, and is explicitly designed for crash recovery and session resumption. It also separates startup, recovery, verification, metrics, and worktree lifecycle into dedicated modules.

High-value references:

- `docs/architecture.md`: disk-backed state, fresh session per unit, explicit dispatch pipeline
- `docs/commands.md`: pause/resume/forensics/visualize/history/export surface

Why this matters for Tero:

- Today our loop is strong during a live run, but it is much less operationally durable if the session dies or needs to be resumed or inspected later.

### 2. Model routing and budget awareness

`gsd-2` has a documented complexity classifier, downgrade-only routing semantics, budget pressure, cross-provider routing, and routing-history feedback loops.

Why this is valuable:

- Tero already has multi-provider support and fallback chains, so this is one of the cleanest "borrow and adapt" areas.
- We can add a real routing policy layer without changing the core coach-player model.

### 3. Verification and operational safety

`gsd-2` invests heavily in verification gates, stuck detection, watchdogs, diagnostics, and post-unit validation before advancing state.

Why this matters:

- Tero already has code review, TDD, and batch review phases, but the evidence/persistence side is still lighter than GSD's operational guarantees.

### 4. Skills/extensions platform

`gsd-2` has a strong concept of bundled extensions, skills, specialist agents, and tool-rich execution surfaces.

Why this matters:

- Tero has persona overlays and phase specialization, which is a good seed.
- GSD's approach suggests a clean next layer: reusable capability packs instead of only prompt-level specialization.

### 5. Docs and product packaging discipline

`gsd-2` has real ADRs, architecture docs, command references, migration docs, cost docs, parallel orchestration docs, and a changelog-driven product narrative.

Why this matters:

- This is not just polish. It lowers contributor ramp-up time and makes large behavioral systems easier to trust and evolve.

## What Tero Does Better

### 1. Execution loop clarity

Tero's core idea is easier to understand and reason about: parse plan, run player, review with coach, optionally add TDD/review/preplan layers, move step by step.

That is a real advantage:

- lower mental overhead
- easier debugging
- faster iteration on loop behavior
- less framework tax

`gsd-2` is more powerful, but also dramatically more complex and operationally heavier.

### 2. Explicit multi-role critique

Tero has a first-class coach-player structure plus separate code review and test-writer roles. That creates a sharper quality loop for implementation tasks than a generic single-worker orchestration model.

This is one of our strongest differentiators and should be preserved.

### 3. Provider flexibility is already deeply integrated into the loop

Tero's config and runtime already expose:

- independent player/coach providers
- separate review/test/batch providers
- coach fallback provider
- provider fallback chains
- preplanner provider
- runtime provider/model switching

This is a very practical strength because it is close to the execution path, not bolted on as an external control panel.

### 4. Persona preplanning is a strong niche advantage

The new preplan/persona layer gives Tero a domain-expert shaping mechanism before execution. That is a sharper, more opinionated implementation aid than the broader skill catalog idea.

If we extend this carefully, it can become a signature feature rather than a copy of GSD's skills system.

### 5. Smaller surface area means better change velocity

Tero can absorb architectural improvements without inheriting a full platform maintenance burden. That is strategically important for us right now.

## What We Should Steal First

### Tier 1: very high ROI

1. Persistent run state and resumability
   - Add a durable `.g3/state/` model for active run, completed step ledger, last known providers/models, verification evidence, and resumable cursor state.
   - Add lock file + recovery metadata for interrupted sessions.

2. Routing policy layer on top of existing providers
   - Keep current providers and fallback chains.
   - Add complexity tiers, downgrade-only routing, budget pressure, and routing history.

3. Stronger verification ledger
   - Persist what was run, whether it passed, and what evidence justified advancing the step.
   - This should work for tests, coach approval, code review, and batch judges.

### Tier 2: medium ROI

4. Operational diagnostics
   - `tero history`
   - `tero doctor`
   - basic "why did this stop" forensic summary

5. Skills-lite layer
   - Start with reusable local prompt/tool packs per domain.
   - Keep this much lighter than GSD extensions.

### Tier 3: only if product scope expands

6. Web UI / VS Code / MCP-first product surface
7. Native acceleration layer
8. Large extension marketplace model

These are powerful, but they are not the right first borrowings for our current size.

## What We Should Not Copy

- The full platform breadth
- The package/workspace sprawl
- The "everything is a product surface" mindset
- Complex state machinery before we nail a smaller durable core

If we copy GSD's shape too early, we will lose Tero's biggest strengths: speed, clarity, and loop quality.

## Practical Recommendation

Best path:

1. Keep Tero as the focused coach-player engine.
2. Import three ideas from GSD-2:
   - durable disk state
   - routing intelligence
   - verification/forensics
3. Grow persona/preplan into Tero's own differentiator instead of cloning the GSD skills platform.

## Source Anchors

### GSD-2

- `README.md`: standalone agent positioning, crash recovery, token/cost tracking, managed milestone execution
- `docs/architecture.md`: disk state, extension system, fresh sessions, dispatch pipeline, recovery modules
- `docs/dynamic-model-routing.md`: complexity routing, downgrade-only semantics, budget pressure, cross-provider routing
- `docs/commands.md`: auto mode, history, forensics, visualization, doctor, parallel orchestration, worktree lifecycle

### Tero

- `src/cli_entry.py`: focused CLI centered on `tero go`, provider selection, TDD/review/preplan/fallback-chain flags
- `src/coach_player.py`: explicit coach-player session, multi-role provider readiness, review/test/preplanner role model
- `src/config.py`: provider-rich runtime config and fallback-chain support
- `src/worktree.py`: lightweight workspace isolation with git/copy fallback
