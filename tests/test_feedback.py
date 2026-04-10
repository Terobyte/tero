"""Tests for feedback module."""

from dataclasses import dataclass
from src.feedback import parse_coach_output, Approved, Feedback, NoVerdict


@dataclass
class MockTextBlock:
    """Mock TextBlock."""
    text: str


@dataclass
class MockToolUseBlock:
    """Mock tool block without text."""
    name: str = "Bash"
    input: dict | None = None


@dataclass
class MockAssistantMessage:
    """Mock AssistantMessage."""
    content: list


@dataclass
class MockResultMessage:
    """Mock ResultMessage."""
    result: str = "done"


class TestParseCoachOutput:
    """Tests for parse_coach_output."""

    def test_approved_marker(self):
        """Test detecting APPROVED marker."""
        msg = MockAssistantMessage(content=[
            MockTextBlock("Great work! IMPLEMENTATION_APPROVED")
        ])

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, Approved)

    def test_feedback_on_issues(self):
        """Test extracting feedback text."""
        msg = MockAssistantMessage(content=[
            MockTextBlock("IMPLEMENTATION_DECLINED\n1. Missing tests\n2. No error handling")
        ])

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, Feedback)
        assert "Missing tests" in verdict.text
        assert "No error handling" in verdict.text

    def test_declined_marker_is_ignored_in_feedback_body(self):
        """Decline marker should not leak into actionable feedback."""
        msg = MockAssistantMessage(content=[
            MockTextBlock("IMPLEMENTATION_DECLINED: 1. Missing tests")
        ])

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, Feedback)
        assert verdict.text == "1. Missing tests"

    def test_declined_without_issues_becomes_structured_fallback(self):
        """A bare decline is invalid because player needs actionable feedback."""
        msg = MockAssistantMessage(content=[
            MockTextBlock("IMPLEMENTATION_DECLINED")
        ])

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, Feedback)
        assert "without concrete numbered issues" in verdict.text
        assert "numbered action items" in verdict.text

    def test_keeps_only_numbered_issues_from_mixed_output(self):
        """Non-actionable chatter should not leak into retry feedback."""
        msg = MockAssistantMessage(content=[
            MockTextBlock(
                "I'll review the changes now.\n"
                "Here is my verdict:\n"
                "1. Missing tests\n"
                "Some extra commentary\n"
                "2. No error handling"
            )
        ])

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, Feedback)
        assert "1. Missing tests Some extra commentary" in verdict.text
        assert "2. No error handling" in verdict.text
        assert "I'll review the changes now." not in verdict.text

    def test_unstructured_output_becomes_structured_fallback(self):
        """Free-form reviewer chatter should be replaced with deterministic feedback."""
        msg = MockAssistantMessage(content=[
            MockTextBlock("I need to inspect more files before I can decide.")
        ])

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, Feedback)
        assert "1. Reviewer did not return a valid structured verdict." in verdict.text
        assert "2. Do not ask clarifying questions" in verdict.text
        assert "I need to inspect more files" not in verdict.text

    def test_empty_coach_output(self):
        """Test handling empty output."""
        msg = MockAssistantMessage(content=[])

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, NoVerdict)

    def test_no_assistant_message(self):
        """Test when no assistant message present."""
        msg = MockResultMessage()

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, NoVerdict)

    def test_ignores_approval_in_tool_result(self):
        """Test that approval in tool result is ignored."""
        # This would be a ToolResultMessage containing "IMPLEMENTATION_APPROVED"
        # from something like grep output
        @dataclass
        class MockToolResult:
            tool_use_id: str = "123"
            content: str = "IMPLEMENTATION_APPROVED found in file"

        assistant = MockAssistantMessage(content=[
            MockTextBlock("Still has issues to fix.")
        ])

        verdict = parse_coach_output([assistant, MockToolResult()])

        # Should NOT be approved because approval is in tool result, not assistant text
        assert isinstance(verdict, Feedback)
        assert "Reviewer did not return a valid structured verdict" in verdict.text

    def test_approved_in_any_assistant_message_wins(self):
        """APPROVED in any assistant message means approved.

        Codex splits one response into multiple messages, so the APPROVED
        marker may be in an earlier message while details follow in later ones.
        """
        msg1 = MockAssistantMessage(content=[
            MockTextBlock("IMPLEMENTATION_APPROVED")
        ])
        msg2 = MockAssistantMessage(content=[
            MockTextBlock("1. No blocking findings.")
        ])

        verdict = parse_coach_output([msg1, msg2])

        assert isinstance(verdict, Approved)

    def test_uses_latest_text_message_when_final_assistant_is_tool_only(self):
        """A trailing tool-only assistant message should not erase coach feedback."""
        msg1 = MockAssistantMessage(content=[
            MockTextBlock("IMPLEMENTATION_DECLINED\n1. Missing tests")
        ])
        msg2 = MockAssistantMessage(content=[MockToolUseBlock(name="Bash", input={})])

        verdict = parse_coach_output([msg1, msg2])

        assert isinstance(verdict, Feedback)
        assert verdict.text == "1. Missing tests"

    def test_uses_latest_text_message_for_approval_when_final_assistant_is_tool_only(self):
        """A trailing tool-only assistant message should not erase approval."""
        msg1 = MockAssistantMessage(content=[
            MockTextBlock("IMPLEMENTATION_APPROVED")
        ])
        msg2 = MockAssistantMessage(content=[MockToolUseBlock(name="Read", input={})])

        verdict = parse_coach_output([msg1, msg2])

        assert isinstance(verdict, Approved)

    def test_assistant_without_role_is_still_detected(self):
        """Test SDK-shaped assistant messages without role field."""
        msg = MockAssistantMessage(content=[
            MockTextBlock("Review feedback without role metadata")
        ])

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, Feedback)
        assert "Reviewer did not return a valid structured verdict" in verdict.text

    def test_tool_like_message_is_not_misclassified_as_assistant(self):
        """Messages with content but role=tool must not become assistant verdicts."""

        @dataclass
        class MockToolMessage:
            role: str
            content: list

        msg = MockToolMessage(
            role="tool",
            content=[MockTextBlock("IMPLEMENTATION_APPROVED")],
        )

        verdict = parse_coach_output([msg])

        assert isinstance(verdict, NoVerdict)
