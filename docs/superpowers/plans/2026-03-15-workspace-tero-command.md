# Workspace Layout + `tero` Command Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure G3 so agent workspaces live at `~/Desktop/workspace/g/` and `~/Desktop/workspace/g1/`, the plan defaults to `requirements.md`, and the CLI is invocable globally as `tero` from any directory.

**Architecture:** Changes propagate workspace configuration from `ResolvedConfig` → `WorktreeManager` → `DuelRunner`. `workspace_base` redirects workspace creation from the hidden `.g3/sessions/UUID/` to the visible project root. All three workspace-path methods (`create`, `get_diff`, `cleanup`) in `WorktreeManager` use the same `base` resolution. `_promote` gets explicit exclusion of workspace dirs (using plain `str` parts, NOT `p.name`). `working_dir` is resolved to absolute at the CLI boundary. Entry point rename is only in `pyproject.toml`.

**Tech Stack:** Python 3.11+, setuptools entry points, pip editable install, argparse, dataclasses

---

## File Map

| File | Action | What changes |
|---|---|---|
| `g3/pyproject.toml` | Modify | `g3` entry point → `tero` |
| `g3/g3.py` | Modify | `prog="tero"`, `--plan` becomes optional with default `requirements.md`, update status text |
| `g3/src/config.py` | Modify | `plan_file` default → `"requirements.md"`, add `agent_a_workspace="g"` and `agent_b_workspace="g1"` |
| `g3/src/worktree.py` | Modify | Add `workspace_base` + `exclude_names` params; fix `create()`, `get_diff()`, `cleanup()` to use `workspace_base`; exclude workspace dirs from copy |
| `g3/src/duel.py` | Modify | Add `workspace_a_name`, `workspace_b_name` to `DuelRunner.__init__`; use them in `run_round` for both `create()` and `get_diff()` calls |
| `g3/src/orchestrator.py` | Modify | Wire `workspace_base`, workspace names into WorktreeManager + DuelRunner; fix `_promote` to exclude `g/`, `g1/` |
| `~/Desktop/workspace/requirements.md` | Create | Plan template file |

---

## Chunk 1: Foundation (pyproject + config)

### Task 1: Rename entry point in `pyproject.toml`

**Files:**
- Modify: `g3/pyproject.toml`

- [ ] **Step 1: Read current pyproject.toml**

```bash
cat /Users/terobyte/Desktop/Projects/Active/tero/g3/pyproject.toml
```

Expected: see `g3 = "g3:main"` under `[project.scripts]`

- [ ] **Step 2: Change the entry point name**

In `g3/pyproject.toml`, replace:
```toml
[project.scripts]
g3 = "g3:main"
```
with:
```toml
[project.scripts]
tero = "g3:main"
```

- [ ] **Step 3: Verify the change**

```bash
grep "tero\|g3" /Users/terobyte/Desktop/Projects/Active/tero/g3/pyproject.toml
```

Expected output:
```
name = "g3-coach"
tero = "g3:main"
```

---

### Task 2: Update `ResolvedConfig` with workspace fields

**Files:**
- Modify: `g3/src/config.py` (lines 22–43)

The `ResolvedConfig` dataclass needs two new fields and a fixed `plan_file` default. Currently `plan_file` defaults to `""` and `--plan` is required. After this change, `requirements.md` in the current directory is the default.

- [ ] **Step 1: Write a test for the new defaults**

In `g3/tests/test_config_defaults.py` (create new file):

```python
"""Tests for new config defaults added in workspace refactor."""
from src.config import ResolvedConfig


def test_plan_file_default_is_requirements_md():
    cfg = ResolvedConfig()
    assert cfg.plan_file == "requirements.md"


def test_workspace_names_default_to_g_and_g1():
    cfg = ResolvedConfig()
    assert cfg.agent_a_workspace == "g"
    assert cfg.agent_b_workspace == "g1"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python -m pytest tests/test_config_defaults.py -v
```

Expected: FAIL — `ResolvedConfig` has no `agent_a_workspace` attribute

- [ ] **Step 3: Add the new fields to `ResolvedConfig`**

In `g3/src/config.py`, find the `@dataclass class ResolvedConfig:` block. It currently ends around:
```python
    plan_file: str = ""
    run_bug_detection: bool = True
    ask_feedback: bool = True
    timeout_s: int = 600
```

