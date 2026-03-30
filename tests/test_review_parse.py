"""Tests for code-review regex patterns in feedback.parse_review_output."""

from dataclasses import dataclass

import pytest

from src.feedback import (
    ReviewIssues,
    ReviewPassed,
    parse_review_output,
    _CODE_REVIEW_PASSED_RE,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


@dataclass
class MockTextBlock:
    text: str


@dataclass
class MockAssistantMessage:
    content: list
    role: str = "assistant"


def _msg(text: str) -> list:
    """Convenience: wrap text in a single assistant message list."""
    return [MockAssistantMessage(content=[MockTextBlock(text)])]


# ---------------------------------------------------------------------------
# _CODE_REVIEW_PASSED_RE – every alternative branch
# ---------------------------------------------------------------------------


class TestCodeReviewPassedRegex:
    """Verify every branch of _CODE_REVIEW_PASSED_RE matches."""

    @pytest.mark.parametrize(
        "text",
        [
            "CODE_REVIEW_PASSED",
            "code_review_passed",
            "Code_Review_Passed",
            "CODE_REVIEW_PASSED\nSome extra notes",
        ],
        ids=["exact", "lower", "mixed", "with-trailing"],
    )
    def test_exact_marker(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "No critical issue",
            "No critical issues",
            "no critical issue",
            "NO CRITICAL ISSUES",
        ],
        ids=["singular", "plural", "lower-singular", "upper-plural"],
    )
    def test_no_critical_issues(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "No issue found",
            "No issues found",
            "no issue found",
            "NO ISSUES FOUND",
        ],
        ids=["singular", "plural", "lower", "upper"],
    )
    def test_no_issues_found(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "look good",
            "looks good",
            "Look Good",
            "LOOKS GOOD",
        ],
        ids=["base", "s", "title", "upper"],
    )
    def test_looks_good(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "LGTM",
            "lgtm",
            "Lgtm",
            "LGTM!",
        ],
        ids=["upper", "lower", "title", "punct"],
    )
    def test_lgtm(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "No bug found",
            "No bugs found",
            "no bug found",
            "NO BUGS FOUND",
        ],
        ids=["singular", "plural", "lower", "upper"],
    )
    def test_no_bugs_found(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Code is correct",
            "code is correct",
            "CODE IS CORRECT",
        ],
        ids=["normal", "lower", "upper"],
    )
    def test_code_is_correct(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Implementation is correct",
            "implementation is correct",
            "IMPLEMENTATION IS CORRECT",
        ],
        ids=["normal", "lower", "upper"],
    )
    def test_implementation_is_correct(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "All components properly implemented",
            "All components properly implemented and tested",
            "all components properly implemented",
            "all components properly implemented and tested",
        ],
        ids=["base", "and-tested", "lower", "lower-and-tested"],
    )
    def test_all_components_properly_implemented(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "All components are properly implemented",
            "All components are properly implemented and tested",
            "all components are properly implemented and tested",
        ],
        ids=["with-are", "with-are-and-tested", "lower"],
    )
    def test_all_components_are_properly_implemented(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "All component implemented correctly",
            "All components implemented correctly",
            "All component are implemented correctly",
            "All components are implemented correctly",
            "all components implemented correctly",
        ],
        ids=["singular", "plural", "singular-are", "plural-are", "lower"],
    )
    def test_all_components_implemented_correctly(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Implementation look correct",
            "Implementation looks correct",
            "implementation looks correct",
            "IMPLEMENTATION LOOKS CORRECT",
        ],
        ids=["base", "s", "lower", "upper"],
    )
    def test_implementation_looks_correct(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Verdict: all components properly implemented",
            "Verdict: all components properly implemented and tested",
            "verdict: all components properly implemented and tested",
            "VERDICT: ALL COMPONENTS PROPERLY IMPLEMENTED AND TESTED",
        ],
        ids=["base", "and-tested", "lower", "upper"],
    )
    def test_verdict_all_components(self, text):
        assert _CODE_REVIEW_PASSED_RE.search(text)

    @pytest.mark.parametrize(
        "text,word",
        [
            ("Everything looks good", "good"),
            ("Everything looks correct", "correct"),
            ("Everything looks fine", "fine"),
            ("Everything look good", "good"),
            ("everything looks fine", "fine"),
            ("EVERYTHING LOOKS GOOD", "good"),
        ],
        ids=[
            "looks-good",
            "looks-correct",
            "looks-fine",
            "look-good",
            "lower-fine",
            "upper",
        ],
    )
    def test_everything_looks(self, text, word):
        assert _CODE_REVIEW_PASSED_RE.search(text)


