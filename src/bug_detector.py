"""Minimal bug detector primitives used by duel/orchestrator tests."""

from dataclasses import dataclass


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
    """Very small bug detector stub.

    The real implementation can be expanded later; tests currently only require
    constructor compatibility and a `run()` method returning `BugReport`.
    """

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
        _ = working_dir
        return BugReport()
