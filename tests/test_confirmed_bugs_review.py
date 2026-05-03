"""Regression tests proving the 7 confirmed bugs from the deep code review.

Each test is designed to:
  - FAIL before the corresponding fix (proving the bug is real)
  - PASS after the fix (proving it is resolved)
"""

from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Bug 1: claude_native.py — stderr pipe deadlock ────────────────────────────

def test_stderr_concurrent_drain_before_wait():
    """Stderr must be drained concurrently (before proc.wait) to prevent deadlock.

    If the subprocess writes >64 KB to stderr, the OS pipe buffer fills up and
    the process blocks.  Without a concurrent reader, proc.wait() never returns.
    Fix: asyncio.create_task(proc.stderr.read()) BEFORE await proc.wait().

    After Phase 6B refactor, this guarantee lives in subprocess_runner.py which
    all subprocess-based providers (claude_native, codex, opencode) delegate to.
    """
    from src.providers import subprocess_runner

    source = inspect.getsource(subprocess_runner.run_subprocess_jsonl)

    assert "create_task" in source, (
        "run_subprocess_jsonl() must use asyncio.create_task(proc.stderr.read()) "
        "before proc.wait() to prevent deadlock when stderr exceeds the OS pipe buffer."
    )

    task_pos = source.find("create_task")
    wait_pos = source.find("await proc.wait()")
    assert task_pos < wait_pos, (
        "asyncio.create_task for stderr must appear BEFORE await proc.wait(). "
        f"create_task at pos={task_pos}, await proc.wait() at pos={wait_pos}."
    )


@pytest.mark.asyncio
async def test_stderr_deadlock_does_not_hang():
    """run() must complete within 5 s even when subprocess writes >64 KB to stderr."""
    import sys
    from src.providers.claude_native import ClaudeNativeProvider

    provider = ClaudeNativeProvider()
    # Script: write 65 KB to stderr first, then emit one JSON line on stdout.
    # Without concurrent stderr draining, the subprocess blocks on the stderr
    # write, stdout is never written, and run() hangs indefinitely.
    script = (
        "import sys, json; "
        "sys.stderr.write('e' * 66560); "
        "sys.stderr.flush(); "
        "print(json.dumps({'type': 'text', 'text': 'done'})); "
        "sys.stdout.flush()"
    )
    with patch.object(provider, "_build_command", return_value=[sys.executable, "-c", script]):
        events = []
        try:
            async def _collect():
                async for event in provider.run("p", "s", "/tmp", max_turns=1):
                    events.append(event)

            await asyncio.wait_for(_collect(), timeout=5.0)
        except asyncio.TimeoutError:
            pytest.fail(
                "Deadlock detected: run() timed out waiting for subprocess "
                "that wrote >64 KB to stderr."
            )

    assert any(e.get("text") == "done" for e in events), (
        f"Expected a 'done' text event; got: {events}"
    )


# ── Bug 2: config.py — debug_*_provider names not normalized ─────────────────

def test_debug_provider_normalization():
    """resolve_config must normalize Z.AI aliases in debug_*_provider fields."""
    from src.config import resolve_config

    cfg = resolve_config({"debug_player_provider": "z.ai"})
    assert cfg.debug_player_provider == "zai", (
        f"debug_player_provider 'z.ai' should normalize to 'zai', got '{cfg.debug_player_provider}'"
    )

    cfg = resolve_config({"debug_tester_provider": "glm-5.1"})
    assert cfg.debug_tester_provider == "zai", (
        f"debug_tester_provider 'glm-5.1' should normalize to 'zai', got '{cfg.debug_tester_provider}'"
    )

    cfg = resolve_config({"debug_fixer_provider": "glm-4.7"})
    assert cfg.debug_fixer_provider == "zai", (
        f"debug_fixer_provider 'glm-4.7' should normalize to 'zai', got '{cfg.debug_fixer_provider}'"
    )