# ---------------------------------------------------------------------------
# Negative cases: text that should NOT match
# ---------------------------------------------------------------------------


class TestRegexNegative:
    """Patterns that must NOT trigger a passed verdict."""

    @pytest.mark.parametrize(
        "text",
        [
            "There are critical issues to fix.",
            "The implementation has bugs.",
            "This code is not correct.",
            "Components are missing.",
            "Everything is broken.",
            "I found several security vulnerabilities.",
        ],
        ids=[
            "critical-issues",
            "has-bugs",
            "not-correct",
            "missing",
            "broken",
            "vulns",
        ],
    )
    def test_code_review_passed_re_does_not_match(self, text):
        assert not _CODE_REVIEW_PASSED_RE.search(text)

# ---------------------------------------------------------------------------
# parse_review_output integration: every regex branch via full parse
# ---------------------------------------------------------------------------


class TestParseReviewAllPatterns:
    """parse_review_output returns ReviewPassed for every supported pattern."""

    @pytest.mark.parametrize(
        "text",
        [
            "CODE_REVIEW_PASSED",
            "code_review_passed",
            "No critical issues",
            "No issue found",
            "No issues found",
            "Looks good",
            "LGTM",
            "No bugs found",
            "Code is correct",
            "Implementation is correct",
            "All components properly implemented",
            "All components properly implemented and tested",
            "All components are properly implemented",
            "All components are properly implemented and tested",
            "All component implemented correctly",
            "All components are implemented correctly",
            "Implementation looks correct",
            "Verdict: all components properly implemented and tested",
            "Everything looks good",
            "Everything looks correct",
            "Everything looks fine",
        ],
    )
    def test_passed_patterns(self, text):
        verdict = parse_review_output(_msg(text))
        assert isinstance(verdict, ReviewPassed), f"Expected ReviewPassed for: {text!r}"

    @pytest.mark.parametrize(
        "text,expect_substring",
        [
            ("1. SQL injection risk\n2. Missing input validation", "SQL injection"),
            ("Found issues:\n1. Missing error handling", "Missing error handling"),
            ("The code has potential XSS in render().", "XSS"),
        ],
        ids=["two-issues", "one-issue", "no-number-free-text"],
    )
    def test_issues_patterns(self, text, expect_substring):
        verdict = parse_review_output(_msg(text))
        assert isinstance(verdict, ReviewIssues)
        assert expect_substring in verdict.text

    def test_empty_output(self):
        verdict = parse_review_output([MockAssistantMessage(content=[])])
        assert isinstance(verdict, ReviewIssues)
        assert "no output" in verdict.text.lower()

    def test_no_assistant_message(self):
        @dataclass
        class _R:
            result: str = "done"

        verdict = parse_review_output([_R()])
        assert isinstance(verdict, ReviewIssues)
        assert "no output" in verdict.text.lower()

    def test_passed_with_extra_commentary(self):
        """CODE_REVIEW_PASSED embedded in a longer response still passes."""
        verdict = parse_review_output(
            _msg(
                "The implementation looks solid overall.\n"
                "No security issues detected.\n"
                "CODE_REVIEW_PASSED"
            )
        )
        assert isinstance(verdict, ReviewPassed)

    def test_verdict_ignores_tool_result_messages(self):
        """Approval text inside a tool-result message must not trigger passed."""

        @dataclass
        class ToolResult:
            result: str = "CODE_REVIEW_PASSED"

        verdict = parse_review_output([ToolResult()])
        assert isinstance(verdict, ReviewIssues)
