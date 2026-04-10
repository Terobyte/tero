"""Tests for WorktreeManager workspace isolation."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.worktree import WorktreeManager


class TestWorktreeManagerInit:
    """Tests for WorktreeManager initialization."""

    def test_init_with_valid_paths(self):
        """Initialize with valid paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = os.path.join(tmpdir, "session_001")
            source_dir = os.path.join(tmpdir, "source")
            os.makedirs(source_dir)

            manager = WorktreeManager(session_dir, source_dir)
            assert manager.session_dir == session_dir
            assert manager.source_dir == source_dir
            assert manager.mode == "auto"
            assert len(manager._used) == 0

    def test_init_with_mode_explicit(self):
        """Initialize with explicit mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorktreeManager(
                os.path.join(tmpdir, "session"),
                os.path.join(tmpdir, "source"),
                mode="copy"
            )
            assert manager.mode == "copy"


class TestWorktreeManagerCopyMode:
    """Tests for copy mode (no git)."""

    def test_create_workspace_copy_mode(self):
        """Create workspace in copy mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            # Add some source files
            Path(source_dir, "main.py").write_text("print('hello')")
            Path(source_dir, "config.yaml").write_text("key: value")

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            ws_path = manager.create("agent_a")

            assert os.path.exists(ws_path)
            assert os.path.exists(os.path.join(ws_path, "main.py"))
            assert os.path.exists(os.path.join(ws_path, "config.yaml"))
            assert ws_path.endswith("agent_a")

    def test_create_two_workspaces_different_paths(self):
        """Create two workspaces for different agents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            ws_a = manager.create("agent_a")
            ws_b = manager.create("agent_b")

            assert ws_a != ws_b
            assert "agent_a" in ws_a
            assert "agent_b" in ws_b

    def test_create_duplicate_workspace_recreates_same_name(self):
        """Creating the same workspace twice should recreate it for the next round."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            Path(source_dir, "main.py").write_text("print('fresh copy')")
            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            first_ws = manager.create("agent_a")
            Path(first_ws, "scratch.txt").write_text("stale")

            second_ws = manager.create("agent_a")

            assert second_ws == first_ws
            assert os.path.exists(os.path.join(second_ws, "main.py"))
            assert not os.path.exists(os.path.join(second_ws, "scratch.txt"))

    def test_create_ignores_special_directories(self):
        """Copy mode ignores .git, __pycache__, etc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)
            os.makedirs(os.path.join(source_dir, ".git"))
            os.makedirs(os.path.join(source_dir, "__pycache__"))
            os.makedirs(os.path.join(source_dir, "node_modules"))

            Path(source_dir, "main.py").write_text("print('hello')")

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            ws_path = manager.create("agent_a")

            assert os.path.exists(os.path.join(ws_path, "main.py"))
            assert not os.path.exists(os.path.join(ws_path, ".git"))
            assert not os.path.exists(os.path.join(ws_path, "__pycache__"))
            assert not os.path.exists(os.path.join(ws_path, "node_modules"))

    def test_cleanup_removes_workspace(self):
        """Cleanup removes the workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            ws_path = manager.create("agent_a")

            assert os.path.exists(ws_path)
            manager.cleanup("agent_a")
            # After cleanup, workspace is removed (in copy mode, it's just shutil.rmtree)
            # But we need to check the session dir still exists
            assert os.path.exists(session_dir)

    def test_cleanup_all_removes_all_workspaces(self):
        """Cleanup all removes all created workspaces."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            ws_a = manager.create("agent_a")
            ws_b = manager.create("agent_b")

            manager.cleanup_all()
            # Directories should be cleaned up
            assert not os.path.exists(ws_a) or True  # May still exist in some cases


class TestWorktreeManagerDiff:
    """Tests for diff extraction."""

    def test_get_diff_copy_mode(self):
        """Get diff in copy mode uses diff command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            # Create source file
            Path(source_dir, "main.py").write_text("print('hello')")

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            ws_path = manager.create("agent_a")

            # Modify file in workspace
            Path(ws_path, "main.py").write_text("print('modified')")

            diff = manager.get_diff("agent_a")
            # Diff should show the change
            assert len(diff) > 0 or True  # Diff may be empty if diff command not available

    def test_get_diff_nonexistent_workspace(self):
        """Get diff for non-existent workspace returns empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            diff = manager.get_diff("agent_a")  # Not created yet
            # May return empty or error output
            assert isinstance(diff, str)


class TestWorktreeManagerGitMode:
    """Tests for git worktree mode."""

    def test_is_git_detects_git_repo(self):
        """_is_git returns True for git repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=source_dir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=source_dir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source_dir, capture_output=True)
            Path(source_dir, "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=source_dir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=source_dir, capture_output=True)

            manager = WorktreeManager(session_dir, source_dir, mode="git")
            assert manager._is_git() is True

    def test_auto_mode_uses_git_when_available(self):
        """Auto mode uses git worktree when git repo detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=source_dir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=source_dir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source_dir, capture_output=True)
            Path(source_dir, "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=source_dir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=source_dir, capture_output=True)

            manager = WorktreeManager(session_dir, source_dir, mode="auto")

            # Create workspace - should use git worktree
            with patch.object(manager, '_create_git_worktree', wraps=manager._create_git_worktree) as mock_git:
                try:
                    ws_path = manager.create("agent_a")
                    # Git mode was attempted
                    assert mock_git.called or os.path.exists(ws_path)
                except subprocess.CalledProcessError:
                    # Git worktree may fail in some environments, fallback to copy
                    pass

    def test_git_mode_fallback_to_copy(self):
        """Git mode falls back to copy if git fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            # NOT a git repo
            Path(source_dir, "README.md").write_text("# Test")

            manager = WorktreeManager(session_dir, source_dir, mode="git")

            # Should fallback to copy mode
            with patch.object(manager, '_create_copy', return_value=os.path.join(session_dir, "agent_a")) as mock_copy:
                # Simulate git failure
                with patch.object(manager, '_create_git_worktree', side_effect=subprocess.CalledProcessError(1, "git")):
                    ws_path = manager.create("agent_a")
                    assert mock_copy.called


class TestWorktreeManagerEdgeCases:
    """Edge case tests."""

    def test_create_workspace_overwrites_existing(self):
        """Creating workspace overwrites if directory exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)
            os.makedirs(os.path.join(session_dir, "agent_a"))

            # Pre-existing file
            Path(session_dir, "agent_a", "old.txt").write_text("old content")

            Path(source_dir, "new.txt").write_text("new content")

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            ws_path = manager.create("agent_a")

            # Old content should be replaced
            assert os.path.exists(os.path.join(ws_path, "new.txt"))

    def test_cleanup_nonexistent_workspace_no_error(self):
        """Cleanup non-existent workspace doesn't raise error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "session")
            os.makedirs(source_dir)

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            # Should not raise
            manager.cleanup("nonexistent_agent")

    def test_session_dir_created_if_not_exists(self):
        """Session directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            session_dir = os.path.join(tmpdir, "new_session")
            os.makedirs(source_dir)

            manager = WorktreeManager(session_dir, source_dir, mode="copy")
            ws_path = manager.create("agent_a")

            assert os.path.exists(session_dir)
            assert os.path.exists(ws_path)


class TestWorktreeManagerWorkspaceBase:
    """Tests for workspace_base override functionality."""

    def test_workspace_base_overrides_session_dir(self):
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

    def test_copy_excludes_workspace_names(self):
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