# ── Bug 3: config.py — missing env var mappings for debug_*_model ─────────────

def test_debug_model_env_vars():
    """G3_DEBUG_*_MODEL env vars must configure debug model fields via _ENV_MAP."""
    from src.config import resolve_config

    env_patch = {
        "G3_DEBUG_PLAYER_MODEL": "test-player-model",
        "G3_DEBUG_TESTER_MODEL": "test-tester-model",
        "G3_DEBUG_FIXER_MODEL": "test-fixer-model",
    }
    with patch.dict(os.environ, env_patch):
        cfg = resolve_config({})

    assert cfg.debug_player_model == "test-player-model", (
        f"G3_DEBUG_PLAYER_MODEL not mapped; got '{cfg.debug_player_model}'"
    )
    assert cfg.debug_tester_model == "test-tester-model", (
        f"G3_DEBUG_TESTER_MODEL not mapped; got '{cfg.debug_tester_model}'"
    )
    assert cfg.debug_fixer_model == "test-fixer-model", (
        f"G3_DEBUG_FIXER_MODEL not mapped; got '{cfg.debug_fixer_model}'"
    )


# ── Bug 4: debugger_bugs.py — hardcoded "3 consecutive clean passes" ──────────

def test_final_report_uses_config_threshold():
    """write_final_report must use the victory_threshold arg, not the hardcoded literal 3."""
    from src.debugger_bugs import write_final_report

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "final.md")
        # Pass a non-default threshold so the hardcoded "3" would be distinguishable.
        write_final_report([], path, 60.0, victory=True, victory_threshold=5)
        content = Path(path).read_text()

    assert "5 consecutive clean passes" in content, (
        f"Expected '5 consecutive clean passes' in final report; got:\n{content[:400]}"
    )
    assert "3 consecutive clean passes" not in content, (
        "Final report still contains hardcoded '3 consecutive clean passes'; "
        "the threshold is not being passed through correctly."
    )


# ── Bug 5: debugger_context.py — inconsistent large-file thresholds ───────────

def test_large_file_threshold_consistency():
    """plan_file_chunks and build_context must use the same render mode for medium files.

    Files with 160-200 lines and rendered size > BUDGET_CHARS_PER_FILE are classified
    as "large" by build_context (skeleton+hotspots) but as "full" by plan_file_chunks.
    This causes bin-packing to use an overestimated size for that file.
    """
    import src.debugger_context as ctx

    with tempfile.TemporaryDirectory() as tmp:
        # 180-line Python file with long lines → rendered size exceeds BUDGET_CHARS_PER_FILE
        lines = ["class DataPipeline:"]
        for i in range(179):
            lines.append(
                f"    def stage_{i:03d}(self, val): "
                f"return val * {i} + {i}  # {'pipeline_step' * 4}"
            )
        content = "\n".join(lines)
        (Path(tmp) / "pipeline.py").write_text(content)

        line_count = len(content.splitlines())
        full_rendered = ctx._render_section("pipeline.py", content)

        # Confirm preconditions
        assert ctx.LARGE_FILE_LINE_THRESHOLD - 40 <= line_count <= ctx.LARGE_FILE_LINE_THRESHOLD, (
            f"Precondition: file must be in contested 160-200 range, got {line_count} lines."
        )
        assert len(full_rendered) > ctx.BUDGET_CHARS_PER_FILE, (
            f"Precondition: full render must exceed budget ({ctx.BUDGET_CHARS_PER_FILE}), "
            f"got {len(full_rendered)} chars."
        )

        # Track which render function is called for our file
        plan_modes: list[str] = []
        build_modes: list[str] = []
        orig_large = ctx._render_large_python_section
        orig_section = ctx._render_section

        def plan_large(rp, c):
            if "pipeline.py" in rp:
                plan_modes.append("large")
            return orig_large(rp, c)

        def plan_section(rp, c):
            if "pipeline.py" in rp:
                plan_modes.append("section")
            return orig_section(rp, c)

        def build_large(rp, c):
            if "pipeline.py" in rp:
                build_modes.append("large")
            return orig_large(rp, c)

        def build_section(rp, c):
            if "pipeline.py" in rp:
                build_modes.append("section")
            return orig_section(rp, c)

        with patch.object(ctx, "_render_large_python_section", plan_large):
            with patch.object(ctx, "_render_section", plan_section):
                ctx.plan_file_chunks(tmp)

        with patch.object(ctx, "_render_large_python_section", build_large):
            with patch.object(ctx, "_render_section", build_section):
                ctx.build_context(tmp, file_subset=["pipeline.py"])

        plan_final = plan_modes[-1] if plan_modes else "unknown"
        build_final = build_modes[-1] if build_modes else "unknown"

        assert plan_final == build_final, (
            f"plan_file_chunks used render mode '{plan_final}' but "
            f"build_context used '{build_final}' for the same {line_count}-line file. "
            "Fix: add the hybrid threshold (line_count >= 160 AND rendered > BUDGET) "
            "to plan_file_chunks so its size estimates match what build_context renders."
        )


