"""
Tests that CONFIRM real bugs.

Each test asserts correct/expected behavior.
  FAIL  → bug still exists
  PASS  → bug has been fixed

Run: pytest tests/test_new_bugs.py -v
"""

import pytest


# ---------------------------------------------------------------------------
# Bug 1 & 2 — get_context_window() dead pattern entries (config.py)
#
# _MODEL_CONTEXT_WINDOWS uses first-match substring lookup.
# "mimo"       (131_072) appears BEFORE "xiaomi/mimo-v2-pro:free" (1_048_576)
# "minimax-m2" (1_000_000) appears BEFORE "minimax/minimax-m2.5:free" (262_144)
#
# Because "mimo" IS a substring of "xiaomi/mimo-v2-pro:free", and
# "minimax-m2" IS a substring of "minimax/minimax-m2.5:free", the specific
# entries at the end are unreachable dead code and the WRONG sizes are returned.
# ---------------------------------------------------------------------------

class TestBug_ContextWindowDeadPatterns:
    """get_context_window returns wrong sizes for models whose patterns are shadowed."""

    def test_minimax_m25_free_returns_correct_size(self):
        """
        BUG: 'minimax-m2' (1_000_000) at index 18 matches before
             'minimax/minimax-m2.5:free' (262_144) at index 20.
        EXPECTED: 262_144
        ACTUAL:   1_000_000
        """
        from src.config import get_context_window

        result = get_context_window("minimax/minimax-m2.5:free")
        assert result == 262_144, (
            f"get_context_window('minimax/minimax-m2.5:free') returned {result}, "
            f"expected 262_144. The 'minimax-m2' pattern (1_000_000) is shadowing "
            f"the more-specific 'minimax/minimax-m2.5:free' entry (262_144)."
        )

    def test_xiaomi_mimo_v2_pro_returns_correct_size(self):
        """
        BUG: 'mimo' (131_072) at index 17 matches before
             'xiaomi/mimo-v2-pro:free' (1_048_576) at index 21.
        EXPECTED: 1_048_576
        ACTUAL:   131_072
        """
        from src.config import get_context_window

        result = get_context_window("xiaomi/mimo-v2-pro:free")
        assert result == 1_048_576, (
            f"get_context_window('xiaomi/mimo-v2-pro:free') returned {result}, "
            f"expected 1_048_576. The 'mimo' pattern (131_072) is shadowing "
            f"the more-specific 'xiaomi/mimo-v2-pro:free' entry (1_048_576)."
        )

    def test_specific_entries_are_reachable(self):
        """Both specific entries must be reachable (not dead code)."""
        from src.config import get_context_window, _MODEL_CONTEXT_WINDOWS

        patterns = [p for p, _ in _MODEL_CONTEXT_WINDOWS]

        # For each specific pattern, verify no earlier general pattern shadows it
        specific_cases = [
            ("minimax/minimax-m2.5:free", 262_144),
            ("xiaomi/mimo-v2-pro:free", 1_048_576),
        ]
        for model, expected_size in specific_cases:
            result = get_context_window(model)
            assert result == expected_size, (
                f"Specific pattern '{model}' is shadowed: got {result}, expected {expected_size}. "
                f"Check ordering in _MODEL_CONTEXT_WINDOWS."
            )


# ---------------------------------------------------------------------------
# Bug 3 — parse_review_output() false positive on negative security phrases
#
# _FREE_TEXT_CONCERN_RE matches security keywords regardless of context.
# A reviewer writing "No XSS vulnerabilities detected" — a clean bill of health —
# is incorrectly classified as ReviewIssues instead of ReviewPassed.
# ---------------------------------------------------------------------------

