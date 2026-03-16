# Coach-Player SDK Audit

Date: 2026-03-16

Target plan: `/Users/terobyte/Desktop/Projects/Active/tero/docs/superpowers/plans/2026-03-15-coach-player-sdk.md`

Target implementation reviewed: `/Users/terobyte/tmp/workspace/g3`

## Verdict

The implementation is not complete and is not shippable.

Only 6 of 17 expected deliverable files exist:

- `g3/requirements.txt`
- `g3/src/__init__.py`
- `g3/src/config.py`
- `g3/src/learning/__init__.py`
- `g3/src/providers/__init__.py`
- `g3/src/providers/ccg.py`

The rest of the planned runtime, CLI, feedback loop, recorder, and tests are missing.

## Audit Scope

This audit compares the current implementation against:

- the implementation plan
- the design spec
- the actual workspace layout in `/Users/terobyte/tmp/workspace`

It also checks the existing files for integration defects and runtime blockers.

## All Problems Found

### P0: Missing required deliverables

1. `g3/g3.py` is missing.
   Without the CLI entrypoint, the rewritten tool cannot be launched as described in the plan or spec.

2. `g3/src/coach_player.py` is missing.
   The central player -> coach -> feedback -> retry loop does not exist.

3. `g3/src/streaming.py` is missing.
   There is no real-time terminal rendering for SDK messages, tool usage, verdicts, or session summaries.

4. `g3/src/prompts.py` is missing.
   The player and coach system prompts, plus prompt builders, do not exist.

5. `g3/src/feedback.py` is missing.
   There is no parser for extracting `IMPLEMENTATION_APPROVED` or coach feedback from the final assistant message.

6. `g3/src/plan_tracker.py` is missing.
   Requirements parsing and checklist rendering are not implemented.

7. `g3/src/learning/recorder.py` is missing.
   Run history and the rewritten `RunRecord` storage model do not exist.

8. All planned test files are missing:
   - `g3/tests/test_config.py`
   - `g3/tests/test_feedback.py`
   - `g3/tests/test_plan_tracker.py`
   - `g3/tests/test_recorder.py`
   - `g3/tests/test_streaming.py`

9. The implementation stops after a partial foundation stage.
   The plan contains three chunks. Only part of chunk 1 was started, and chunks 2 and 3 were not implemented.

### P0: End-to-end functionality cannot work

10. There is no executable path from `requirements.md` to an approved implementation.
    Even if `config.py` and `ccg.py` were perfect, there is nothing that actually runs the player, runs the coach, loops across turns, or prints a session report.

11. There is no history command.
    The spec requires `tero history [--limit N]`, but there is no CLI surface or recorder implementation to support it.

12. There is no graceful interrupt handling.
    The spec requires `Ctrl+C` handling, but there is no loop or signal handling code where this behavior could exist.

13. There is no max-turn termination behavior.
    The spec requires stop conditions for approval, timeout, and max turns reached. None of this exists yet.

### P1: Provider implementation does not match the planned SDK

14. `g3/src/providers/ccg.py` imports `claude_code_sdk`, but `g3/requirements.txt` declares `claude-agent-sdk`.
    This is a direct dependency mismatch.

15. `g3/src/providers/ccg.py` uses a different API shape from the one specified in the design.
    The design expects `from claude_agent_sdk import query, ClaudeAgentOptions`, but the implementation uses `ClaudeCode` and `ClaudeCodeOptions`.

16. `g3/src/providers/ccg.py` tells the user to install the wrong package.
    The runtime error says `pip install claude-code-sdk`, while the plan and requirements file point to `claude-agent-sdk`.

17. The provider is documented as “Claude Agent SDK” but implemented as “Claude Code SDK”.
    This inconsistency makes the module misleading even before runtime.

18. Current runtime behavior proves the mismatch is a hard blocker.
    Running the provider currently fails with:
    `ImportError: claude-code-sdk not installed. Install with: pip install claude-code-sdk`

### P1: Config implementation is not wired to the real workspace shape

19. `g3/src/config.py` reads `project.get("provider", {})`, but the real workspace config at `/Users/terobyte/tmp/workspace/.g3/config.yaml` uses `providers`, not `provider`.
    As a result, provider settings such as `claude_home` are silently ignored.

20. The real workspace config still uses the old duel-system schema.
    It contains keys like `max_rounds`, `agent_a`, `agent_b`, `judge`, `judge_mode`, `selection`, and `run_bug_detection`.
    The rewritten config loader does not migrate or reject these keys, so old config can appear “accepted” while actually being ignored.

