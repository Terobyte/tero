"""Tests for LdbRunner pipeline and Mode 3 auto-commit."""

from __future__ import annotations

import asyncio
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.config import Config
from src.ldb.runner import LdbResult, LdbRunner, _LdbBug


def _make_config(**overrides):
    defaults = dict(
        working_dir=".",
        ldb_input_provider="zai",
        ldb_player_provider="zai",
        ldb_tester_provider="zai",
        ldb_fixer_provider="zai",
        ldb_input_model="",
        ldb_player_model="",
        ldb_tester_model="",
        ldb_fixer_model="",
        ldb_mode=2,
        ldb_target_file="",
        ldb_target_entry="",
        ldb_test_input="",
        ldb_scope_all=False,
        ldb_max_iterations=3,
        ldb_timeout_s=30,
    )
    defaults.update(overrides)
    return Config(**defaults)


class TestGitCommit:
    def test_git_commit_calls_git_add_and_commit(self, tmp_path):
        cfg = _make_config(working_dir=str(tmp_path))
        runner = LdbRunner(cfg)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner._git_commit("iteration 1 — 2 bug(s) fixed")
            assert mock_run.call_count == 2
            mock_run.assert_any_call(
                ["git", "add", "-A"],
                cwd=str(tmp_path),
                check=True,
                capture_output=True,
            )
            mock_run.assert_any_call(
                ["git", "commit", "-m", "ldb fix: iteration 1 — 2 bug(s) fixed"],
                cwd=str(tmp_path),
                check=True,
                capture_output=True,
            )

    def test_git_commit_swallows_error(self, tmp_path):
        cfg = _make_config(working_dir=str(tmp_path))
        runner = LdbRunner(cfg)
        with patch(
            "subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")
        ):
            runner._git_commit("should not raise")


class TestLdbRunnerRun:
    @pytest.mark.asyncio
    async def test_mode2_no_fixer_no_commit(self, tmp_path):
        cfg = _make_config(working_dir=str(tmp_path), ldb_mode=2)
        runner = LdbRunner(cfg)
        with (
            patch.object(runner, "_run_player", return_value=[]),
            patch.object(runner, "_run_tester") as mock_tester,
            patch.object(runner, "_run_fixer") as mock_fixer,
            patch.object(runner, "_git_commit") as mock_commit,
        ):
            result = await runner.run()
            assert result.bugs_found == 0
            assert result.success is True
            mock_fixer.assert_not_called()
            mock_commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_mode3_fixer_success_auto_commits(self, tmp_path):
        cfg = _make_config(working_dir=str(tmp_path), ldb_mode=3)
        runner = LdbRunner(cfg)

        call_count = 0

        async def fake_player(source, entry):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_LdbBug(id=1, file="foo.py", line=10, description="off-by-one")]
            return []

        async def fake_tester(bugs):
            for b in bugs:
                b.status = "confirmed"
            return 1

        with (
            patch.object(runner, "_run_player", side_effect=fake_player),
            patch.object(runner, "_run_tester", side_effect=fake_tester),
            patch.object(runner, "_run_fixer", return_value=1),
            patch.object(runner, "_git_commit") as mock_commit,
        ):
            result = await runner.run()
            assert result.bugs_found == 1
            assert result.bugs_fixed == 1
            assert result.success is True
            mock_commit.assert_called_once()
            commit_msg = mock_commit.call_args[0][0]
            assert "bug(s) fixed" in commit_msg

    @pytest.mark.asyncio
    async def test_mode3_fixer_zero_fixes_no_commit(self, tmp_path):
        cfg = _make_config(working_dir=str(tmp_path), ldb_mode=3)
        runner = LdbRunner(cfg)

        call_count = 0

        async def fake_player(source, entry):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_LdbBug(id=1, file="foo.py", line=10, description="bug")]
            return []

        async def fake_tester(bugs):
            for b in bugs:
                b.status = "confirmed"
            return 1

        with (
            patch.object(runner, "_run_player", side_effect=fake_player),
            patch.object(runner, "_run_tester", side_effect=fake_tester),
            patch.object(runner, "_run_fixer", return_value=0),
            patch.object(runner, "_git_commit") as mock_commit,
        ):
            result = await runner.run()
            assert result.bugs_fixed == 0
            mock_commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_player_returns_none_stops_pipeline(self, tmp_path):
        cfg = _make_config(working_dir=str(tmp_path), ldb_mode=3)
        runner = LdbRunner(cfg)

        async def fake_player(source, entry):
            return None

        with (
            patch.object(runner, "_run_player", side_effect=fake_player),
            patch.object(runner, "_run_tester") as mock_tester,
        ):
            result = await runner.run()
            assert result.bugs_found == 0
            mock_tester.assert_not_called()