Change to:
```python
    plan_file: str = "requirements.md"
    run_bug_detection: bool = True
    ask_feedback: bool = True
    timeout_s: int = 600
    agent_a_workspace: str = "g"
    agent_b_workspace: str = "g1"
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python -m pytest tests/test_config_defaults.py -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && git add pyproject.toml src/config.py tests/test_config_defaults.py && git commit -m "feat: add tero entry point and workspace name config fields"
```

---

## Chunk 2: WorktreeManager — workspace_base support

### Task 3: Add `workspace_base` and exclusion to `WorktreeManager`

**Files:**
- Modify: `g3/src/worktree.py`
- Test: `g3/tests/test_worktree_manager.py` (already exists — extend it)

The key change: `WorktreeManager` currently puts workspaces at `session_dir/agent_name`. With `workspace_base` set, it puts them at `workspace_base/agent_name` instead. The copy step must also exclude the workspace dir names to avoid infinite recursion (copying `workspace/` into `workspace/g/` would include `workspace/g1/` in the copy).

- [ ] **Step 1: Write the failing tests**

Add to `g3/tests/test_worktree_manager.py`:

```python
import os
import tempfile
from unittest.mock import patch, MagicMock
from src.worktree import WorktreeManager


def test_workspace_base_overrides_session_dir():
    """When workspace_base is set, workspaces go there, not session_dir."""
    with tempfile.TemporaryDirectory() as tmp:
        session_dir = os.path.join(tmp, "session")
        workspace_base = os.path.join(tmp, "project")
        source_dir = os.path.join(tmp, "source")
        os.makedirs(source_dir)
        os.makedirs(workspace_base)

        # Write a dummy file in source so copytree has something to copy
        with open(os.path.join(source_dir, "main.py"), "w") as f:
            f.write("print('hi')")

        wm = WorktreeManager(
            session_dir=session_dir,
            source_dir=source_dir,
            mode="copy",
            workspace_base=workspace_base,
        )
        result = wm.create("g")

        assert result == os.path.join(workspace_base, "g")
        assert os.path.isdir(result)


def test_copy_excludes_workspace_names():
    """Agent workspace dirs are excluded from the copy to prevent recursion."""
    with tempfile.TemporaryDirectory() as tmp:
        source_dir = os.path.join(tmp, "project")
        os.makedirs(source_dir)
        # Simulate existing g/ and g1/ dirs inside the project
        os.makedirs(os.path.join(source_dir, "g"))
        os.makedirs(os.path.join(source_dir, "g1"))
        with open(os.path.join(source_dir, "main.py"), "w") as f:
            f.write("x = 1")

        wm = WorktreeManager(
            session_dir=tmp,
            source_dir=source_dir,
            mode="copy",
            workspace_base=tmp,
            exclude_names={"g", "g1"},
        )
        result = wm.create("g")

        # g/ dir in the copy should NOT contain another g/ or g1/
        assert not os.path.exists(os.path.join(result, "g"))
        assert not os.path.exists(os.path.join(result, "g1"))
        # But the main file should be there
        assert os.path.exists(os.path.join(result, "main.py"))
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python -m pytest tests/test_worktree_manager.py::test_workspace_base_overrides_session_dir tests/test_worktree_manager.py::test_copy_excludes_workspace_names -v
```

Expected: FAIL — `WorktreeManager.__init__` does not accept `workspace_base` or `exclude_names`

- [ ] **Step 3: Update `WorktreeManager.__init__` signature**

In `g3/src/worktree.py`, update `__init__`:

```python
def __init__(
    self,
    session_dir: str,
    source_dir: str,
    mode: str = "auto",
    workspace_base: str | None = None,
    exclude_names: set[str] | None = None,
):
    self.session_dir = session_dir
    self.source_dir = source_dir
    self.mode = mode
    self.workspace_base = workspace_base
    self.exclude_names = exclude_names or set()
    self._used: set[str] = set()
    self._workspace_modes: dict[str, str] = {}
```

- [ ] **Step 4: Update `create()` to use `workspace_base`**

In `g3/src/worktree.py`, the `create()` method currently does:
```python
ws = os.path.join(self.session_dir, agent_name)
```