21. `resolve_config()` does not align the default plan path with the actual filesystem layout.
    The default `plan_file` is `requirements.md`, but the implementation was placed in `/Users/terobyte/tmp/workspace/g3` while the real file lives at `/Users/terobyte/tmp/workspace/requirements.md`.
    If the future CLI is launched from `g3`, the default plan path will miss the real requirements file.

22. `Config.claude_home` is not actually connected to `CcgEnv.from_env()`.
    The config object can hold a custom `claude_home`, but nothing in the existing code passes it into `CcgEnv.from_env()`.
    Even after parsing config, the provider would still use the default `~/.claude-glm` unless future code manually wires it.

23. `resolve_config()` silently drops unknown keys instead of failing on incompatible config.
    This is risky during a rewrite because a stale `.g3/config.yaml` can look valid while its important settings are ignored.

24. The current config behavior proves the schema mismatch.
    Running `resolve_config({"working_dir": "/Users/terobyte/tmp/workspace"})` returns default values and does not pick up provider details from the existing config file.

### P1: The rewrite is not integrated into the real project

25. The work was done in `/Users/terobyte/tmp/workspace/g3`, not in the actual project tree `/Users/terobyte/Desktop/Projects/Active/tero/g3`.
    Even if it were more complete, it would still not replace the current implementation in the real repo.

26. Git status in `/Users/terobyte/tmp/workspace` shows `g3/` as an untracked addition rather than an integrated rewrite.
    This confirms the work currently lives as a sidecar directory, not as an applied migration.

27. The plan called for deleting old duel-system modules in the target project.
    That migration has not happened in the actual repo under review, so the rewrite is not just incomplete, it is also not installed in place of the old system.

### P2: Test and verification gaps

28. There are no executable tests for the new behavior.
    `g3/tests` only contains `__init__.py`.

29. Running `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests` inside `/Users/terobyte/tmp/workspace/g3` reports:
    `no tests ran in 0.01s`

30. There is no verification for config loading, provider wiring, prompt generation, verdict parsing, checklist formatting, streaming UI, or recorder persistence.

31. There is no smoke test for importing the actual SDK path described by the plan.
    The only import that succeeds today is the guarded module import; the real provider execution path fails when invoked.

### P2: Repository hygiene and deliverable quality issues

32. Compiled artifacts were left inside the implementation tree:
    - `g3/src/__pycache__/...`
    - `g3/src/providers/__pycache__/...`

33. The temporary implementation directory contains tooling artifacts:
    - `.pytest_cache`
    - `.coverage`
    - `.DS_Store`

34. The reportable deliverable is not cleanly packaged.
    A partial feature with caches and generated files mixed in makes later review and cherry-picking harder.

35. `g3/tests/__init__.py` says `"""Tests for tero."""`, but there are no actual tests.
    This is minor, but it reinforces that the testing layer was scaffolded and abandoned before implementation.

## Gap Matrix Against Plan

| Planned Item | Status | Notes |
|---|---|---|
| Rewrite `g3.py` | Missing | CLI not implemented |
| Rewrite `src/config.py` | Partial | Exists, but schema integration is incomplete |
| Create `src/coach_player.py` | Missing | Main loop absent |
| Create `src/streaming.py` | Missing | No live terminal UI |
| Create `src/prompts.py` | Missing | No role prompts |
| Create `src/feedback.py` | Missing | No verdict parser |
| Create `src/plan_tracker.py` | Missing | No checklist support |
| Create `src/providers/ccg.py` | Partial | Exists, but wrong SDK/package path |
| Rewrite `src/learning/recorder.py` | Missing | History not implemented |
| Create `tests/test_feedback.py` | Missing | No test |
| Create `tests/test_plan_tracker.py` | Missing | No test |
| Rewrite `tests/test_config.py` | Missing | No test |
| Rewrite `tests/test_recorder.py` | Missing | No test |
| Create `tests/test_streaming.py` | Missing | No test |

## Verification Performed

Commands run during the audit:

```bash
find /Users/terobyte/tmp/workspace/g3 -maxdepth 4 -type f | sort
cd /Users/terobyte/tmp/workspace/g3 && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests
cd /Users/terobyte/tmp/workspace/g3 && python3 - <<'PY'
import sys
sys.path.insert(0, '.')
from src.config import resolve_config
print(resolve_config({'working_dir': '/Users/terobyte/tmp/workspace'}))
PY
cd /Users/terobyte/tmp/workspace/g3 && python3 - <<'PY'
import sys, asyncio
sys.path.insert(0, '.')
from src.providers.ccg import run_agent
from src.config import CcgEnv
async def main():
    try:
        agen = run_agent('x', 'y', '/tmp', CcgEnv.from_env())
        await agen.__anext__()
    except Exception as e:
        print(type(e).__name__ + ': ' + str(e))
asyncio.run(main())
PY
git -C /Users/terobyte/tmp/workspace status --short
```

