"""Tests for src/role_router.py — role → provider resolution."""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from src.errors import ProviderNotReadyError
from src.role_router import (
    RoleRouter,
    _ROLE_CONFIG_MAP,
    format_provider_display,
    _provider_model,
    _provider_account,
)


@dataclass
class FakeConfig:
    player_provider: str = "zai"
    player_model: str = "glm-5"
    coach_provider: str = "zai"
    coach_model: str = "glm-5"
    review_provider: str = ""
    review_model: str = ""
    coach_fallback_provider: str = ""
    coach_fallback_model: str = ""
    batch_pre_provider: str = "zai"
    batch_pre_model: str = ""
    batch_post_provider: str = "zai"
    batch_post_model: str = ""
    batch_judge_provider: str = "codex"
    batch_judge_model: str = ""


def _mock_provider(name: str = "mock", ready: bool = True):
    p = MagicMock()
    p.display_name = name
    p.check_ready.return_value = (ready, "" if ready else "not ready")
    env = MagicMock()
    env.model = "default-model"
    env.account_label = "test-account"
    p.env = env
    p.config = MagicMock()
    p.config.default_model = "default-model"
    return p


def _make_router(cfg=None, **overrides):
    cfg = cfg or FakeConfig(**overrides)
    player = _mock_provider("zai")
    coach = _mock_provider("zai")
    cache = {"zai": player, "claude": _mock_provider("claude")}

    def get_or_create(name):
        return cache.setdefault(name, _mock_provider(name))

    return RoleRouter(
        config=cfg,
        get_or_create_provider=get_or_create,
        player_provider=player,
        coach_provider=player,
    ), cache


class TestRoleConfigMap:
    def test_all_roles_have_provider_and_model_attrs(self):
        for role, (prov_attr, model_attr) in _ROLE_CONFIG_MAP.items():
            cfg = FakeConfig()
            assert hasattr(cfg, prov_attr), f"Missing attr {prov_attr} for role {role}"
            assert hasattr(cfg, model_attr), (
                f"Missing attr {model_attr} for role {role}"
            )


class TestProviderNameFor:
    def test_player_returns_configured(self):
        router, _ = _make_router()
        assert router.provider_name_for("player") == "zai"

    def test_coach_returns_configured(self):
        router, _ = _make_router()
        assert router.provider_name_for("coach") == "zai"

    def test_reviewer_falls_back_to_coach(self):
        router, _ = _make_router()
        assert router.provider_name_for("reviewer") == "zai"

    def test_reviewer_uses_explicit(self):
        router, _ = _make_router(cfg=FakeConfig(review_provider="claude"))
        assert router.provider_name_for("reviewer") == "claude"

    def test_coach_fallback_falls_back_to_coach_when_empty(self):
        router, _ = _make_router()
        assert router.provider_name_for("coach_fallback") == "zai"


class TestProviderFor:
    def test_player_returns_cached_instance(self):
        router, cache = _make_router()
        result = router.provider_for("player")
        assert result is cache["zai"]

    def test_coach_returns_cached_instance(self):
        router, cache = _make_router()
        result = router.provider_for("coach")
        assert result is cache["zai"]

    def test_unknown_role_raises(self):
        router, _ = _make_router()
        with pytest.raises(ValueError, match="Unknown role"):
            router._role_config("nonexistent")


class TestCheckRolesReady:
    def test_all_ready_succeeds(self):
        router, _ = _make_router()
        router.check_roles_ready(["player", "coach"])

    def test_not_ready_raises(self):
        router, _ = _make_router()
        not_ready = _mock_provider("broken", ready=False)
        router._player_provider = not_ready
        with pytest.raises(ProviderNotReadyError):
            router.check_roles_ready(["player"])

    def test_empty_roles_succeeds(self):
        router, _ = _make_router()
        router.check_roles_ready([])


class TestSwitchRole:
    def test_switch_coach_updates_config(self):
        cfg = FakeConfig()
        router, cache = _make_router(cfg=cfg)
        new_provider = _mock_provider("claude")
        cache["claude"] = new_provider

        label = router.switch_role("coach", "claude", "sonnet-4")
        assert cfg.coach_provider == "claude"
        assert cfg.coach_model == "sonnet-4"
        assert "claude" in label

    def test_switch_player_updates_config(self):
        cfg = FakeConfig()
        router, cache = _make_router(cfg=cfg)
        new_provider = _mock_provider("codex")
        cache["codex"] = new_provider

        label = router.switch_role("player", "codex", "o3")
        assert cfg.player_provider == "codex"
        assert cfg.player_model == "o3"

    def test_switch_unsupported_role_raises(self):
        router, _ = _make_router()
        with pytest.raises(ValueError, match="Unsupported"):
            router.switch_role("reviewer", "codex", "o3")

    def test_switch_coach_syncs_batch_when_aligned(self):
        cfg = FakeConfig(batch_pre_provider="zai", batch_pre_model="glm-5")
        router, cache = _make_router(cfg=cfg)
        cache["claude"] = _mock_provider("claude")

        router.switch_role("coach", "claude", "sonnet-4")
        assert cfg.batch_pre_provider == "claude"
        assert cfg.batch_pre_model == "sonnet-4"

    def test_switch_coach_does_not_sync_batch_when_different(self):
        cfg = FakeConfig(batch_pre_provider="codex", batch_pre_model="o3")
        router, cache = _make_router(cfg=cfg)
        cache["claude"] = _mock_provider("claude")

        router.switch_role("coach", "claude", "sonnet-4")
        assert cfg.batch_pre_provider == "codex"
        assert cfg.batch_pre_model == "o3"