Change to:
```python
base = self.workspace_base if self.workspace_base is not None else self.session_dir
ws = os.path.join(base, agent_name)
```

Keep all other logic in `create()` unchanged — the git/copy detection and `_workspace_modes` tracking still work the same.

- [ ] **Step 4b: Fix `get_diff()` to use `workspace_base`**

`get_diff()` also builds the workspace path from `session_dir`, missing the new base. In `g3/src/worktree.py`, find:

```python
def get_diff(self, agent_name: str) -> str:
    ws = os.path.join(self.session_dir, agent_name)
```

Change to:

```python
def get_diff(self, agent_name: str) -> str:
    base = self.workspace_base if self.workspace_base is not None else self.session_dir
    ws = os.path.join(base, agent_name)
```

- [ ] **Step 4c: Fix `cleanup()` to use `workspace_base`**

`cleanup()` has the same bug — it uses `session_dir` to find the workspace path. In `g3/src/worktree.py`, find:

```python
def cleanup(self, agent_name: str):
    ws = os.path.join(self.session_dir, agent_name)
```

Change to:

```python
def cleanup(self, agent_name: str):
    base = self.workspace_base if self.workspace_base is not None else self.session_dir
    ws = os.path.join(base, agent_name)
```

Without this fix, `cleanup_all()` called in `orchestrator.py`'s `finally` block would silently fail to remove `g/` and `g1/` after each run, leaving stale agent workspaces.

- [ ] **Step 5: Update `_create_copy()` to exclude workspace dirs**

Current `_create_copy`:
```python
def _create_copy(self, ws: str) -> str:
    shutil.copytree(
        self.source_dir, ws,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "node_modules", ".venv", "*.pyc", ".g3"
        ),
    )
    return ws
```

Change to:
```python
def _create_copy(self, ws: str) -> str:
    base_excludes = {".git", "__pycache__", "node_modules", ".venv", "*.pyc", ".g3"}
    all_excludes = base_excludes | self.exclude_names
    shutil.copytree(
        self.source_dir, ws,
        ignore=shutil.ignore_patterns(*all_excludes),
    )
    return ws
```

- [ ] **Step 6: Run the new tests**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python -m pytest tests/test_worktree_manager.py -v
```

Expected: all tests PASS (including existing ones)

- [ ] **Step 7: Commit**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && git add src/worktree.py tests/test_worktree_manager.py && git commit -m "feat: add workspace_base and exclude_names to WorktreeManager"
```

---

## Chunk 3: DuelRunner — configurable workspace names

### Task 4: Make workspace names configurable in `DuelRunner`

**Files:**
- Modify: `g3/src/duel.py` (lines 26–49)
- Test: `g3/tests/test_duel_runner.py` (already exists — extend it)

Currently `duel.py` calls `self.worktree.create("agent_a")` and `self.worktree.create("agent_b")` — hardcoded strings that become directory names. We need these to be `"g"` and `"g1"` (passed from config).

- [ ] **Step 1: Write the failing test**

Add to `g3/tests/test_duel_runner.py`:

```python
def test_duel_runner_uses_configured_workspace_names():
    """DuelRunner.run_round uses workspace_a_name and workspace_b_name."""
    from unittest.mock import MagicMock, patch
    from src.duel import DuelRunner

    mock_worktree = MagicMock()
    mock_worktree.create.side_effect = lambda name: f"/tmp/{name}"
    mock_worktree.get_diff.return_value = ""

    mock_registry = MagicMock()
    mock_agent = MagicMock()
    mock_agent.run.return_value = MagicMock(success=True, output="done", duration_s=1.0)
    mock_registry.get.return_value = mock_agent

    mock_bug_detector = MagicMock()
    mock_bug_detector.run.return_value = MagicMock(total=0, tests=0, lint=0, types=0, compile=0)

    mock_judge = MagicMock()
    # DuelRunner calls self.judge.compare(), not self.judge.judge()
    mock_judge.compare.return_value = MagicMock(action="winner_a", confidence="high", reason="a is better")

    runner = DuelRunner(
        registry=mock_registry,
        worktree=mock_worktree,
        bug_detector=mock_bug_detector,
        judge=mock_judge,
        workspace_a_name="g",
        workspace_b_name="g1",
    )
    import asyncio
    asyncio.run(runner.run_round("do the task", "ccg", "ccg2"))

    # Check that create was called with "g" and "g1", not "agent_a"/"agent_b"
    calls = [c.args[0] for c in mock_worktree.create.call_args_list]
    assert "g" in calls
    assert "g1" in calls
    assert "agent_a" not in calls
    assert "agent_b" not in calls
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python -m pytest tests/test_duel_runner.py::test_duel_runner_uses_configured_workspace_names -v
```

