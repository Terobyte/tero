"""Tests for new config defaults added in workspace refactor."""
from src.config import ResolvedConfig


def test_plan_file_default_is_requirements_md():
    cfg = ResolvedConfig()
    assert cfg.plan_file == "requirements.md"


def test_workspace_names_default_to_g_and_g1():
    cfg = ResolvedConfig()
    assert cfg.agent_a_workspace == "g"
    assert cfg.agent_b_workspace == "g1"
