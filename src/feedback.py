"""Parse coach output for approval or feedback."""

import re
from dataclasses import dataclass


@dataclass
class Approved:
    """Coach approved the implementation."""

    pass


@dataclass
class Feedback:
    """Coach found issues to fix."""

    text: str


@dataclass
class NoVerdict:
    """Coach completed without a verdict (no text / didn't respond).

    This is NOT feedback for Player. This is a signal to retry coach.
    """

    pass


@dataclass
class ReviewPassed:
    """Code review passed with no critical issues."""

    pass


@dataclass
class ReviewIssues:
    """Code review found issues."""

    text: str


Verdict = Approved | Feedback | NoVerdict
ReviewVerdict = ReviewPassed | ReviewIssues


_NUMBERED_ISSUE_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
_DECLINED_MARKER_RE = re.compile(r"IMPLEMENTATION_DECLINED\b[:\-\s]*", re.IGNORECASE)
_APPROVED_MARKER_RE = re.compile(r"IMPLEMENTATION_APPROVED\b", re.IGNORECASE)
_NO_OUTPUT_FEEDBACK_START = "1. Coach produced no output"
_INVALID_VERDICT_FEEDBACK_START = (
    "1. Reviewer did not return a valid structured verdict."
)
_DECLINED_WITHOUT_ISSUES_FEEDBACK_START = (
    "1. Reviewer returned IMPLEMENTATION_DECLINED without concrete numbered issues."
)


def parse_coach_output(messages: list) -> Verdict:
    """Extract verdict from coach messages.

    Looks at the latest assistant message that contains text.
    Ignores IMPLEMENTATION_APPROVED in ToolResultMessage.

    Args:
        messages: List of SDK messages from coach query()

    Returns:
        Approved if IMPLEMENTATION_APPROVED found in final assistant text
        Feedback with the full text if there's text but no approval
        NoVerdict if there's no assistant message or empty text
    """
    text = _latest_assistant_text(messages)
    if not text:
        return NoVerdict()

    # Check for approval marker
    if _APPROVED_MARKER_RE.search(text):
        return Approved()

    declined = bool(_DECLINED_MARKER_RE.search(text))
    normalized_text = _DECLINED_MARKER_RE.sub("", text).strip()

    issues = _extract_numbered_issues(normalized_text)
    if issues:
        return Feedback("\n".join(issues))

    if declined:
        return Feedback(
            "1. Reviewer returned IMPLEMENTATION_DECLINED without concrete numbered issues.\n"
            "2. Re-review the current implementation and list the exact problems as numbered action items.\n"
            "3. Do not block on prompt discussion; provide actionable fixes only."
        )

    return Feedback(_fallback_structured_feedback())


def _is_assistant_message(msg) -> bool:
    """Check if message is an AssistantMessage."""
    if type(msg).__name__.endswith("AssistantMessage"):
        return True
    if hasattr(msg, "role"):
        return msg.role == "assistant"
    return False


def _latest_assistant_text(messages: list) -> str:
    """Return text from the latest assistant message that actually contains text."""
    for msg in reversed(messages):
        if not _is_assistant_message(msg):
            continue
        text = _extract_text_from_message(msg).strip()
        if text:
            return text
    return ""


def _extract_text_from_message(msg) -> str:
    """Extract all text from TextBlocks in a message."""
    texts = []

    # Try content attribute (list of blocks)
    content = getattr(msg, "content", None)
    if content is None:
        # Try text attribute directly
        text = getattr(msg, "text", None)
        if text:
            return text
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        for block in content:
            block_type = type(block).__name__
            # TextBlock
            if block_type == "TextBlock":
                if hasattr(block, "text"):
                    texts.append(block.text)
            elif hasattr(block, "type") and block.type == "text":
                if hasattr(block, "text"):
                    texts.append(block.text)
            # Fallback: any block with text attribute
            elif hasattr(block, "text"):
                texts.append(block.text)

    return "\n".join(texts)


def _extract_numbered_issues(text: str) -> list[str]:
    """Extract only numbered issues, discarding reviewer chatter."""
    issues: list[str] = []
    current_issue: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = _NUMBERED_ISSUE_RE.match(line)
        if match:
            if current_issue:
                issues.append(current_issue)
            current_issue = f"{match.group(1)}. {match.group(2).strip()}"
            continue

        if current_issue:
            current_issue += f" {line}"

    if current_issue:
        issues.append(current_issue)

    return issues


def _fallback_structured_feedback() -> str:
    """Deterministic fallback when the reviewer ignores the required format."""
    return (
        "1. Reviewer did not return a valid structured verdict.\n"
        "2. Do not ask clarifying questions and do not comment on prompt quality.\n"
        "3. Re-read the planned task, implement the missing work, run verification, and return the required completion markers."
    )