Expected: FAIL — `DuelRunner.__init__` does not accept `workspace_a_name`

- [ ] **Step 3: Update `DuelRunner.__init__`**

In `g3/src/duel.py`, find `class DuelRunner:` and update `__init__`:

```python
def __init__(
    self,
    registry: ProviderRegistry,
    worktree: WorktreeManager,
    bug_detector: BugDetector,
    judge: JudgeRunner,
    workspace_a_name: str = "g",
    workspace_b_name: str = "g1",
):
    self.registry = registry
    self.worktree = worktree
    self.bug_detector = bug_detector
    self.judge = judge
    self.workspace_a_name = workspace_a_name
    self.workspace_b_name = workspace_b_name
```

- [ ] **Step 4: Update `run_round()` to use the names**

In `g3/src/duel.py`, inside `run_round()`, change:
```python
ws_a = self.worktree.create("agent_a")
ws_b = self.worktree.create("agent_b")
```
to:
```python
ws_a = self.worktree.create(self.workspace_a_name)
ws_b = self.worktree.create(self.workspace_b_name)
```

Also update the `get_diff` calls in the same method. Find:
```python
diff_a = self.worktree.get_diff("agent_a")
diff_b = self.worktree.get_diff("agent_b")
```
Change to:
```python
diff_a = self.worktree.get_diff(self.workspace_a_name)
diff_b = self.worktree.get_diff(self.workspace_b_name)
```

Without this fix, the judge always receives empty diffs and can't evaluate the agents' work.

- [ ] **Step 5: Run all duel tests**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python -m pytest tests/test_duel_runner.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && git add src/duel.py tests/test_duel_runner.py && git commit -m "feat: make workspace names configurable in DuelRunner"
```

---

## Chunk 4: Orchestrator — wire everything together

### Task 5: Update `Orchestrator` to use workspace_base and workspace names

**Files:**
- Modify: `g3/src/orchestrator.py`

Three changes in one file:
1. Pass `workspace_base=config.working_dir` and `exclude_names` to `WorktreeManager`
2. Pass `workspace_a_name`, `workspace_b_name` to `DuelRunner`
3. Fix `_promote()` to exclude workspace dirs from deletion

- [ ] **Step 1: Update `WorktreeManager` instantiation**

In `g3/src/orchestrator.py`, find the `self.worktree = WorktreeManager(...)` block (currently lines 50–54):

```python
self.worktree = WorktreeManager(
    session_dir=str(self.session_dir),
    source_dir=config.working_dir,
    mode=config.worktree_mode,
)
```

Replace with:

```python
_ws_names = {config.agent_a_workspace, config.agent_b_workspace}
self.worktree = WorktreeManager(
    session_dir=str(self.session_dir),
    source_dir=config.working_dir,
    mode=config.worktree_mode,
    workspace_base=config.working_dir,
    exclude_names=_ws_names,
)
```

- [ ] **Step 2: Pass workspace names when constructing DuelRunner**

In `g3/src/orchestrator.py`, the `run()` method constructs `DuelRunner` (around line 95). Change:

```python
duel = DuelRunner(self.registry, self.worktree, bug_detector, judge)
```

to:

```python
duel = DuelRunner(
    self.registry, self.worktree, bug_detector, judge,
    workspace_a_name=self.config.agent_a_workspace,
    workspace_b_name=self.config.agent_b_workspace,
)
```

Do the same in `resume()` (the second occurrence of `DuelRunner(...)` construction).

- [ ] **Step 3: Fix `_promote()` to exclude workspace dirs**

In `g3/src/orchestrator.py`, the `_promote()` method has three loops that check for excluded directories. Currently all three use:

```python
if any(p.name in (".git", "__pycache__", ".g3") for p in rel.parts):
    continue
