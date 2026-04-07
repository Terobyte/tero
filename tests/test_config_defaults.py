"""Tests for new config defaults added in workspace refactor."""
from src.config import ResolvedConfig


def test_plan_file_default_is_requirements_md():
    cfg = ResolvedConfig()
    assert cfg.plan_file == "requirements.md"


def test_workspace_names_default_to_g_and_g1():
    cfg = ResolvedConfig()
    assert cfg.agent_a_workspace == "g"
    assert cfg.agent_b_workspace == "g1"


def test_config_preplan_defaults():
    """Pre-Planner config fields have correct opt-in defaults."""
    from src.config import Config

    cfg = Config()
    assert cfg.preplan_mode is False  # opt-in, like tdd_mode
    assert cfg.preplan_provider == "black"
    assert cfg.preplan_model == ""
    assert cfg.preplan_timeout_s == 120