def is_invalid_feedback(feedback: Feedback) -> bool:
    """Return True when feedback indicates the reviewer failed to produce a valid review."""
    return feedback.text.startswith(
        (
            _NO_OUTPUT_FEEDBACK_START,
            _INVALID_VERDICT_FEEDBACK_START,
            _DECLINED_WITHOUT_ISSUES_FEEDBACK_START,
        )
    )


_FREE_TEXT_CONCERN_RE = re.compile(
    r"\b(?:XSS|CSRF|SQL\s+injection|injection\s+risk|buffer\s+overflow"
    r"|vulnerabilit(?:y|ies)|exploit|security\s+(?:issue|risk|flaw)"
    r"|memory\s+leak|race\s+condition|deadlock|null\s+(?:pointer|dereference)"
    r"|uninitialized\s+(?:var|variable)|use\s+after\s+free"
    r"|command\s+injection|path\s+traversal|open\s+redirect"
    r"|hardcoded\s+(?:secret|password|credential|api\s+key)"
    r"|information\s+(?:leak|disclosure)|denial\s+of\s+service"
    r"|(?:potential|possible)\s+(?:bug|issue|problem|crash|error))\b",
    re.IGNORECASE,
)

# Words that negate a security keyword when they appear immediately before it.
_NEGATIVE_PREFIX_RE = re.compile(r"\b(?:no|not|without|zero|any)\b", re.IGNORECASE)

_EXPLICIT_CODE_REVIEW_PASSED_RE = re.compile(r"CODE_REVIEW_PASSED\b", re.IGNORECASE)


def _has_real_security_concern(text: str) -> bool:
    """Return True only when _FREE_TEXT_CONCERN_RE matches outside a denial context.

    Phrases like "No XSS vulnerabilities detected" or "no potential issue" contain
    security keywords but express the *absence* of a problem.  We suppress those
    false positives by checking whether a negative word ("no", "not", "without",
    "zero", "any") appears within 40 characters before each match.
    """
    for match in _FREE_TEXT_CONCERN_RE.finditer(text):
        start = max(0, match.start() - 40)
        prefix = text[start : match.start()]
        if not _NEGATIVE_PREFIX_RE.search(prefix):
            return True
    return False


_CODE_REVIEW_PASSED_RE = re.compile(
    r"CODE_REVIEW_PASSED\b"
    r"|no\s+critical\s+issues?"
    r"|no\s+issues?\s+found"
    r"|looks?\s+good"
    r"|\bLGTM\b"
    r"|no\s+bugs?\s+found"
    r"|code\s+is\s+correct"
    r"|implementation\s+is\s+correct"
    r"|all\s+components\s+properly\s+implemented(?:\s+and\s+tested)?"
    r"|all\s+components\s+(?:are\s+)?properly\s+implemented(?:\s+and\s+tested)?"
    r"|all\s+components?\s+(?:are\s+)?implemented\s+correctly"
    r"|implementation\s+looks?\s+correct"
    r"|verdict:\s*all\s+components\s+properly\s+implemented(?:\s+and\s+tested)?"
    r"|everything\s+looks?\s+(good|correct|fine)",
    re.IGNORECASE,
)


def parse_review_output(messages: list) -> ReviewVerdict:
    """Parse code reviewer output for verdict.

    Args:
        messages: List of SDK messages from code reviewer

    Returns:
        ReviewPassed if CODE_REVIEW_PASSED found
        ReviewIssues with numbered issues otherwise
    """
    text = _latest_assistant_text(messages)
    if not text:
        return ReviewIssues("1. Code reviewer produced no output.")

    # The explicit sentinel wins — unless the reviewer also mentions a real
    # security concern in the same message (e.g. "CODE_REVIEW_PASSED. However,
    # XSS vulnerability at line 42.").  Security concerns take priority.
    if _EXPLICIT_CODE_REVIEW_PASSED_RE.search(text):
        if _has_real_security_concern(text):
            return ReviewIssues(text)
        return ReviewPassed()

    issues = _extract_numbered_issues(text)
    if issues:
        return ReviewIssues("\n".join(issues))

    if _CODE_REVIEW_PASSED_RE.search(text):
        return ReviewPassed()

    # No explicit pass marker and no numbered issues. Check if the reviewer
    # described concrete concerns in free text (e.g. "potential XSS in render()").
    # Use the negative-context-aware helper to avoid false positives.
    if _has_real_security_concern(text):
        return ReviewIssues(text)

    # Ambiguous or neutral text with no clear problems — treat as passed.
    # If the reviewer had concerns, they should have used numbered issues.
    return ReviewPassed()