Observed results:

- only the foundation files exist
- pytest finds zero tests
- config parsing falls back to defaults against the real workspace config
- provider execution fails immediately on the SDK import mismatch
- the new `g3/` lives as a temp-side addition, not an integrated rewrite

## Resolution Plan

### Phase 1: Make the rewrite structurally real

1. Move the work into the actual project tree that is supposed to be rewritten.
2. Remove generated junk from the deliverable tree:
   - `__pycache__`
   - `.pytest_cache`
   - `.coverage`
   - `.DS_Store`
3. Decide whether the rewrite replaces the existing `g3` in-place or is staged on a dedicated branch, but stop building it as an orphan temp folder.

### Phase 2: Fix the foundation before adding more files

1. Choose one SDK and use it consistently across:
   - `requirements.txt`
   - provider imports
   - provider runtime code
   - docs and error messages
2. Rewrite `src/providers/ccg.py` to match the design spec exactly.
3. Wire `Config.claude_home` into `CcgEnv.from_env()`.
4. Decide how `.g3/config.yaml` should be handled:
   - support only the new schema and fail fast on old keys
   - or add explicit migration logic from old duel config
5. Fix default path resolution so `requirements.md` is found from the actual working directory layout.

### Phase 3: Implement the missing runtime in dependency order

1. Add `src/prompts.py`.
2. Add `src/feedback.py`.
3. Add `src/plan_tracker.py`.
4. Add `src/streaming.py`.
5. Add `src/learning/recorder.py`.
6. Add `src/coach_player.py`.
7. Add `g3.py`.

Reason for this order:

- prompts, feedback, and checklist are leaf modules and easy to test first
- streaming can be added before the main loop
- recorder depends on final session result shapes
- `coach_player.py` depends on almost everything else
- `g3.py` should be last, once the runtime API is stable

### Phase 4: Restore test-first coverage

Create and pass these tests before declaring the rewrite done:

1. `tests/test_config.py`
   Validate defaults, env overrides, CLI overrides, token resolution, and project config loading.

2. `tests/test_feedback.py`
   Validate approval parsing, issue extraction, empty output handling, and ignoring approval markers outside assistant text.

3. `tests/test_plan_tracker.py`
   Validate numbered lists, checkbox parsing, dash lists, approved formatting, and issue-mode formatting.

4. `tests/test_streaming.py`
   Validate tool counting, text truncation, result handling, and verdict rendering.

5. `tests/test_recorder.py`
   Validate record append, history retrieval, and schema serialization.

6. Add one light integration test for the session loop with a fake provider stream.
   This catches state transitions, checklist updates, and stop conditions.

### Phase 5: Add runtime hardening

1. Implement timeouts separately for player and coach.
2. Implement retry with backoff only around provider failures.
3. Handle empty coach output with the spec-defined fallback message.
4. Implement max-turn failure reporting.
5. Implement clean `KeyboardInterrupt` handling with a partial session report.

### Phase 6: Finish the migration

1. Remove the old duel-era modules in the actual target repo.
2. Remove or migrate stale config keys from `.g3/config.yaml`.
3. Ensure the new CLI entrypoint is the one packaging and launchers actually invoke.
4. Run the final verification set:
   - unit tests
   - import smoke tests
   - one manual `tero go` dry run in a sample repo
   - one manual `tero history` check

## Minimum Done Definition

The rewrite should not be considered complete until all of the following are true:

- all planned source files exist
- all planned tests exist and pass
- provider uses the same SDK that the dependency file installs
- config reads the intended schema from the actual workspace
- default `requirements.md` resolution works from the real runtime location
- `tero go` completes a full player/coach cycle
- `tero history` can read recorded runs
- the old duel path is removed or clearly retired

## Recommended Fix Order

If you want the shortest path to a real working build, do it in this order:

1. Fix SDK mismatch in `providers/ccg.py`.
2. Fix config/schema/path wiring in `config.py`.
3. Add `feedback.py`, `plan_tracker.py`, `prompts.py`.
4. Add tests for those modules immediately.
5. Add `streaming.py`.
6. Add `learning/recorder.py`.
7. Add `coach_player.py`.
8. Add `g3.py`.
9. Run tests and one manual session smoke test.
10. Only then migrate into the real repo tree and delete old duel code.
