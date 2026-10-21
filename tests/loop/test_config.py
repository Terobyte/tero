"""LoopConfig provider names: aliases yes, zai/kilo no."""

import pytest

from types import SimpleNamespace

from src.config import Config
from src.loop.config import (
    LoopConfig,
    UnknownProviderError,
    apply_saved_config,
    config_from_namespace,
    normalize_provider,
    split_role_spec,
)


def test_defaults_match_canon():
    cfg = LoopConfig()
    assert cfg.player_provider == "muse"
    assert cfg.player_model == "muse-spark-1.3"
    assert cfg.judge_provider == "codex"
    assert cfg.judge_model == "gpt-5.6-terra"
    assert cfg.tester_provider == "gemini"
    assert cfg.tester_model == "gemini-3.8-flash"
    assert cfg.max_rounds == 15


def test_aliases_map_to_live_providers():
    assert normalize_provider("cursor-headless") == "cursor"


def test_unknown_provider_zai_rejected():
    with pytest.raises(UnknownProviderError, match="zai"):
        normalize_provider("zai")


def test_unknown_provider_kilo_rejected():
    with pytest.raises(UnknownProviderError, match="kilo"):
        normalize_provider("kilo")


def test_split_role_spec():
    assert split_role_spec("muse:muse-spark-1.3") == ("muse", "muse-spark-1.3")
    assert split_role_spec("codex") == ("codex", "")


def test_config_from_namespace_player_spec():
    cfg = config_from_namespace(
        SimpleNamespace(
            plan="plan.md",
            working_dir=".",
            player_spec="muse:muse-spark-1.3",
            judge_spec="codex:gpt-5.6-terra",
            tester_spec=None,
            escalation_spec=None,
            no_escalation=False,
            test_command=None,
            test_command_scoped=None,
            max_rounds=12,
            verbose=False,
            no_menu=True,
        )
    )
    assert cfg.player_provider == "muse"
    assert cfg.judge_model == "gpt-5.6-terra"
    assert cfg.max_rounds == 12


def test_apply_saved_config_copies_menu_judge_and_tests():
    """Menu Start must feed v4 loop, including Judge stored as batch_judge_*."""
    loop = LoopConfig()
    saved = Config(
        working_dir="/tmp/cracy",
        plan_file="requirements.md",
        player_provider="opencode",
        player_model="opencode/muse-spark-1.3-contributor-free",
        batch_judge_provider="claude",
        batch_judge_model="claude-opus-5",
        tester_provider="gemini",
        tester_model="gemini-3.8-flash",
        test_command="./scripts/test.sh",
        coach_fallback_provider="",
        coach_fallback_model="",
    )
    args = SimpleNamespace(
        plan="requirements.md",
        player_spec=None,
        judge_spec=None,
        tester_spec=None,
        escalation_spec=None,
        no_escalation=False,
        test_command=None,
        test_command_scoped=None,
    )

    cfg = apply_saved_config(loop, saved, args)

    assert cfg.working_dir == "/tmp/cracy"
    assert cfg.plan_file == "requirements.md"
    assert cfg.player_provider == "opencode"
    assert cfg.player_model == "opencode/muse-spark-1.3-contributor-free"
    assert cfg.judge_provider == "claude"
    assert cfg.judge_model == "claude-opus-5"
    assert cfg.tester_provider == "gemini"
    assert cfg.test_command == "./scripts/test.sh"
    assert cfg.escalation_provider == ""


def test_apply_saved_config_copies_effort():
    loop = LoopConfig()
    saved = Config(
        player_provider="cursor",
        player_model="cursor-grok-4.6",
        player_effort="high",
        batch_judge_provider="codex",
        batch_judge_model="gpt-5.6-terra",
        batch_judge_effort="max",
        tester_provider="gemini",
        tester_model="gemini-3.8-flash",
        tester_effort="medium",
        coach_fallback_provider="claude",
        coach_fallback_model="claude-opus-5",
        coach_fallback_effort="xhigh",
    )
    args = SimpleNamespace(
        plan=None,
        player_spec=None,
        judge_spec=None,
        tester_spec=None,
        escalation_spec=None,
        no_escalation=False,
        test_command=None,
        test_command_scoped=None,
    )
    cfg = apply_saved_config(loop, saved, args)
    assert cfg.player_effort == "high"
    assert cfg.judge_effort == "max"
    assert cfg.tester_effort == "medium"
    assert cfg.escalation_effort == "xhigh"


def test_apply_saved_config_cli_judge_wins():
    args = SimpleNamespace(
        plan="plan.md",
        working_dir=".",
        verbose=False,
        no_menu=True,
        player_spec=None,
        judge_spec="codex:gpt-5.6-terra",
        tester_spec=None,
        escalation_spec=None,
        no_escalation=False,
        test_command=None,
        test_command_scoped=None,
        max_rounds=None,
    )
    loop = config_from_namespace(args)
    saved = Config(batch_judge_provider="claude", batch_judge_model="claude-opus-5")

    cfg = apply_saved_config(loop, saved, args)

    assert cfg.judge_provider == "codex"
    assert cfg.judge_model == "gpt-5.6-terra"
