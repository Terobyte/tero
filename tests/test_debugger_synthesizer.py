"""Tests for the synthesizer agent integration in src/debugger.py."""

import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.debugger import Debugger
from src.debugger_bugs import BugEntry


def _make_config(**overrides):
    from src.config import Config

    defaults = dict(
        working_dir=".",
        debug_player_provider="zai",
        debug_tester_provider="zai",
        debug_fixer_provider="zai",
        debug_synthesizer_provider="zai",
        debug_player_model="",
        debug_tester_model="",
        debug_fixer_model="",
        debug_synthesizer_model="",
        debug_intensity="low",
        debug_limit_mode="iterations",
        debug_limit_value=1,
        debug_victory_threshold=3,
        context_limit=100000,
        compact_threshold=0.8,
        debug_file="dummy.py",
        debug_entry="dummy_func",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _bug(id=1, file="foo.py", line=10, description="test bug", severity="high"):
    return BugEntry(
        id=id, file=file, line=line, description=description, severity=severity
    )


# ── _parse_entry_lines ──────────────────────────────────────────────────────


class TestParseEntryLines:
    def test_extracts_entry_lines(self):
        raw = textwrap.dedent("""\
            Here are some inputs:
            entry(1, 2)
            entry(0, 0)
            entry(-5, 3)
        """)
        result = Debugger._parse_entry_lines(raw)
        assert "entry(1, 2)" in result
        assert "entry(0, 0)" in result
        assert "entry(-5, 3)" in result

    def test_ignores_non_entry_lines(self):
        raw = "some prose\nnot_entry(x)\nentry(42)"
        result = Debugger._parse_entry_lines(raw)
        assert result == "entry(42)"

    def test_returns_empty_for_no_matches(self):
        result = Debugger._parse_entry_lines("no entries here")
        assert result == ""

    def test_entry_with_kwargs(self):
        raw = "entry(1, key='value')"
        result = Debugger._parse_entry_lines(raw)
        assert result == "entry(1, key='value')"


# ── _find_call_sites ────────────────────────────────────────────────────────


class TestFindCallSites:
    def test_finds_call_in_other_file(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "module.py").write_text("def target_fn(x):\n    return x + 1\n")
        (tmp_path / "caller.py").write_text(
            "from module import target_fn\nresult = target_fn(5)\n"
        )

        result = dbg._find_call_sites("target_fn", "module.py")
        assert "caller.py:2" in result
        assert "target_fn(5)" in result

    def test_returns_empty_when_no_calls(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "module.py").write_text("def lonely(x):\n    return x\n")

        result = dbg._find_call_sites("lonely", "module.py")
        assert result == ""

    def test_limits_to_max_sites(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "target.py").write_text("def fn(): pass\n")
        for i in range(10):
            (tmp_path / f"caller_{i}.py").write_text(f"fn()\n")

        result = dbg._find_call_sites("fn", "target.py", max_sites=3)
        assert len(result.splitlines()) == 3


# ── _extract_bug_context includes call sites ────────────────────────────────


class TestExtractBugContext:
    def test_includes_call_sites_section(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        buggy = tmp_path / "lib.py"
        buggy.write_text(
            textwrap.dedent("""\
            def add(a, b):
                \"\"\"Add two numbers.\"\"\"
                return a + b
        """)
        )
        (tmp_path / "main.py").write_text("result = add(1, 2)\n")

        bug = _bug(file="lib.py", line=1)
        ctx = dbg._extract_bug_context(bug, "")

        assert "Signature:" in ctx
        assert "add" in ctx
        assert "Neighboring call sites:" in ctx
        assert "main.py" in ctx

    def test_no_call_sites_section_when_none_found(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "solo.py").write_text("def solo(x):\n    return x\n")
        bug = _bug(file="solo.py", line=1)
        ctx = dbg._extract_bug_context(bug, "")

        assert "Neighboring call sites" not in ctx


# ── _run_synthesizer integration ────────────────────────────────────────────


class TestRunSynthesizer:
    @pytest.mark.asyncio
    async def test_produces_entry_dict(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "bug.py").write_text("def foo(x):\n    return x\n")

        synth_output = "entry(1)\nentry(2)\n"
        mock_result = MagicMock()
        mock_result.completed = True
        mock_result.text = synth_output

        with patch.object(dbg, "_collect_text", return_value=mock_result):
            bugs = [_bug(file="bug.py", line=1)]
            results = await dbg._run_synthesizer(bugs)

        assert len(results) == 1
        assert results[0]["bug_id"] == "1"
        assert "entry(1)" in results[0]["entries"]

    @pytest.mark.asyncio
    async def test_skips_when_no_output(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "bug.py").write_text("def foo(x):\n    return x\n")

        mock_result = MagicMock()
        mock_result.completed = True
        mock_result.text = ""

        with patch.object(dbg, "_collect_text", return_value=mock_result):
            bugs = [_bug(file="bug.py", line=1)]
            results = await dbg._run_synthesizer(bugs)

        assert results == []


# ── _run_tester receives synth_entries ──────────────────────────────────────


class TestTesterReceivesSynthEntries:
    @pytest.mark.asyncio
    async def test_synth_entries_in_prompt(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "bug.py").write_text("def foo(x):\n    return x + 1\n")

        synth = [{"bug_id": "1", "entries": "entry(42)\nentry(0)"}]
        captured: dict = {}

        async def fake_collect(provider, *, prompt, **kw):
            captured["prompt"] = prompt
            mock_result = MagicMock()
            mock_result.completed = True
            mock_result.text = (
                "```json\n"
                '[{"bug_id": 1, "status": "false_positive", "test_file": null}]'
                "\n```"
            )
            return mock_result

        with patch.object(dbg, "_collect_text", side_effect=fake_collect):
            bugs = [_bug(file="bug.py", line=1)]
            await dbg._run_tester(bugs, synth_entries=synth)

        prompt = captured.get("prompt", "")
        assert "Synthesized Test Inputs" in prompt
        assert "entry(42)" in prompt

    @pytest.mark.asyncio
    async def test_no_synth_block_when_empty(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "bug.py").write_text("def foo(x):\n    return x + 1\n")

        captured: dict = {}

        async def fake_collect(provider, *, prompt, **kw):
            captured["prompt"] = prompt
            mock_result = MagicMock()
            mock_result.completed = True
            mock_result.text = (
                "```json\n"
                '[{"bug_id": 1, "status": "false_positive", "test_file": null}]'
                "\n```"
            )
            return mock_result

        with patch.object(dbg, "_collect_text", side_effect=fake_collect):
            bugs = [_bug(file="bug.py", line=1)]
            await dbg._run_tester(bugs, synth_entries=[])

        prompt = captured.get("prompt", "")
        assert "Synthesized Test Inputs" not in prompt


# ── env config mapping ──────────────────────────────────────────────────────


class TestTesterReceivesFailingTest:
    @pytest.mark.asyncio
    async def test_failing_test_injected_when_set(self, tmp_path):
        config = _make_config(
            working_dir=str(tmp_path),
            debug_failing_test="assert foo(1) == 2",
        )
        dbg = Debugger(config)

        (tmp_path / "bug.py").write_text("def foo(x):\n    return x + 1\n")

        captured: dict = {}

        async def fake_collect(provider, *, prompt, **kw):
            captured["prompt"] = prompt
            mock_result = MagicMock()
            mock_result.completed = True
            mock_result.text = (
                "```json\n"
                '[{"bug_id": 1, "status": "false_positive", "test_file": null}]'
                "\n```"
            )
            return mock_result

        with patch.object(dbg, "_collect_text", side_effect=fake_collect):
            bugs = [_bug(file="bug.py", line=1)]
            await dbg._run_tester(bugs, failing_test="assert foo(1) == 2")

        prompt = captured.get("prompt", "")
        assert "Failing Test" in prompt
        assert "assert foo(1) == 2" in prompt
        assert "Synthesized Test Inputs" not in prompt

    @pytest.mark.asyncio
    async def test_no_failing_test_block_when_unset(self, tmp_path):
        config = _make_config(working_dir=str(tmp_path))
        dbg = Debugger(config)

        (tmp_path / "bug.py").write_text("def foo(x):\n    return x + 1\n")

        captured: dict = {}

        async def fake_collect(provider, *, prompt, **kw):
            captured["prompt"] = prompt
            mock_result = MagicMock()
            mock_result.completed = True
            mock_result.text = (
                "```json\n"
                '[{"bug_id": 1, "status": "false_positive", "test_file": null}]'
                "\n```"
            )
            return mock_result

        with patch.object(dbg, "_collect_text", side_effect=fake_collect):
            bugs = [_bug(file="bug.py", line=1)]
            await dbg._run_tester(bugs)

        prompt = captured.get("prompt", "")
        assert "Failing Test" not in prompt

    @pytest.mark.asyncio
    async def test_synthesizer_skipped_and_test_injected(self, tmp_path):
        config = _make_config(
            working_dir=str(tmp_path),
            debug_failing_test="assert foo(1) == 2",
        )
        dbg = Debugger(config)

        (tmp_path / "bug.py").write_text("def foo(x):\n    return x + 1\n")

        synth_called = False

        async def fake_synth(self_inner, bugs):
            nonlocal synth_called
            synth_called = True
            return []

        tester_captured: dict = {}

        async def fake_collect(provider, *, prompt, **kw):
            tester_captured["prompt"] = prompt
            mock_result = MagicMock()
            mock_result.completed = True
            mock_result.text = (
                "```json\n"
                '[{"bug_id": 1, "status": "false_positive", "test_file": null}]'
                "\n```"
            )
            return mock_result

        player_bugs = [_bug(file="bug.py", line=1)]

        with (
            patch.object(type(dbg), "_run_synthesizer", fake_synth),
            patch.object(dbg, "_run_player", return_value=player_bugs),
            patch.object(dbg, "_collect_text", side_effect=fake_collect),
            patch.object(dbg, "_run_fixer", return_value=0),
            patch.object(dbg, "_git_commit"),
        ):
            await dbg.run()

        assert not synth_called, "Synthesizer should have been skipped"
        prompt = tester_captured.get("prompt", "")
        assert "assert foo(1) == 2" in prompt
        assert "Failing Test" in prompt


class TestSynthesizerConfig:
    def test_env_override_synthesizer_provider(self, monkeypatch):
        monkeypatch.setenv("G3_DEBUG_SYNTHESIZER_PROVIDER", "claude")
        from src.config import resolve_config

        cfg = resolve_config({"working_dir": "."})
        assert cfg.debug_synthesizer_provider == "claude"

    def test_env_override_synthesizer_model(self, monkeypatch):
        monkeypatch.setenv("G3_DEBUG_SYNTHESIZER_MODEL", "sonnet")
        from src.config import resolve_config

        cfg = resolve_config({"working_dir": "."})
        assert cfg.debug_synthesizer_model == "sonnet"
