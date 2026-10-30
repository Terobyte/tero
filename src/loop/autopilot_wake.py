"""Окружение разбуженного агента (вынесено из autopilot.py, план 09, фаза 1).

autopilot.py реэкспортит `wake_env`, старые импорты продолжают работать.
"""

from __future__ import annotations

import os


def wake_env(host: str = "") -> dict[str, str]:
    """Environment of a woken agent: no provider keys (except for a Muse host), no
    identity of the session that started the owner."""
    from src.loop.host_id import strip_host_env
    from src.providers.claude_native import _BLOCKED_ENV_VARS

    env = dict(os.environ)
    strip_host_env(env)
    if host != "muse":
        for name in _BLOCKED_ENV_VARS:
            env.pop(name, None)
    return env
