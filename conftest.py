"""Minimal asyncio support for the local test suite.

This keeps async tests runnable without requiring an external pytest plugin.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "asyncio: run async test in a local event loop")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Keep every test away from the real ~/.g3 (the menu saves there on start)."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


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
