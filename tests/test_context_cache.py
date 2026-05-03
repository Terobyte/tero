"""Tests for ContextCache in debugger_context."""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.debugger_context import (
    ContextCache,
    _CacheEntry,
    build_context,
    plan_file_chunks,
)


# ── ContextCache unit tests ───────────────────────────────────────────────────


class TestContextCacheGet:
    def test_get_returns_none_for_uncached_path(self, tmp_path):
        cache = ContextCache()
        f = tmp_path / "foo.py"
        f.write_text("x = 1")
        assert cache.get(f) is None

    def test_put_then_get_returns_entry(self, tmp_path):
        cache = ContextCache()
        f = tmp_path / "bar.py"
        f.write_text("def bar(): pass")
        cache.put(f, "def bar(): pass", "## rendered", "full")
        entry = cache.get(f)
        assert entry is not None
        assert entry.content == "def bar(): pass"
        assert entry.rendered == "## rendered"
        assert entry.render_mode == "full"

    def test_get_returns_none_after_mtime_changes(self, tmp_path):
        cache = ContextCache()
        f = tmp_path / "baz.py"
        f.write_text("x = 1")
        cache.put(f, "x = 1", "rendered_x", "full")
        # Advance mtime by writing the file again (guarantees a new mtime_ns)
        time.sleep(0.01)
        f.write_text("x = 2")
        assert cache.get(f) is None

    def test_get_returns_none_for_nonexistent_path(self, tmp_path):
        cache = ContextCache()
        missing = tmp_path / "nonexistent.py"
        # Should not raise; stat() fails with OSError which get() handles
        assert cache.get(missing) is None


class TestContextCachePut:
    def test_put_stores_entry_with_current_mtime(self, tmp_path):
        cache = ContextCache()
        f = tmp_path / "x.py"
        f.write_text("pass")
        cache.put(f, "pass", "rendered_pass", "full")
        assert len(cache._store) == 1

    def test_put_on_nonexistent_path_is_a_noop(self, tmp_path):
        cache = ContextCache()
        missing = tmp_path / "gone.py"
        # Should not raise
        cache.put(missing, "content", "rendered", "full")
        assert len(cache._store) == 0


class TestContextCacheClear:
    def test_clear_wipes_all_entries(self, tmp_path):
        cache = ContextCache()
        for i in range(3):
            f = tmp_path / f"f{i}.py"
            f.write_text(f"x = {i}")
            cache.put(f, f"x = {i}", f"rendered_{i}", "full")
        assert len(cache._store) == 3
        cache.clear()
        assert len(cache._store) == 0

    def test_clear_on_empty_cache_is_safe(self):
        cache = ContextCache()
        cache.clear()  # should not raise
        assert len(cache._store) == 0


# ── Integration: build_context with cache ─────────────────────────────────────


class TestBuildContextWithCache:
    def test_second_call_hits_cache_not_read_text(self, tmp_path):
        """build_context should not read files that are already cached."""
        f = tmp_path / "module.py"
        f.write_text("def foo(): pass\n")

        cache = ContextCache()

        # First call — populates cache
        result1 = build_context(str(tmp_path), file_subset=["module.py"], cache=cache)
        assert "foo" in result1

        # Second call — should use cache, so read_text must NOT be called
        with patch.object(Path, "read_text", side_effect=AssertionError("read_text called")) as mock_rt:
            result2 = build_context(str(tmp_path), file_subset=["module.py"], cache=cache)

        assert result1 == result2

    def test_no_cache_still_works(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 42\n")
        result = build_context(str(tmp_path), file_subset=["mod.py"])
        assert "x = 42" in result

    def test_cache_miss_on_changed_file(self, tmp_path):
        f = tmp_path / "changing.py"
        f.write_text("x = 1\n")
        cache = ContextCache()

        result1 = build_context(str(tmp_path), file_subset=["changing.py"], cache=cache)
        assert "x = 1" in result1

        # Modify the file (advances mtime)
        time.sleep(0.01)
        f.write_text("x = 999\n")

        result2 = build_context(str(tmp_path), file_subset=["changing.py"], cache=cache)
        assert "x = 999" in result2


# ── Integration: plan_file_chunks with cache ──────────────────────────────────


class TestPlanFileChunksWithCache:
    def test_second_call_hits_cache_not_read_text(self, tmp_path):
        """plan_file_chunks should not re-read files already in the cache."""
        f = tmp_path / "pkg.py"
        f.write_text("def run(): pass\n")

        cache = ContextCache()

        # First call — populates cache
        chunks1 = plan_file_chunks(str(tmp_path), cache=cache)
        assert chunks1  # at least one chunk

        # Second call — should use cache
        with patch.object(Path, "read_text", side_effect=AssertionError("read_text called")):
            chunks2 = plan_file_chunks(str(tmp_path), cache=cache)

        assert chunks1 == chunks2

    def test_no_cache_still_works(self, tmp_path):
        f = tmp_path / "simple.py"
        f.write_text("pass\n")
        chunks = plan_file_chunks(str(tmp_path))
        assert chunks == [["simple.py"]]

    def test_cache_populated_after_plan(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("class A: pass\n")
        cache = ContextCache()
        plan_file_chunks(str(tmp_path), cache=cache)
        # Cache should now have an entry for a.py
        assert cache.get(f) is not None
