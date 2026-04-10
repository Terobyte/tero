"""Integration tests for Phase 0 pre-planning."""

import asyncio
from unittest.mock import MagicMock, patch

from src.config import Config
from src.prompts import PREPLANNER_SYSTEM_PROMPT, build_preplan_prompt


def test_phase_zero_fallback_on_step_count_mismatch(tmp_path, capsys):
    """If Pre-Planner changes step count, the session must fall back safely."""
    from src.coach_player import CoachPlayerSession, TurnResult

    cfg = Config(
        preplan_mode=True,
        preplan_provider="zai",
        working_dir=str(tmp_path),
    )

    raw_plan = "1. Step one\n2. Step two\n3. Step three"
    bad_output = "## Steps\n1. [devops] Step one\n2. [devops] Step two\n"

    with patch("src.coach_player.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (True, "")
        mock_create.return_value = mock_provider

        session = CoachPlayerSession(cfg, raw_plan, "")

        async def fake_run_turn(role, prompt, system_prompt, **kwargs):
            return TurnResult(
                role=role,
                duration_s=0.1,
                tools_used=0,
                messages=[],
                text=bad_output,
            )

        session._run_turn = fake_run_turn

        items, phases = asyncio.run(session._run_phase_zero(raw_plan))

    assert items == []
    assert phases == []
    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_phase_zero_preserves_done_flags_and_writes_enriched_plan(tmp_path):
    """Enriched plans should keep checkbox progress and be persisted to .g3."""
    from src.coach_player import CoachPlayerSession, TurnResult

    enriched_output = """\
## Phases
- Phase 1: "Setup" → steps 1-2

## Steps
1. [devops] Create pyproject.toml
2. [security] Add auth middleware
"""
    cfg = Config(
        preplan_mode=True,
        preplan_provider="zai",
        working_dir=str(tmp_path),
    )
    raw_plan = "- [x] Create pyproject.toml\n- [ ] Add auth middleware\n"

    with patch("src.coach_player.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (True, "")
        mock_create.return_value = mock_provider

        session = CoachPlayerSession(cfg, raw_plan, "")

        async def fake_run_turn(role, prompt, system_prompt, **kwargs):
            return TurnResult(
                role=role,
                duration_s=0.1,
                tools_used=0,
                messages=[],
                text=enriched_output,
            )

        session._run_turn = fake_run_turn
        items, phases = asyncio.run(session._run_phase_zero(raw_plan))

    assert [item.done for item in items] == [True, False]
    assert [step.done for step in phases[0].steps] == [True, False]
    enriched_path = tmp_path / ".g3" / "enriched-plan.md"
    assert enriched_path.exists()
    assert "## Phases" in enriched_path.read_text()


def test_preplan_mode_false_session_starts_without_registry(tmp_path):
    """Without preplanning, the session should not pre-populate a persona registry."""
    from src.coach_player import CoachPlayerSession

    cfg = Config(preplan_mode=False, working_dir=str(tmp_path))

    with patch("src.coach_player.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (True, "")
        mock_create.return_value = mock_provider

        session = CoachPlayerSession(cfg, "1. Step one", "")

    assert session._persona_registry is None


def test_phase_zero_skips_llm_when_plan_is_already_polished(tmp_path):
    """Already-enriched plans should move forward without another model turn."""
    from src.coach_player import CoachPlayerSession

    polished_plan = """\
## Phases
- Phase 1: "Setup" → steps 1-2

## Steps
1. [devops] Create pyproject.toml
2. [security] Add auth middleware
"""
    cfg = Config(
        preplan_mode=True,
        preplan_provider="zai",
        working_dir=str(tmp_path),
    )

    with patch("src.coach_player.create_provider") as mock_create, patch(
        "src.coach_player.PersonaRegistry"
    ) as mock_registry_cls:
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (True, "")
        mock_create.return_value = mock_provider

        mock_registry = MagicMock()
        mock_registry.available_roles.return_value = [
            {"name": "devops", "description": "CI/CD"},
            {"name": "security", "description": "Auth and vulns"},
        ]
        mock_registry_cls.return_value = mock_registry

        session = CoachPlayerSession(cfg, polished_plan, "")

        async def fail_run_turn(*args, **kwargs):
            raise AssertionError("preplanner turn should not run for an already polished plan")

        session._run_turn = fail_run_turn
        items, phases = asyncio.run(session._run_phase_zero(polished_plan))

    assert [item.roles for item in items] == [("devops",), ("security",)]
    assert phases[0].display_name == "Setup"
    enriched_path = tmp_path / ".g3" / "enriched-plan.md"
    assert enriched_path.exists()
    assert enriched_path.read_text() == polished_plan


def test_phase_zero_uses_single_quick_polish_turn(tmp_path):
    """Pre-planning should use a single quick polish pass, not a long agent loop."""
    from src.coach_player import CoachPlayerSession, TurnResult

    cfg = Config(
        preplan_mode=True,
        preplan_provider="zai",
        working_dir=str(tmp_path),
    )
    captured: dict[str, object] = {}
    enriched_output = """\
## Phases
- Phase 1: "Setup" → steps 1-2

## Steps
1. [devops] Create pyproject.toml
2. [security] Add auth middleware
"""

    with patch("src.coach_player.create_provider") as mock_create:
        mock_provider = MagicMock()
        mock_provider.check_ready.return_value = (True, "")
        mock_create.return_value = mock_provider

        session = CoachPlayerSession(
            cfg,
            "1. Create pyproject.toml\n2. Add auth middleware",
            "",
        )

        async def fake_run_turn(role, prompt, system_prompt, **kwargs):
            captured["role"] = role
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            captured.update(kwargs)
            return TurnResult(
                role=role,
                duration_s=0.1,
                tools_used=0,
                messages=[],
                text=enriched_output,
            )

        session._run_turn = fake_run_turn
        asyncio.run(
            session._run_phase_zero("1. Create pyproject.toml\n2. Add auth middleware")
        )

    assert captured["role"] == "preplanner"
    assert captured["max_turns"] == 1
    assert captured["disable_tools"] is True


def test_preplanner_prompt_is_text_only_and_allows_no_op_return():
    """Prompt should tell the model to judge and polish from text alone."""
    prompt = build_preplan_prompt(
        "- [ ] Create pyproject.toml",
        [{"name": "devops", "description": "CI/CD"}],
    )

    assert "Do NOT inspect the repository" in PREPLANNER_SYSTEM_PROMPT
    assert "If it is already polished, return it unchanged." in prompt
