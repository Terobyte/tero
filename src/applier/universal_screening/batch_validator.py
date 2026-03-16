# src/applier/universal_screening/batch_validator.py
"""
Batch validator for job application screening questions.

Validates that screening answers meet quality thresholds before submission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ValidationSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationResult:
    """Result of validating a single screening answer."""
    question_id: str
    passed: bool
    severity: ValidationSeverity
    message: str
    details: dict = field(default_factory=dict)


class BatchStepValidator:
    """
    Validates batches of screening question answers.

    Ensures answers are:
    - Complete (no empty required fields)
    - Within character limits
    - Properly formatted
    - Not duplicates
    """

    def __init__(
        self,
        max_answer_length: int = 5000,
        min_answer_length: int = 1,
        check_duplicates: bool = True,
    ):
        self.max_answer_length = max_answer_length
        self.min_answer_length = min_answer_length
        self.check_duplicates = check_duplicates
        self._seen_answers: dict[str, str] = {}

    def validate_answer(
        self,
        question_id: str,
        answer: str,
        required: bool = True,
    ) -> ValidationResult:
        """Validate a single screening answer."""
        # Check empty
        if not answer or not answer.strip():
            if required:
                return ValidationResult(
                    question_id=question_id,
                    passed=False,
                    severity=ValidationSeverity.ERROR,
                    message="Required answer is empty",
                    details={"answer": answer},
                )
            return ValidationResult(
                question_id=question_id,
                passed=True,
                severity=ValidationSeverity.INFO,
                message="Optional answer is empty",
                details={"answer": answer},
            )

        # Check length
        if len(answer) < self.min_answer_length:
            return ValidationResult(
                question_id=question_id,
                passed=False,
                severity=ValidationSeverity.ERROR,
                message=f"Answer too short (min {self.min_answer_length})",
                details={"length": len(answer)},
            )

        if len(answer) > self.max_answer_length:
            return ValidationResult(
                question_id=question_id,
                passed=False,
                severity=ValidationSeverity.ERROR,
                message=f"Answer too long (max {self.max_answer_length})",
                details={"length": len(answer)},
            )

        # Check duplicate
        if self.check_duplicates:
            answer_hash = hash(answer.strip().lower())
            if question_id not in self._seen_answers:
                self._seen_answers[question_id] = str(answer_hash)
            elif self._seen_answers[question_id] != str(answer_hash):
                return ValidationResult(
                    question_id=question_id,
                    passed=True,
                    severity=ValidationSeverity.WARNING,
                    message="Answer differs from previous submission",
                    details={"previous_hash": self._seen_answers[question_id]},
                )

        return ValidationResult(
            question_id=question_id,
            passed=True,
            severity=ValidationSeverity.INFO,
            message="Answer validated successfully",
        )

    def validate_batch(
        self,
        answers: dict[str, str],
        required_questions: Optional[set[str]] = None,
    ) -> list[ValidationResult]:
        """Validate a batch of screening answers."""
        results = []
        required_questions = required_questions or set()

        for question_id, answer in answers.items():
            is_required = question_id in required_questions
            result = self.validate_answer(question_id, answer, required=is_required)
            results.append(result)

        # Check for missing required questions
        missing = required_questions - set(answers.keys())
        for question_id in missing:
            results.append(ValidationResult(
                question_id=question_id,
                passed=False,
                severity=ValidationSeverity.ERROR,
                message="Required question not answered",
            ))

        return results

    def clear_cache(self) -> None:
        """Clear the duplicate detection cache."""
        self._seen_answers.clear()


def validate_batch(
    answers: dict[str, str],
    required: Optional[set[str]] = None,
) -> list[ValidationResult]:
    """Convenience function for one-off batch validation."""
    validator = BatchStepValidator()
    return validator.validate_batch(answers, required)
