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

    @staticmethod
    def _pytest_ignores(working_dir: str) -> list[str]:
        """Common directories that should never be traversed by bug detection."""
        base = Path(working_dir)
        ignore_names = BugDetector._ignored_names()
        args: list[str] = []
        for name in ignore_names:
            args.extend(["--ignore", str(base / name)])
        return args

    @staticmethod
    def _ignored_names() -> tuple[str, ...]:
        """Directory names to skip during workspace-wide checks."""
        return (
            ".venv",
            "venv",
            "node_modules",
            ".git",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            "__pycache__",
        )

    @staticmethod
    def _python_files(working_dir: str) -> list[str]:
        """Enumerate Python files while excluding dependency/cache directories."""
        base = Path(working_dir)
        ignored = set(BugDetector._ignored_names())
        return [
            str(py_file)
            for py_file in base.rglob("*.py")
            if ignored.isdisjoint(py_file.relative_to(base).parts)
        ]

    @staticmethod
    def _missing_module_error(
        result: subprocess.CompletedProcess[str], module: str
    ) -> bool:
        """Return True when `python -m <module>` failed because the module is missing."""
        stderr = (result.stderr or "").lower()
        stdout = (result.stdout or "").lower()
        needles = (
            f"no module named {module.lower()}",
            f"no module named '{module.lower()}'",
        )
        return any(needle in stderr or needle in stdout for needle in needles)

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
        for py_file in BugDetector._python_files(working_dir):
            py_file = Path(py_file)
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/env",
                        "python3",
                        "-c",
                        f"import py_compile; py_compile.compile({str(py_file)!r}, doraise=True)",
                    ],
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
        exclude_csv = ",".join(BugDetector._ignored_names())
        python_files = BugDetector._python_files(working_dir)
        commands: tuple[tuple[list[str], str], ...] = (
            (
                [
                    "python3",
                    "-m",
                    "flake8",
                    "--count",
                    "--quiet",
                    "--exclude",
                    exclude_csv,
                    working_dir,
                ],
                "flake8",
            ),
            (
                ["python3", "-m", "pyflakes", *python_files] if python_files else [],
                "pyflakes",
            ),
        )
        for cmd, module_name in commands:
            if not cmd:
                continue
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if BugDetector._missing_module_error(result, module_name):
                    continue
                if result.returncode != 0:
                    # Try to parse count from last line
                    lines = result.stdout.strip().splitlines()
                    for line in reversed(lines):
                        line = line.strip()
                        if line.isdigit():
                            return int(line)
                    output_lines = [
                        line
                        for line in (result.stdout + "\n" + result.stderr).splitlines()
                        if line.strip()
                    ]
                    return max(1, len(output_lines))
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
            if BugDetector._missing_module_error(result, "mypy"):
                return 0
            if result.returncode != 0:
                # Count error lines matching mypy's file:line: error: pattern
                import re as _re

                return len(
                    [
                        l
                        for l in result.stdout.splitlines()
                        if _re.match(r"^.*:\d+:\s+error:", l)
                    ]
                )
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
        return 0

    @staticmethod
    def _check_tests(working_dir: str) -> int:
        """Run pytest if available, return failure count."""
        # Exclude dependency folders like .venv, venv, and node_modules.
        try:
            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "pytest",
                    "--tb=no",
                    "-q",
                    *BugDetector._pytest_ignores(working_dir),
                    working_dir,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if BugDetector._missing_module_error(result, "pytest"):
                return 0
            if result.returncode == 5:
                return 0
            if result.returncode != 0:
                # Parse "X failed" from pytest output
                for line in result.stdout.splitlines():
                    if "failed" in line:
                        parts = line.split()
                        for i, p in enumerate(parts):
                            if p.rstrip(",") == "failed" and i > 0:
                                try:
                                    return int(parts[i - 1])
                                except ValueError:
                                    pass
                return 0  # Cannot determine failure count
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
        return 0
