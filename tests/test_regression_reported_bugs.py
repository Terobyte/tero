"""Regression tests for the reported confirmed bugs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import g3

from scripts.run_slug_collection import extract_slug_from_url
from src.ai.resume_tailor import ResumeTailor, TailorStrategy
from src.applier.universal_screening.batch_validator import (
    BatchStepValidator,
    ValidationSeverity,
)
from src.batch_executor import has_required_completion_report, parse_completed_steps
from src.bug_detector import BugDetector
from src.config import _load_yaml
from src.debugger import Debugger
from src.debugger_bugs import BugEntry
from src.plan_tracker import PlanItem, Phase


def _phase(name: str = "Create") -> Phase:
    return Phase(
        name=name,
        type="update",
        steps=[PlanItem(text="step a"), PlanItem(text="step b")],
    )


def test_slug_extraction_uses_subdomain_for_cdx_sources():
    assert (
        extract_slug_from_url(
            "https://acme.jobs.lever.co/software-engineer",
            "jobs.lever.co",
        )
        == "acme"
    )
    assert (
        extract_slug_from_url(
            "https://orbital.boards.greenhouse.io/jobs/123",
            "boards.greenhouse.io",
        )
        == "orbital"
    )


def test_phase_completion_requires_matching_phase_name_and_report_sections():
    phase = _phase("Create")
    wrong_phase_text = (
        "PHASE_COMPLETE: Different Phase\n"
        "What changed:\n"
        "- updated files\n"
        "Evidence:\n"
        "- saw the changes\n"
        "Verification:\n"
        "- pytest"
    )

    assert parse_completed_steps(SimpleNamespace(text=wrong_phase_text), phase) == []
    assert has_required_completion_report(wrong_phase_text, phase) is False

    valid_text = (
        "PHASE_COMPLETE: Create. All work is complete.\n"
        "What changed:\n"
        "- updated files\n"
        "Evidence:\n"
        "- checked the workspace\n"
        "Verification:\n"
        "- pytest"
    )
    assert parse_completed_steps(SimpleNamespace(text=valid_text), phase) == [
        "step a",
        "step b",
    ]
    assert has_required_completion_report(valid_text, phase) is True


def test_batch_validator_detects_duplicates_within_batch_only():
    validator = BatchStepValidator(check_duplicates=True)

    first_batch = validator.validate_batch({"q1": "Same answer", "q2": "Same answer"})
    assert first_batch[0].passed is True
    assert first_batch[1].severity == ValidationSeverity.WARNING
    assert first_batch[1].details["duplicate_of"] == "q1"

    second_batch = validator.validate_batch({"q3": "Same answer"})
    assert second_batch[0].passed is True
    assert second_batch[0].severity == ValidationSeverity.INFO


def test_bug_detector_skips_missing_lint_and_test_modules(tmp_path):
    python_file = tmp_path / "example.py"
    python_file.write_text("print('ok')\n")

    def fake_run(cmd, **kwargs):
        module = cmd[2] if len(cmd) > 2 else ""
        if module == "flake8":
            return subprocess.CompletedProcess(
                cmd,
                1,
                "",
                "/usr/bin/python3: No module named flake8\n",
            )
        if module == "pyflakes":
            return subprocess.CompletedProcess(
                cmd,
                1,
                f"{python_file}:1: undefined name 'x'\n{python_file}:1: unused import 'y'\n",
                "",
            )
        if module == "pytest":
            return subprocess.CompletedProcess(
                cmd,
                1,
                "",
                "/usr/bin/python3: No module named pytest\n",
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    with patch("src.bug_detector.subprocess.run", side_effect=fake_run):
        assert BugDetector._check_lint(str(tmp_path)) == 2
        assert BugDetector._check_tests(str(tmp_path)) == 0


def test_resume_tailor_applies_distinct_strategies(tmp_path):
    resume = "Python Engineer\nBuilt Python APIs with Docker\nLed backend delivery"
    job_description = "Looking for a Python engineer with Docker and AWS experience."
    tailor = ResumeTailor()

    keyword_result = tailor.tailor(
        resume,
        job_description,
        strategy=TailorStrategy.KEYWORD_MATCH,
    )
    experience_result = tailor.tailor(
        resume,
        job_description,
        strategy=TailorStrategy.EXPERIENCE_HIGHLIGHT,
    )
    skills_result = tailor.tailor(
        resume,
        job_description,
        strategy=TailorStrategy.SKILLS_ALIGNMENT,
    )

    assert "Target Keywords" in keyword_result.tailored_resume
    assert "Relevant Experience Highlights" in experience_result.tailored_resume
    assert "Skills Alignment" in skills_result.tailored_resume
    assert len(
        {
            keyword_result.tailored_resume,
            experience_result.tailored_resume,
            skills_result.tailored_resume,
        }
    ) == 3

    resume_path = tmp_path / "resume.txt"
    output_path = tmp_path / "tailored.txt"
    resume_path.write_text(resume)

    file_result = tailor.tailor_file(
        resume_path,
        job_description,
        output_path=output_path,
        strategy=TailorStrategy.SKILLS_ALIGNMENT,
    )

    assert file_result.success is True
    assert "Skills Alignment" in output_path.read_text()


def test_load_yaml_ignores_non_mapping_roots(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("- item\n- item2\n")

    assert _load_yaml(config_path) == {}


def test_g3_main_dispatches_debug_subcommand(monkeypatch):
    parser = SimpleNamespace(
        parse_args=lambda: SimpleNamespace(command="debug"),
    )
    called = {}

    monkeypatch.setattr(g3, "build_parser", lambda: parser)
    monkeypatch.setattr(g3, "run_debug", lambda args: called.setdefault("args", args))

    g3.main()

    assert called["args"].command == "debug"


def test_debugger_does_not_confirm_or_fix_without_parseable_proof(tmp_path):
    debugger = Debugger.__new__(Debugger)
    debugger.working_dir = str(tmp_path)

    assert debugger._parse_tester_results("not json at all") == {}

    bug_a = BugEntry(
        id=1,
        file="a.py",
        line=1,
        description="bug a",
        severity="high",
        status="confirmed",
        test_file="tests/test_a.py",
    )
    bug_b = BugEntry(
        id=2,
        file="b.py",
        line=2,
        description="bug b",
        severity="high",
        status="confirmed",
        test_file="tests/test_b.py",
    )

    def fake_pytest(cmd, **kwargs):
        if "test_a.py" in cmd[3]:
            return subprocess.CompletedProcess(cmd, 0, ".", "")
        return subprocess.CompletedProcess(cmd, 1, "F", "")

    with patch("src.debugger.subprocess.run", side_effect=fake_pytest):
        fixed_count = debugger._verify_confirmed_fixes([bug_a, bug_b])

    assert fixed_count == 1
    assert bug_a.status == "fixed"
    assert bug_b.status == "confirmed"