class TestLdbRunnerSync:
    def test_run_sync_delegates_to_run(self, tmp_path):
        cfg = _make_config(working_dir=str(tmp_path), ldb_mode=2)
        runner = LdbRunner(cfg)
        expected = LdbResult(success=True, bugs_found=0, tests_written=0, bugs_fixed=0)
        with patch.object(runner, "run", return_value=expected) as mock_run:
            result = runner.run_sync()
            assert result == expected
            mock_run.assert_called_once()


class TestLdbEnvMap:
    def test_ldb_target_file_env(self, monkeypatch):
        from src.config import resolve_config

        monkeypatch.setenv("G3_LDB_TARGET_FILE", "main.py")
        cfg = resolve_config({"working_dir": "."})
        assert cfg.ldb_target_file == "main.py"

    def test_ldb_target_entry_env(self, monkeypatch):
        from src.config import resolve_config

        monkeypatch.setenv("G3_LDB_TARGET_ENTRY", "my_func")
        cfg = resolve_config({"working_dir": "."})
        assert cfg.ldb_target_entry == "my_func"

    def test_ldb_test_input_env(self, monkeypatch):
        from src.config import resolve_config

        monkeypatch.setenv("G3_LDB_TEST_INPUT", "pytest -x")
        cfg = resolve_config({"working_dir": "."})
        assert cfg.ldb_test_input == "pytest -x"

    def test_ldb_scope_all_env(self, monkeypatch):
        from src.config import resolve_config

        monkeypatch.setenv("G3_LDB_SCOPE_ALL", "true")
        cfg = resolve_config({"working_dir": "."})
        assert cfg.ldb_scope_all is True


class TestExtractSignature:
    def test_plain_function_excludes_body(self):
        from src.ldb.inputs import _extract_signature

        src = "def foo(x, y):\n    z = x + y\n    return z\n"
        sig = _extract_signature(src, "foo")
        assert "z = x + y" not in sig
        assert sig == "def foo(x, y)"

    def test_method_with_docstring_no_duplication(self):
        from src.ldb.inputs import _extract_signature

        src = (
            "class C:\n"
            "    def bar(self):\n"
            '        """Docstring here."""\n'
            "        return 42\n"
        )
        sig = _extract_signature(src, "C.bar")
        assert sig.count("Docstring") == 1
        assert "return 42" not in sig

    def test_multiline_signature_excludes_body(self):
        from src.ldb.inputs import _extract_signature

        src = "def baz(\n    a,\n    b,\n):\n    pass\n"
        sig = _extract_signature(src, "baz")
        assert "pass" not in sig
        assert "def baz(" in sig

    def test_missing_function_returns_fallback(self):
        from src.ldb.inputs import _extract_signature

        sig = _extract_signature("x = 1\n", "nonexistent")
        assert sig == "def nonexistent(...): ..."

    def test_dotted_entry_class_method(self):
        from src.ldb.inputs import _extract_signature

        src = "class MyClass:\n    def my_method(self, x):\n        return x * 2\n"
        sig = _extract_signature(src, "MyClass.my_method")
        assert "def my_method(self, x)" in sig
        assert "return x * 2" not in sig


