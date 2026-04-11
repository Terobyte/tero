# src/applier/universal_screening/batch_validator.py
"""
Batch validator for job application screening questions.

Validates that screening answers meet quality thresholds before submission.
"""
from __future__ import annotations

import hashlib
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

    @staticmethod
    def _hash_answer(answer: str) -> str:
        """Build a deterministic fingerprint for normalized answer text."""
        return hashlib.sha256(answer.strip().lower().encode()).hexdigest()

    def _validate_duplicate(
        self,
        question_id: str,
        answer_hash: str,
        seen_answers: dict[str, str],
    ) -> ValidationResult | None:
        """Return a warning when the same answer text was already used."""
        if not self.check_duplicates:
            return None

        first_question_id = seen_answers.get(answer_hash)
        if first_question_id is None:
            seen_answers[answer_hash] = question_id
            return None

        return ValidationResult(
            question_id=question_id,
            passed=False,
            severity=ValidationSeverity.WARNING,
            message="Duplicate answer detected",
            details={"duplicate_of": first_question_id, "answer_hash": answer_hash},
        )

    def validate_answer(
        self,
        question_id: str,
        answer: str,
        required: bool = True,
    ) -> ValidationResult:
        """Validate a single screening answer."""
        return self._validate_answer(
            question_id,
            answer,
            required=required,
            seen_answers=self._seen_answers,
        )

    def _validate_answer(
        self,
        question_id: str,
        answer: str,
        *,
        required: bool,
        seen_answers: dict[str, str],
    ) -> ValidationResult:
        """Validate one answer using the provided duplicate-tracking state."""
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

        duplicate_result = self._validate_duplicate(
            question_id,
            self._hash_answer(answer),
            seen_answers,
        )
        if duplicate_result is not None:
            return duplicate_result

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
        batch_seen: dict[str, str] = {}

        for question_id, answer in answers.items():
            is_required = question_id in required_questions
            result = self._validate_answer(
                question_id,
                answer,
                required=is_required,
                seen_answers=batch_seen,
            )
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
