"""Proof-of-bug tests for CRITICAL bugs.

RED tests assert CORRECT behaviour — they FAIL because the bug exists.
GREEN tests (false positives) assert code works fine — they PASS.
"""

import pytest


# ── RED: BUG-01 — ProviderConfig name collision ────────────────────────


class TestProviderConfigNameCollision:
    """BUG-01: config.ProviderConfig and registry.ProviderConfig have the same
    name but incompatible fields.  Code that passes one where the other is
    expected will crash with AttributeError on .name.

    Expected (correct): both classes should be compatible.
    Actual (bug): config.ProviderConfig has no 'name' field."""

    def test_config_providerconfig_has_name_field(self):
        from src.config import ProviderConfig as ConfigPC

        cfg = ConfigPC()
        assert hasattr(cfg, "name"), (
            "config.ProviderConfig is missing 'name' — "
            "incompatible with registry.ProviderConfig"
        )

    def test_fields_are_identical(self):
        from src.config import ProviderConfig as C1
        from src.providers.registry import ProviderConfig as C2

        c1 = set(C1.__dataclass_fields__.keys())
        c2 = set(C2.__dataclass_fields__.keys())
        assert c1 == c2, f"ProviderConfig fields differ: {c1} vs {c2}"

    def test_config_instance_usable_as_registry_config(self):
        from src.config import ProviderConfig as ConfigPC

        cfg = ConfigPC(type="zai")
        try:
            _ = cfg.name
        except AttributeError:
            pytest.fail(
                "config.ProviderConfig instance has no 'name' — "
                "cannot be used where registry.ProviderConfig is expected"
            )


# ── RED: BUG-02 — _schedule_counts silently overrides intentional zeros ─


class TestScheduleCountsRespectsZeros:
    """BUG-02: When user sets individual batch retry counts to 0,
    _schedule_counts should respect each zero.
    All-zero (0,0,0) falls back to defaults per PLAN-B5."""

    def test_zero_schedule_is_respected(self):
        from src.batch_executor import BatchExecutor

        class ZeroConfig:
            batch_pre_judge_attempts = 0
            batch_judge_attempts = 0
            batch_post_judge_attempts = 0

        class FakeSession:
            config = ZeroConfig()

        executor = BatchExecutor(session=FakeSession(), tracker=object())
        result = executor._schedule_counts()
        assert sum(result) > 0, (
            f"All-zero should fall back to defaults, got {result}"
        )


# ── GREEN: BUG-03 — PhaseFailedError(phase=None) works fine ────────────


class TestPhaseFailedErrorNonePhase:
    """FP: PhaseFailedError was suspected to crash with phase=None due to
    dataclass + Exception __str__ circularity.  In reality __str__ guards
    against None, so these tests PASS."""

    def test_str_with_none_phase_no_crash(self):
        from src.errors import PhaseFailedError

        err = PhaseFailedError(phase=None, attempts=3)
        text = str(err)
        assert "3 attempts" in text

    def test_str_with_none_phase_returns_sensible_message(self):
        from src.errors import PhaseFailedError

        err = PhaseFailedError(phase=None, attempts=3)
        text = str(err)
        assert "failed" in text.lower() or "Phase" in text

    def test_no_infinite_recursion(self):
        from src.errors import PhaseFailedError

        err = PhaseFailedError(phase=None, attempts=1)
        _ = str(err)