class TestBug_ReviewOutputFalsePositive:
    """parse_review_output() fires ReviewIssues when reviewer says there are NO issues."""

    def _make_msg(self, text: str):
        """Build a minimal assistant message stub."""
        class _Msg:
            role = "assistant"
            content = text
        return _Msg()

    def test_no_xss_phrase_is_not_an_issue(self):
        """
        BUG: 'No XSS vulnerabilities detected' contains 'XSS' which triggers
        _FREE_TEXT_CONCERN_RE, producing ReviewIssues even though the reviewer
        is explicitly saying there are NO issues.
        EXPECTED: ReviewPassed
        ACTUAL:   ReviewIssues
        """
        from src.feedback import parse_review_output, ReviewPassed

        text = "The code is clean. No XSS vulnerabilities detected."
        result = parse_review_output([self._make_msg(text)])
        assert isinstance(result, ReviewPassed), (
            f"Expected ReviewPassed for '{text}', got {type(result).__name__}. "
            f"_FREE_TEXT_CONCERN_RE matches 'XSS' and 'vulnerabilities' without "
            f"checking whether they appear in a negative/denial context."
        )

    def test_no_csrf_phrase_is_not_an_issue(self):
        """
        BUG: 'No CSRF issues found' triggers _FREE_TEXT_CONCERN_RE on 'CSRF'.
        EXPECTED: ReviewPassed
        ACTUAL:   ReviewIssues
        """
        from src.feedback import parse_review_output, ReviewPassed

        text = "Reviewed auth module. No CSRF issues found. All good."
        result = parse_review_output([self._make_msg(text)])
        assert isinstance(result, ReviewPassed), (
            f"Expected ReviewPassed for '{text}', got {type(result).__name__}. "
            f"'CSRF' keyword match fires even inside denial phrases."
        )

    def test_no_vulnerabilities_phrase_is_not_an_issue(self):
        """
        BUG: Reviewer says 'no vulnerabilities' but 'vulnerabilities' still
        matches _FREE_TEXT_CONCERN_RE, producing ReviewIssues.
        EXPECTED: ReviewPassed
        ACTUAL:   ReviewIssues
        """
        from src.feedback import parse_review_output, ReviewPassed

        text = "Code review complete. No vulnerabilities detected in the codebase."
        result = parse_review_output([self._make_msg(text)])
        assert isinstance(result, ReviewPassed), (
            f"Expected ReviewPassed for '{text}', got {type(result).__name__}. "
            f"The regex does not exclude negative contexts like 'No ... detected'."
        )

    def test_no_sql_injection_phrase_is_not_an_issue(self):
        """
        BUG: 'No SQL injection risk' triggers the regex on 'SQL injection'.
        EXPECTED: ReviewPassed
        ACTUAL:   ReviewIssues
        """
        from src.feedback import parse_review_output, ReviewPassed

        text = "Inputs are properly parameterized. No SQL injection risk present."
        result = parse_review_output([self._make_msg(text)])
        assert isinstance(result, ReviewPassed), (
            f"Expected ReviewPassed for '{text}', got {type(result).__name__}. "
            f"'SQL injection' matches _FREE_TEXT_CONCERN_RE regardless of 'No ... risk'."
        )

    def test_potential_issue_phrase_is_not_an_issue_when_denied(self):
        """
        BUG: 'no potential issue' triggers _FREE_TEXT_CONCERN_RE on 'potential issue'.
        EXPECTED: ReviewPassed (the reviewer is saying there is no problem)
        ACTUAL:   ReviewIssues
        Note: 'potential bugs' (plural) is NOT affected due to \\b boundary,
              but 'potential issue', 'potential problem', 'potential bug' are.
        """
        from src.feedback import parse_review_output, ReviewPassed

        text = "I reviewed this carefully. There is no potential issue with the logic."
        result = parse_review_output([self._make_msg(text)])
        assert isinstance(result, ReviewPassed), (
            f"Expected ReviewPassed for '{text}', got {type(result).__name__}. "
            f"'potential issue' in _FREE_TEXT_CONCERN_RE fires even when the "
            f"reviewer is explicitly denying there is a problem."
        )
