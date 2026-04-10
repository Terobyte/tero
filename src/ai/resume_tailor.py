# src/ai/resume_tailor.py
"""
Resume tailoring module using AI.

Analyzes job descriptions and tailors resume content to match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger


class TailorStrategy(Enum):
    KEYWORD_MATCH = "keyword_match"
    EXPERIENCE_HIGHLIGHT = "experience_highlight"
    SKILLS_ALIGNMENT = "skills_alignment"


@dataclass
class TailoringResult:
    """Result of resume tailoring."""
    success: bool
    tailored_resume: str
    original_resume: str
    job_description: str
    matched_keywords: list[str] = field(default_factory=list)
    highlighted_experiences: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    error_message: Optional[str] = None


class ResumeTailor:
    """
    Tailors resume content to match job descriptions.

    Features:
    - Keyword extraction and matching
    - Experience section optimization
    - Skills alignment
    - Summary customization
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-3-5-sonnet",
        max_keywords: int = 15,
    ):
        self.api_key = api_key
        self.model = model
        self.max_keywords = max_keywords

    def extract_keywords(self, text: str) -> list[str]:
        """Extract key skills and requirements from job description."""
        # Simple keyword extraction (would use AI in production)
        keywords = []
        text_lower = text.lower()

        # Common tech keywords
        tech_keywords = [
            "python", "javascript", "typescript", "rust", "go", "java",
            "react", "vue", "angular", "node", "django", "flask", "fastapi",
            "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
            "docker", "kubernetes", "aws", "gcp", "azure",
            "machine learning", "ai", "llm", "nlp", "data science",
            "git", "ci/cd", "agile", "scrum", "microservices",
        ]

        for kw in tech_keywords:
            if kw in text_lower:
                keywords.append(kw)
            if len(keywords) >= self.max_keywords:
                break

        return keywords

    def highlight_experiences(
        self,
        resume: str,
        keywords: list[str],
    ) -> tuple[str, list[str]]:
        """Highlight relevant experiences in resume."""
        highlighted = []
        lines = resume.split("\n")
        result_lines = []

        for line in lines:
            line_lower = line.lower()
            matched = [kw for kw in keywords if kw in line_lower]
            if matched:
                highlighted.append(line.strip())
                result_lines.append(f"[Relevant to role] {line}")
            else:
                result_lines.append(line)

        return "\n".join(result_lines), highlighted

    @staticmethod
    def _append_section(base_text: str, heading: str, items: list[str]) -> str:
        """Append a simple bullet section when there is content to add."""
        if not items:
            return base_text
        section = f"{heading}\n" + "\n".join(f"- {item}" for item in items)
        if not base_text.strip():
            return section
        return f"{base_text.rstrip()}\n\n{section}"

    def _apply_strategy(
        self,
        resume: str,
        highlighted_resume: str,
        keywords: list[str],
        highlighted: list[str],
        strategy: TailorStrategy,
    ) -> str:
        """Produce strategy-specific tailored output."""
        if strategy == TailorStrategy.EXPERIENCE_HIGHLIGHT:
            return self._append_section(
                highlighted_resume,
                "Relevant Experience Highlights",
                highlighted[:5],
            )

        if strategy == TailorStrategy.SKILLS_ALIGNMENT:
            return self._append_section(
                resume,
                "Skills Alignment",
                keywords[: self.max_keywords],
            )

        return self._append_section(
            highlighted_resume,
            "Target Keywords",
            keywords[: self.max_keywords],
        )

    def tailor(
        self,
        resume: str,
        job_description: str,
        strategy: TailorStrategy = TailorStrategy.KEYWORD_MATCH,
    ) -> TailoringResult:
        """
        Tailor a resume to match a job description.

        Args:
            resume: Original resume text
            job_description: Job posting text
            strategy: Tailoring strategy to use

        Returns:
            TailoringResult with tailored resume and metadata
        """
        if not resume.strip():
            return TailoringResult(
                success=False,
                tailored_resume="",
                original_resume=resume,
                job_description=job_description,
                error_message="Resume cannot be empty",
            )

        if not job_description.strip():
            return TailoringResult(
                success=False,
                tailored_resume=resume,
                original_resume=resume,
                job_description=job_description,
                error_message="Job description cannot be empty",
            )

        try:
            # Extract keywords from job description
            keywords = self.extract_keywords(job_description)

            # Highlight relevant experiences
            highlighted_resume, highlighted = self.highlight_experiences(resume, keywords)
            tailored = self._apply_strategy(
                resume,
                highlighted_resume,
                keywords,
                highlighted,
                strategy,
            )

            # Generate suggestions
            suggestions = []
            if len(keywords) < 5:
                suggestions.append("Job description has few technical keywords")
            if len(highlighted) < 3:
                suggestions.append("Consider adding more relevant experience")
            if strategy == TailorStrategy.SKILLS_ALIGNMENT and keywords:
                suggestions.append("Mirror these skill keywords in your summary and skills sections")
            elif strategy == TailorStrategy.EXPERIENCE_HIGHLIGHT and highlighted:
                suggestions.append("Move the highlighted experience bullets higher on the page")
            elif strategy == TailorStrategy.KEYWORD_MATCH and keywords:
                suggestions.append("Use the target keywords naturally throughout the resume")

            return TailoringResult(
                success=True,
                tailored_resume=tailored,
                original_resume=resume,
                job_description=job_description,
                matched_keywords=keywords,
                highlighted_experiences=highlighted,
                suggestions=suggestions,
            )

        except Exception as e:
            logger.error(f"Resume tailoring failed: {e}")
            return TailoringResult(
                success=False,
                tailored_resume=resume,
                original_resume=resume,
                job_description=job_description,
                error_message=str(e),
            )

    def tailor_file(
        self,
        resume_path: Path,
        job_description: str,
        output_path: Optional[Path] = None,
        strategy: TailorStrategy = TailorStrategy.KEYWORD_MATCH,
    ) -> TailoringResult:
        """Tailor a resume from a file."""
        if not resume_path.exists():
            return TailoringResult(
                success=False,
                tailored_resume="",
                original_resume="",
                job_description=job_description,
                error_message=f"Resume file not found: {resume_path}",
            )

        resume = resume_path.read_text()
        result = self.tailor(resume, job_description, strategy=strategy)

        if result.success and output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(result.tailored_resume)

        return result
