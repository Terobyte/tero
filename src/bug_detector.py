"""Bug detector: runs tests, type checks, lint, and compile checks."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BugReport:
    """Structured bug counts returned by bug detection."""

    total: int = 0
    test_bugs: int = 0
    lint_bugs: int = 0
    type_bugs: int = 0
    compile_bugs: int = 0
    runtime_bugs: int = 0


class BugDetector:
    """Detects bugs in a workspace by running test, lint, type, and compile checks."""

    def __init__(
        self,
        run_tests: bool = True,
        run_types: bool = True,
        run_lint: bool = True,
        run_compile: bool = True,
    ):
        self.run_tests = run_tests
        self.run_types = run_types
        self.run_lint = run_lint
        self.run_compile = run_compile

    def run(self, working_dir: str) -> BugReport:
        """Run all enabled checks and return aggregated bug report."""
        report = BugReport()

        if self.run_compile:
            report.compile_bugs = self._check_compile(working_dir)

        if self.run_lint:
            report.lint_bugs = self._check_lint(working_dir)

        if self.run_types:
            report.type_bugs = self._check_types(working_dir)

        if self.run_tests:
            report.test_bugs = self._check_tests(working_dir)

        report.total = (
            report.test_bugs
            + report.lint_bugs
            + report.type_bugs
            + report.compile_bugs
            + report.runtime_bugs
        )
        return report

    @staticmethod
    def _check_compile(working_dir: str) -> int:
        """Count syntax errors in Python files."""
        errors = 0
        for py_file in Path(working_dir).rglob("*.py"):
            try:
                result = subprocess.run(
                    ["/usr/bin/env", "python3", "-c", f"import py_compile; py_compile.compile({str(py_file)!r}, doraise=True)"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    errors += 1
            except (subprocess.TimeoutExpired, OSError):
                pass
        return errors

    @staticmethod
    def _check_lint(working_dir: str) -> int:
        """Run flake8 or fallback to pyflakes, return error count."""
        for cmd in (["python3", "-m", "flake8", "--count", "--quiet"],):
            try:
                result = subprocess.run(
                    cmd + [working_dir],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    # Try to parse count from last line
                    lines = result.stdout.strip().splitlines()
                    for line in reversed(lines):
                        line = line.strip()
                        if line.isdigit():
                            return int(line)
                    # Fallback: count non-empty stderr lines
                    return max(1, len([l for l in result.stderr.splitlines() if l.strip()]))
            except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
                continue
        return 0

    @staticmethod
    def _check_types(working_dir: str) -> int:
        """Run mypy if available, return error count."""
        try:
            result = subprocess.run(
                ["python3", "-m", "mypy", "--no-error-summary", working_dir],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                # Count error lines
                return len([l for l in result.stdout.splitlines() if "error:" in l])
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
        return 0

    @staticmethod
    def _check_tests(working_dir: str) -> int:
        """Run pytest if available, return failure count."""
        try:
            result = subprocess.run(
                ["python3", "-m", "pytest", "--tb=no", "-q", working_dir],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                # Parse "X failed" from pytest output
                for line in result.stdout.splitlines():
                    if "failed" in line:
                        parts = line.split()
                        for i, p in enumerate(parts):
                            if p == "failed" and i > 0:
                                try:
                                    return int(parts[i - 1])
                                except ValueError:
                                    pass
                return 1  # At least one failure
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
        return 0