class TestIterTargets:
    def test_finds_top_level_functions(self, tmp_path):
        from src.ldb.scope import iter_targets

        (tmp_path / "mod.py").write_text(
            "def public_func():\n    pass\n\ndef _private_func():\n    pass\n"
        )
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert "public_func" in names
        assert "_private_func" not in names

    def test_finds_class_methods(self, tmp_path):
        from src.ldb.scope import iter_targets

        (tmp_path / "mod.py").write_text(
            "class Foo:\n"
            "    def bar(self):\n        pass\n"
            "    def _baz(self):\n        pass\n"
            "    def qux(self):\n        pass\n"
        )
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert "Foo.bar" in names
        assert "Foo.qux" in names
        assert "Foo._baz" not in names

    def test_skips_test_and_cache_dirs(self, tmp_path):
        from src.ldb.scope import iter_targets

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_mod.py").write_text(
            "def test_something():\n    pass\n"
        )
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "helper.py").write_text("def helper():\n    pass\n")
        (tmp_path / "real.py").write_text("def real_func():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert "real_func" in names
        assert "test_something" not in names
        assert "helper" not in names

    def test_yields_sorted_by_file_and_lineno(self, tmp_path):
        from src.ldb.scope import iter_targets

        (tmp_path / "a.py").write_text(
            "def alpha():\n    pass\n\ndef beta():\n    pass\n"
        )
        (tmp_path / "b.py").write_text("def gamma():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert names == ["alpha", "beta", "gamma"]

    def test_ignores_syntax_errors(self, tmp_path):
        from src.ldb.scope import iter_targets

        (tmp_path / "broken.py").write_text("def (:\n")
        (tmp_path / "good.py").write_text("def fine():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert names == ["fine"]


class TestRunAll:
    @pytest.mark.asyncio
    async def test_run_all_iterates_targets(self, tmp_path):
        from src.ldb.scope import LdbTarget

        cfg = _make_config(working_dir=str(tmp_path), ldb_scope_all=True, ldb_mode=2)
        runner = LdbRunner(cfg)

        targets_seen = []

        async def fake_player(source, entry):
            targets_seen.append(entry)
            return []

        fake_targets = [
            LdbTarget(file="a.py", name="alpha", lineno=1, end_lineno=3),
            LdbTarget(file="b.py", name="Beta.method", lineno=1, end_lineno=5),
        ]

        (tmp_path / "a.py").write_text("def alpha():\n    pass\n")
        (tmp_path / "b.py").write_text(
            "class Beta:\n    def method(self):\n        pass\n"
        )

        with (
            patch("src.ldb.runner.iter_targets", return_value=fake_targets),
            patch.object(runner, "_run_player", side_effect=fake_player),
        ):
            result = await runner._run_all()
            assert result.success is True
            assert targets_seen == ["alpha", "Beta.method"]

    @pytest.mark.asyncio
    async def test_run_all_mode3_fixes_and_commits(self, tmp_path):
        from src.ldb.scope import LdbTarget

        cfg = _make_config(working_dir=str(tmp_path), ldb_scope_all=True, ldb_mode=3)
        runner = LdbRunner(cfg)

        player_calls = 0

        async def fake_player(source, entry):
            nonlocal player_calls
            player_calls += 1
            if player_calls == 1:
                return [_LdbBug(id=1, file="a.py", line=1, description="bug")]
            return []

        async def fake_tester(bugs):
            for b in bugs:
                b.status = "confirmed"
                b.test_file = "test_a.py"
            return 1

        fake_targets = [
            LdbTarget(file="a.py", name="alpha", lineno=1, end_lineno=3),
        ]
        (tmp_path / "a.py").write_text("def alpha():\n    pass\n")

        with (
            patch("src.ldb.runner.iter_targets", return_value=fake_targets),
            patch.object(runner, "_run_player", side_effect=fake_player),
            patch.object(runner, "_run_tester", side_effect=fake_tester),
            patch.object(runner, "_run_fixer", return_value=1),
            patch.object(runner, "_git_commit") as mock_commit,
        ):
            result = await runner._run_all()
            assert result.bugs_found == 1
            assert result.bugs_fixed == 1
            mock_commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_all_empty_targets(self, tmp_path):
        cfg = _make_config(working_dir=str(tmp_path), ldb_scope_all=True, ldb_mode=2)
        runner = LdbRunner(cfg)

        with (
            patch("src.ldb.runner.iter_targets", return_value=[]),
            patch.object(runner, "_run_player") as mock_player,
        ):
            result = await runner._run_all()
            assert result.success is True
            assert result.bugs_found == 0
            mock_player.assert_not_called()