```

Replace all three occurrences with:

```python
_skip = {".git", "__pycache__", ".g3",
         self.config.agent_a_workspace, self.config.agent_b_workspace}
if any(p in _skip for p in rel.parts):
    continue
```

Note: `rel.parts` returns plain `str` segments (e.g. `("src", "main.py")`), NOT `Path` objects — do NOT write `p.name`, just `p`. The `_skip` set can be computed once at the top of `_promote()`, not inside each loop.

The updated `_promote()` method should look like:

```python
def _promote(self, winner_workspace: str):
    """Copy winner's changes to main working directory."""
    import shutil

    ws = Path(winner_workspace)
    target = Path(self.config.working_dir)
    _skip = {".git", "__pycache__", ".g3",
              self.config.agent_a_workspace, self.config.agent_b_workspace}

    # Track all files in winner workspace
    winner_files: set[Path] = set()
    for item in ws.rglob("*"):
        if item.is_file():
            rel = item.relative_to(ws)
            if any(p in _skip for p in rel.parts):  # parts are str, not Path
                continue
            winner_files.add(rel)

    # Delete files in target that don't exist in winner (skip special dirs)
    for item in target.rglob("*"):
        if item.is_file():
            rel = item.relative_to(target)
            if any(p in _skip for p in rel.parts):
                continue
            if rel not in winner_files:
                item.unlink()
                print(f"  Removed: {rel}")

    # Copy/update files from winner
    for rel in winner_files:
        src = ws / rel
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    # Remove empty directories in target that don't exist in winner
    winner_dirs = {d.relative_to(ws) for d in ws.rglob("*") if d.is_dir()}
    for item in sorted(target.rglob("*"), reverse=True):
        if item.is_dir() and not any(item.iterdir()):
            rel = item.relative_to(target)
            if rel not in winner_dirs:
                if not any(p in _skip for p in rel.parts):
                    item.rmdir()

    print("📦 Promoted changes to main workspace")
```

- [ ] **Step 4: Smoke test the wiring (dry run)**

```bash
cd ~/Desktop/workspace && tero go --dry-run
```

If `tero` isn't installed yet, run:
```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python g3.py go --dry-run
```

Expected output like:
```
DRY RUN
  agents  : ccg vs ccg2
  judge   : ccg
  plan    : requirements.md
  rounds  : 3
  bugs    : on
```

- [ ] **Step 5: Commit**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && git add src/orchestrator.py && git commit -m "feat: wire workspace_base and workspace names through orchestrator"
```

---

## Chunk 5: CLI rename + workspace setup

### Task 6: Update `g3.py` CLI — rename prog and fix `--plan`

**Files:**
- Modify: `g3/g3.py`

Three small text changes:
1. `prog="g3"` → `prog="tero"`
2. `--plan` removes `required=True`, adds `default="requirements.md"`
3. `"Run 'g3 resume'"` → `"Run 'tero resume'"`

- [ ] **Step 1: Update the argparse prog name**

In `g3/g3.py` line 183, change:
```python
parser = argparse.ArgumentParser(
    prog="g3",
    description="G3 Coach-Player: dual-agent orchestration system"
)
```
to:
```python
parser = argparse.ArgumentParser(
    prog="tero",
    description="G3 Coach-Player: dual-agent orchestration system"
)
```

- [ ] **Step 2: Make `--plan` optional with default**

In `g3/g3.py`, find the `go` subparser definition:
```python
go.add_argument("--plan", required=True, help="Path to task/plan file")
```

Change to:
```python
go.add_argument("--plan", default="requirements.md", help="Path to task/plan file (default: requirements.md)")
```

- [ ] **Step 2b: Resolve `working_dir` to an absolute path in `cmd_go`**

`ResolvedConfig.working_dir` defaults to `"."`. If anything inside orchestration changes the cwd (which can happen in async code), all path operations silently target the wrong directory. Fix this at the CLI boundary.

In `g3/g3.py`, find `cmd_go(args)`:
```python
def cmd_go(args):
    """Run a new duel session."""
    cli_args = {
        "plan_file": args.plan,
        "working_dir": getattr(args, "working_dir", "."),
```

