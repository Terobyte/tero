"""Tests proving real bugs. Each test asserts correct behavior and FAILS because the bug exists."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.bug_detector import BugDetector
from src.debugger import Debugger
from src.feedback import (
    ReviewIssues,
    ReviewPassed,
    _has_real_security_concern,
    parse_review_output,
)


class TestBug1RoundNumNameError:
    """Bug: orchestrator.run() raises NameError when exception occurs before round_num=0.

    orchestrator.py:99 calls Path(plan_file).read_text() which can raise FileNotFoundError.
    round_num is assigned on line 116 but the except handler on line 250 references it.
    If the exception happens before line 116, NameError masks the original error.
    """

    def test_missing_plan_file_returns_file_not_found_error(self):
        from src.config import Config
        from src.orchestrator import Orchestrator, OrchestratorResult

        config = Config(
            plan_file="/nonexistent/path/requirements.md",
            working_dir="/tmp",
        )
        providers = {"zai": {"type": "zai"}}

        with (
            patch.object(Path, "mkdir"),
            patch("src.orchestrator.SessionManager") as MockSession,
            patch("src.orchestrator.ProviderRegistry"),
            patch("src.orchestrator.WorktreeManager"),
            patch("src.orchestrator.RunRecorder"),
            patch("src.orchestrator._LEARNING_AVAILABLE", False),
        ):
            mock_session = MagicMock()
            mock_session._state = {}
            MockSession.return_value = mock_session

            orch = Orchestrator(config, providers)

            result = orch.run()

            assert isinstance(result, OrchestratorResult), (
                "run() should return OrchestratorResult, not raise NameError"
            )
            assert result.success is False
            assert "round_num" not in str(result.error), (
                f"NameError about 'round_num' masked the real error: {result.error}"
            )


class TestBug2ExtractTextDuplication:
    """Bug: _extract_text duplicates text when dict has both type=text and result keys.

    debugger.py:464-469 use independent `if` instead of `elif`.
    A dict {"type": "text", "text": "X", "result": "X"} appends "X" twice.
    """

    def test_dict_with_text_and_result_no_duplicate(self):
        parts = []
        message = {"type": "text", "text": "Found 3 bugs", "result": "Found 3 bugs"}
        Debugger._extract_text(message, parts)

        assert len(parts) == 1, f"Expected 1, got {len(parts)}: {parts}"

    def test_dict_with_different_text_and_result_both_extracted(self):
        parts = []
        message = {"type": "text", "text": "header", "result": "body"}
        Debugger._extract_text(message, parts)

        assert len(parts) == 1, (
            f"Expected 1 (text wins over result via elif), got {len(parts)}: {parts}"
        )
        assert parts[0] == "header"


class TestBug3NoTestsCollectedFalsePositive:
    """Bug: _check_tests returns 1 when pytest exits with code 5 (no tests collected).

    bug_detector.py:229 falls through to `return 1` for any non-zero exit code
    that doesn't contain "failed" in stdout. Exit code 5 means no tests were
    collected, which is not a bug — the workspace simply has no test files.
    """

    def test_exit_code_5_returns_zero(self):
        mock_result = subprocess.CompletedProcess(
            args=["pytest"],
            returncode=5,
            stdout="collected 0 items\n\nNO TESTS RAN in 0.01s",
            stderr="",
        )

        with patch("subprocess.run", return_value=mock_result):
            result = BugDetector._check_tests("/tmp/fake_workspace")

        assert result == 0, f"Expected 0 (no tests collected ≠ bug), got {result}"


class TestBug4SecurityConcernFalseNegative:
    """Bug: _has_real_security_concern suppresses real findings via overly wide negative prefix.

    feedback.py:236-249 checks for negative words within 40 chars before a security keyword.
    "any" in _NEGATIVE_PREFIX_RE incorrectly negates "any exploit found".
    "No" in a previous sentence within 40 chars suppresses a real finding.
    """

    def test_any_vulnerability_is_affirmative(self):
        text = "Fix any XSS vulnerability in the codebase immediately."

        result = _has_real_security_concern(text)

        assert result is True, (
            "'any XSS vulnerability' is affirmative, should not be suppressed"
        )

    def test_exploit_after_no_in_previous_sentence(self):
        text = "No issues with imports here. However, exploit found at line 42."

        result = _has_real_security_concern(text)

        assert result is True, (
            "'exploit found' is a real finding despite 'No' in previous sentence"
        )

        result = _has_real_security_concern(text)

        assert result is True, (
            "XSS vulnerability should be detected despite 'No' in previous sentence"
        )

    def test_any_vulnerability_is_affirmative(self):
        text = "Fix any XSS vulnerability in the codebase immediately."

        result = _has_real_security_concern(text)

        assert result is True, (
            "'any XSS vulnerability' is affirmative, should not be suppressed"
        )


class TestBug5ReviewPassedWithRealXSS:
    """Bug: parse_review_output returns ReviewPassed when real exploit exists with CODE_REVIEW_PASSED.

    A consequence of Bug 4. When CODE_REVIEW_PASSED appears alongside a real finding,
    the false negative in _has_real_security_concern causes the whole review to pass.
    """

    def test_code_review_passed_with_exploit_returns_issues(self):
        class FakeTextBlock:
            __name__ = "TextBlock"
            text = (
                "CODE_REVIEW_PASSED\n"
                "No issues with imports.\n"
                "However, exploit found at line 42 in render()."
            )

        messages = [
            MagicMock(role="assistant", content=[FakeTextBlock()]),
        ]

        result = parse_review_output(messages)

        assert isinstance(result, ReviewIssues), (
            f"Real exploit should return ReviewIssues, got {type(result).__name__}"
        )
