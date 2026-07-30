"""Tests for src/ldb/scope.py — iter_targets, LdbTarget."""

from __future__ import annotations

import pytest

from src.ldb.scope import LdbTarget, iter_targets


class TestIterTargets:
    def test_finds_top_level_functions(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "def public_func():\n    pass\n\ndef _private_func():\n    pass\n"
        )
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert "public_func" in names
        assert "_private_func" not in names

    def test_finds_class_methods(self, tmp_path):
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
        (tmp_path / "a.py").write_text(
            "def alpha():\n    pass\n\ndef beta():\n    pass\n"
        )
        (tmp_path / "b.py").write_text("def gamma():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert names == ["alpha", "beta", "gamma"]

    def test_ignores_syntax_errors(self, tmp_path):
        (tmp_path / "broken.py").write_text("def (:\n")
        (tmp_path / "good.py").write_text("def fine():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert names == ["fine"]

    def test_skips_private_classes(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "class _Internal:\n    def method(self):\n        pass\n\nclass Public:\n    def method(self):\n        pass\n"
        )
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert "Public.method" in names
        assert "_Internal.method" not in names

    def test_includes_async_functions(self, tmp_path):
        (tmp_path / "mod.py").write_text("async def async_handler():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert "async_handler" in names

    def test_empty_dir_returns_no_targets(self, tmp_path):
        targets = list(iter_targets(tmp_path))
        assert targets == []

    def test_target_has_correct_fields(self, tmp_path):
        (tmp_path / "mod.py").write_text("def my_func():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        assert len(targets) == 1
        t = targets[0]
        assert t.file == "mod.py"
        assert t.name == "my_func"
        assert t.lineno == 1
        assert t.end_lineno >= 1

    def test_target_is_frozen(self, tmp_path):
        (tmp_path / "mod.py").write_text("def my_func():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        t = targets[0]
        with pytest.raises(AttributeError):
            t.name = "changed"

    def test_finds_methods_of_inner_classes(self, tmp_path):
        """iter_targets should discover methods on nested (inner) classes."""
        (tmp_path / "mod.py").write_text(
            "class Outer:\n"
            "    class Inner:\n"
            "        def inner_method(self):\n"
            "            pass\n"
            "    def outer_method(self):\n"
            "        pass\n"
        )
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert "Outer.outer_method" in names
        assert "Outer.Inner.inner_method" in names

    def test_skips_underscore_prefixed_files(self, tmp_path):
        """Issue #4: only the FILE name is checked for _* prefix, not path components.

        _private.py should be skipped, but subdir/_underscore/file.py should NOT
        be skipped just because it lives under a _-prefixed directory.
        """
        (tmp_path / "_private.py").write_text("def secret():\n    pass\n")
        (tmp_path / "__init__.py").write_text("def init_func():\n    pass\n")
        (tmp_path / "public.py").write_text("def visible():\n    pass\n")
        targets = list(iter_targets(tmp_path))
        names = [t.name for t in targets]
        assert "visible" in names
        assert "secret" not in names
        assert "init_func" not in names