# ── Bug 6: zai.py — temp dir leaked when shutil.copy2 raises ─────────────────

@pytest.mark.asyncio
async def test_zai_temp_dir_cleaned_up_on_copy_error():
    """Temp dir must be removed even when shutil.copy2 raises before the try block."""
    import shutil as _shutil
    from src.providers.zai import ZaiProvider

    created_dirs: list[str] = []
    orig_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(prefix="", **kwargs):
        d = orig_mkdtemp(prefix=prefix, **kwargs)
        created_dirs.append(d)
        return d

    with tempfile.TemporaryDirectory() as fake_home:
        # Create a settings.json so the `if base_settings.exists()` branch is taken
        # and shutil.copy2 is actually called.
        (Path(fake_home) / "settings.json").write_text('{"env": {"ZAI_API_KEY": "tok"}}')

        provider = ZaiProvider()
        provider.config.claude_home = fake_home

        with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
            with patch("shutil.copy2", side_effect=PermissionError("denied")):
                with pytest.raises(PermissionError):
                    async for _ in provider.run("prompt", "system", "/tmp"):
                        pass  # pragma: no cover

    assert created_dirs, "mkdtemp should have been called"
    for d in created_dirs:
        assert not os.path.exists(d), (
            f"Temp dir leaked: '{d}' still exists after PermissionError in copy2. "
            "Fix: move tempfile.mkdtemp() and shutil.copy2() inside the try block."
        )


# ── Bug 7: debugger.py — str.replace replaces all occurrences in path ─────────

def test_bugs_final_path_not_corrupted_by_str_replace():
    """Path.with_name() must be used instead of str.replace() for final report path.

    If working_dir itself contains 'bugs.md' as a component, str.replace corrupts
    the entire path string by replacing every occurrence.
    """
    # Demonstrate the str.replace bug directly
    tricky_bugs_md = "/home/user/bugs.md_project/bugs.md"
    buggy_result = tricky_bugs_md.replace("bugs.md", "bugs_final.md")
    correct_result = str(Path(tricky_bugs_md).with_name("bugs_final.md"))

    assert buggy_result != correct_result, (
        "Precondition failed: str.replace and Path.with_name should differ on tricky paths."
    )
    assert correct_result == "/home/user/bugs.md_project/bugs_final.md"

    # Verify that debugger.py now uses Path.with_name()
    from src import debugger as dbg_module

    source = inspect.getsource(dbg_module.Debugger.run)
    assert "with_name" in source, (
        "debugger.py Debugger.run() must use Path(...).with_name('bugs_final.md') "
        "to construct the final report path — str.replace() corrupts paths that "
        "contain 'bugs.md' as a directory component."
    )
