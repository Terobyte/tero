"""Изоляция workspace для каждого агента."""

import os
import shutil
import subprocess


class WorktreeManager:
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
        # Bug fix #8: Track actual workspace mode (git vs copy) for proper cleanup
        self._workspace_modes: dict[str, str] = {}

    def create(self, agent_name: str) -> str:
        """Создать изолированный workspace. Возвращает абсолютный путь."""
        base = self.workspace_base if self.workspace_base is not None else self.session_dir
        ws = os.path.join(base, agent_name)
        if agent_name in self._used or agent_name in self._workspace_modes or os.path.exists(ws):
            self.cleanup(agent_name)
        self._used.add(agent_name)
        if os.path.exists(ws):
            shutil.rmtree(ws, ignore_errors=True)

        # Track which mode was actually used for proper cleanup
        use_git = self.mode == "git" or (self.mode == "auto" and self._is_git())

        # Bug fix #1: In git mode, check if repo is dirty - if so, use copy mode
        # to preserve uncommitted and untracked changes
        if use_git and self._is_dirty():
            use_git = False

        if use_git:
            try:
                workspace = self._create_git_worktree(agent_name, ws)
                self._workspace_modes[agent_name] = "git"
                return workspace
            except subprocess.CalledProcessError:
                self._workspace_modes[agent_name] = "copy"
                return self._create_copy(ws)
        else:
            self._workspace_modes[agent_name] = "copy"
            return self._create_copy(ws)

    def get_diff(self, agent_name: str) -> str:
        base = self.workspace_base if self.workspace_base is not None else self.session_dir
        ws = os.path.join(base, agent_name)
        if not os.path.exists(ws):
            return ""
        if self._is_git() and os.path.exists(os.path.join(ws, ".git")):
            r = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=ws, capture_output=True, text=True
            )
            return r.stdout
        # Fallback: diff against source
        try:
            r = subprocess.run(
                ["diff", "-ruN", "--exclude=.git", self.source_dir, ws],
                capture_output=True, text=True
            )
            if r.returncode == 2:  # diff error (not just "files differ")
                return f"diff error: {r.stderr.strip()}"
            return r.stdout
        except FileNotFoundError:
            return "diff command not available"

    def cleanup(self, agent_name: str):
        base = self.workspace_base if self.workspace_base is not None else self.session_dir
        ws = os.path.join(base, agent_name)

        # Bug fix #8: Use tracked mode instead of self._is_git()
        ws_mode = self._workspace_modes.pop(agent_name, None)

        # Even if not in _workspace_modes, check if it's a git worktree to avoid leaks from crashed processes
        is_git_worktree = ws_mode == "git"
        if not is_git_worktree and os.path.exists(ws):
            # Check if it looks like a git worktree (has .git file)
            git_dot_file = os.path.join(ws, ".git")
            if os.path.isfile(git_dot_file):
                is_git_worktree = True

        if is_git_worktree:
            # Try to remove via git first
            subprocess.run(
                ["git", "worktree", "remove", ws, "--force"],
                cwd=self.source_dir, capture_output=True
            )
            session_id = os.path.basename(self.session_dir)
            branch = f"g3/{session_id}/{agent_name}"
            # Also try the branch name used in Phase 1 (if we can't find it, it's fine)
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=self.source_dir, capture_output=True
            )
            # Try to delete any branch that might look like our worktree branch
            # based on the workspace path if needed, but session_id/agent_name is usually enough.

        if os.path.exists(ws):
            shutil.rmtree(ws, ignore_errors=True)
        self._used.discard(agent_name)

    def cleanup_all(self):
        names = list(self._used | set(self._workspace_modes))
        for name in names:
            self.cleanup(name)

    def _is_git(self) -> bool:
        return os.path.exists(os.path.join(self.source_dir, ".git"))

    def _is_dirty(self) -> bool:
        """Bug fix #1: Check if repo has uncommitted or untracked changes."""
        if not self._is_git():
            return False
        # Check for staged or unstaged changes
        r = subprocess.run(
            ["git", "diff", "HEAD", "--quiet"],
            cwd=self.source_dir, capture_output=True
        )
        if r.returncode != 0:
            return True
        # Check for untracked files
        r = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=self.source_dir, capture_output=True, text=True
        )
        return bool(r.stdout.strip())

    def _create_git_worktree(self, agent_name: str, ws: str) -> str:
        session_id = os.path.basename(self.session_dir)
        branch = f"g3/{session_id}/{agent_name}"
        subprocess.run(
            ["git", "branch", "-f", branch, "HEAD"],
            cwd=self.source_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "worktree", "add", ws, branch],
            cwd=self.source_dir, check=True, capture_output=True
        )
        return ws

    def _create_copy(self, ws: str) -> str:
        base_excludes = {".git", "__pycache__", "node_modules", ".venv", "*.pyc", ".g3"}
        all_excludes = base_excludes | self.exclude_names
        shutil.copytree(
            self.source_dir, ws,
            ignore=shutil.ignore_patterns(*all_excludes),
        )
        return ws