Change to:
```python
def cmd_go(args):
    """Run a new duel session."""
    import os
    cli_args = {
        "plan_file": args.plan,
        "working_dir": os.path.abspath(getattr(args, "working_dir", ".")),
```

- [ ] **Step 3: Update the status command help text**

In `g3/g3.py`, `cmd_status()` prints:
```python
print("\nRun 'g3 resume' to continue.")
```

Change to:
```python
print("\nRun 'tero resume' to continue.")
```

- [ ] **Step 4: Verify the help output is correct**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python g3.py --help
```

Expected: shows `prog: tero` and `--plan` without `required`

```bash
python g3.py go --help
```

Expected: `--plan` shows `(default: requirements.md)`

- [ ] **Step 5: Commit**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && git add g3.py && git commit -m "feat: rename CLI prog to tero, make --plan optional"
```

---

### Task 7: Create `~/Desktop/workspace/` and `requirements.md` template

**Files:**
- Create: `~/Desktop/workspace/requirements.md`

- [ ] **Step 1: Create the workspace directory**

```bash
mkdir -p ~/Desktop/workspace
```

- [ ] **Step 2: Create the requirements.md template**

Create `~/Desktop/workspace/requirements.md` with:

```markdown
# Task Requirements

## Goal
<!-- One sentence: what should be built or fixed? -->

## Context
<!-- What already exists? What are the constraints? -->

## Acceptance Criteria
- [ ] ...
- [ ] ...

## Notes
<!-- Anything the agents need to know: tech stack, coding style, tests to pass, etc. -->
```

- [ ] **Step 3: Verify the file exists**

```bash
ls -la ~/Desktop/workspace/
```

Expected: `requirements.md` visible

---

### Task 8: Install `tero` globally via `pip install -e`

- [ ] **Step 1: Install in editable mode**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && pip install -e .
```

Expected output ends with: `Successfully installed g3-coach-0.1.0`

- [ ] **Step 2: Confirm `tero` is in PATH**

```bash
which tero
```

Expected: path like `/usr/local/bin/tero` or `~/.local/bin/tero` or inside your venv

- [ ] **Step 3: Confirm `tero --help` works from any directory**

```bash
cd ~/Desktop && tero --help
```

Expected: prints the tero help text with subcommands `go`, `resume`, `insights`, `history`, `status`

- [ ] **Step 4: Confirm `tero go --dry-run` from workspace reads `requirements.md`**

```bash
cd ~/Desktop/workspace && tero go --dry-run
```

Expected:
```
DRY RUN
  agents  : ccg vs ccg2
  judge   : ccg
  plan    : requirements.md
  rounds  : 3
  bugs    : on
```

---

## Chunk 6: Verification

### Task 9: Run full test suite

- [ ] **Step 1: Run all unit tests**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python -m pytest tests/ -v --ignore=tests/e2e
```

Expected: all existing tests pass, plus 5 new tests (2 worktree + 1 duel + 2 config defaults)

- [ ] **Step 2: Check for any import errors**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && python -c "from src.config import ResolvedConfig; from src.worktree import WorktreeManager; from src.duel import DuelRunner; print('imports OK')"
```

Expected: `imports OK`

- [ ] **Step 3: Final commit with summary**

```bash
cd /Users/terobyte/Desktop/Projects/Active/tero/g3 && git add -A && git status
```

Verify nothing unexpected is staged, then:

```bash
git commit -m "feat: complete workspace layout + tero command

- workspace/g/ and workspace/g1/ at project root
- tero command available globally via pip install -e
- --plan defaults to requirements.md
- _promote() excludes workspace dirs from deletion"
```

---

## Summary of Changes

| What | Before | After |
|---|---|---|
| CLI command | `python g3/g3.py go --plan req.md` | `tero go` (from any dir) |
| Plan location | must specify `--plan /path/to/file` | auto-reads `requirements.md` in cwd |
| Agent A workspace | `.g3/sessions/UUID/agent_a/` | `workspace/g/` |
| Agent B workspace | `.g3/sessions/UUID/agent_b/` | `workspace/g1/` |
| Workspace visibility | hidden in `.g3/` | visible at project root |
| Promote safety | would delete `g1/` from target | correctly skips `g/`, `g1/`, `.g3/` |
