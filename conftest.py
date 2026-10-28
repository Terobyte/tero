"""Minimal asyncio support for the local test suite.

This keeps async tests runnable without requiring an external pytest plugin.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import signal
import threading
from pathlib import Path

import pytest

import src

_ROOT = Path(__file__).resolve().parent


def pytest_configure(config) -> None:
    # an installed tero from another checkout would make red→green fake
    if not Path(src.__file__).resolve().is_relative_to(_ROOT):
        raise pytest.UsageError(f"src imported from {src.__file__}, not from {_ROOT}")
    config.addinivalue_line("markers", "asyncio: run async test in a local event loop")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Keep every test away from the real ~/.tero (the menu saves there on start)."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    # golden по умолчанию включает jev и claude-эскалацию; тесты живут без пресета
    monkeypatch.setenv("TERO_PRESET", "none")
    return home


@pytest.fixture(autouse=True)
def _no_real_opencode_models(monkeypatch):
    """`opencode models` on the real CLI is a network call; tests pass their own command."""
    from src.providers import free_models

    real = free_models.list_models
    monkeypatch.setattr(
        free_models, "list_models", lambda command="opencode": [] if command == "opencode" else real(command)
    )


@pytest.fixture(autouse=True)
def _no_hud_window(monkeypatch, request):
    """No real HUD window from tests (phase 7+).

    `run()` goes through `RunSession`, which calls
    `src.loop.session.maybe_launch_hud`; on macOS with pywebview installed
    that would open a real window in every test calling `run()`.
    Only `tests/hud/test_launch_rule.py` exercises the real launcher.
    """
    nodeid = getattr(request.node, "nodeid", "") or ""
    if "tests/hud/test_launch_rule" in nodeid.replace("\\", "/"):
        return
    import src.loop.session as _session

    monkeypatch.setattr(_session, "maybe_launch_hud", lambda *a, **k: None)


def pytest_xdist_auto_num_workers(config):
    """`-n auto` = performance cores: each test forks hundreds of git calls,
    and workers on efficiency cores made the suite 2-3x slower, not faster."""
    import subprocess

    try:
        out = subprocess.run(["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                             capture_output=True, text=True, timeout=5).stdout
        return max(1, int(out.strip()))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# a lost wakeup must fail fast with a traceback, not sit out a 600 s phase timeout
TEST_TIMEOUT_S = float(os.environ.get("TERO_TEST_TIMEOUT", "120"))


class TestTimeout(Exception):
    pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if TEST_TIMEOUT_S <= 0 or threading.current_thread() is not threading.main_thread():
        yield
        return

    def _expired(signum, frame):
        raise TestTimeout(f"test exceeded {TEST_TIMEOUT_S:g}s (TERO_TEST_TIMEOUT)")

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, TEST_TIMEOUT_S)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def pytest_pyfunc_call(pyfuncitem):
    if "asyncio" not in pyfuncitem.keywords:
        return None

    test_func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_func):
        return None

    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(test_func(**kwargs))
    finally:
        asyncio.set_event_loop(None)
        loop.close()
    return True