class TestDisplayLabel:
    def test_display_label_contains_provider_name(self):
        router, _ = _make_router()
        label = router.display_label_for("player")
        assert "zai" in label

    def test_display_label_contains_model(self):
        router, _ = _make_router()
        label = router.display_label_for("player")
        assert "model=" in label


class TestResolveReview:
    def test_review_provider_defaults_to_coach(self):
        router, cache = _make_router()
        prov = router._resolve_review_provider()
        assert prov is cache["zai"]

    def test_review_provider_uses_explicit(self):
        cfg = FakeConfig(review_provider="claude")
        router, cache = _make_router(cfg=cfg)
        cache["claude"] = _mock_provider("claude")
        prov = router._resolve_review_provider()
        assert prov is cache["claude"]

    def test_review_model_uses_explicit(self):
        cfg = FakeConfig(review_model="sonnet-4")
        router, _ = _make_router(cfg=cfg)
        assert router._resolve_review_model() == "sonnet-4"

    def test_review_model_falls_back_to_coach_model(self):
        cfg = FakeConfig(review_model="", coach_model="glm-5")
        router, _ = _make_router(cfg=cfg)
        assert router._resolve_review_model() == "glm-5"


class TestFormatProviderDisplay:
    def test_basic_label(self):
        prov = _mock_provider()
        label = format_provider_display("zai", prov)
        assert "zai" in label
        assert "model=" in label

    def test_model_override(self):
        prov = _mock_provider()
        label = format_provider_display("zai", prov, model_override="custom-model")
        assert "custom-model" in label

    def test_includes_account(self):
        prov = _mock_provider()
        label = format_provider_display("zai", prov)
        assert "account=" in label


class TestProviderModelHelper:
    def test_reads_from_env(self):
        prov = MagicMock()
        prov.env = MagicMock(model="gpt-5")
        prov.config = MagicMock(default_model="fallback")
        assert _provider_model(prov) == "gpt-5"

    def test_reads_from_config_fallback(self):
        prov = MagicMock()
        prov.env = MagicMock(model="")
        prov.config = MagicMock(default_model="sonnet-4")
        assert _provider_model(prov) == "sonnet-4"

    def test_returns_empty_when_nothing(self):
        prov = MagicMock(spec=[])
        assert _provider_model(prov) == ""


class TestProviderAccountHelper:
    def test_reads_account_label(self):
        prov = MagicMock()
        prov.env = MagicMock(account_label="org-123")
        assert _provider_account(prov) == "org-123"

    def test_returns_empty_when_no_env(self):
        prov = MagicMock(spec=[])
        assert _provider_account(prov) == ""


class TestUpdatePreResolved:
    def test_update_player(self):
        router, _ = _make_router()
        new = _mock_provider("new-player")
        router.update_pre_resolved(player_provider=new)
        assert router._player_provider is new

    def test_update_coach(self):
        router, _ = _make_router()
        new = _mock_provider("new-coach")
        router.update_pre_resolved(coach_provider=new)
        assert router._coach_provider is new


class TestJudgeRole:
    def test_provider_for_judge_returns_batch_judge_provider(self):
        router, _ = _make_router(batch_judge_provider="codex")
        provider = router.provider_for("judge")
        assert provider is not None  # _get_or_create_provider("codex") called

    def test_provider_name_for_judge_with_configured_name_returns_it(self):
        router, _ = _make_router(batch_judge_provider="codex")
        assert router.provider_name_for("judge") == "codex"

    def test_provider_name_for_judge_with_empty_returns_codex_fallback(self):
        router, _ = _make_router(batch_judge_provider="")
        assert router.provider_name_for("judge") == "codex"

    def test_display_label_for_judge_does_not_raise(self):
        router, _ = _make_router(batch_judge_provider="codex")
        label = router.display_label_for("judge")
        assert "codex" in label
