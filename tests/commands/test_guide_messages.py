"""Фаза 1 (F1): гайд после кода 6 должен вести на `wait --messages`, иначе код 7 теряется."""

from __future__ import annotations

from src.commands.guide_cmds import HOSTS_GUIDE


def test_guide_after_code6_keeps_messages():
    after_6 = HOSTS_GUIDE.split("код `6`", 1)[1]
    assert "`wait --messages` снова" in after_6
    assert "запусти `wait` снова" not in after_6
